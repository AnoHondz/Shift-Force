"""Real HTTP proves the probe detects shared counters and requires both workers."""

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.production_workload import StaffClient, verify_shared_chat_limit


@contextmanager
def limiter_servers(shared=True, distinct_workers=True):
    counts, active, peaks = {}, [0], [0]
    lock = threading.Lock()
    servers, threads = [], []

    class Server(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            assert b"csrf_token=fixture-csrf" in payload
            assert self.path == "/s/fixture/api/chat/messages"
            group = "shared" if shared else self.server.server_port
            with lock:
                counts[group] = counts.get(group, 0) + 1
                status = 201 if counts[group] <= 29 else 429
                active[0] += 1
                peaks[0] = max(peaks[0], active[0])
            time.sleep(0.01)
            self.send_response(status)
            self.send_header(
                "X-Verification-Worker",
                str(self.server.server_port) if distinct_workers else "one-worker",
            )
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            with lock:
                active[0] -= 1

        def log_message(self, *args):
            pass

    try:
        for _ in range(2):
            server = Server(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            servers.append(server)
            threads.append(thread)
            thread.start()
        yield [f"http://127.0.0.1:{server.server_port}" for server in servers], peaks
    finally:
        for server, thread in zip(servers, threads, strict=True):
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_probe_bursts_both_http_workers_and_observes_one_shared_budget():
    with limiter_servers() as (origins, peaks):
        first = StaffClient(origins)
        first.csrf = "fixture-csrf"
        assert verify_shared_chat_limit(first, "/s/fixture", origins) == 29
        assert peaks[0] > 1


def test_probe_rejects_independent_per_worker_counters():
    with limiter_servers(shared=False) as (origins, _):
        first = StaffClient(origins)
        first.csrf = "fixture-csrf"
        with pytest.raises(AssertionError, match="actual shared chat limit"):
            verify_shared_chat_limit(first, "/s/fixture", origins)


def test_probe_requires_evidence_of_both_workers_even_with_a_shared_limit():
    with limiter_servers(distinct_workers=False) as (origins, _):
        first = StaffClient(origins)
        first.csrf = "fixture-csrf"
        with pytest.raises(AssertionError, match="both workers"):
            verify_shared_chat_limit(first, "/s/fixture", origins)
