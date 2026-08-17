---
name: implement-and-review
description: Execute one or more coding tasks through independent cross-model implementation and review, with Beads status and on-bead comments, isolated worktrees, fixed commit-range reviews, fix/re-review loops, and evidence-backed completion. Use when the user says “run these with review,” requests implementation by Claude with review by Sol or vice versa, wants reviewed parallel/subagent work, or asks to automate implementer/reviewer pairing.
---

# Implement and Review

Run a bounded pipeline; the user manages tasks. No mailbox identities — every phase records its context as `br comments` on the Bead.

Read [references/protocol.md](references/protocol.md) before launching agents.

## Intake

1. Resolve the repository, target branch, tasks, acceptance criteria, and dependencies from the request and environment.
2. If material product/design decisions remain open, use Grill Me first. Launch nothing until the user confirms the shared understanding.
3. Search CASS for relevant prior work before planning repeated research, design, or debugging.
4. Reuse existing Beads or create outcome-sized Beads with acceptance criteria. In Precog, always set `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads` and leave the checkout free of `.beads/`.
5. Treat an explicit request such as “run these with review” as approval for this standard pipeline. State the task split, provider roles, number of agent sessions, batching, and expected extra model usage before launch. Ask again only for an expanded or materially riskier workflow.

## Defaults

- Implementer: latest Claude Opus through Claude Code.
- Reviewer: `gpt-5.6-sol` through Codex.
- Reverse roles when the user asks.
- Run at most three independent implementations concurrently; batch the rest.
- Allow two fix/re-review cycles. Escalate unresolved findings to the user: the agent with context posts a `br comments` ESCALATION on the Bead, tags it `needs-human`, and holds its claim (the gate). Resume with `--remove-label needs-human --assignee "" --status open`. See `swarm-status.sh` for what is waiting (it shows each escalation's latest comment inline).
- Every phase records its context as `br comments` on the Bead itself (implementer → review request, reviewer → verdict, either → human escalation). No Agent Mail, no mailbox identities. The supervisor authors no messages; it only spawns agents and mechanically checks the expected comment landed.
- Keep the reviewer read-only during each review pass.

## Pipeline

1. Pin the base SHA before any work.
2. Create one isolated worktree and `u/tfriedman/iar-<bead-id>` branch per ready task. Preserve unrelated checkout changes.
3. No mailbox identities to manage — each agent records its own context as `br comments` on the Bead. Inject the repo, Bead ID, worktree, and base SHA into each role prompt.
4. Launch provider-specific sessions with the role prompts in the protocol. Prefer native tracked subagents for the current harness and a tracked local CLI process for the other provider.
5. Each implementer commits, verifies acceptance criteria, and posts a `REVIEW REQUEST` comment (fixed base/head SHAs) on the Bead.
6. Only after the head SHA is fixed, run the reviewer (read-only); it reviews the fixed range and posts its findings + `VERDICT:` as a comment on the Bead. Either agent escalates by posting an ESCALATION comment + `needs-human` when blocked. Before advancing, the supervisor mechanically checks the expected comment landed; re-spawn or block if absent.
7. Route requested changes back to the implementer. Repeat against the new head SHA, up to the cycle limit.
8. Close the Bead only after approval and passing checks. Release reservations and post the final evidence summary as a comment on the Bead.
9. Merge approved task branches sequentially into an integration worktree, run combined checks, then update the target branch when safe. If the target checkout is dirty or integration conflicts, leave a reviewed integration branch and report the exact handoff instead of overwriting user work.

## Completion

Report a compact table: Bead, implementer, reviewer, head SHA, verdict, checks, integration state, and blockers. Point to each Bead (`br show <id>` / `br comments <id>`) so the user can inspect the full review thread or intervene.

Finish only when every task is approved and integrated, or explicitly blocked with the reviewed branches and evidence preserved.
