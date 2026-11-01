#!/usr/bin/env python3
"""Select a version root from a saved H360 Versions response; no network access."""
import json
import re
import sys
from decimal import Decimal


def select_version(response):
    if isinstance(response, dict):
        if response.get("status_code", 200) != 200:
            raise ValueError("Versions request did not succeed")
        response = response.get("body")
    if not isinstance(response, list) or not response:
        raise ValueError("Expected a nonempty Versions array")
    for entry in response:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Versions entry")
        version = entry.get("version")
        if (not isinstance(version, str) or not re.fullmatch(r"[1-9]\d*\.\d+", version)
                or entry.get("url") != "/services/data/v" + version):
            raise ValueError("Expected numeric versions and matching relative URLs")
    selected = max(response, key=lambda entry: Decimal(entry["version"]))
    return {"version": selected["version"], "url": selected["url"]}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) > 1:
            raise ValueError("Usage: h360_api_version.py [versions-response.json]")
        if args:
            with open(args[0], encoding="utf-8") as stream:
                response = json.load(stream)
        else:
            response = json.load(sys.stdin)
        print(json.dumps(select_version(response)))
        return 0
    except (OSError, ValueError, RecursionError):
        print("Could not select an API version: provide a successful Versions response "
              "with numeric versions and matching relative URLs.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
