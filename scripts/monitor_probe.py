"""Run outside the application host; output health only, without workforce data."""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def probe(origin):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Monitor a canonical HTTPS origin.")
    started = time.monotonic()
    try:
        with urlopen(origin.rstrip("/") + "/readyz", timeout=10) as response:
            result = json.load(response)
            ready = response.status == 200 and result.get("status") == "ready"
    except (HTTPError, URLError, TimeoutError, ValueError):
        ready = False
    return {
        "event": "external_readiness",
        "healthy": ready,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    result = probe(args.origin)
    print(json.dumps(result))
    raise SystemExit(0 if result["healthy"] else 1)


if __name__ == "__main__":
    main()
