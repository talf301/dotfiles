# Attention router

This is a small, offline request/result ledger. A caller supplies request, session, and task identities, an objective, question, closed action menu, evidence with provenance, and any already verified grants. A supervisor or model may supply a structured proposal. The router records the proposal and applies only deterministic validation gates.

It never executes actions, grants permissions, launches a model, resumes Claude, scrapes terminals, reads Firstmate state, or sends network requests. `answer` means that a proposal selected an allowed action and cited supplied evidence; the ledger marks that answer as supervisor-authored, but does not treat a proposal boolean as proof of source identity. `need_evidence` means a proposal is absent or incomplete. `needs_human` is the fail-closed result for malformed or unauthorized proposals.

Run a fixture-only call:

```sh
cd agents/attention-router
python3 -m unittest -v
printf '%s\n' '{"request_id":"r1","revision":0,"session_id":"s1","task_id":"t1","objective":"x","question":"y","options":["continue"],"evidence":[{"id":"e1","provenance":{"source":"fixture"}}],"grants":[]}' \
  | python3 cli.py --db "$HOME/.local/state/attention-router/router.sqlite"
```

The database path must be inside an existing owner-only (`0700`) directory. The ledger and SQLite WAL sidecars are created with mode `0600`; symlinks and foreign-owned ledger files are refused. The caller must create the private parent directory before invoking the CLI.

The same `(request_id, revision)` is idempotent only when the payload hash matches. Later revisions must increase and are retained separately; stale revisions return a machine error before a cached answer can be used. A malformed proposal is stored as `needs_human`; it is not retried or replaced with a guessed answer, and its raw JSON is retained for review. Evidence provenance is copied into the request ledger, while returned answers are always marked `supervisor_authored`; a proposal cannot create a human grant.

The first slice has no live adapter. A later caller must provide the request and proposal explicitly and must perform any approved action through its native permission path. Recovery can replay a committed request safely, but it cannot infer the state of an external job or blindly resend a model call after a crash.

The optional native Claude delegate is bounded and proposal-only:

```sh
python3 cli.py --db "$HOME/.local/state/attention-router/router.sqlite" --delegate claude < request.json
```

It uses the fixed Claude CLI with safe mode, no tools, an empty MCP configuration, default permissions, JSON schema output, two turns, a timeout, and a 1 MiB output bound. A reservation is committed before launch. Completed raw responses and model usage/cost metadata are recoverable from SQLite; timeout, crash, and nonzero exit states are recorded as unknown and are never automatically retried. This adapter does not connect to Linear or an inbox and does not resume sessions or execute selected actions.
