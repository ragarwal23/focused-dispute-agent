"""Evaluation, outside the run: score the agent against the answer key, then check that the scores hold.

    golden.py    data/scenarios.xlsx as a committed golden workbook, one sheet per scenario, drift-checked
    evaluate.py  run every scenario in data/scenarios.xlsx and score it (the `eval` command)
    report.py    one eval JSON next to golden.xlsx as a release workbook (the `report` command)
    validate.py  stability over repeated evals, and LangSmith trace-shape checks (`stability`, `trace`)

README.md explains the workbooks and records the phase 2 metrics.
"""
