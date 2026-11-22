"""Offline tests for coding_agent.py: sandbox rules, the independent check, snapshots, and the
plan -> act -> check -> fix loop. Claude is replaced with scripted fakes, so no API key is needed."""

import json
from pathlib import Path

import pytest

from coding_agent import Plan, Workspace, WorkspaceError, check_work, revert, run_pytest, run_workflow, snapshot

GOOD_MODULE = "def add(a, b):\n    return a + b\n"
BUGGY_MODULE = "def add(a, b):\n    return a - b\n"
BASE_TEST = "from calc import add\n\n\ndef test_add_zero():\n    assert add(2, 0) == 2\n"
NEW_TEST = "from calc import add\n\n\ndef test_add_positive():\n    assert add(2, 3) == 5\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tiny project with a bug that its one existing test doesn't catch."""
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "calc.py").write_text(BUGGY_MODULE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(BASE_TEST)
    return tmp_path


def make_plan(tests_to_add=("add(2, 3) returns 5",)) -> Plan:
    return Plan(summary="Fix add", steps=["Add a test", "Fix add"], files_to_change=["calc.py"],
                tests_to_add=list(tests_to_add), risks=[], confidence=0.8,
                confidence_reason="Small, well-tested change.")


# --- Workspace sandbox -------------------------------------------------------

def test_workspace_rejects_paths_outside_project(project):
    ws = Workspace(project)
    with pytest.raises(WorkspaceError, match="outside"):
        ws.read_file("../secret.txt")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.write_file("/tmp/evil.py", "x = 1")


def test_workspace_hides_excluded_folders(project):
    (project / ".venv").mkdir()
    (project / ".venv" / "lib.py").write_text("")
    ws = Workspace(project)
    assert ".venv/lib.py" not in ws.list_files()
    with pytest.raises(WorkspaceError, match="excluded"):
        ws.read_file(".venv/lib.py")


def test_workspace_protects_harness_and_non_code_files(project):
    ws = Workspace(project)
    with pytest.raises(WorkspaceError, match="protected"):
        ws.write_file("coding_agent.py", "")
    with pytest.raises(WorkspaceError, match="protected"):
        ws.write_file("tests/test_coding_agent.py", "")
    with pytest.raises(WorkspaceError, match="can only write"):
        ws.write_file("run.sh", "rm -rf /")


def test_edit_file_requires_exactly_one_match(project):
    ws = Workspace(project)
    (project / "calc.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(WorkspaceError, match="appears 2 times"):
        ws.edit_file("calc.py", "x = 1", "x = 2")
    with pytest.raises(WorkspaceError, match="appears 0 times"):
        ws.edit_file("calc.py", "y = 1", "y = 2")
    ws.edit_file("calc.py", "x = 1\nx = 1", "x = 2")
    assert (project / "calc.py").read_text() == "x = 2\n"
    assert ws.changed == {"calc.py"}


def test_run_tests_rejects_option_injection(project):
    ws = Workspace(project)
    with pytest.raises(WorkspaceError):
        ws.run_tests("--rootdir=/")
    assert "1 passed" in ws.run_tests("tests/test_calc.py")


# --- Independent check -------------------------------------------------------

def test_run_pytest_records_each_test(project):
    (project / "tests" / "test_new.py").write_text(NEW_TEST)
    run = run_pytest(project, [])
    assert run.passed == {"tests.test_calc::test_add_zero"}
    assert run.failed == {"tests.test_new::test_add_positive"}


def test_check_fails_when_a_test_fails(project):
    baseline = run_pytest(project, [])
    (project / "tests" / "test_new.py").write_text(NEW_TEST)
    result = check_work(project, baseline, set(), expect_new_tests=True)
    assert not result.ok
    assert any("does not pass" in p for p in result.problems)


def test_check_catches_deleted_tests(project):
    baseline = run_pytest(project, [])
    (project / "tests" / "test_calc.py").write_text(NEW_TEST)  # replaced the old test
    (project / "calc.py").write_text(GOOD_MODULE)
    result = check_work(project, baseline, {"calc.py"}, expect_new_tests=True)
    assert not result.ok
    assert any("removed or renamed" in p and "test_add_zero" in p for p in result.problems)


def test_check_requires_new_tests_when_plan_promised_them(project):
    baseline = run_pytest(project, [])
    (project / "calc.py").write_text(GOOD_MODULE)
    assert not check_work(project, baseline, {"calc.py"}, expect_new_tests=True).ok
    assert check_work(project, baseline, {"calc.py"}, expect_new_tests=False).ok


def test_check_passes_for_a_real_fix(project):
    baseline = run_pytest(project, [])
    (project / "tests" / "test_new.py").write_text(NEW_TEST)
    (project / "calc.py").write_text(GOOD_MODULE)
    assert check_work(project, baseline, {"calc.py", "tests/test_new.py"}, expect_new_tests=True).ok


# --- Snapshots ---------------------------------------------------------------

def test_revert_restores_changed_and_removes_new_files(project, tmp_path_factory):
    before = tmp_path_factory.mktemp("before")
    snapshot(project, before)
    (project / "calc.py").write_text(GOOD_MODULE)
    (project / "tests" / "test_new.py").write_text(NEW_TEST)

    touched = revert(project, before)

    assert (project / "calc.py").read_text() == BUGGY_MODULE
    assert not (project / "tests" / "test_new.py").exists()
    assert sorted(touched) == ["removed tests/test_new.py", "restored calc.py"]


# --- Full workflow with a scripted fake Claude --------------------------------

def test_workflow_fixes_its_own_mistake_after_a_failed_check(project):
    calls = []

    def fake_actor(client, ws, task, plan, feedback):
        calls.append(feedback)
        if feedback is None:  # round 1: adds the test but "forgets" the fix
            ws.write_file("tests/test_new.py", NEW_TEST)
            return "Added a test."
        assert "test_add_positive" in feedback  # round 2 sees which test failed
        ws.edit_file("calc.py", "a - b", "a + b")
        return "Fixed add."

    result = run_workflow("Fix add", root=project, planner=lambda c, ws, t: make_plan(), actor=fake_actor)

    assert result.ok and result.rounds == 2
    assert len(calls) == 2
    report = result.report_path.read_text()
    assert "## Round 2 (FIX)" in report and "-    return a - b" in report and "+    return a + b" in report

    record = json.loads((result.report_path.parent / "result.json").read_text())
    assert record["outcome"] == "passed" and record["plan"]["confidence"] == 0.8
    assert [c["ok"] for c in record["checks"]] == [False, True]
    assert record["checks"][0]["failed"] == ["tests.test_new::test_add_positive"]
    assert record["changed_files"] == ["calc.py", "tests/test_new.py"]
    trace = (result.report_path.parent / "trace.md").read_text()
    assert "## CHECK (round 1)" in trace and "**Independent check:** FAIL" in trace
    assert "## CHECK (round 2)" in trace and "**Independent check:** PASS" in trace


def test_workflow_gives_up_after_max_rounds(project):
    def lazy_actor(client, ws, task, plan, feedback):
        return "Did nothing."

    result = run_workflow("Fix add", root=project, max_rounds=2,
                          planner=lambda c, ws, t: make_plan(), actor=lazy_actor)

    assert not result.ok and result.rounds == 2


def test_workflow_stops_when_plan_is_rejected(project):
    def must_not_run(*args):
        raise AssertionError("actor ran without approval")

    result = run_workflow("Fix add", root=project, approve=lambda plan: False,
                          planner=lambda c, ws, t: make_plan(), actor=must_not_run)

    assert not result.ok and result.rounds == 0
    assert (project / "calc.py").read_text() == BUGGY_MODULE


# --- Real SDK wiring, with the HTTP layer mocked (no network, no API key) ------

def _message(content, stop_reason):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def _mock_client(responses, requests):
    import json

    import anthropic
    import httpx2

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=responses[len(requests) - 1])

    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def test_make_plan_sends_structured_request_and_parses_plan(project):
    import json

    from coding_agent import MODEL, make_plan as real_make_plan

    plan_json = make_plan().model_dump()
    requests = []
    client = _mock_client([_message([{"type": "text", "text": json.dumps(plan_json)}], "end_turn")], requests)

    plan = real_make_plan(client, Workspace(project), "Fix add")

    assert plan == make_plan()
    body = requests[0]
    assert body["model"] == MODEL
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "Fix add" in body["messages"][0]["content"]


def test_act_runs_tools_requested_by_claude(project):
    from coding_agent import act

    requests = []
    client = _mock_client([
        _message([{"type": "thinking", "thinking": "The bug is the minus sign in add().", "signature": "sig"},
                  {"type": "text", "text": "I'll fix the operator."},
                  {"type": "tool_use", "id": "toolu_1", "name": "edit_file",
                   "input": {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}},
                  {"type": "tool_use", "id": "toolu_2", "name": "write_file",
                   "input": {"path": "coding_agent.py", "contents": ""}}], "tool_use"),
        _message([{"type": "text", "text": "Fixed add."}], "end_turn"),
    ], requests)
    ws = Workspace(project)

    summary = act(client, ws, "Fix add", make_plan(), feedback=None)

    assert summary == "Fixed add."
    assert (project / "calc.py").read_text() == GOOD_MODULE
    results = {r["tool_use_id"]: r for r in requests[1]["messages"][-1]["content"]}
    assert not results["toolu_1"].get("is_error")
    assert results["toolu_2"]["is_error"] is True  # protected file refused, reported back to Claude
    assert {t["name"] for t in requests[0]["tools"]} == {"list_files", "read_file", "write_file", "edit_file", "run_tests"}
    assert requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}

    # The reasoning trace records thinking, messages, tool calls, and tool results in order.
    kinds = [(e["kind"], e.get("tool")) for e in ws.trace]
    assert kinds == [("thinking", None), ("message", None), ("tool_call", "edit_file"), ("tool_call", "write_file"),
                     ("tool_result", "edit_file"), ("tool_result", "write_file"), ("message", None)]
    assert ws.trace[0]["text"] == "The bug is the minus sign in add()."
    assert ws.trace[3]["args"]["contents"] == "<0 lines>"  # file bodies are summarized, not copied
    assert ws.trace[5]["ok"] is False and "protected" in ws.trace[5]["text"]
