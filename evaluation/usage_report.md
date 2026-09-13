# Token Usage and Cost Analysis

This report describes the final full-dataset production run that generated `output.csv`.

## Runtime model usage

| Provider/model | Calls | Input tokens | Output tokens | Total tokens | Avg tokens/request | Estimated total cost | Avg cost/request |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| none | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Overall production model calls: 0

The submitted solver is deterministic at runtime. It reads the provided CSV and PNG evidence files, applies rule-based parsing, lifecycle resolution, recurrence inference, simulation, planning, verification, and CSV formatting locally. It does not call an LLM, model API, live banking service, or live FX service while producing `output.csv`.
