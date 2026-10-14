"""The workload client signs in over real TLS without bypassing browser CSRF checks."""

import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

import pytest
from conftest import TEST_PASSWORD
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import make_server

from scripts.production_workload import StaffClient, certificate, free_port


def test_workload_https_signin_cookies_and_cross_origin_rejection(app, users, tmp_path):
    app.config.update(
        WTF_CSRF_ENABLED=True, SESSION_COOKIE_SECURE=True, REMEMBER_COOKIE_SECURE=True
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    backend = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=backend.serve_forever, daemon=True)
    worker.start()
    cert, private = certificate(tmp_path)
    port = free_port()
    command = [
        sys.executable,
        "-m",
        "scripts.load_proxy",
        "--port",
        str(port),
        "--upstream",
        str(backend.server_port),
        "--certificate",
        str(cert),
        "--key",
        str(private),
    ]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    with (tmp_path / "proxy.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, **options)
        try:
            origin = f"https://127.0.0.1:{port}"
            client = StaffClient([origin], cert)
            for attempt in range(100):
                assert process.poll() is None
                try:
                    client.page("/login")
                    break
                except (URLError, ConnectionError):
                    if attempt == 99:
                        raise
                    time.sleep(0.05)
            client.post(
                "/login",
                {"username": "employee_user", "password": TEST_PASSWORD, "remember_me": "on"},
            )
            assert client.last_status == 302
            cookies = list(client.cookies)
            assert len(cookies) == 2 and all(cookie.secure for cookie in cookies)
            client.page("/employee/dashboard")
            hostile = Request(
                origin + "/attendance/shift",
                data=urlencode({"action": "clock_in", "csrf_token": client.csrf}).encode(),
                headers={"Referer": "https://wrong.example/"},
            )
            with pytest.raises(HTTPError) as refused:
                client.opener.open(hostile, timeout=5)
            assert refused.value.code == 400
            refused.value.close()
            client.post("/attendance/shift", {"action": "clock_in"})
            body, _ = client.page("/employee/dashboard")
            assert "On shift" in body
            client.post("/attendance/shift", {"action": "clock_out"})
        finally:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    process.terminate()
            process.wait(timeout=10)
            backend.shutdown()
            backend.server_close()
            worker.join(timeout=5)
