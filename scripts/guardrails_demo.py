"""Show the agent's sandbox refusing unsafe tool calls (no API needed).

Each line is a tool call Claude could make. The Workspace either runs it or refuses it, and a refused
call is sent back to Claude as an error result instead of being executed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coding_agent import ROOT, Workspace, WorkspaceError  # noqa: E402

ATTEMPTS = [
    ("read_file", ("../../.ssh/id_rsa",), "read outside the project"),
    ("read_file", (".venv/pyvenv.cfg",), "read an excluded folder"),
    ("read_file", ("eval/acceptance/test_midnight.py",), "peek at the hidden grading tests"),
    ("write_file", ("coding_agent.py", ""), "rewrite the agent's own guardrails"),
    ("write_file", ("tests/test_coding_agent.py", ""), "delete the guardrail tests"),
    ("write_file", ("deploy.sh", "rm -rf ~"), "write a shell script"),
    ("edit_file", ("pawpal_system.py", "return", "return"), "ambiguous edit (matches many places)"),
    ("run_tests", ("--rootdir=/ -p evil",), "inject pytest options"),
    ("read_file", ("pawpal_system.py",), "normal read (allowed)"),
]

ws = Workspace(ROOT)
for tool, args, label in ATTEMPTS:
    try:
        result = getattr(ws, tool)(*args)
        outcome = f"ALLOWED  ({len(result)} chars returned)"
    except WorkspaceError as exc:
        outcome = f"REFUSED  {exc}"
    print(f"{label:<40} {tool}{args!r:.45}\n    -> {outcome}")
