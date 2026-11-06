# Attention router

A small SQLite request ledger with an optional native Claude delegate. It returns proposals to the caller. It never executes the selected action, creates a human grant, resumes sessions, or manages workers.

Requests contain `request_id`, `revision`, `session_id`, `task_id`, `objective`, `question`, `options`, `evidence` and `grants`. Evidence needs unique IDs and source provenance. Grants describe permissions the caller has already verified; the ledger does not authenticate those assertions. Keep credentials out of requests.

Results have four dispositions:

- `answer`: a supervisor-authored choice from the supplied options, with cited evidence. Native permissions still apply.
- `need_evidence`: the caller must retrieve missing context and submit a new revision.
- `needs_human`: the proposal identifies a remaining human decision. The caller uses its native Inbox tool.
- `error`: malformed input/output, a stale revision, or a technical failure. The CLI exits nonzero; this is not a human question.

## Use

Create a private state directory before using the CLI:

```sh
mkdir -m 700 -p "$HOME/.local/state/attention-router"
cd agents/attention-router
python3 -m unittest -v
python3 cli.py --db "$HOME/.local/state/attention-router/router.sqlite" --request request.json --delegate claude
```

Without `--delegate`, supply a local `--proposal` JSON file, or receive `need_evidence` when no proposal is available. Request and proposal files and stdin are limited to 256 KiB. For non-answer proposals, `action` must be null; retrieval instructions belong in `reason`.

The parent directory must be owner-only (`0700`). New ledgers and WAL sidecars use `0600`; symlink and foreign-owned ledgers are refused. Existing sidecars need their own permission check before reuse. Prototype ledgers whose model-call table lacks request payloads are refused before schema changes. Preserve and reconcile those records; do not move unknown calls to a fresh ledger merely to retry them.

## Native delegate

The fixed Claude invocation uses safe mode, no built-in tools, empty MCP configuration, default permissions and no session persistence. It requests structured output, allows two turns, and limits elapsed time to 90 seconds. Output size is checked while the direct child runs; at most a 1 MiB prefix is retained after failure. Only this newly launched direct child is stopped on timeout or overflow; descendant cleanup is not a tested guarantee.

The CLI's $0.25 estimated-cost stop is a soft limit, not a guaranteed billing cap. The adapter retains returned model/usage/cost metadata privately and uses the native default model without an override. Missing model identity remains incomplete audit evidence. Safe mode keeps native authentication and administered policy. See the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) for the supported flags.

## Recovery and delivery

The same request ID and revision is immutable. A matching completed request returns its saved result; superseded revisions fail before returning a cached answer or launching another call.

Before a model call, the ledger commits its full request and a reservation. Completed raw output can be recovered without another call. Timeout, interruption and nonzero exits retain evidence and block automatic retry. A recorded failure never becomes approval.

This adapter does not connect to Linear or Plannotator. The native caller records decisions in Linear and sends genuine human needs through the Inbox with a stable request/revision idempotency key. The work skill defines reply correlation and retrieval. No generic background wake or terminal input path is installed.
