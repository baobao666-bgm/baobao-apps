#!/usr/bin/env python3
"""Validate bucket manifests against the vendored Scoop schema + a few local rules.

Usage:
    python .github/scripts/validate_manifests.py
"""
import glob
import json
import os
import sys

try:
    import jsonschema
except ImportError:
    print("ERROR: jsonschema is not installed. Run: pip install jsonschema", file=sys.stderr)
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA_PATH = os.path.join(ROOT, ".github", "schema", "scoop-schema.json")
MANIFEST_GLOB = os.path.join(ROOT, "bucket", "*.json")


def local_rules(name, data):
    """Repo-specific rules that the generic schema cannot express."""
    errors = []
    # typora0.11.18 is pinned on purpose: it must never grow checkver/autoupdate.
    if name.startswith("typora") and ("checkver" in data or "autoupdate" in data):
        errors.append("pinned package must not define checkver/autoupdate (remove them)")
    return errors


def main():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)

    manifests = sorted(glob.glob(MANIFEST_GLOB))
    if not manifests:
        print("ERROR: no manifests found under bucket/", file=sys.stderr)
        sys.exit(2)

    failed = False
    for path in manifests:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: invalid JSON: {exc}")
            failed = True
            continue

        errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.path)))
        problems = []
        for err in errors:
            location = "/".join(map(str, err.path)) or "(root)"
            problems.append(f"{location}: {err.message}")
        problems.extend(local_rules(name, data))

        if problems:
            failed = True
            for p in problems:
                print(f"FAIL {name}: {p}")
        else:
            print(f"OK   {name}")

    if failed:
        print("\nManifest validation FAILED.")
        sys.exit(1)
    print("\nAll manifests valid.")


if __name__ == "__main__":
    main()
