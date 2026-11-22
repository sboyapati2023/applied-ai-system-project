"""PawPal+ coding agent (applied AI system): plans a change, writes code + tests, checks its own work, and fixes failures.

Workflow (each stage is a separate step you can see in the terminal and in the run report):

    0. BASELINE  Snapshot the project and record which tests exist and pass today.
    1. PLAN      Claude reads the project and returns a structured plan (you approve it).
    2. ACT       Claude edits files and runs pytest through a small set of sandboxed tools.
    3. CHECK     This script (not Claude) re-runs the full test suite and verifies that
                 every test passes, no existing test was deleted, new tests were added,
                 and every changed Python file compiles.
    4. FIX       If CHECK fails, the failure report goes back to Claude for another ACT
                 round, up to --max-rounds times.

Usage:
    python coding_agent.py "Fix: a low-priority task can push a high-priority one later"
    python coding_agent.py --plan-only "Make tasks with no time fill gaps in the day"
    python coding_agent.py --revert 20261122-153000     # undo a run

Every run writes .agent_runs/<run_id>/report.md (plan, tool actions, check results, diff).
Needs ANTHROPIC_API_KEY (or another Anthropic credential) in the environment.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"
# If the model declines a request, the API retries it on a suitable fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The agent can't see or touch these.
EXCLUDED_DIRS = {".venv", ".venv_new", ".git", "__pycache__", ".pytest_cache", ".agent_runs", "assets", "eval"}
# The agent can read these but not change them: its own harness and guardrail tests.
PROTECTED_FILES = {"coding_agent.py", "tests/test_coding_agent.py", "conftest.py", ".gitignore"}
WRITABLE_SUFFIXES = {".py", ".md", ".txt", ".mmd"}
MAX_READ_CHARS = 60_000
TEST_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# Workspace: the only way the agent touches the project
# ---------------------------------------------------------------------------

class WorkspaceError(Exception):
    """A tool call the workspace refuses (bad path, protected file, ambiguous edit)."""


class Workspace:
    """Sandboxed file and test operations, confined to one project folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.actions: list[str] = []  # human-readable log of every tool call
        self.changed: set[str] = set()  # relative paths written by the agent
        self.stage = "PLAN"  # label for trace entries, set by run_workflow
        self.trace: list[dict] = []  # intermediate reasoning: thinking, messages, tool calls and results

    def note(self, kind: str, **fields) -> None:
        """Record one step of Claude's intermediate reasoning for the trace log."""
        self.trace.append({"stage": self.stage, "kind": kind, **fields})

    def _resolve(self, rel_path: str) -> Path:
        path = (self.root / rel_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceError(f"'{rel_path}' is outside the project folder")
        if any(part in EXCLUDED_DIRS for part in path.relative_to(self.root).parts):
            raise WorkspaceError(f"'{rel_path}' is in an excluded folder")
        return path

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def list_files(self) -> str:
        files = [
            self._rel(p) for p in sorted(self.root.rglob("*"))
            if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.relative_to(self.root).parts)
        ]
        self.actions.append("list_files")
        return "\n".join(files)

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"no file named '{path}'")
        text = target.read_text(encoding="utf-8", errors="replace")
        self.actions.append(f"read_file {path}")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n\n[... truncated: file is {len(text)} characters]"
        return text

    def _check_writable(self, path: str) -> Path:
        target = self._resolve(path)
        rel = self._rel(target)
        if rel in PROTECTED_FILES:
            raise WorkspaceError(f"'{rel}' is protected and can't be changed by the agent")
        if target.suffix not in WRITABLE_SUFFIXES:
            raise WorkspaceError(f"can only write {sorted(WRITABLE_SUFFIXES)} files, not '{rel}'")
        return target

    def write_file(self, path: str, contents: str) -> str:
        target = self._check_writable(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        rel = self._rel(target)
        self.changed.add(rel)
        self.actions.append(f"write_file {rel} ({len(contents.splitlines())} lines)")
        return f"Wrote {rel}."

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        target = self._check_writable(path)
        if not target.is_file():
            raise WorkspaceError(f"no file named '{path}'; use write_file to create it")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise WorkspaceError(
                f"old_text must appear exactly once in '{path}' but appears {count} times; "
                "include more surrounding lines to make it unique"
            )
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        rel = self._rel(target)
        self.changed.add(rel)
        self.actions.append(f"edit_file {rel}")
        return f"Edited {rel}."

    def run_tests(self, target: str = "") -> str:
        target = target.strip()
        if target and not re.fullmatch(r"tests/[\w/.\-]+\.py(::[\w\[\]\-.]+)*", target):
            raise WorkspaceError("target must look like 'tests/test_x.py' or 'tests/test_x.py::test_name'")
        result = run_pytest(self.root, [target] if target else [])
        self.actions.append(f"run_tests {target or '(all)'} -> {result.summary()}")
        return result.output_tail()


# ---------------------------------------------------------------------------
# Running pytest and checking the agent's work
# ---------------------------------------------------------------------------

@dataclass
class TestRun:
    exit_code: int
    output: str
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)  # failures and errors
    skipped: set[str] = field(default_factory=set)

    @property
    def all_tests(self) -> set[str]:
        return self.passed | self.failed | self.skipped

    def summary(self) -> str:
        return f"{len(self.passed)} passed, {len(self.failed)} failed, {len(self.skipped)} skipped"

    def output_tail(self, limit: int = 8000) -> str:
        text = self.output.strip()
        return text if len(text) <= limit else "[... earlier output trimmed]\n" + text[-limit:]


def run_pytest(root: Path, args: list[str]) -> TestRun:
    """Run pytest in `root` and parse per-test results from its JUnit XML report."""
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "results.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}", *args]
        try:
            # A fresh bytecode cache per run: an edit that keeps a file's size and mtime second
            # (e.g. "a - b" -> "a + b") would otherwise load stale .pyc files.
            env = {**os.environ, "PYTHONPYCACHEPREFIX": str(Path(tmp) / "pycache")}
            proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True,
                                  timeout=TEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return TestRun(exit_code=-1, output=f"pytest timed out after {TEST_TIMEOUT_SECONDS}s")
        run = TestRun(exit_code=proc.returncode, output=proc.stdout + proc.stderr)
        if xml_path.exists():
            for case in ET.parse(xml_path).iter("testcase"):
                test_id = f"{case.get('classname')}::{case.get('name')}"
                if case.find("failure") is not None or case.find("error") is not None:
                    run.failed.add(test_id)
                elif case.find("skipped") is not None:
                    run.skipped.add(test_id)
                else:
                    run.passed.add(test_id)
    return run


@dataclass
class CheckResult:
    ok: bool
    problems: list[str]
    tests: TestRun

    def report(self) -> str:
        lines = [f"Tests: {self.tests.summary()} (pytest exit code {self.tests.exit_code})"]
        lines += [f"- PROBLEM: {p}" for p in self.problems] or ["- All checks passed."]
        if not self.ok:
            lines += ["", "pytest output:", self.tests.output_tail()]
        return "\n".join(lines)


def check_work(root: Path, baseline: TestRun, changed: set[str], expect_new_tests: bool) -> CheckResult:
    """Independently verify the agent's work. Claude's own claims are not trusted here."""
    tests = run_pytest(root, [])
    problems = []
    if tests.exit_code != 0 or tests.failed:
        failing = ", ".join(sorted(tests.failed)) or "see pytest output"
        problems.append(f"test suite does not pass: {failing}")
    missing = baseline.all_tests - tests.all_tests
    if missing:
        problems.append(f"existing tests were removed or renamed: {', '.join(sorted(missing))}")
    if expect_new_tests and not (tests.all_tests - baseline.all_tests):
        problems.append("the plan called for new tests, but no new tests were added")
    for rel in sorted(changed):
        if rel.endswith(".py") and (root / rel).exists():
            try:
                py_compile.compile(str(root / rel), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{rel} does not compile: {exc.msg}")
    return CheckResult(ok=not problems, problems=problems, tests=tests)


# ---------------------------------------------------------------------------
# Snapshots: every run can be diffed and reverted
# ---------------------------------------------------------------------------

def _project_files(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*")
        if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.relative_to(root).parts)
    ]


def snapshot(root: Path, dest: Path) -> None:
    for path in _project_files(root):
        copy_to = dest / path.relative_to(root)
        copy_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy_to)


def diff_against_snapshot(root: Path, before: Path) -> str:
    before_files = {p.relative_to(before).as_posix() for p in before.rglob("*") if p.is_file()}
    after_files = {p.relative_to(root).as_posix() for p in _project_files(root)}
    chunks = []
    for rel in sorted(before_files | after_files):
        old = (before / rel).read_text(errors="replace").splitlines(keepends=True) if rel in before_files else []
        new = (root / rel).read_text(errors="replace").splitlines(keepends=True) if rel in after_files else []
        if old != new:
            chunks.append("".join(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}")))
    return "\n".join(chunks)


def revert(root: Path, before: Path) -> list[str]:
    """Restore the project to a snapshot. Returns the paths that were restored or removed."""
    before_files = {p.relative_to(before).as_posix() for p in before.rglob("*") if p.is_file()}
    touched = []
    for path in _project_files(root):
        rel = path.relative_to(root).as_posix()
        if rel not in before_files:
            path.unlink()
            touched.append(f"removed {rel}")
    for rel in sorted(before_files):
        src, dst = before / rel, root / rel
        if not dst.exists() or src.read_bytes() != dst.read_bytes():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            touched.append(f"restored {rel}")
    return touched


# ---------------------------------------------------------------------------
# Claude: PLAN and ACT
# ---------------------------------------------------------------------------

class Plan(BaseModel):
    summary: str
    steps: list[str]
    files_to_change: list[str]
    tests_to_add: list[str]
    risks: list[str]
    confidence: float = Field(description="0.0-1.0: how likely this plan passes every check on the first try")
    confidence_reason: str = Field(description="One sentence on what drives that confidence up or down")

    def render(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"  - {item}" for item in items) or "  (none)"
        steps = "\n".join(f"  {i}. {step}" for i, step in enumerate(self.steps, 1))
        return (
            f"Summary: {self.summary}\n\nSteps:\n{steps}\n\nFiles to change:\n{bullets(self.files_to_change)}"
            f"\n\nTests to add:\n{bullets(self.tests_to_add)}\n\nRisks:\n{bullets(self.risks)}"
            f"\n\nConfidence: {self.confidence:.2f} ({self.confidence_reason})"
        )


SYSTEM_PROMPT = """\
You are a careful Python coding assistant working on PawPal+, a small pet-care scheduling project \
(Streamlit UI in app.py, scheduling logic in pawpal_system.py, pytest tests in tests/).

Ground rules:
- Keep changes small and focused on the task. Match the existing code style, type hints, and docstrings.
- Every behavior change needs a pytest test in tests/ that would fail without the change.
- Never delete, skip, or weaken an existing test to make the suite pass. If an existing test \
truly conflicts with the task, stop and explain instead of changing it.
- The scheduling logic must stay independent of Streamlit.
- If README.md describes behavior you changed (for example its "Known gaps" list), update it.
- If you add or change a public class, method, or attribute, update diagrams/uml.mmd to match."""


def _project_context(ws: Workspace) -> str:
    """The project listing plus the files most relevant to planning."""
    parts = [f"Project files:\n{ws.list_files()}"]
    for rel in ["README.md", "pawpal_system.py", "tests/test_pawpal.py", "main.py"]:
        if (ws.root / rel).exists():
            parts.append(f"===== {rel} =====\n{ws.read_file(rel)}")
    return "\n\n".join(parts)


def make_plan(client, ws: Workspace, task: str) -> Plan:
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "high"},
        thinking={"type": "adaptive", "display": "summarized"},  # readable reasoning summaries for the trace
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_format=Plan,
        messages=[{
            "role": "user",
            "content": (
                f"{_project_context(ws)}\n\n"
                f"Task: {task}\n\n"
                "Plan this change before any code is written. List concrete steps, the files to "
                "change, the tests to add (each as a one-line description of the behavior it checks), "
                "and risks such as existing tests or behaviors that could break. Finally, rate your "
                "confidence from 0.0 to 1.0 that this plan will pass every check on the first attempt "
                "(full suite passes, no existing test removed, new tests added); be calibrated, not optimistic."
            ),
        }],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"Planning did not return a plan (stop_reason={response.stop_reason}).")
    for block in response.content:
        if block.type == "thinking" and block.thinking.strip():
            ws.note("thinking", text=block.thinking.strip())
    plan = response.parsed_output
    ws.note("plan", text=plan.render())
    plan.confidence = min(1.0, max(0.0, plan.confidence))  # guard against out-of-range values
    return plan


def _preview(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" [... {len(text) - limit} more characters]"


def _tool_args_for_trace(name: str, args: dict) -> dict:
    """Tool arguments for the trace, with whole-file contents summarized instead of copied."""
    shown = dict(args)
    if "contents" in shown:
        shown["contents"] = f"<{len(str(shown['contents']).splitlines())} lines>"
    for key in ("old_text", "new_text"):
        if key in shown:
            shown[key] = _preview(str(shown[key]), 400)
    return shown


def build_tools(ws: Workspace) -> list:
    from anthropic import beta_tool
    from anthropic.lib.tools import ToolError

    def guarded(fn: Callable[..., str], *args) -> str:
        try:
            result = fn(*args)
        except WorkspaceError as exc:
            ws.actions.append(f"refused: {exc}")
            ws.note("tool_result", tool=fn.__name__, ok=False, text=str(exc))
            raise ToolError(str(exc)) from None
        ws.note("tool_result", tool=fn.__name__, ok=True, text=_preview(result))
        return result

    @beta_tool
    def list_files() -> str:
        """List every file in the project the agent can see, one relative path per line."""
        return guarded(ws.list_files)

    @beta_tool
    def read_file(path: str) -> str:
        """Read a project file.

        Args:
            path: Path relative to the project root, e.g. pawpal_system.py or tests/test_pawpal.py.
        """
        return guarded(ws.read_file, path)

    @beta_tool
    def write_file(path: str, contents: str) -> str:
        """Create a file or replace its entire contents. Prefer edit_file for changes to existing files.

        Args:
            path: Path relative to the project root.
            contents: The complete new file contents.
        """
        return guarded(ws.write_file, path, contents)

    @beta_tool
    def edit_file(path: str, old_text: str, new_text: str) -> str:
        """Replace one exact snippet in an existing file. old_text must appear exactly once.

        Args:
            path: Path relative to the project root.
            old_text: Exact text to find, including indentation. Include enough lines to be unique.
            new_text: Text to put in its place.
        """
        return guarded(ws.edit_file, path, old_text, new_text)

    @beta_tool
    def run_tests(target: str = "") -> str:
        """Run pytest and return its output. Leave target empty to run the whole suite.

        Args:
            target: Optional test file or single test, e.g. tests/test_pawpal.py::test_sort_by_time_returns_chronological_order.
        """
        return guarded(ws.run_tests, target)

    return [list_files, read_file, write_file, edit_file, run_tests]


def act(client, ws: Workspace, task: str, plan: Plan, feedback: Optional[str]) -> str:
    """One ACT round: Claude edits files and runs tests until it says it's done."""
    if feedback is None:
        instruction = (
            "Implement the approved plan. Work test-first: add the new tests, run them and watch them "
            "fail, then change the code until the whole suite passes. When you're done, reply with a "
            "short summary of what you changed and why."
        )
    else:
        instruction = (
            "You already worked on this task, but an independent check of the project failed:\n\n"
            f"{feedback}\n\n"
            "Read the relevant files again, find the root cause, and fix it in the code (not by "
            "weakening tests). Run the whole suite before you finish, then summarize the fix."
        )
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "high"},
        thinking={"type": "adaptive", "display": "summarized"},  # readable reasoning summaries for the trace
        betas=[FALLBACK_BETA],
        fallbacks="default",
        tools=build_tools(ws),
        max_iterations=40,
        messages=[{
            "role": "user",
            "content": f"Task: {task}\n\nApproved plan:\n{plan.render()}\n\n{instruction}",
        }],
    )
    final = None
    for message in runner:
        final = message
        for block in message.content:
            if block.type == "thinking" and block.thinking.strip():
                ws.note("thinking", text=block.thinking.strip())
            elif block.type == "text" and block.text.strip():
                ws.note("message", text=block.text.strip())
                print(f"  claude: {block.text.strip()[:300]}")
            elif block.type == "tool_use":
                ws.note("tool_call", tool=block.name, args=_tool_args_for_trace(block.name, block.input))
                arg = block.input.get("path") or block.input.get("target") or ""
                print(f"  tool:   {block.name} {arg}")
    if final is None:
        return "(no response)"
    if final.stop_reason in ("refusal", "max_tokens"):
        return f"(round ended early: stop_reason={final.stop_reason})"
    return "\n".join(b.text for b in final.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    run_id: str
    ok: bool
    rounds: int
    report_path: Path
    record: dict = field(default_factory=dict)  # same data as result.json


def run_workflow(
    task: str,
    client=None,
    root: Path = ROOT,
    max_rounds: int = 3,
    approve: Callable[[Plan], bool] = lambda plan: True,
    planner: Callable = make_plan,
    actor: Callable = act,
    plan_only: bool = False,
) -> RunResult:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = root / ".agent_runs" / run_id
    ws = Workspace(root)
    log = [f"# Agent run {run_id}", "", f"**Task:** {task}", ""]
    # Machine-readable record of the run: what was tried, what failed, and why.
    record: dict = {"run_id": run_id, "task": task, "model": MODEL, "plan": None, "checks": [], "outcome": None}

    def finish(ok: bool, rounds: int, outcome: str) -> RunResult:
        log.extend(["## Diff", "", "```diff", diff_against_snapshot(root, run_dir / "before") or "(no changes)", "```"])
        report = run_dir / "report.md"
        report.write_text("\n".join(log) + "\n", encoding="utf-8")
        record.update(ok=ok, rounds=rounds, outcome=outcome, changed_files=sorted(ws.changed),
                      tool_calls=len(ws.actions), refused_tool_calls=[a for a in ws.actions if a.startswith("refused")])
        (run_dir / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        (run_dir / "trace.json").write_text(json.dumps(ws.trace, indent=2), encoding="utf-8")
        (run_dir / "trace.md").write_text(render_trace(run_id, task, ws.trace), encoding="utf-8")
        return RunResult(run_id, ok, rounds, report, record)

    print("[0/4] BASELINE  snapshotting project and running existing tests...")
    snapshot(root, run_dir / "before")
    baseline = run_pytest(root, [])
    print(f"      {baseline.summary()}")
    log += ["## Baseline", "", baseline.summary(), ""]
    record["baseline"] = {"passed": len(baseline.passed), "failed": len(baseline.failed)}

    print("[1/4] PLAN      asking Claude for a plan...")
    plan = planner(client, ws, task)
    print("\n" + plan.render() + "\n")
    log += ["## Plan", "", "```", plan.render(), "```", ""]
    record["plan"] = plan.model_dump()
    if plan_only:
        log.append("Stopped after planning (--plan-only).")
        return finish(ok=True, rounds=0, outcome="plan_only")
    if not approve(plan):
        log.append("Plan rejected by the user; no changes made.")
        return finish(ok=False, rounds=0, outcome="plan_rejected")

    feedback = None
    for round_no in range(1, max_rounds + 1):
        label = "ACT" if feedback is None else "FIX"
        ws.stage = f"{label} (round {round_no})"
        print(f"[2/4] {label:<9} round {round_no}/{max_rounds}: Claude is editing and testing...")
        start = len(ws.actions)
        summary = actor(client, ws, task, plan, feedback)
        log += [f"## Round {round_no} ({label})", "", "Tool actions:", ""]
        log += [f"- {a}" for a in ws.actions[start:]] or ["- (none)"]
        log += ["", "Claude's summary:", "", summary, ""]

        print(f"[3/4] CHECK     round {round_no}: independently re-running the full suite...")
        result = check_work(root, baseline, ws.changed, expect_new_tests=bool(plan.tests_to_add))
        print("      " + "\n      ".join(result.report().splitlines()[: 2 + len(result.problems)]))
        log += [f"### Check after round {round_no}", "", "```", result.report(), "```", ""]
        ws.stage = f"CHECK (round {round_no})"
        ws.note("check", ok=result.ok, text=result.report())
        record["checks"].append({
            "round": round_no, "ok": result.ok, "problems": result.problems,
            "passed": len(result.tests.passed), "failed": sorted(result.tests.failed),
        })
        if result.ok:
            print("[4/4] DONE      all checks passed.")
            return finish(ok=True, rounds=round_no, outcome="passed")
        feedback = result.report()

    print(f"[4/4] STOPPED   checks still failing after {max_rounds} rounds.")
    return finish(ok=False, rounds=max_rounds, outcome="gave_up")


def render_trace(run_id: str, task: str, trace: list[dict]) -> str:
    """Human-readable reasoning trace: what Claude thought, said, called, and got back, per stage."""
    lines = [f"# Reasoning trace: run {run_id}", "", f"**Task:** {task}", ""]
    stage = None
    for entry in trace:
        if entry["stage"] != stage:
            stage = entry["stage"]
            lines += [f"## {stage}", ""]
        kind = entry["kind"]
        if kind == "thinking":
            lines += ["> **Reasoning (summary):** " + entry["text"].replace("\n", "\n> "), ""]
        elif kind == "plan":
            lines += ["**Plan returned:**", "", "```", entry["text"], "```", ""]
        elif kind == "message":
            lines += [f"**Claude:** {entry['text']}", ""]
        elif kind == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in entry["args"].items())
            lines += [f"**→ {entry['tool']}**({args})", ""]
        elif kind == "tool_result":
            mark = "✓" if entry["ok"] else "✗ refused"
            body = entry["text"] or "(no output)"
            lines += [f"<details><summary>{mark} {entry['tool']} result</summary>", "", "```", body, "```",
                      "", "</details>", ""]
        elif kind == "check":
            lines += [f"**Independent check:** {'PASS' if entry['ok'] else 'FAIL'}", "", "```", entry["text"], "```", ""]
    return "\n".join(lines) + "\n"


def _ask_approval(plan: Plan) -> bool:
    return input("Proceed with this plan? [y/N] ").strip().lower() in {"y", "yes"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", nargs="?", help="what you want changed, in plain English")
    parser.add_argument("--max-rounds", type=int, default=3, help="ACT/FIX attempts before giving up (default 3)")
    parser.add_argument("--yes", action="store_true", help="skip the plan approval prompt")
    parser.add_argument("--plan-only", action="store_true", help="stop after printing the plan")
    parser.add_argument("--revert", metavar="RUN_ID", help="restore the project to before that run")
    args = parser.parse_args(argv)

    if args.revert:
        before = ROOT / ".agent_runs" / args.revert / "before"
        if not before.is_dir():
            parser.error(f"no snapshot for run '{args.revert}'")
        for line in revert(ROOT, before) or ["nothing to revert"]:
            print(line)
        return 0
    if not args.task:
        parser.error("give a task, e.g. python coding_agent.py \"Fix: plans can run past midnight\"")

    import anthropic

    client = anthropic.Anthropic()
    try:
        client.models.retrieve(MODEL)  # fail fast on missing/invalid credentials, before any work
    except (anthropic.AuthenticationError, TypeError):  # TypeError: no credentials configured at all
        print("No valid Anthropic credentials. Set ANTHROPIC_API_KEY (export ANTHROPIC_API_KEY=...) and retry.")
        return 2
    except anthropic.APIConnectionError:
        print("Could not reach the Anthropic API. Check your network connection.")
        return 2

    try:
        result = run_workflow(
            args.task,
            client=client,
            max_rounds=args.max_rounds,
            approve=(lambda plan: True) if args.yes else _ask_approval,
            plan_only=args.plan_only,
        )
    except anthropic.AuthenticationError:
        print("Authentication failed. Set ANTHROPIC_API_KEY (export ANTHROPIC_API_KEY=...) and try again.")
        return 2
    except anthropic.APIConnectionError:
        print("Could not reach the Anthropic API. Check your network connection.")
        return 2

    print(f"\nReport: {result.report_path.relative_to(ROOT)}")
    if result.rounds:
        print(f"Undo with: python coding_agent.py --revert {result.run_id}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
