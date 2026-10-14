"""Inventory historical credential/data exposure without printing sensitive contents."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args])


def historical_inventory():
    exposed = []
    secrets = set()
    paths = set()
    for commit in git("rev-list", "--all").decode().splitlines():
        names = git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
        sensitive = [
            name
            for name in names
            if name == ".env" or re.search(r"\.(?:db|sqlite|sqlite3)(?:\.|$)", name, re.I)
        ]
        if sensitive:
            exposed.append({"commit": commit, "paths": sensitive})
            paths.update(sensitive)
        if ".env" in names:
            content = git("show", commit + ":.env").decode(errors="replace")
            for line in content.splitlines():
                key, separator, value = line.partition("=")
                if (
                    separator
                    and re.search("SECRET|PASSWORD|SIGNUP_CODE", key, re.I)
                    and len(value.strip().strip("\"'")) >= 8
                ):
                    secrets.add(value.strip().strip("\"'"))
    return exposed, sorted(paths), secrets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    exposed, paths, secrets = historical_inventory()
    reused_keys = []
    current = Path(".env")
    if current.is_file():
        for line in current.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and value.strip().strip("\"'") in secrets:
                reused_keys.append(name.strip())
    report = {
        "exposed_commits": exposed,
        "sensitive_paths": paths,
        "historical_secret_count": len(secrets),
        "reused_local_environment_keys": reused_keys,
        "provider_rotation_verified": False,
        "exposed_employee_data_reviewed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "exposed_commits": len(exposed),
                "sensitive_paths": paths,
                "historical_secret_count": len(secrets),
                "reused_local_environment_keys": reused_keys,
                "external_review": "pending",
            }
        )
    )


if __name__ == "__main__":
    main()
