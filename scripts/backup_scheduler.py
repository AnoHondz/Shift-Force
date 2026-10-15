"""Daily encrypted off-host backup and verified restore, beside the web master."""

import json
import logging
import os
import secrets
import signal
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import boto3
from filelock import FileLock

from .backup_store_bundle import create_bundle, digest, restore_bundle

logger = logging.getLogger("shiftspace.backups")


def install_shutdown_handlers():
    # Let maintenance's finally block remove only this process's marker on deploy shutdown.
    def shutdown(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)


@contextmanager
def maintenance(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / ".maintenance-control.lock"), timeout=60):
        marker = root / "maintenance.lock"
        nonce = secrets.token_hex(16)
        with marker.open("x") as file:
            json.dump({"owner": nonce, "pid": os.getpid(), "created_at": time.time()}, file)
        try:
            yield
        finally:
            if (
                marker.parent == root
                and marker.exists()
                and json.loads(marker.read_text()).get("owner") == nonce
            ):
                marker.unlink()


def backup_cycle(config, client=None):
    root = Path(config["DATA_ROOT"]).resolve()
    directory = root / "backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4) + ".enc"
    )
    bucket = config.get("BACKUP_BUCKET")
    if not bucket:
        raise ValueError("Configure an owned, private BACKUP_BUCKET for off-host backups.")
    key = config["BACKUP_ENCRYPTION_KEY"]
    hub = config["HUB_DATABASE_PATH"]
    stores = config["STORE_DATABASE_ROOT"]
    # Existing in-flight writers finish before the SQLite locks are acquired.
    with maintenance(root):
        manifest = create_bundle(hub, stores, destination, key)
    client = client or boto3.client("s3")
    object_key = config.get("BACKUP_PREFIX", "shiftspace").strip("/") + "/" + destination.name
    checksum = digest(destination)
    client.upload_file(
        str(destination),
        bucket,
        object_key,
        ExtraArgs={"ServerSideEncryption": "AES256", "Metadata": {"sha256": checksum}},
    )
    with tempfile.TemporaryDirectory() as scratch:
        downloaded = Path(scratch) / "backup.enc"
        client.download_file(bucket, object_key, str(downloaded))
        if digest(downloaded) != checksum:
            raise ValueError("Off-host backup verification failed.")
        restored = restore_bundle(downloaded, Path(scratch) / "restore-drill", key)
        if restored != manifest:
            raise ValueError("Restored inventory differs from the backup.")
    stamp = {
        "offsite_verified_at": time.time(),
        "restore_verified_at": time.time(),
        "databases": len(manifest["files"]),
    }
    temporary = directory / (".status-" + secrets.token_hex(8) + ".json")
    temporary.write_text(json.dumps(stamp))
    os.chmod(temporary, 0o600)
    temporary.replace(directory / "status.json")
    # Retain two local encrypted snapshots; remote retention is an operator bucket policy.
    for old in sorted(directory.glob("*.enc"), key=lambda p: p.stat().st_mtime, reverse=True)[2:]:
        if old.resolve().parent == directory.resolve():
            old.unlink()
    logger.info(json.dumps({"event": "backup_restore_verified", **stamp}))
    return stamp


def settings():
    from sqlalchemy.engine import make_url

    from config import Config

    config = {key: getattr(Config, key) for key in dir(Config) if key.isupper()}
    config["HUB_DATABASE_PATH"] = make_url(config["SQLALCHEMY_DATABASE_URI"]).database
    return config


def main():
    install_shutdown_handlers()
    logging.basicConfig(level=logging.INFO)
    config = settings()
    interval = int(config.get("BACKUP_INTERVAL_SECONDS", 86400))
    if interval < 60:
        raise ValueError("Backup interval must be at least one minute.")
    while True:
        time.sleep(interval)
        failures = 0
        while True:
            try:
                backup_cycle(config)
                break
            except Exception as error:
                failures += 1
                # Retain the last good status; readiness fails when backups grow stale.
                logger.error(
                    json.dumps(
                        {
                            "event": "backup_failed",
                            "error_type": type(error).__name__,
                            "attempt": failures,
                        }
                    )
                )
                time.sleep(min(300 * failures, 1800))


if __name__ == "__main__":
    main()
