"""Disposable workload server; never used by the production launcher."""

import json
import os
from pathlib import Path

from app import create_app


def application():
    path = Path(os.environ["SHIFTSPACE_LOAD_CONFIG"])
    app = create_app(json.loads(path.read_text()))

    wrapped = app.wsgi_app

    def verification_worker(environ, start_response):
        def record_worker(status, headers, exc_info=None):
            # Cover child store dispatch too; never installed by the real production launcher.
            headers.append(("X-Verification-Worker", str(os.getpid())))
            return start_response(status, headers, exc_info)

        return wrapped(environ, record_worker)

    app.wsgi_app = verification_worker

    return app


if __name__ == "__main__":
    from waitress import serve

    serve(application(), host="127.0.0.1", port=int(os.environ["PORT"]), threads=4)
