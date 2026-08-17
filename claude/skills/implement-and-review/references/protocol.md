# Implement-and-review protocol

## State machine

```text
planned → implementing → review_requested → reviewing
                                  ↑             ↓
                                  └── fixing ← changes_requested
                                                ↓
                                             approved
                                                ↓
                                  integrated → verified → closed
```

The Bead status and `iar:` labels reflect the work state. `br comments` on the Bead preserve the phase transitions and discussion. Git SHAs pin the artifact being reviewed.

## Execution model

Every phase's context is recorded as `br comments` on the Bead — one store, no Agent Mail. The implementer (full-access Claude) writes its own review-request and escalation comments directly. The reviewer runs `codex exec -s read-only`, which cannot acquire the Beads write lock, so it emits its findings + `VERDICT:` to stdout and the orchestrator records that as the verdict comment (the same reason the orchestrator, not the reviewer, stamps the verdict label).

The orchestrator/supervisor is pure mechanism: pin SHAs, create worktrees, spawn agents, record the read-only reviewer's stdout verdict as a comment + label, route findings between phases, recover stale claims. It authors no implementer prose. Its only bookkeeping duty is a dumb, mechanical **check**: before advancing a phase, confirm the expected comment exists on the Bead (an author/prefix scan of `br comments <bead-id>`); if absent, re-spawn or block. That catches "an agent silently skipped its record" without making the supervisor smart.

Ephemeral process: each implementer/reviewer process is short-lived and spawned on demand. No identities to register; no agent idles waiting.

## Orchestrator setup

For each ready Bead:

1. Record `base_sha = git rev-parse <target>`.
2. Create an isolated worktree and branch named `u/tfriedman/iar-<bead-id>`.
3. Inject the project path, Bead ID, worktree, and base SHA into each role prompt. There are no mailbox identities to create — every phase records its context as `br comments` on the Bead, and escalations go to `needs-human` + an ESCALATION comment (see "Raising to the human").

The Bead is the single thread; there is no identity roster to manage.

## Implementer prompt

```text
You are the implementer for <bead-id> in <worktree>.
Read the Bead (`br show <bead-id>`), its acceptance criteria, repository instructions, and relevant CASS evidence.
Reserve only the files you will edit. Implement the smallest complete solution and run proportional checks.
Commit all task changes to the assigned branch. Do not include unrelated working-tree changes.
Then record a REVIEW REQUEST comment on the Bead. WRITE it to a file (do NOT pack prose into a shell
`-m` argument — quoting will bite you), then attach it with `br comments add <bead-id> -f <file>`. The
file must contain:
- REVIEW REQUEST: <title>
- base SHA and head SHA
- acceptance criteria
- concise change summary
- exact checks and results
- known risks or uncertainty
On CHANGES REQUESTED, address only actionable findings, commit fixes, and post a new fixed-SHA REVIEW
REQUEST comment the same way.
If you are blocked or need a human decision, record it on the Bead: WRITE a file whose first line is
`ESCALATION — <the issue and exactly what input you need>`, then the exact commands to respond via the
Bead — to decide and resume: `br update <bead-id> --notes "<decision>"` then `br update <bead-id>
--remove-label needs-human`; to resolve directly: `br close <bead-id>` — then attach it with
`br comments add <bead-id> -f <file>`. Then run `br update <bead-id> --add-label needs-human` (leave it
in_progress — do NOT release the claim, so no duplicate agent is spawned). If the block is STRUCTURAL
or needs a rethink rather than a quick factual decision, ALSO run `br update <bead-id> --add-label
needs-fable` so the human can open a fresh Fable session on it. Then stop.
Do not close the Bead yourself.
```

## Reviewer prompt

```text
You are the independent reviewer for <bead-id> in <worktree>. Stay read-only; do not modify code, and
do NOT run `br` (the read-only sandbox blocks the Beads write lock — the orchestrator records your
output on the Bead). Read the REVIEW REQUEST comment and acceptance criteria the orchestrator gives you.
Review ONLY the fixed range <base-sha>...<head-sha>.
Assess two independent axes:
1. Spec: every Bead acceptance criterion, missing behavior, wrong behavior, and scope creep.
2. Standards: repository instructions, correctness, security, tests, maintainability, and relevant code smells.
Reproduce or run focused checks where useful — for any test run, use `iar-run-tests.sh <worktree>
<pytest-args>` (never bazel; never a bare pytest/venv invocation). Under `codex exec -s read-only`
pytest cannot run at all (see Test execution below): rely on the pass/fail evidence the review
packet already carries, and use non-writing sanity checks (`python3 -c "import ..."`, `ast.parse`)
where you need to verify something yourself. Do not accept the implementer's claims without evidence.
Print your findings to stdout, ordered by severity (each: file:line, impact, concrete correction),
ending with EXACTLY one line: `VERDICT: APPROVED` or `VERDICT: CHANGES REQUESTED`. The orchestrator
posts this as the verdict comment on the Bead and stamps the matching label.
Approve only when no actionable findings remain and the evidence supports the acceptance criteria.
If you need a human decision (ambiguous spec, missing context), print `NEEDS HUMAN: <the question>`
before your verdict line and withhold approval; the orchestrator records it as an ESCALATION comment
on the Bead and tags `needs-human`.
```

## Review packet and replies

Each phase records its own comment on the Bead; the supervisor authors none of the implementer/reviewer prose (it only posts the read-only reviewer's stdout verdict).

Prefix each comment's first line so the thread reads cleanly:

- `START: <title>`
- `REVIEW REQUEST: <title>`
- `CHANGES REQUESTED: <title>`
- `RE-REVIEW REQUEST: <title>`
- `APPROVED: <title>`
- `COMPLETE: <title>`

## Raising to the human

Escalation is authored by the agent with context (the implementer, or the orchestrator on the read-only reviewer's behalf), never invented by the supervisor. When blocked or needing a decision, it records an `ESCALATION` comment on the Bead stating the issue and the exact input needed, tags `needs-human`, and holds. If the block is **structural** (needs a rethink rather than a quick factual decision) it also tags `needs-fable`, which `swarm` marks with 🧠 so the operator can open a fresh Fable session (the `f` key) on it. The human reads escalations in `swarm-status.sh` (which shows each `needs-human` Bead's latest comment inline) or via `br comments <bead-id>`; a sketchybar indicator surfaces the `needs-human` count.

**The comment is a record, not a channel — the human responds by acting on the Bead.** The escalating agent is ephemeral (it has exited by the time you read the comment), so every escalation comment must END with the exact response commands, and the human acts on the Bead (which the *next* agent reads via `br show`):
- Decide and resume: `br update <bead-id> --notes "<decision>"` then `br update <bead-id> --remove-label needs-human`. The supervisor re-picks it up and the fresh agent reads the note. (For an enforced directive, `--agent-context '<json>'` is the governing-instructions channel.)
- Resolve directly, no agent needed: `br close <bead-id>` (or amend the Bead) and remove the worktree.

**The gate** (adopted from Gas City's escalation-as-blocking-gate, minus its machinery): the escalating agent also tags the Bead `br update <bead-id> --add-label needs-human` and leaves it `in_progress` (claim held). Because the supervisor only claims *unassigned* candidates, a held claim is never re-spawned — so the escalated Bead simply waits, visibly, without a duplicate agent starting on it. `swarm-status.sh` lists everything carrying `needs-human` under "Waiting on YOU". To resume after you reply, hand it back to the swarm: `br update <bead-id> --remove-label needs-human --assignee "" --status open` — the supervisor re-claims it and spawns a fresh IAR session that re-enters the existing `u/tfriedman/iar-<bead-id>` worktree. ponytail: re-entry re-runs the implement step rather than resuming mid-thought; acceptable because escalations are rare and the branch/worktree persist. Build state-resume only if escalation-round-trips become frequent.

## Supervisor audit (mechanical, no authoring)

Before advancing a phase, the supervisor performs one dumb check: does the expected comment exist on the Bead, authored by the phase's actor and newer than the phase start (an author/prefix scan of `br comments <bead-id>`, no content judgement)? If absent, re-spawn or block. This catches an agent that silently skipped its own record without giving the supervisor any authoring role.

## Fix loop

On `CHANGES REQUESTED`:

1. Return the complete finding list to the same implementer session.
2. Keep the reviewer read-only.
3. Require a new commit and new head SHA.
4. Review the full original base through new head so fixes cannot hide regressions.
5. Stop after two fix cycles unless the user explicitly extends the limit.

When blocked, leave the Bead open, post a `BLOCKED` comment, preserve the worktree/branch, and report the unresolved findings.

## Integration

Merge only approved heads. Use a separate integration worktree when tasks ran concurrently. Merge in dependency order, run combined checks, and never overwrite unrelated changes in the user's checkout.

If combined checks expose an interaction regression, reopen the affected Bead and route it through the same review loop. Individual approval is not proof that the combined result works.

## Test execution

Findings from precog-iar-infra-friction-wga (bazel serialization, worktree python resolution,
codex's stdin/tempdir behavior) collapse into two rules:

- **Any test run inside an agent session goes through `iar-run-tests.sh <worktree> <pytest-args>`**
  (`~/.local/bin/`), never bazel and never a bare `pytest`/venv invocation. The helper globs every
  package's `*/src` under the worktree into `PYTHONPATH` (not just the package the last bug
  happened to be in — a single-package hardcode silently resolves other packages' `sc.*` back to
  the main checkout) and runs the main-repo venv's python, since a worktree's own `.venv` lacks
  pytest. It always runs with stdin closed. Reserve bazel for the orchestrator's serial
  whole-repo checks — a shared Bazel server queues concurrent agents' invocations behind each
  other, and a review that should take ~1 min has blown the tool timeout waiting 6-10 min for the
  server.
- **Always launch `codex exec` with its own stdin closed** (`... < /dev/null`), and instruct the
  agent to run exactly one non-interactive command per invocation. Without this, a child shell
  command that opens a REPL (a bare `python`/`pytest` invocation) blocks reading `codex exec`'s
  stdin and hangs the session until timeout; this has happened twice.
- **`codex exec -s read-only` has no writable tempdir, and there is no config knob that fixes
  it** — verified empirically: `TMPDIR` overrides, `-c sandbox_permissions=[...]`, and
  `-c sandbox_workspace_write.writable_roots=[...]` were all tried against a plain
  `tempfile.mkdtemp()` and all still fail with `FileNotFoundError: No usable temporary directory
  found`. Read-only blocks all writes, including a scratch tmp; there's no partial carve-out. So a
  Sol reviewer running under `-s read-only` cannot invoke pytest itself, full stop — the fix isn't
  a sandbox flag, it's not asking the reviewer to run pytest. The orchestrator runs
  `iar-run-tests.sh` outside the sandbox and hands the reviewer verified pass/fail evidence in the
  review packet; the reviewer still reviews code directly and may run non-writing sanity checks
  (`python3 -c "import ..."`, `ast.parse`) that don't touch a tempdir.

## Provider launch rules

- From Codex: use native Sol subagents for review. Launch Claude Code implementers as completion-aware local sessions; never detach them without a tracked callback.
- From Claude Code: use native Claude subagents for implementation. Launch Sol reviewers with `codex exec -m gpt-5.6-sol` in read-only mode as completion-aware local sessions, stdin closed (see Test execution above).
- If the requested provider is unavailable or lacks quota, report the affected task and ask before substituting another model.
- Interactive single-bead runs keep normal permission boundaries — you are present to approve.
- Autonomous swarm spawns (`swarm-supervisor.sh`) run headless in `--permission-mode bypassPermissions` because a `-p` session cannot answer prompts and a scoped allowlist is brittle. This drops the permission gate: the fence is the isolated worktree plus this being the operator's own machine and beads under active watch. Move spawns into a container/VM before running untrusted work.
