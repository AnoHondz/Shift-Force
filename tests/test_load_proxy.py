"""Exercise real TLS and forwarding so the Linux workload proxy cannot pass by inspection."""

import json
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

from scripts.production_workload import certificate, free_port


def test_tls_proxy_forwards_get_post_and_trusted_headers(tmp_path):
    class Backend(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            value = json.dumps(
                {
                    "method": self.command,
                    "body": body.decode(),
                    "forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                    "forwarded_for": self.headers.get("X-Forwarded-For"),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        do_GET = do_POST = handle_request

        def log_message(self, format, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    worker = threading.Thread(target=backend.serve_forever, daemon=True)
    worker.start()
    cert, private = certificate(tmp_path)
    tls_port = free_port()
    command = [
        sys.executable,
        "-m",
        "scripts.load_proxy",
        "--port",
        str(tls_port),
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
            context = ssl.create_default_context(cafile=str(cert))
            origin = f"https://127.0.0.1:{tls_port}"
            for attempt in range(100):
                assert process.poll() is None, "TLS proxy stopped unexpectedly"
                try:
                    with urlopen(origin + "/healthz", context=context, timeout=2) as response:
                        result = json.load(response)
                    break
                except (URLError, ConnectionError):
                    if attempt == 99:
                        raise
                    time.sleep(0.05)
            assert result == {
                "method": "GET",
                "body": "",
                "forwarded_proto": "https",
                "forwarded_for": "127.0.0.1",
            }
            request = Request(
                origin + "/form",
                data=b"safe-test-payload",
                headers={"X-Forwarded-For": "untrusted", "X-Forwarded-Proto": "http"},
            )
            with urlopen(request, context=context, timeout=3) as response:
                result = json.load(response)
            assert result == {
                "method": "POST",
                "body": "safe-test-payload",
                "forwarded_proto": "https",
                "forwarded_for": "127.0.0.1",
            }
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
