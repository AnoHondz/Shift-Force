"""Rotate the local signing key while preserving unrelated environment settings."""

import argparse
import os
import re
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    target = args.env_file.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    if target != (root / ".env").resolve():
        parser.error("This helper rotates only the workspace's local .env signing key.")
    content = target.read_text(encoding="utf-8")
    private = root / "artifacts" / ("env-before-rotation-" + secrets.token_hex(8) + ".private")
    private.parent.mkdir(parents=True, exist_ok=True)
    with private.open("x", encoding="utf-8") as file:
        os.chmod(private, 0o600)
        file.write(content)
    updated, count = re.subn(
        r"(?m)^FLASK_SECRET_KEY=.*$", "FLASK_SECRET_KEY=" + secrets.token_hex(32), content
    )
    if count != 1:
        raise ValueError("Expected exactly one local FLASK_SECRET_KEY entry.")
    # These obsolete bootstrap controls have no reader in the current application.
    updated = re.sub(
        r"(?m)^(?:DB_PASSWORD|EXEC_USERNAME|EXEC_PASSWORD|EXECUTIVE_SIGNUP_CODE|ADMIN_SIGNUP_CODE|MANAGER_SIGNUP_CODE)=.*\n?",
        "",
        updated,
    )
    temporary = target.parent / (".env.rotation-" + secrets.token_hex(8))
    with temporary.open("x", encoding="utf-8") as file:
        os.chmod(temporary, 0o600)
        file.write(updated)
    temporary.replace(target)
    print(
        "Local signing key rotated; obsolete bootstrap controls removed. Database values and demo credentials were not changed. External credentials still require provider confirmation."
    )


if __name__ == "__main__":
    main()
