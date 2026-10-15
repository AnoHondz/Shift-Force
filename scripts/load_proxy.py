"""Loopback TLS terminator for the disposable production workload."""

import argparse
import ssl
from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream", type=int, required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()

    class Proxy(BaseHTTPRequestHandler):
        def handle_request(self):
            excluded = {"connection", "keep-alive", "transfer-encoding"}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
            headers.update(
                {"X-Forwarded-For": self.client_address[0], "X-Forwarded-Proto": "https"}
            )
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with closing(HTTPConnection("127.0.0.1", args.upstream, timeout=60)) as connection:
                connection.request(self.command, self.path, body=body, headers=headers)
                upstream = connection.getresponse()
                content = upstream.read()
                self.send_response(upstream.status)
                for name, value in upstream.getheaders():
                    if name.lower() not in excluded | {"content-length", "server", "date"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        do_GET = do_POST = handle_request

        def log_message(self, format, *args):
            pass  # No bodies, reset links, account names or cookies in verification logs.

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.certificate, args.key)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Proxy)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
