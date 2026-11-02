#!/usr/bin/env python3
"""Check the Participant registry schema.

Two things, both of which have been wrong before:

  1. The chart's copy and the quick-start copy are the same file.
  2. The schema accepts and refuses what scripts/registry-schema-cases.json
     says it does.

Run locally exactly as CI runs it:

    pip install jsonschema
    ./scripts/check-registry-schema.py
"""

import json
import pathlib
import sys

from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUICK_START_COPY = ROOT / "quick-start/config/registry/schemas/Participant.json"
CHART_COPY = ROOT / "charts/registry/files/schemas/Participant.json"
CASES = ROOT / "scripts/registry-schema-cases.json"


def copies_match():
    if QUICK_START_COPY.read_bytes() == CHART_COPY.read_bytes():
        return True
    print(f"FAIL  {QUICK_START_COPY.relative_to(ROOT)} and "
          f"{CHART_COPY.relative_to(ROOT)} differ. Edit one, copy it to the other.")
    return False


def participant_validator():
    # setup.py posts the participant itself, with no {"Participant": {...}}
    # wrapper, so that is the shape the registry validates and the shape the
    # cases carry.
    schema = json.loads(QUICK_START_COPY.read_text())
    return Draft7Validator({"$ref": "#/definitions/Participant",
                            "definitions": schema["definitions"]})


def cases_hold():
    validator = participant_validator()
    passed = True
    for case in json.loads(CASES.read_text()):
        errors = sorted(validator.iter_errors(case["participant"]), key=str)
        accepted = not errors
        if accepted == case["valid"]:
            print(f"ok    {case['name']}")
            continue
        passed = False
        print(f"FAIL  {case['name']}")
        print(f"      the schema should {'accept' if case['valid'] else 'refuse'} this")
        for error in errors[:3]:
            print(f"      {list(error.absolute_path)}: {error.message}")
    return passed


if __name__ == "__main__":
    # Both run even when the first fails: a drifted copy and a broken case
    # are separate problems and the author should see both.
    checks = [copies_match(), cases_hold()]
    sys.exit(0 if all(checks) else 1)
