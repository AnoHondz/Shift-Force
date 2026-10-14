"""Prepare and verify a scrubbed mirror; never push or change the working repository."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .exposure_inventory import git, historical_inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    source = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    destination = args.destination.resolve()
    artifacts = (source / "artifacts").resolve()
    if not destination.is_relative_to(artifacts) or destination.exists():
        parser.error("Use a new directory inside this workspace's ignored artifacts folder.")
    exposed, paths, secrets = historical_inventory()
    original_refs = git("show-ref").decode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--mirror", "--no-local", str(source), str(destination)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    replacements = destination.parent / (destination.name + "-replacements.private.txt")
    with replacements.open("x", encoding="utf-8") as file:
        for secret in sorted(secrets):
            if "\n" in secret or "==>" in secret:
                raise ValueError("Review this credential format before automatic replacement.")
            file.write("literal:" + secret + "==>REMOVED_EXPOSED_CREDENTIAL\n")
    os.chmod(replacements, 0o600)
    command = [
        sys.executable,
        "-m",
        "git_filter_repo",
        "--force",
        "--invert-paths",
        "--replace-text",
        str(replacements),
    ]
    for path in paths:
        command.extend(["--path", path])
    try:
        subprocess.run(
            command,
            cwd=destination,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        replacements.unlink()
    for commit in (
        subprocess.check_output(["git", "rev-list", "--all"], cwd=destination).decode().splitlines()
    ):
        names = (
            subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", commit], cwd=destination
            )
            .decode()
            .splitlines()
        )
        if any(path in names for path in paths):
            raise ValueError("Sensitive data survived the proposed cleanup.")
        # Scan tracked source blobs without exposing any matched value.
        for name in names:
            if name.endswith((".py", ".md", ".example", ".txt", ".sql")):
                blob = subprocess.check_output(
                    ["git", "show", commit + ":" + name], cwd=destination
                )
                if any(secret.encode() in blob for secret in secrets):
                    raise ValueError("A historical credential survived cleanup.")
    if git("show-ref").decode() != original_refs:
        raise RuntimeError("Original refs changed unexpectedly.")
    report = {
        "status": "prepared_and_verified",
        "mirror": str(destination),
        "affected_original_commits": len(exposed),
        "removed_paths": paths,
        "source_refs_unchanged": True,
        "remote_push_performed": False,
        "provider_rotation": "requires operator confirmation",
        "employee_data_response": "requires owner review",
    }
    (destination.parent / (destination.name + "-review.json")).write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
