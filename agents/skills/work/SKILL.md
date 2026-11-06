---
name: work
description: Execute a scoped task with native tools, Linear, CASS, Plannotator, and the attention router. Use for implementation, research, experiments, or operational work.
---

# Work

1. Read the request and repository-local instructions. State the deliverable and the completion condition.
2. Search CASS before repeating relevant work. Inspect current source and record evidence.
3. Check Linear when the work needs durable scope, decisions, or status. Keep the task record current.
4. Make routine reversible choices within the objective and existing grants. If the installed attention router is available, use it before asking the person when a bounded delegated answer may be enough.
5. Send one concrete remaining human need through the supported Plannotator Inbox path. If an Inbox MCP tool is available, use it; do not duplicate that decision through a blocking native question. Ask for new resources, spending, authentication, or irreversible actions only through the required native approval path.
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

## Router request

When asking the attention router, submit:

- stable request, session, and task identifiers plus the request revision;
- the objective and actual question;
- available options and constraints;
- evidence already checked, with source provenance;
- existing grants and the consequence of delay.

Treat `answer` as a delegated agent answer. Treat `need_evidence` as a bounded retrieval task: inspect the requested source, then submit a new revision with the added evidence. Treat `needs_human` as a concrete remaining question for the person. A router answer never grants permission.

Use the router's explicit CLI when it is installed: `python3 ~/dotfiles/agents/attention-router/cli.py --db <private-ledger> --request <request.json> --delegate claude`. The ledger parent must already be an owner-only directory. This starts one tool-free native delegate call; it does not execute the selected action.

Send a remaining human question through the native Plannotator Inbox tool with a stable idempotency key derived from the request ID and revision. Record the returned message ID, task and request revision in Linear. Use the actual person reply to that exact message; a later task revision can make an older answer stale. Read and reconcile the existing thread after interruption before sending again.

Treat a nonzero exit or `status: error` as a technical failure, not as a request for human judgment. An in-flight or unknown model call must be reconciled from its existing record; do not change IDs to retry it automatically. Until a supported wake path is verified, the caller returns the result in its own active session; no terminal injection or generic background resume is part of this workflow.

## Experiments

Review results for method, controls, analysis, reproducibility, and claims supported by evidence. Keep setup, evaluation criteria, metrics, and result collection in the repository or task record. Do not turn experiment-specific guidance into global workflow rules.

