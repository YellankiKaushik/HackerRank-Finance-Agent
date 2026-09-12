from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "code") not in sys.path:
    sys.path.insert(0, str(ROOT / "code"))

from buywait.loaders import load_dataset
from buywait.planner import OUTPUT_COLUMNS, decide_request, row_to_csv_dict
from buywait.verifier import verify_decision


def main() -> int:
    started = time.perf_counter()
    dataset = load_dataset(ROOT / "dataset")
    rows = []
    failures: list[str] = []
    for request in dataset.requests:
        decision = decide_request(dataset, request)
        verification = verify_decision(dataset, request, decision)
        if not verification.valid:
            failures.append(f"{request.request_id}: {verification.errors}")
        rows.append(row_to_csv_dict(decision))
    if failures:
        for failure in failures:
            print(f"VERIFY_FAIL {failure}", file=sys.stderr)
        return 1
    output_path = ROOT / "output.csv"
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    elapsed = time.perf_counter() - started
    print(f"Wrote {len(rows)} rows to {output_path}")
    print(f"Runtime: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
