"""data/config.json: the settings that belong to whoever runs the docket.

The school, the page's own words, the senders and lists you have added. Git
ignores the file; data/config.example.json shows every key.

FFF_CONFIG in the environment points at another file instead -- for tests, and
for running a second docket from the same copy.
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(ROOT, "data", "config.json")


def path() -> str:
    return os.environ.get("FFF_CONFIG") or DEFAULT


def load() -> dict:
    """The settings, or {} when there is no file yet.

    A file that is there but broken stops everything with a clear message
    rather than reading as empty: empty means "no school", and with no school
    every email would be refused without a word as to why.
    """
    try:
        with open(path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise SystemExit(f"{path()} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"{path()} should hold one JSON object, {{...}}")
    return data


def save(data: dict) -> None:
    """Write the settings back, whole, in place of the old file."""
    target = path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    partial = target + ".part"
    with open(partial, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(partial, target)
