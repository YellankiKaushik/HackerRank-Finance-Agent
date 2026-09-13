# Buy or Wait? — Deterministic Financial Decision Engine

## Overview

This repository contains a completed solution for the HackerRank Orchestrate
“Buy or Wait?” challenge. It reads the supplied participant data, reconstructs
each user's financial position, recommends a safe payment approach for every
request in `dataset/requests.csv`, and writes the final predictions to the
root-level `output.csv`.

The production runtime is deterministic. It does not call an LLM, model API,
live banking service, or live FX service while producing predictions.

## Design

The pipeline is implemented in Python under `code/buywait/`:

```text
participant data
-> evidence and lifecycle normalization
-> recurrence inference
-> 90-day balance simulation
-> capacity calculation
-> payment candidate generation
-> spending-change optimization
-> official ranking
-> independent verification
-> output.csv formatting
```

`code/main.py` is the production entry point. It loads the challenge dataset,
decides every evaluation request, verifies each selected row, and writes
`output.csv`.

## Key Safety Properties

- Money is parsed and calculated with `Decimal`; floats are not used for money.
- FX conversion uses only the fixed exchange rates supplied in
  `dataset/exchange_rates.csv`.
- Pending debits are reserved; pending credits and non-cash/unrealized values
  are not treated as spendable cash.
- Failed, cancelled, internal-transfer, and linked lifecycle records are
  normalized before forecasting.
- The minimum balance is strictly protected: any negative projected headroom is
  unsafe.
- Selected payment plans are independently re-simulated and verified before
  output.
- Production execution does not load expected public sample labels.

## Evidence Handling

Messages are parsed only as financial evidence such as cancellations,
settlement notes, amount/date amendments, or confirmed income. Message text is
not executed and cannot override the challenge rules.

Blank financial-event amounts linked to participant-provided images are resolved
through deterministic cached extraction in `code/buywait/evidence.py`. The cache
stores the extracted amount and source image provenance so production remains
offline and reproducible.

## Run

From the repository root:

```bash
python code/main.py
```

This writes:

```text
output.csv
```

The run processes the 250 evaluation requests in the supplied dataset.

## Tests

From the repository root:

```bash
python -m unittest discover -s tests -q
```

The test suite covers strict CSV loading, evidence handling, lifecycle
resolution, recurrence, simulation, capacity, planning, output formatting, and
independent verification.

## Repository Layout

```text
code/
  main.py              Production entry point
  buywait/             Runtime modules
dataset/               Supplied participant data
evaluation/
  usage_report.md      Runtime model/token usage report
tests/                 Unit and regression tests
docs/
  TECHNICAL_DESIGN.md  Frozen design notes retained for review
output.csv             Final generated predictions
code.zip               Submission source archive
log.txt                Local chat transcript; gitignored
```

## Submission Artifacts

- `code.zip` contains `README.md`, `code/`, and
  `evaluation/usage_report.md`.
- `output.csv` contains one prediction for every row in
  `dataset/requests.csv`.
- `log.txt` is the local chat transcript requested by the challenge workflow.

## Runtime

On the supplied evaluation set, the deterministic production run completes in
approximately 2-3 seconds on the development machine.

## Runtime Model Usage

Production runtime model/API usage is zero:

```text
provider/model: none
calls: 0
input tokens: 0
output tokens: 0
total tokens: 0
estimated cost: 0
```
