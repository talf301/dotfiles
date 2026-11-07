---
name: work
description: Execute a scoped task with native tools, Linear, CASS, Plannotator, and the attention router. Use for implementation, research, experiments, or operational work.
---

# Work

1. Read the request and repository-local instructions. State the deliverable and the completion condition.
2. Search CASS before repeating relevant work. Inspect current source and record evidence. Filter Codex history with `cass search --agent codex`; `cass sessions` has no `--agent` flag. Omit `--no-maintenance` for normal searches so lexical maintenance can run.
3. Check Linear when the work needs durable scope, decisions, or status. Keep the task record current.
4. Make routine reversible choices within the objective and existing grants. If the installed attention router is available, use it before asking the person when a bounded delegated answer may be enough.
5. Use the current conversation for questions and handoffs; do not use Plannotator Inbox. Use Plannotator for reviewing plans, diffs, or artifacts when useful.
6. Implement or run the work in the repository's normal environment. Preserve unrelated changes.
7. Run relevant checks. Record the result, evidence, unresolved risks, and the exact next action.

## Deliverables and final review

Declare the requested outputs: a PR with passing checks, results with a reproducible branch/commit, or both. Use one worker lifecycle and deliver each requested output.

For substantive work, complete this loop on the latest revision and requested artifacts:

1. Ask an independent reviewer using the other configured default provider when available. Give the reviewer exact base and head revisions, objective, acceptance criteria, repository rules, connected source, and check evidence. Record the actual model; do not silently upgrade it.
2. Fix valid findings in the implementation and rerun the relevant checks.
3. Ask the same reviewer for a targeted re-review of the fixes and latest substantive revision.
4. Record the review verdict, findings, fixes, checks, and latest revision in Linear.

Completion requires passing relevant checks, resolved blocking findings, and an independent verdict on the latest substantive revision and final artifacts. Explain disputed findings with evidence. New substantive changes after acceptance need review of the changed range. The review is read-only; the implementer owns fixes. Do not merge, auto-merge, or approve Tal's pull request.

## Linear closeout

Before marking a ticket Done, leave one concise Markdown completion comment that stands on its own:

- **Summary:** one or two sentences describing the outcome and why it matters.
- **Delivered:** link the PR, commits, results, or artifacts; distinguish local work from pushed, merged, or deployed work.
- **Validation:** summarize the checks and independent review actually performed, including material limits.
- **Remaining:** include caveats or follow-up only when something remains; keep the ticket open if required work is incomplete.

Update an existing completion comment when revising the same deliverable. Keep raw logs and machine inventories out of the writeup.

## Router request

When asking the attention router, submit:

- stable request, session, and task identifiers plus the request revision;
- the objective and actual question;
- available options and constraints;
- evidence already checked, with source provenance;
- existing grants and the consequence of delay.

Treat `answer` as a delegated agent answer. Treat `need_evidence` as a bounded retrieval task: inspect the requested source, then submit a new revision with the added evidence. Treat `needs_human` as a concrete remaining question for the person. A router answer never grants permission.

Use the router's explicit CLI when it is installed: `python3 ~/dotfiles/agents/attention-router/cli.py --db <private-ledger> --request <request.json> --delegate claude`. The ledger parent must already be an owner-only directory. This starts one tool-free native delegate call; it does not execute the selected action.

Ask a remaining human question in the current conversation, identifying the task and request revision. Use the person’s reply to that question; a later revision can make an older answer stale. Record material decisions in Linear and reconcile the conversation after interruption before asking again.

Treat a nonzero exit or `status: error` as a technical failure, not as a request for human judgment. An in-flight or unknown model call must be reconciled from its existing record; do not change IDs to retry it automatically. Until a supported wake path is verified, the caller returns the result in its own active session; no terminal injection or generic background resume is part of this workflow.

## Experiments

Review results for method, controls, analysis, reproducibility, and claims supported by evidence. Keep setup, evaluation criteria, metrics, and result collection in the repository or task record. Do not turn experiment-specific guidance into global workflow rules.

