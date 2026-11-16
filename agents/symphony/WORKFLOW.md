---
# Tal's Symphony workflow. Reloaded live: edits apply to the next dispatch.
# Section numbers refer to docs/SPEC.md; choices are explained in docs/PLAN.md and README.md.
tracker:
  kind: linear
  provider:
    team_key: TAL
    # Linear's MCP server over OAuth. `symphony login` stores the token in this Keychain
    # service; no API key anywhere.
    endpoint: https://mcp.linear.app/mcp
    keychain_service: symphony-linear-mcp
    # "me" resolves at startup to the Linear user id of the logged-in user (Tal).
    assignee: me
    # Parked = label `waiting` on an In Progress ticket, or state In Review. A parked ticket
    # runs again only after a comment from Tal or the bridge newer than its last turn start.
    waiting_label: waiting
    wake_states: [In Review]
    in_progress_state: In Progress
    human_review_state: In Review
  required_labels: [agent]
  active_states: [Todo, In Progress, In Review]
  terminal_states: [Done, Canceled, Duplicate]

polling:
  interval_ms: 60000

workspace:
  root: ~/symphony-workspaces
  # Label repo:<name> on a ticket -> SYMPHONY_REPO=<path> for the hooks below. Without a
  # repo label the workspace is a plain directory. Fill in before tagging code tickets, e.g.
  #   dotfiles: ~/dotfiles
  #   precog: ~/code/precog
  repos:
    # Symphony itself, until it merges to dotfiles master.
    symphony: ~/.worktrees/dotfiles-symphony

hooks:
  timeout_ms: 120000
  # New workspace + repo label -> git worktree on u/tfriedman/<ticket-lower> (existing branch
  # reused). The worktree starts from the repo checkout's current HEAD.
  after_create: |
    set -euo pipefail
    if [ -n "${SYMPHONY_REPO:-}" ]; then
      branch="u/tfriedman/$(printf '%s' "$SYMPHONY_ISSUE_IDENTIFIER" | tr '[:upper:]' '[:lower:]')"
      if git -C "$SYMPHONY_REPO" show-ref --verify --quiet "refs/heads/$branch"; then
        git -C "$SYMPHONY_REPO" worktree add "$PWD" "$branch"
      else
        git -C "$SYMPHONY_REPO" worktree add -b "$branch" "$PWD"
      fi
    fi
  # Worktree workspaces are removed with git, never --force: a dirty worktree makes this fail,
  # and Symphony then keeps the workspace and notifies (deviation 2). A plain workspace with
  # any content (reports, results, nested clones) is moved to <root>/.archive instead of
  # being deleted.
  before_remove: |
    set -euo pipefail
    if [ -f .git ]; then
      common="$(git rev-parse --path-format=absolute --git-common-dir)"
      git -C "$(dirname "$common")" worktree remove "$PWD"
    elif [ -n "$(ls -A)" ]; then
      archive="$(dirname "$PWD")/.archive"
      mkdir -p "$archive"
      mv "$PWD" "$archive/$(basename "$PWD")-$(date +%Y%m%dT%H%M%S)"
    fi

agent:
  max_concurrent_agents: 3
  max_turns: 20
  max_retry_backoff_ms: 300000

codex:
  # Shared Codex app-server daemon (deviation 1), so threads are live in the Codex app and Rex.
  transport: daemon
  socket: ~/.codex/app-server-control/app-server-control.sock
  approval_policy: on-request
  approvals_reviewer: auto_review
  thread_sandbox: workspace-write
  # Per-turn sandbox: workspace writes only, but with network. Without network, `claude -p`
  # (independent review) fails as "Not logged in", and experiment launches can't reach Flyte/S3.
  # experiment-run keeps its registrations and launch intents under ~/.local/state/experiment-status.
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
    writableRoots: [/Users/tal/.local/state/experiment-status]
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

budget:
  token_cap: 3000000      # per ticket; label budget:<n>k overrides

notify:
  enabled: true
  states: [In Review, Done]
  failure_attempts: 3

rex:
  enabled: true
  session: symphony
---
You are running unattended for Linear issue {{ issue.identifier }}: {{ issue.title }}.
Tal is not watching this thread. Symphony started it and will resume this same thread after
every pause, so keep your notes and decisions in Linear comments, not only in this thread.
{% if issue.url %}Issue: {{ issue.url }}
{% endif %}
Your working directory is this ticket's workspace. Labels: {{ issue.labels | join: ", " }}.

## Every comment you post

Every Linear comment you write posts as Tal, so the first line of every comment must be
exactly:

🤖 agent (Codex, {{ issue.identifier }})

A comment without that line is read as Tal's and wakes this ticket.

Post a new comment for each milestone, question and the final result. Don't edit earlier
comments to add results: edits are easy to miss, especially on Tal's phone.

## Steps

1. Read {{ issue.identifier }} and all its comments with the Linear MCP. The issue defines the
   task and its definition of done. If the definition of done is missing or ambiguous, ask
   (step 6) instead of guessing.
2. Follow the work skill, and the experiment-execution skill for experiments. Keep effort
   proportionate to the ticket; do not add review or evidence steps it does not need.
3. Launch only compute that {{ issue.identifier }} authorizes. Creating any new AWS or Baseten
   resource always needs a question to Tal first (step 6), every time, even if a similar one
   was approved before.
4. Launch every experiment with
   `experiment-run run <spec> --provider linear --issue {{ issue.identifier }} -- <command>`.
   Run status lives in comments: the bridge keeps one "🤖 experiment-run status" comment per
   run up to date, and posts a signed "🤖 experiment-run" comment when a run reaches an
   outcome worth acting on. Never resubmit a launch whose outcome is uncertain; reconcile it
   first.
5. While runs are in flight, keep working if there is work (fixes, other runs, analysis
   prep). Only when you have nothing to do until a run changes state: post a short comment
   naming the runs you wait on, add the `waiting` label (leave the state In Progress), and
   end your turn. A run outcome or a reply from Tal resumes this thread; Symphony removes the
   label when it does. Never remove the `agent` label.
6. If you need a decision only Tal can make, post one comment starting "Question for Tal:"
   with the question, the options and your recommendation, move the ticket to In Review,
   and end your turn. Make routine, reversible decisions yourself and record them in a
   comment as agent decisions.
7. Code changes go in this workspace's git worktree on its `u/tfriedman/` branch, with tests;
   run the repository's relevant checks. Push or open a draft PR only if the ticket asks for
   one. Never merge, approve, or push to main.
8. Comment at milestones: plan, launch or PR, result.
9. Substantive changes and analyses get an independent Claude review before Done or In
   Review: follow the review skill and run Claude headless and read-only,
   `claude -p --permission-mode plan`, with the fixed revision, the objective and the
   evidence. Post the review summary as a comment that names the reviewer (Claude).
10. Finishing:
    - Code changes end at In Review, never Done: post a final comment (what changed,
      branch, checks, review result, open questions) and move the ticket to In Review.
      A code change means a change to an existing repository. Scripts, configs and
      contracts written in this workspace only to run an experiment or analysis are not
      code changes, even if you commit them locally.
    - Other work may move to Done only when the definition of done is met, the evidence is in
      a comment (what was done, paths, run ids, metrics, open questions), and no runs are in
      flight.

## Limits

- Do not publish claude.ai artifacts. If a plan or report helps, write it as a Markdown file
  in the workspace and name its path in a comment.
- Do not open Plannotator or wait for interactive review.
- Your Linear access can reach other tickets; only touch {{ issue.identifier }}.
- If Codex asks for an approval that its reviewer declines, Symphony turns it into a question
  for Tal and stops this turn. Do not try to work around a declined approval.
