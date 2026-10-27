"""Evaluation, outside the run: score the agent against the answer key, then check that the scores hold.

    golden.py    data/scenarios.xlsx as a committed golden workbook, one sheet per scenario, drift-checked; and the
                 golden_echo / empty_stub producers that score the key itself and an empty answer (no LLM)
    evaluate.py  run every scenario in a split and score it with its pass/fail checks (the `eval` command)
    policy_checks.py  our P1-P7 checks in code: audits the golden labels, and feeds the `policy_consistent` check
    metrics.py   the six axes over those checks (PASS / PARTIAL / FAIL), grade, score, slices, before/after
    judge.py     the Opus judge: four 0-3 scores on the case note (`judge`), and agreement with hand labels (`calibrate`)
    report.py    one eval JSON next to golden.xlsx as a release workbook with verdicts and judge panels (`report`)
    validate.py  stability over repeated evals, LangSmith trace-shape checks, and the LangSmith dataset + experiment
                 upload (`stability`, `trace`, `langsmith-sync`)

README.md explains the workbooks, the metrics and the three stages: eval, judge, report.
"""
