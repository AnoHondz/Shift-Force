"""Five-store/100-staff HTTP workload against separate real server processes.

Windows runs two Waitress processes without claiming Redis/TLS production coverage.
Linux with --redis-uri runs the actual two-worker Gunicorn config behind local TLS.
All credentials, certificates and databases are disposable; no live app data is read.
"""

import argparse
import copy
import html
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from flask_migrate import upgrade
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Employee, Role, ScheduledShift, Store, User
from app.stores import store_snapshot
from scripts.backup_store_bundle import create_bundle, restore_bundle


def free_port():
    with closing(socket.socket()) as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def certificate(root):
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable Shiftspace test")])
    now = datetime.now(timezone.utc)
    signed = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert, private = root / "certificate.pem", root / "private.pem"
    cert.write_bytes(signed.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(private, 0o600)
    return cert, private


@contextmanager
def database_context(app):
    with app.app_context():
        try:
            yield
        finally:
            db.session.remove()
            db.engine.dispose()


def seed(config, password):
    app = create_app({**config, "APP_ENV": "development", "RATELIMIT_ENABLED": False})
    credentials = []
    password_hash = generate_password_hash(password)
    with database_context(app):
        upgrade()
        owner = User(
            username="verification-owner",
            role=Role.OWNER,
            password_hash=password_hash,
            employee=Employee(
                first_name="Verification",
                last_name="Owner",
                email="owner@example.invalid",
                hourly_rate=0,
            ),
        )
        db.session.add(owner)
        db.session.commit()
        owner_id = owner.id
    run_id = secrets.token_hex(4)
    for store_index in range(5):
        with database_context(app):
            store = Store(
                owner_id=owner_id,
                owner_account_id=1,
                name="Disposable Virginia store",
                location=f"Verification location {store_index}",
                slug=f"verification-{run_id}-{store_index}",
                timezone="America/New_York",
                database_key=secrets.token_hex(16),
            )
            db.session.add(store)
            db.session.flush()
            info = store_snapshot(store)
            child = app.extensions["stores"].provision(
                info,
                dict(
                    username="verification-owner",
                    first_name="Verification",
                    last_name="Owner",
                    email="owner@example.invalid",
                ),
            )
            db.session.commit()
        with database_context(child):
            for staff_index in range(20):
                marker = f"private-{store_index}-{staff_index:02d}"
                employee = Employee(
                    first_name="Synthetic",
                    last_name=f"Crew{store_index}{staff_index:02d}",
                    email=f"crew-{store_index}-{staff_index}@example.invalid",
                    hourly_rate=16,
                )
                user = User(
                    username=f"crew-{store_index}-{staff_index}",
                    role=Role.EMPLOYEE,
                    employee=employee,
                    password_hash=password_hash,
                )
                db.session.add(user)
                db.session.flush()
                db.session.add(
                    ScheduledShift(
                        employee_id=employee.id,
                        starts_at=datetime(2026, 10, 19, 13),
                        ends_at=datetime(2026, 10, 19, 21),
                        notes=marker,
                    )
                )
                credentials.append(dict(slug=info["slug"], username=user.username, marker=marker))
            db.session.commit()
    return credentials


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StaffClient:
    def __init__(self, origins, cert=None, cookies=None):
        self.origins = origins
        self.cookies = cookies or CookieJar()
        handlers = [HTTPCookieProcessor(self.cookies), NoRedirect()]
        if cert:
            handlers.append(HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
        self.opener = build_opener(*handlers)
        self.count = 0
        self.workers = set()
        self.durations = []
        self.csrf = None
        self.last_status = None

    def request(self, path, data=None, expected=200):
        self.count += 1
        origin = self.origins[self.count % len(self.origins)]
        payload = urlencode(data).encode() if data is not None else None
        headers = {"Connection": "close"}
        if payload is not None:
            # Match browser HTTPS POSTs; Flask-WTF intentionally requires same-origin referrers.
            headers.update({"Referer": origin + "/", "Origin": origin})
        request = Request(origin + path, data=payload, headers=headers)
        started = time.monotonic()
        try:
            response = self.opener.open(request, timeout=60)
        except HTTPError as error:
            response = error
        with response:
            self.last_status = response.status
            body = response.read().decode()
            self.durations.append(time.monotonic() - started)
            worker = response.headers.get("X-Verification-Worker")
            if worker:
                self.workers.add(worker)
            assert response.status in (expected if isinstance(expected, tuple) else (expected,)), (
                f"Unexpected HTTP {response.status} on {path.split('?')[0]}"
            )
            return body, response.headers

    def page(self, path):
        body, headers = self.request(path)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', body)
        if match:
            self.csrf = html.unescape(match[1])
        return body, headers

    def post(self, path, data, expected=302):
        assert self.csrf, "CSRF token missing from the actual page"
        return self.request(path, {**data, "csrf_token": self.csrf}, expected)


def staff_workflow(credential, password, origins, cert, production, markers):
    client = StaffClient(origins, cert)
    prefix = "/s/" + credential["slug"]
    client.page(prefix + "/login")
    client.post(
        prefix + "/login",
        dict(username=credential["username"], password=password, remember_me="on"),
    )
    cookies = list(client.cookies)
    assert cookies and all(cookie.path == prefix for cookie in cookies)
    assert all(cookie.has_nonstandard_attr("HttpOnly") for cookie in cookies)
    if production:
        assert all(cookie.secure for cookie in cookies), "Production issued an insecure cookie"
    client.page(prefix + "/employee/dashboard")
    schedule, _ = client.page(prefix + "/schedule?week=2026-10-19")
    assert credential["marker"] in schedule, "The staff schedule did not show their shift"
    assert all(marker not in schedule for marker in markers if marker != credential["marker"]), (
        "Employee schedule exposed another person's shift"
    )
    client.post(prefix + "/attendance/shift", dict(action="clock_in"))
    dashboard, _ = client.page(prefix + "/employee/dashboard")
    assert "On shift" in dashboard
    client.post(prefix + "/api/chat/messages", dict(body=credential["marker"]), expected=201)
    client.post(prefix + "/attendance/shift", dict(action="clock_out"))
    attendance, _ = client.page(prefix + "/attendance/")
    assert "Synthetic" in attendance
    messages, _ = client.request(prefix + "/api/chat/messages")
    messages = json.loads(messages)["messages"]
    assert all(
        message["body"].startswith("private-" + credential["slug"][-1] + "-")
        for message in messages
    )
    client.page(prefix + "/schedule/availability")
    client.post(
        prefix + "/schedule/availability",
        {"version_id": "0", **{f"mode_{day}": "any" for day in range(7)}},
    )
    client.page(prefix + "/schedule/coverage")
    personal_calendar, _ = client.request(prefix + "/schedule/calendar.ics?week=2026-10-19")
    assert credential["marker"] in personal_calendar
    assert all(
        marker not in personal_calendar for marker in markers if marker != credential["marker"]
    )
    client.page(prefix + "/payroll/clock-hours")
    return client, credential


def wait_for_server(client, processes):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("Disposable workload server exited during startup")
        try:
            client.request("/readyz")
            return
        except (URLError, ConnectionError, AssertionError):
            time.sleep(0.1)
    raise RuntimeError("Disposable workload server did not become ready")


def verify_databases(root, expected_records=100):
    records = 0
    for path in (root / "stores").glob("*.db"):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 21
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM attendance_records WHERE clock_out_at IS NULL"
                ).fetchone()[0]
                == 0
            )
            count = connection.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
            records += count
            assert connection.execute("SELECT COUNT(*) FROM scheduled_shifts").fetchone()[0] == 20
            assert (
                connection.execute("SELECT COUNT(*) FROM availability_profiles").fetchone()[0] == 20
            )
            assert connection.execute("SELECT COUNT(*) FROM payroll_records").fetchone()[0] == 0
    assert records == expected_records, (
        f"Expected {expected_records} completed punches, got {records}"
    )


def sample_memory(master_pid, stop, samples):
    """Actual Linux process RSS for the web master and its worker children, excluding TLS fixture."""
    while not stop.wait(0.1):
        try:
            children = Path(f"/proc/{master_pid}/task/{master_pid}/children").read_text().split()
            total = 0
            for pid in [str(master_pid), *children]:
                status = Path(f"/proc/{pid}/status").read_text()
                match = re.search(r"^VmRSS:\s+(\d+) kB", status, flags=re.MULTILINE)
                if match:
                    total += int(match[1])
            samples.append(total / 1024)
        except (OSError, ValueError):
            pass  # A worker may recycle between samples; no invented zero-memory result.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-uri", help="Disposable Redis test DB; never use the production DB")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    production = bool(options.redis_uri)
    if production and sys.platform == "win32":
        parser.error("Run the Gunicorn/Redis/HTTPS production workload on Linux")
    if production:
        from redis import Redis

        redis_url = urlsplit(options.redis_uri)
        if (
            redis_url.scheme != "redis"
            or redis_url.hostname not in {"127.0.0.1", "localhost"}
            or redis_url.path != "/15"
            or redis_url.username
            or redis_url.password
        ):
            parser.error("Use a disposable loopback Redis service and test database15")
        storage = Redis.from_url(options.redis_uri)
        assert storage.ping(), "The actual shared limiter service is unavailable"
    processes = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="shiftspace-workload-") as directory:
        root = Path(directory)
        ports = [free_port(), free_port()]
        tls_port = free_port()
        origins = [f"http://127.0.0.1:{port}" for port in ports]
        if production:
            origins = [f"https://127.0.0.1:{tls_port}"]
        config = dict(
            SECRET_KEY=secrets.token_hex(32),
            SQLALCHEMY_DATABASE_URI="sqlite:///" + (root / "hub.db").as_posix(),
            STORE_DATABASE_ROOT=str(root / "stores"),
            DATA_ROOT=str(root),
            APP_ENV="production" if production else "development",
            RATELIMIT_ENABLED=production,
            RATELIMIT_STORAGE_URI=options.redis_uri or "memory://",
            SESSION_COOKIE_SECURE=production,
            REMEMBER_COOKIE_SECURE=production,
            MFA_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            REQUIRE_PRIVILEGED_MFA=production,
            PUBLIC_REGISTRATION=False,
            PUBLIC_STORE_OWNER_REGISTRATION=False,
            PUBLIC_ORIGIN=origins[0] if production else "",
            TRUST_PROXY=production,
            WORKFORCE_TIMEZONE="America/New_York",
        )
        password = secrets.token_urlsafe(24)
        credentials = seed(config, password)
        backup_dir = root / "backups"
        backup_dir.mkdir()
        # Workload fixture only: off-host backup verification has separate actual restore tests.
        (backup_dir / "status.json").write_text(
            json.dumps(dict(offsite_verified_at=time.time(), restore_verified_at=time.time()))
        )
        configuration = root / "config.json"
        configuration.write_text(json.dumps(config))
        os.chmod(configuration, 0o600)
        cert, key = certificate(root) if production else (None, None)
        environment = {**os.environ, "SHIFTSPACE_LOAD_CONFIG": str(configuration)}
        output = root / "server.log"
        memory_stop = threading.Event()
        memory_thread = None
        memory_samples = []
        with output.open("wb") as log:
            try:
                if production:
                    command = [
                        sys.executable,
                        "-m",
                        "gunicorn",
                        "--config",
                        "gunicorn.conf.py",
                        "--bind",
                        f"127.0.0.1:{ports[0]}",
                        "scripts.load_server:application()",
                    ]
                    processes.append(
                        subprocess.Popen(command, env=environment, stdout=log, stderr=log)
                    )
                    processes.append(
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "scripts.load_proxy",
                                "--port",
                                str(tls_port),
                                "--upstream",
                                str(ports[0]),
                                "--certificate",
                                str(cert),
                                "--key",
                                str(key),
                            ],
                            stdout=log,
                            stderr=log,
                        )
                    )
                else:
                    for port in ports:
                        processes.append(
                            subprocess.Popen(
                                [sys.executable, "-m", "scripts.load_server"],
                                env={**environment, "PORT": str(port)},
                                stdout=log,
                                stderr=log,
                            )
                        )
                probe = StaffClient(origins, cert)
                wait_for_server(probe, processes)
                if production:
                    memory_thread = threading.Thread(
                        target=sample_memory,
                        args=(processes[0].pid, memory_stop, memory_samples),
                        daemon=True,
                    )
                    memory_thread.start()
                markers = [entry["marker"] for entry in credentials]
                workload_started = time.monotonic()
                with ThreadPoolExecutor(max_workers=24) as pool:
                    completed = list(
                        pool.map(
                            lambda entry: staff_workflow(
                                entry, password, origins, cert, production, markers
                            ),
                            credentials,
                        )
                    )
                workload_seconds = time.monotonic() - workload_started
                workers = set().union(*(client.workers for client, _ in completed))
                assert len(workers) == 2, (
                    f"Expected two actual server worker processes, got {len(workers)}"
                )
                durations = sorted(value for client, _ in completed for value in client.durations)
                verify_databases(root)
                first, credential = completed[0]
                prefix = "/s/" + credential["slug"]
                # Simultaneous duplicate clock-ins across processes must produce one active punch.
                raced = []
                for index in range(2):
                    cookies = CookieJar()
                    for cookie in first.cookies:
                        cookies.set_cookie(copy.copy(cookie))
                    raced.append(StaffClient([origins[index % len(origins)]], cert, cookies))
                for client in raced:
                    client.csrf = first.csrf
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(
                        pool.map(
                            lambda client: client.post(
                                prefix + "/attendance/shift",
                                dict(action="clock_in"),
                                expected=(302, 409),
                            ),
                            raced,
                        )
                    )
                first.post(prefix + "/attendance/shift", dict(action="clock_out"))
                verify_databases(root, expected_records=101)
                limiter_verified = False
                limiter_accepted = None
                if production:
                    # Allow the earlier message's minute window to have expired during a slow run.
                    # An unshared limiter would accept all31 when split between both processes.
                    limiter_workers = set()
                    limiter_accepted = 0
                    for index in range(31):
                        _, headers = first.post(
                            prefix + "/api/chat/messages",
                            dict(body=f"limit-check-{index}"),
                            expected=(201, 429),
                        )
                        limiter_workers.add(headers["X-Verification-Worker"])
                        if first.last_status == 429:
                            break
                        limiter_accepted += 1
                    assert limiter_accepted in {29, 30} and first.last_status == 429, (
                        "The actual shared chat limit did not reject the31st message"
                    )
                    assert len(limiter_workers) == 2, (
                        "The rate-limit probe did not reach both workers"
                    )
                    limiter_verified = True
                encryption_key = Fernet.generate_key().decode()
                archive = root / "completed-workload.enc"
                manifest = create_bundle(root / "hub.db", root / "stores", archive, encryption_key)
                restored = root / "restored"
                assert restore_bundle(archive, restored, encryption_key) == manifest
                verify_databases(restored, expected_records=101)
                # Store tables and every row, including audit/auth/notifications/chat, must match.
                for name in manifest["files"]:
                    with (
                        closing(
                            sqlite3.connect(root / ("hub.db" if name == "hub.db" else name))
                        ) as original,
                        closing(sqlite3.connect(restored / name)) as recovered,
                    ):
                        assert list(original.iterdump()) == list(recovered.iterdump())
                report = dict(
                    status="passed",
                    stores=5,
                    staff=100,
                    concurrent_clients=24,
                    server="Gunicorn" if production else "Waitress",
                    actual_worker_processes=len(workers),
                    https_verified=production,
                    shared_redis_limit_verified=limiter_verified,
                    shared_limit_accepted_messages=limiter_accepted,
                    workload_requests=len(durations),
                    workload_seconds=round(workload_seconds, 2),
                    request_p95_seconds=round(durations[int(len(durations) * 0.95)], 3),
                    peak_server_rss_mb=round(max(memory_samples), 1) if memory_samples else None,
                    restored_databases=len(manifest["files"]),
                    completed_punches=101,
                    availability_profiles_verified=100,
                    total_seconds=round(time.monotonic() - started, 2),
                    limits="Synthetic loopback workload; no hosted-network, long soak, or outage evidence.",
                )
                if options.report:
                    options.report.parent.mkdir(parents=True, exist_ok=True)
                    options.report.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report))
            except Exception:
                # The disposable server log excludes secrets/request bodies; preserve useful failure details.
                log.flush()
                print(output.read_text(errors="replace")[-4000:], file=sys.stderr)
                raise
            finally:
                memory_stop.set()
                if memory_thread:
                    memory_thread.join(timeout=1)
                for process in reversed(processes):
                    if process.poll() is None:
                        if sys.platform == "win32":
                            # Windows virtualenv executables can launch a second Python process.
                            # Kill only this recorded disposable server's process tree.
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                        else:
                            process.terminate()
                for process in reversed(processes):
                    try:
                        process.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
