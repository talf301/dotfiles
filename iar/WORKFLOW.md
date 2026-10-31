---
{
  "tracker": {
    "kind": "br",
    "beads_dir": "/Users/tal/.local/share/beads/precog/.beads",
    "dispatch_label": "auto",
    "active_states": ["open", "in_progress"],
    "terminal_states": ["closed"]
  },
  "polling": {"interval_ms": 60000},
  "agent": {
    "max_concurrent": 3,
    "max_concurrent_by_state": {"in_progress": 3}
  },
  "stall_after_min": 30,
  "max_retry_backoff_ms": 300000,
  "compute_allowlist": [],
  "actions": {
    "answer-pending": false,
    "question-deadline": false,
    "malformed-question": false,
    "inbox-pending": false,
    "stalled": false,
    "zombie": false,
    "stale-lease": false,
    "closed-bead-thread": false,
    "dispatch": false
  }
}
---
# Bead {{bead}}: {{title}}

You are a background session working alone on one bead. Tal sees you in agent view as `{{name}}`. Work only in this slot: {{slot}}. You are attempt {{attempt}} (`{{bead}}#{{attempt}}`).

Bead: run `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads br show {{bead}}` and read its parent spec (`br show <parent>`). Base SHA: {{base_sha}}. Branch: {{branch}} ({{branch_action}}). Done when: `{{done_when}}` passes and every acceptance criterion holds. PR group: `{{pr_group}}` (`none`: the bead has no `pr:` label).

Push only your own branch and, in step 7, your PR group branch. Never rebase onto another branch, retarget a PR or force-push a shared branch: the shape of a PR stack belongs to Tal (`pr-stack` skill).

Steps:
1. Create or check out the branch. Read the bead, the parent spec, and the repo instructions.
2. Implement the smallest complete change. Run tests only with `iar-run-tests.sh {{slot}} <pytest-args>`. Commit with single-line messages and no co-author.
3. Run the done-when command. Fix until it passes.
4. Review: `ACTOR=tal-bg iar-review.sh {{bead}} {{slot}} {{base_sha}}` (exit 0 approved, 1 changes requested, 2 needs human, 3 no verdict). The reviewer picks the tests, the script runs them outside the sandbox, and the reviewer judges their output; failed tests or no tests run can never give exit 0. Read `~/.local/state/iar/review-{{bead}}.log`. Fix the actionable findings and review again, at most 2 fix rounds. On exit 2 or after 2 rounds, ask Tal.
5. Push the branch: `git push -u origin {{branch}}`.
6. Add a bead comment from a file (`br comments add {{bead}} --actor tal-bg -f <file>`): what changed, the tests executed (command and pass/fail counts) apart from checks only reviewed, the code review verdict, open risks.
7. Integrate or ask. Use your `plannotator_inbox` tool and follow the Inbox etiquette below: the fixed precog `project_path`, `thread: "{{name}}"` on every send, a subject that states the result or the decision. If the tool is missing or fails, say so in your summary.
   - **Approved, PR group is not `none`** (step 4 exit 0 and the done-when command passes): integrate without asking. In the slot: `git fetch origin`, then `git checkout --detach origin/{{pr_group}}` (`origin/main` if that branch does not exist yet), `git merge --no-ff {{branch}}`, run the done-when command again, then `git push origin HEAD:refs/heads/{{pr_group}}`. If the push is rejected, do the fetch, checkout and merge again one time. Only after the push: `br close {{bead}} --reason "integrated into {{pr_group}} at <sha>" --actor tal-bg` (closing unblocks dependents, so they must find this bead in the PR group branch). Then call `get_guide_brief` and `submit_guide` with a guided review of `{{base_sha}}..{{branch}}` as an FYI, subject `{{bead}}: integrated <sha>`, open risks and your decisions in the intent. It is an FYI only and asks nothing. Do not resolve the thread and do not return the slot: the reconciler does both after the bead closes. If the merge conflicts, the done-when command fails after the merge, or the push is rejected two times: do not close the bead; ask as in the next item.
   - **Not approved, or PR group is `none`** (step 4 exit 2 or fix rounds used up; a bead without a `pr:` label still asks, because the PR it would open is gated): call `get_guide_brief`, then `submit_guide` with a guided review of `{{base_sha}}..HEAD`, subject `{{bead}}: integrate?`. Put open risks and your decisions in the guide's intent. End the body with one `:::question`: integrate now (for no PR group: open a draft PR or leave the branch pushed for `swarm pr`), or the fix you would make first, with your recommendation.
   - **Blocked on a decision only Tal can make** (at any step): `send_message` with one `:::question` block that has a `Stopped:` line, the evidence, 2–3 options and your recommendation. Then wait for the answer.
8. End with a short summary. Its first line is exactly `READY: integrated <sha>` after an integration, else `READY: <one-line result>` or `QUESTION: <the one question>`. Then add the Inbox thread link, what changed, the executed tests with results, and the code review verdict as separate items. Then wait. Do not open a PR or return the slot, and do not integrate except as step 7 says.

When Tal replies (from the Inbox, the reply arrives in this session by itself):
- **"Integrate", or a yes to integrating:** follow steps 6 and 7 of `~/dotfiles/claude/skills/next-bead/SKILL.md` (merge into the PR-group branch or offer a draft PR, close the bead, post what landed in the thread and resolve it, return the treehouse slot).
- **Comments or "fix first":** fix, rerun the checks and the review, push, then post an updated guide in the same thread (`submit_guide` with `reply_to`, and `supersedes` set to the earlier guide's open question).
- **A live code review in Plannotator:** follow step 5b of `/next-bead`.

{{include scope-self-review.md}}

{{include autonomy.md}}

{{include escalation-rules.md}}

{{include inbox-etiquette.md}}
