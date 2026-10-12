"""Check tracked sources without printing secret values; optional history inventory."""

import argparse
import ast
import re
import subprocess
from pathlib import Path


def tracked_paths():
    return [
        path
        for path in subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
        if path
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    options = parser.parse_args()
    failures = []
    for path in tracked_paths():
        if not Path(path).exists():
            continue  # Files removed from the index in the staged changes are not scanned.
        if (
            path == ".env"
            or re.search(r"\.(db|sqlite|sqlite3)(\.|$)", path)
            or "__pycache__" in path
            or path.endswith(".pyc")
        ):
            failures.append(f"Sensitive/generated file tracked: {path}")
        try:
            source = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # Binary screenshots and fonts are not source text.
        patterns = [
            r"gh[pousr]_[A-Za-z0-9]{30,}",
            r"AKIA[0-9A-Z]{16}",
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        ]
        if any(re.search(pattern, source) for pattern in patterns):
            failures.append(f"Potential credential material: {path}")
        if path.endswith(".py"):
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    for target in node.targets:
                        name = target.id if isinstance(target, ast.Name) else ""
                        if name.upper() in {
                            "SECRET_KEY",
                            "DEFAULT_ADMIN_PASSWORD",
                            "MANAGER_SIGNUP_CODE",
                            "ADMIN_SIGNUP_CODE",
                            "TEST_PASSWORD",
                        }:
                            failures.append(
                                f"Literal credential assignment: {path}:{node.lineno} ({name})"
                            )
    if options.history:
        for commit in subprocess.check_output(["git", "rev-list", "--all"]).decode().splitlines():
            paths = (
                subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit])
                .decode()
                .splitlines()
            )
            sensitive = [
                path
                for path in paths
                if path == ".env" or re.search(r"\.(db|sqlite|sqlite3)(\.|$)", path)
            ]
            if sensitive:
                print(f"History exposure {commit[:12]}: {', '.join(sensitive)}")
    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    print(
        "Tracked-file security checks passed. This focused check does not prove absence of all secrets."
    )


if __name__ == "__main__":
    main()
