"""Create a local .env with a fresh secret, without overwriting existing settings."""

import secrets
from pathlib import Path


def main():
    path = Path(".env")
    with path.open("x", encoding="utf-8") as environment:
        environment.write(
            "APP_ENV=development\n"
            f"FLASK_SECRET_KEY={secrets.token_hex(32)}\n"
            "WORKFORCE_TIMEZONE=UTC\n"
            "RATELIMIT_STORAGE_URI=memory://\n"
        )
    print("Created .env with a new local secret. Existing environment variables take precedence.")


if __name__ == "__main__":
    main()
