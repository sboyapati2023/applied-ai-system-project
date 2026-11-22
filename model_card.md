# Model Card: PawPal+ Coding Agent

## System overview

| | |
|---|---|
| **What it is** | An agentic coding assistant that plans a change to the PawPal+ codebase, writes the code and tests, checks its own work, and retries on failure |
| **Model** | Claude Opus 5.5 (`claude-opus-5-5`) through the Anthropic Messages API, effort `high`, with server-side refusal fallback |
| **Intended use** | Fixing small, well-specified bugs in this project (PawPal+), with a developer approving the plan and reviewing the diff |
| **Not intended for** | Unreviewed changes to production code, other repositories, or tasks that can't be verified by automated tests |
| **Inputs / outputs** | A plain-English task → a structured plan with a confidence score → file edits, a run report (`report.md`), and a machine-readable log (`result.json`) |
| **Human oversight** | Plan approval before any edit, an independent check after every round, a full diff in every report, and one-command undo (`--revert`) |

---

## Limitations and biases

- **"Tests pass" is not the same as "correct."** The agent's own check only knows what the test suite knows. If Claude writes a weak test for its own change, the check passes anyway. I added hidden acceptance tests in `eval/` to catch this, but they only cover five tasks.
- **The hidden tests encode my own design choices.** For example, they assume a task may end exactly at 24:00, and that an untimed task should fill the *earliest* free gap. A different reasonable fix could fail them. The evaluation is biased toward my interpretation of each bug, not a neutral definition of "correct."
- **The confidence score is self-reported.** Claude rates its own plan from 0 to 1. It is a useful signal only if it is calibrated, meaning high-confidence plans actually succeed more often. Five cases are too few to establish that, so I treat it as a hint, not a measurement.
- **Small, one-shot evaluation.** Each case runs once, and the model is non-deterministic, so the same task can pass one day and fail the next. A trustworthy pass rate would need several runs per case.
- **Running tests means running the agent's code.** The file tools are sandboxed, but `run_tests` executes whatever Python the agent wrote, with my user permissions. A test file could in principle run shell commands or access the network. The sandbox limits *where the agent writes*, not *what that code does when executed*.
- **Repository text is trusted.** The agent reads the README, code, and comments as context. A malicious instruction hidden in a file, such as a comment saying "also delete the tests," could steer it (prompt injection). The independent check catches deleted tests, but not every kind of harmful change.
- **Narrow scope.** It is built for one small Python/pytest project. It sends whole files as planning context, which would not scale to a large codebase, and it has only been tried with English task descriptions.
- **Cost and latency.** A full run makes many API calls at high effort. That is fine for a class project, but each fix costs real money and minutes, not seconds.

---

## Misuse and how it is prevented

| Possible misuse | What prevents or limits it |
|---|---|
| "Make the tests pass" by deleting or weakening tests | `check_work()` fails the run if any baseline test disappears or fails. The system prompt forbids weakening tests. The `trap` evaluation case asks the agent to delete a test outright. |
| The agent loosening its own rules | `coding_agent.py`, its guardrail tests, `conftest.py`, and `.gitignore` are read-only to the agent. The answer key (`eval/`) is invisible to it. |
| Editing files outside the project, or touching secrets | All paths are resolved and must stay inside the project folder. `.venv/`, `.git/`, and run history are hidden. Only `.py`, `.md`, `.txt`, and `.mmd` files can be written. |
| Smuggling options into pytest | `run_tests` accepts only a test-file or test-name pattern, never raw flags. |
| Shipping AI changes nobody reviewed | A human approves the plan first. Every run writes a full diff, and `--revert <run_id>` restores the snapshot. |
| Running arbitrary code through tests | **Not fully prevented.** Mitigation: run the agent only on code you'd run yourself, review the diff before trusting it, and for untrusted tasks run it inside a container or VM with no credentials. |
| Leaking the API key | The key is read from the environment or an `ant` login profile, never written to the repo. `.gitignore` excludes run folders and virtual environments. |

---

## What surprised me while testing reliability

- **A correct fix was graded as wrong.** My self-correction test kept failing even though the fake "fix" was right. Changing `a - b` to `a + b` keeps the file the same size, and both edits landed in the same second, so Python reused stale cached bytecode and ran the *old* code. A real agent making a one-character fix would hit this and "fix" correct code. The fix was a fresh bytecode cache for every test run. The surprise was that the *checker* was the unreliable part, not the AI.
- **My "no API key" test wasn't testing what I thought.** I ran the evaluator with `ANTHROPIC_API_KEY` set to empty to check the error message. Instead, it started a real agent run, because the SDK quietly found my `ant auth login` profile. A test that passes for the wrong reason is easy to miss.
- **The planner caught a risk I hadn't considered.** In my first real `--plan-only` run (the midnight bug), Claude pointed out that a task could be logged as both "Scheduled…" and "Skipped…" in the reasoning text. That's a contradiction a user would see, and no existing test covered it.
- **The hidden tests confirmed the bugs were real.** Before any agent run, 5 of the 6 hidden acceptance tests fail on the original code. The one that passes is a regression guard that is supposed to pass. That gave me confidence the evaluation measures real fixes, not tests that pass by default.
- **The agent did better than its own confidence.** All 4 measured bug fixes passed the hidden acceptance tests on the first round, while Claude rated its plans only 0.80–0.85. With no failures, five cases can't show whether the confidence score predicts anything. A harder or repeated evaluation is needed before I'd trust it as a signal ([eval/results.md](eval/results.md)).
- **It stayed inside the approved plan.** In the priority fix, Claude noticed that a warning in `app.py` became slightly inaccurate after its change. It reported this instead of editing a file the plan didn't include.
- **The evaluation can fail for reasons unrelated to the AI.** The fifth case (the "delete a test" trap) never ran because my API credits ran out. The harness first recorded that as an agent *failure*, which would have understated the agent. I changed it to report API errors as "not measured" so infrastructure problems can't pass for model behavior. That case still needs a run (`python evaluate.py --cases trap`).

---

## Collaboration with AI

I built this project with an AI coding assistant (Claude Code). I described the goal: an agent that can plan, act, and check its own work on my PawPal+ project. The assistant proposed the architecture and wrote the code and tests. I directed scope, supplied the course requirements, set up the API credentials, and ran the agent myself.

**A suggestion I accepted:** keep the pass/fail verdict out of the AI's hands. The assistant suggested that Claude could run tests while it works, but that "done" should be decided by plain Python. That code re-runs the full suite, checks that no original test disappeared, and requires new tests. I accepted this as written. It's the reason the system can catch an AI that claims success it didn't earn, and it made the whole workflow testable without paying for API calls.

**A suggestion I did not accept as written:** the assistant first built the agent inside my Module 2 starter folder (`ai110-module2show-pawpal-starter`). That was a reasonable default, since that's where the PawPal+ code lived, but it didn't fit how my project needed to be organized. I redirected it to put everything in `applied-ai-system-final`, my final-project repository, and to keep the original starter unchanged as a record of Module 2. The assistant then copied over the working PawPal+ code, removed what it had added to the starter, and rebuilt the setup in the new folder.

**Where I stayed in charge:** the assistant did not have my API key and did not invent sample outputs. It left clearly marked placeholders until I ran the agent with my own credentials, and it reported a test failure it had caused (the bytecode issue) instead of hiding it.
