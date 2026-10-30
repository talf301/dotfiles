## When to stop and ask Tal (required)

Stop and ask only when:
- The work needs a Flyte or cluster run, VPN, AWS, or a paid run. Give the exact launch command and the expected cost.
- An acceptance criterion requires a human read (corpus review, conversation QA, final code review).
- Two acceptance criteria conflict, a criterion cannot be met, or the fix needs a spec change. Give the evidence, 2–3 options and a recommendation.
- The bead needs a product default or design choice that the bead, its parent spec and the repo do not answer. Do not choose it yourself.
- The action is outward-facing or cannot be undone: force-push to a shared branch, opening or retargeting a PR, deleting branches, writing to Linear or Slack.
- The change needs a new shared enum or proto value, or touches another PR group's closed beads.

Handle these yourself, and record what you did in your final summary:
- The reviewer's findings are concrete code fixes. Fix them, rerun the tests, review again.
- A test or job is still running. Wait for it in the foreground. Never end your turn while work you started is still running.
- The reviewer reviewed commits outside this bead's range, or contradicts an earlier finding. Point it at this bead's range, show the contradiction, review again.
- A finding belongs to another bead or to the base branch. File a bead for it (`br create`, with a blocks edge if needed) and continue on scope.
- A small change outside the listed files is needed to meet the criteria. Make the smallest change and write one line why.
- A process hung or a tool failed for infra reasons. Retry once. Ask only if it fails a second time.
- A disputed assumption can be measured. Measure it, then act on the result.

Ask early. Raise any decision you can foresee before you build on it, not after the work depends on a guess. Never proceed on an assumed answer: until Tal replies, wait, or work only on parts that do not depend on the answer. A missing or timed-out answer is never a yes.

Every question to Tal starts with a line `QUESTION: <the one question>` and contains: the evidence, 2–3 options, your recommendation, and what you will do after each answer.
