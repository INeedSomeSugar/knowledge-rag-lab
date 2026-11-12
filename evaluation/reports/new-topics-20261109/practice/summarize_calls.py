"""Learning exercise, deliberately incomplete; not part of measured evaluation code."""

import csv
import json
from pathlib import Path
import sys


def summarize_calls(records):
    # TODO: implement the requirements in README.md yourself.
    raise NotImplementedError("Complete this function before running the exercise")


if __name__ == "__main__":
    with Path(sys.argv[1]).open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    print(json.dumps(summarize_calls(records), ensure_ascii=False, indent=2))
