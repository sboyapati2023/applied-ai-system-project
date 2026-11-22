"""Tests for evaluate.py's results writer (no API needed)."""

import json

import evaluate


def test_write_results_summarizes_and_writes_parseable_table(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "EVAL_DIR", tmp_path)
    rows = [
        {"id": "a", "kind": "fix", "task": "Fix a | b", "criteria": "c", "hidden_passed": 2, "hidden_total": 2,
         "rounds": 1, "confidence": 0.9, "result": "PASS", "notes": "-"},
        {"id": "b", "kind": "fix", "task": "Fix b", "criteria": "c", "hidden_passed": 0, "hidden_total": 1,
         "rounds": 3, "confidence": 0.5, "result": "FAIL", "notes": "hidden tests failed: t"},
        {"id": "c", "kind": "guardrail", "task": "Delete it", "criteria": "c", "rounds": 2, "confidence": 0.7,
         "result": "PASS", "notes": "protected test kept"},
    ]

    evaluate.write_results(rows, max_rounds=3)

    data = json.loads((tmp_path / "results.json").read_text())
    assert data["summary"].startswith("2/3 measured cases passed (1 on the first round). Mean plan confidence 0.70: "
                                      "0.80 on passing cases, 0.50 on failing cases.")
    table = [line for line in (tmp_path / "results.md").read_text().splitlines() if line.startswith("| ")]
    assert len(table) == 4  # header + 3 cases
    assert all(line.count(" | ") == 7 for line in table)  # 8 columns; the escaped pipe in "a \\| b" is not a separator
    assert "| a | Fix a \\| b | c | 2/2 | 1 | 0.90 | **PASS** | - |" in table


def test_api_errors_are_not_counted_as_agent_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "EVAL_DIR", tmp_path)
    rows = [
        {"id": "a", "kind": "fix", "task": "t", "criteria": "c", "rounds": 1, "confidence": 0.8,
         "result": "PASS", "notes": "-"},
        {"id": "b", "kind": "guardrail", "task": "t", "criteria": "c", "result": "ERROR",
         "notes": "not measured, API error: BadRequestError: credit balance is too low"},
    ]

    evaluate.write_results(rows, max_rounds=3)

    summary = json.loads((tmp_path / "results.json").read_text())["summary"]
    assert summary.startswith("1/1 measured cases passed (1 on the first round); 1 not measured (API error: b).")


def test_rerunning_one_case_keeps_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "EVAL_DIR", tmp_path)
    old = [{"id": c["id"], "result": "PASS"} for c in evaluate.CASES]
    old[-1]["result"] = "ERROR"
    (tmp_path / "results.json").write_text(json.dumps({"summary": "", "cases": old}))

    merged = evaluate.merge_with_previous([{"id": evaluate.CASES[-1]["id"], "result": "PASS"}])

    assert [r["id"] for r in merged] == [c["id"] for c in evaluate.CASES]
    assert all(r["result"] == "PASS" for r in merged)
