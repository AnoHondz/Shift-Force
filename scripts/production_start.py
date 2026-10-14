"""Explicit offline migration/backup phase, then supervised Gunicorn workers."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from app.models import Store
from app.stores import store_snapshot
from scripts.backup_scheduler import backup_cycle, maintenance, settings
from scripts.backup_store_bundle import create_bundle


def initialize(app):
    config = settings()
    root = Path(config["DATA_ROOT"]).resolve()
    hub = Path(config["HUB_DATABASE_PATH"])
    backup = root / "backups"
    backup.mkdir(parents=True, exist_ok=True)
    with maintenance(root), app.app_context():
        if hub.exists() and hub.stat().st_size:
            import secrets

            create_bundle(
                hub,
                config["STORE_DATABASE_ROOT"],
                backup / ("pre-migration-" + secrets.token_hex(8) + ".enc"),
                config["BACKUP_ENCRYPTION_KEY"],
            )
        upgrade()
        stores = [store_snapshot(store) for store in Store.query.all()]
        for info in stores:
            if not app.extensions["stores"].database_path(info).is_file():
                raise RuntimeError(
                    "A registered store database is missing. Restore it before startup."
                )
            child = app.extensions["stores"].build(info, provisioning=True)
            with child.app_context():
                try:
                    upgrade()
                finally:
                    db.session.remove()
                    db.engine.dispose()
    backup_cycle(config)


def main():
    if sys.platform == "win32":
        raise RuntimeError("The production launcher targets Linux; use the local demo on Windows.")

    def initialize_shutdown(signum, frame):
        # Offline migration/backup contexts must release their maintenance marker.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, initialize_shutdown)
    signal.signal(signal.SIGINT, initialize_shutdown)
    app = create_app()
    if app.config.get("APP_ENV") != "production":
        raise RuntimeError("Production launcher requires APP_ENV=production.")
    initialize(app)
    server = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "--config", "gunicorn.conf.py", "run:app"]
    )
    backups = subprocess.Popen([sys.executable, "-m", "scripts.backup_scheduler"])

    def stop(signum, frame):
        for process in (server, backups):
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while server.poll() is None:
            if backups.poll() is not None:
                print(json.dumps({"event": "backup_worker_stopped"}), flush=True)
                server.terminate()
                return 1
            time.sleep(1)
        return server.returncode
    finally:
        stop(None, None)
        for process in (server, backups):
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
