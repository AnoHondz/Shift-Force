"""Coordinated encrypted backups; restore only into a new directory."""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from filelock import FileLock

MAGIC = b"SHIFTSPACE-BACKUP-1\n"
BLOCK = 1024 * 1024


def key_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}=", value):
        raise ValueError("Use a URL-safe base64 backup key containing 32 random bytes.")
    key = base64.b64decode(value, altchars=b"-_", validate=True)
    if len(key) != 32:
        raise ValueError("Use a URL-safe base64 backup key containing 32 random bytes.")
    return key


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_database(path):
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        if (
            connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or connection.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Backup database integrity or references failed.")


def encrypt_file(source, destination, key):
    nonce = secrets.token_bytes(12)
    encryptor = Cipher(algorithms.AES(key_bytes(key)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with Path(source).open("rb") as original, Path(destination).open("xb") as encrypted:
        os.chmod(destination, 0o600)
        encrypted.write(MAGIC + nonce)
        while block := original.read(BLOCK):
            encrypted.write(encryptor.update(block))
        encrypted.write(encryptor.finalize() + encryptor.tag)


def decrypt_file(source, destination, key):
    size = Path(source).stat().st_size
    if size <= len(MAGIC) + 12 + 16:
        raise ValueError("Invalid encrypted backup.")
    with Path(source).open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise ValueError("Unknown backup format.")
        nonce = encrypted.read(12)
        encrypted.seek(-16, 2)
        decryptor = Cipher(
            algorithms.AES(key_bytes(key)), modes.GCM(nonce, encrypted.read(16))
        ).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        encrypted.seek(len(MAGIC) + 12)
        remaining = size - len(MAGIC) - 12 - 16
        with Path(destination).open("xb") as output:
            os.chmod(destination, 0o600)
            while remaining:
                block = encrypted.read(min(BLOCK, remaining))
                if not block:
                    raise ValueError("Incomplete encrypted backup.")
                remaining -= len(block)
                output.write(decryptor.update(block))
            output.write(decryptor.finalize())  # Authentication before any restored file is used.


def create_bundle(hub, stores, destination, key):
    hub = Path(hub).resolve(strict=True)
    stores, destination = Path(stores).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Backups never overwrite existing files.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        FileLock(str(hub.parent / ".backup-operation.lock")),
        tempfile.TemporaryDirectory() as directory,
        ExitStack() as stack,
    ):
        scratch = Path(directory)
        hub_lock = stack.enter_context(closing(sqlite3.connect(hub, timeout=30)))
        # Once every IMMEDIATE lock is held, no hub/store writer can change the set.
        hub_lock.execute("BEGIN IMMEDIATE")
        has_stores = hub_lock.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stores'"
        ).fetchone()
        keys = (
            [
                row[0]
                for row in hub_lock.execute("SELECT database_key FROM stores ORDER BY database_key")
            ]
            if has_stores
            else []
        )
        paths = {"hub.db": hub}
        for key_value in keys:
            if not re.fullmatch(r"[a-f0-9]{32}", key_value):
                raise ValueError("Invalid registered store storage key.")
            path = (stores / (key_value + ".db")).resolve(strict=True)
            if path.parent != stores:
                raise ValueError("Store database leaves the registered storage directory.")
            paths["stores/" + key_value + ".db"] = path
            writer = stack.enter_context(closing(sqlite3.connect(path, timeout=30)))
            writer.execute("BEGIN IMMEDIATE")
        manifest = {"format": 1, "files": {}}
        for name, original in paths.items():
            copied = scratch / name
            copied.parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(copied)) as backup,
            ):
                source.backup(backup)
            validate_database(copied)
            manifest["files"][name] = {"sha256": digest(copied), "size": copied.stat().st_size}
        archive_path = scratch / "bundle.zip"
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name in paths:
                archive.write(scratch / name, name)
        encrypt_file(archive_path, destination, key)
    return manifest


def restore_bundle(source, destination, key):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Restore into a new directory; live databases are never overwritten.")
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        archive_path = scratch / "verified.zip"
        decrypt_file(source, archive_path, key)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.getinfo("manifest.json").file_size > BLOCK:
                raise ValueError("Invalid backup inventory.")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format") != 1
                or not isinstance(manifest.get("files"), dict)
                or "hub.db" not in manifest["files"]
            ):
                raise ValueError("Invalid backup manifest.")
            files = manifest["files"]
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != {"manifest.json", *files}:
                raise ValueError("Backup inventory does not match archive.")
            for name, metadata in files.items():
                if name != "hub.db" and not re.fullmatch(r"stores/[a-f0-9]{32}\.db", name):
                    raise ValueError("Unsafe backup path.")
                copied = scratch / name
                copied.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as original, copied.open("xb") as output:
                    shutil.copyfileobj(original, output, BLOCK)
                if (
                    copied.stat().st_size != metadata["size"]
                    or digest(copied) != metadata["sha256"]
                ):
                    raise ValueError("Backup checksum mismatch.")
                validate_database(copied)
            with closing(
                sqlite3.connect((scratch / "hub.db").as_uri() + "?mode=ro", uri=True)
            ) as hub:
                has_stores = hub.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stores'"
                ).fetchone()
                expected = (
                    {
                        "stores/" + row[0] + ".db"
                        for row in hub.execute("SELECT database_key FROM stores")
                    }
                    if has_stores
                    else set()
                )
                if expected != set(files) - {"hub.db"}:
                    raise ValueError("Restored hub does not match its store databases.")
            destination.mkdir(parents=True, mode=0o700, exist_ok=False)
            for name in files:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output, (scratch / name).open("rb") as original:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(original, output, BLOCK)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--hub", type=Path, required=True)
    backup.add_argument("--stores", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not key:
        parser.error("Set BACKUP_ENCRYPTION_KEY privately; never pass keys as command arguments.")
    if args.command == "backup":
        manifest = create_bundle(args.hub, args.stores, args.destination, key)
    else:
        manifest = restore_bundle(args.source, args.destination, key)
    print(
        json.dumps(
            {"status": "verified", "databases": len(manifest["files"]), "operation": args.command}
        )
    )


if __name__ == "__main__":
    main()
