# W23 item 1: br -> bd tracker spike

Bead: precog-b7tf (W23a). Epic: precog-am8w. Date: 2026-10-30.
Scope: spec W23 item 1 only. Items 2 and 3 (wider scan, Linear) are in W23b (`factory-scan.md`).

## Recommendation

**Keep `br`. Do not switch to `bd` now. No migration bead.**

- Both tools passed the 5-agent concurrency test with 0 failed calls and 0 lost writes.
- The migration to `bd` is clean (421/421 issues, all comments, labels, deps), but it needs 2
  workarounds (ephemeral issues, timestamp precision). It is possible, not free.
- `bd` embedded mode is 4-6x slower per call (about 0.25-0.4 s against 0.06-0.08 s). The
  reconciler (W10) and `board.py` call the tracker on every tick. `bd` server mode is fast, but it
  adds a long-lived `dolt sql-server` process to supervise.
- `bd gate` gives nothing that the spec's question bead plus a `blocks` dependency does not give.
- The only hard reason to use `bd` is Gas Town (it needs `bd` >= 0.57). Agent Mail uses `br`.
  W23b decides on Gas Town. If W23b adopts a Gas Town component that reads the store, reopen this.
- W24 (`iar/ledger.py`) puts every tracker call in one file, so a later switch stays cheap.

Ideas from `bd` worth copying into `ledger.py` (no switch needed): compare-and-set updates
(`--if-assignee`, `--if-status`, exit 13 on mismatch) and claim leases (`heartbeat`, `reclaim`).
They fit the spec's zombie and stale-claim problems.

## W-items this changes

- **W24**: `ledger.py` must return its own normalized JSON, not the raw tool output. The shapes
  differ (see Parity). Add one more rule: an ephemeral flag on an issue hides it from
  `issues.jsonl` (see Finding 3), so `ledger.py` reads the DB (`br list/show`), never the JSONL.
- **W10**: no change to the design. One snapshot call (`br list --all --json --limit 0`) takes about
  60 ms on 421 issues.
- No migration bead. Separate item for Tal: the installed `br` is a 2026-08-14 build that reports
  `0.3.2`; upstream is `v0.7.6` (2026-10-15). All tests here used the installed build.

## Evidence

Versions: installed `br 0.3.2 (release) (harmonize/vlsf2@33527f2)` at `~/.local/bin/br`,
installed `bd 1.3.1 (Homebrew)` (upstream latest `v1.3.2`). Machine: Tal's Mac, 2026-10-30.

### 1. Feature parity for the commands the spec and tooling use

Inventory: 106 `br` calls in 12 files under `iar/`, `bin/`, `claude/skills/` (main checkout, grep).
Used: `update` (-s, --assignee, --add-label, --remove-label, --notes, --actor), `show`,
`comments` / `comments add`, `close --reason`, `list` (--id, --all, --status, -l, --assignee),
`ready --json`, `create` / `q`, `scheduler`. W24 adds `dep`, `label`, `blocked`.

| Need | br | bd | Note |
|---|---|---|---|
| create, q | yes | yes | |
| comments add / list --json | yes | yes | comment id: br int, bd UUID string |
| show --json | includes comments | **omits comments** (`comments_omitted: true`) | bd needs a second call |
| list --json | object `{issues,total,...}` | bare array | bd default `--limit 50`; pass `--limit 0` |
| update -s / --assignee / labels / --notes | yes | yes | bd also has `--append-notes` |
| atomic claim | `update --claim` | `update --claim`, `ready --claim` | |
| compare-and-set update | no | `--if-assignee`, `--if-status` (exit 13) | useful for the reconciler |
| claim lease / dead-worker recovery | no | `heartbeat`, `reclaim` | maps to spec "zombie" |
| close --reason, dep, label, ready, blocked | yes | yes | |
| scheduler (used by `bin/swarm-*`) | yes | no (`ready --explain` is closest) | `swarm` is retired by this epic |
| external store via `BEADS_DIR` | yes | yes | tested: `BEADS_DIR=... bd where` |
| labels per issue | **max 64** (`Validation failed: labels: exceeds 64 labels`) | no limit hit | seen in the first concurrency run |
| init side effects | none outside `.beads/` | writes AGENTS.md, CLAUDE.md, `.claude/`, `.codex/`, `.cursor/`, git hooks unless `--stealth` | matters for the external Precog store |

### 2. Native human gates (`bd gate`)

`bd gate create --type=human --blocks <id>` makes a gate issue that blocks `<id>` in `bd ready`
until `bd gate resolve`. Other types: `timer`, `gh:run`, `gh:pr`, `bead`; these close only when
something runs `bd gate check`. Test in a scratch store:

```
work=precog-zsg
✓ Created gate precog-a34 (type: human)
  Blocks: precog-zsg (work item)
  Reason: Tal approves budget
ready:        ['precog-x48']
✓ Gate resolved: precog-a34
ready after:  ['precog-x48', 'precog-zsg']
```

`br gate` is different: it records gate *results* (`gate report`, `gate list`), it does not block.
In `br` the same effect is a question bead with a `blocks` dependency, which is the spec's design.
Verdict: `bd gate` is a convenience, not a reason to switch. A `timer` gate is close to the spec's
question timeout, but the spec forbids timeouts on gated questions, and a reconciler must still
run the check.

### 3. Migration of the existing store (`iar/research/tracker-migrate-test.sh`)

The script snapshots the Precog store with `sqlite3 .backup` (read-only on the source), exports
JSONL with `br`, imports it into a new embedded `bd` store, exports from `bd`, and diffs every
issue: title, description, design, acceptance, notes, status, priority, type, assignee,
close reason, created/closed time, labels, comments (author + text), deps (target + type).

Store size today: 421 issues + 7 tombstones (the spec said 72; it has grown).

```
$ ./tracker-migrate-test.sh
br 0.3.2  bd 1.3.1
issues in br DB but not in br JSONL (ephemeral), added from br show: precog-bhf
bd import: 8 s; 0 skipped deps
issues    br=421 (+7 tombstones, not migrated) bd=421
comments  br=1343 bd=1343
labels    br=780 bd=780
deps      br=751 bd=751 (0 br deps point at tombstones)
ephemeral br=0 bd=0
RESULT: PASS
```

Findings (each one found by an earlier failing run of the same script):
1. **Timestamps lose sub-second precision.** `bd` rounds to whole seconds
   (`05:26:41.644529Z -> 05:26:42Z`). 333 fields differed before the diff allowed < 1 s. Order of
   events within one second is lost.
2. **Tombstones are not migrated** (`bd import` skips status `tombstone`). Expected.
3. **Ephemeral issues are a trap.** `precog-bhf` (closed epic, 10 children) has `ephemeral=1` in
   the `br` DB. `br sync --flush-only` leaves it out of `issues.jsonl`, so a JSONL-only migration
   drops it and `bd import` skips 10 parent-child deps (`target not found`). If the record is
   added with `ephemeral: true`, `bd` stores it as a wisp: hidden from `list`/`export`, deleted by
   `bd purge`, and deps to it are still skipped. The script adds missing issues from
   `br show --json` and drops the flag; then all deps survive.
4. Import speed: 421 issues in about 8 s.

### 4. Concurrent writes from 5 agents (`iar/research/tracker-concurrency-test.sh`)

5 agents in parallel, 12 rounds each, no retries. Each round: create an own issue, comment on one
shared issue, add a unique label to the shared issue, claim the own issue (status + assignee).
`failed` = the call exited non-zero. `lost` = the call exited 0 but its effect is missing after
all agents finish. 12 rounds keep the shared issue under the `br` 64-label cap.

```
$ ./tracker-concurrency-test.sh br
br br 0.3.2  agents=5 ops=12
wall time: 34 s for 240 calls
calls=240 failed=0 lost=0
RESULT: PASS

$ ./tracker-concurrency-test.sh bd
bd bd version 1.3.1 (Homebrew)  agents=5 ops=12
wall time: 96 s for 240 calls
calls=240 failed=0 lost=0
RESULT: PASS

$ ./tracker-concurrency-test.sh bd-server
bd-server bd version 1.3.1 (Homebrew)  agents=5 ops=12
wall time: 11 s for 240 calls
stderr:    2 ... dolt: commit "bd: comment t-l2y" made no dolt commit (nothing to commit):
           change absorbed into a concurrent writer's commit, or an already-committed retry
calls=240 failed=0 lost=0
RESULT: PASS
```

Each mode also passed a second full run. No lock errors in any mode. `bd` embedded is documented
as "single writer" (README, Storage Modes); in practice it serialized the 5 writers through its
lock file without errors, but slowly. `bd-server` starts a local `dolt sql-server` in the temp
dir and stops it on exit (checked: no `dolt` process left).

Single-call latency (10-call mean, same machine):

| Call | br (421-issue copy) | bd embedded (380-issue copy) |
|---|---|---|
| show --json | 78 ms | 385 ms |
| ready --json | 63 ms | 235 ms |
| list --all --json | 60 ms | 413 ms |

### 5. Release activity and maintenance (GitHub API, 2026-10-30)

| | br: Dicklesworthstone/beads_rust | bd: gastownhall/beads (was steveyegge/beads) |
|---|---|---|
| Latest release | v0.7.6, 2026-10-15 | v1.3.2, 2026-10-24 |
| Releases in last 90 days | 25 | 13 (incl. RCs) |
| Commits since 2026-09-30 / 2026-07-30 | 274 / 1298 | 492 / 1372 |
| Contributors | 6 | 513 |
| Open PRs | 0 | 524 |
| Open issues (+PRs) | 22 | 1609 |
| Issues closed in last 30 days | 31 | 123 |
| Stars | 1143 | 28314 |
| License | NOASSERTION | MIT |

Both are very active. `br` is close to a one-maintainer project with a small, fast-moving surface.
`bd` has a large community and a large backlog. Neither is a maintenance risk today; `br` has the
higher bus factor risk.

### 6. What Gas Town and Agent Mail need

- **Gas Town** (`gastownhall/gastown`, `gt 1.1.0` installed): README requirements table,
  "Beads (`bd`) | 0.57.0+ | Required for native installs", and `gt up` "boots Dolt". So any Gas
  Town component that reads or writes beads needs `bd` and Dolt.
- **MCP Agent Mail** (`Dicklesworthstone/mcp_agent_mail`, now deprecated for
  `mcp_agent_mail_rust`): its installer "Installs Beads Rust (`br`) ... and creates a `bd` shell
  alias pointing to `br`"; the Rust rewrite pins `beads_rust =0.6.0`. Agent Mail needs `br`, not
  `bd`. The spec premise "Gas Town and Agent Mail need bd >= 0.57" is true only for Gas Town.

## Primary sources

- bd README, Storage Modes: https://github.com/gastownhall/beads/blob/main/README.md
- bd CLI help: `bd --help`, `bd gate --help`, `bd gate create --help`, `bd update --help`,
  `bd init --help`, `bd import --help` (bd 1.3.1)
- br CLI help: `br --help`, `br gate --help`, `br sync --help`, `br update --help` (br 0.3.2)
- br repo and releases: https://github.com/Dicklesworthstone/beads_rust/releases
- bd releases: https://github.com/gastownhall/beads/releases
- Gas Town README, requirements table: https://github.com/gastownhall/gastown/blob/main/README.md
- Agent Mail README, "Beads Rust (br) Replaces Beads Go (bd)":
  https://github.com/Dicklesworthstone/mcp_agent_mail/blob/main/README.md
- Agent Mail Rust README (`beads_rust =0.6.0`): https://github.com/Dicklesworthstone/mcp_agent_mail_rust
- GitHub REST API counts: `repos/<r>`, `releases`, `commits?since=`, `contributors`, `search/issues`

## Rerun

```
cd ~/dotfiles/iar/research
./tracker-migrate-test.sh                 # read-only snapshot of the Precog store; exit 0 = PASS
./tracker-concurrency-test.sh br          # scratch store; also: bd, bd-server; [AGENTS] [OPS]
```

Not done: the `br v0.7.6` release binary was not tested (download blocked in this run). Put a
newer `br` first on `PATH` and rerun the two scripts to compare.
