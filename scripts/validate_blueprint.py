"""Validate Render configuration against its official schema without deploying."""

import json
from pathlib import Path
from urllib.request import urlopen

import jsonschema
import yaml


def main():
    with urlopen("https://render.com/schema/render.yaml.json", timeout=30) as response:
        schema = json.load(response)
    document = yaml.safe_load(Path("render.yaml").read_text())
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = list(validator.iter_errors(document))
    if errors:
        for error in errors:
            print("/".join(str(part) for part in error.path) + ": " + error.message)
        raise SystemExit(1)
    print("Render Blueprint passed the official JSON Schema. No resources were created.")


if __name__ == "__main__":
    main()
