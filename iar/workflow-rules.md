## Workflow scripts for unattended work

Rules for a Workflow script that implements beads while Tal is away. A script is written for one run and then discarded, so these rules live here, not in any script.

- **Questions follow `autonomy.md`.** A bead agent asks Tal only in the cases `autonomy.md` lists, and asks as `escalation-rules.md` says. Then:
  - If the answer cannot change an interface, file or behavior that a later bead builds on: continue with its recommended answer, say so in the question ("I went on with <recommendation>; answer to change it"), add the label `needs-human` to the bead, and return `integrated`. Tal checks it on the group's draft PR.
  - Otherwise return `blocked`. The script skips only the beads and PR groups that depend on that bead. Independent beads and groups keep running.
- **Answers come from the Inbox, not from script edits.** A blocked agent saves its Inbox thread id in a bead comment. On resume, the bead agent reads that thread (`read_thread`) and acts on Tal's reply. Resume with `resumeFromRunId` and the unchanged script, so finished agents replay from cache. Edit the script only to change the plan itself.
- **Gate before launch.** Before starting the run, check every bead against the readiness gate in `~/.claude/skills/dispatch-bead/SKILL.md` step 2. Ask Tal about the beads that fail it before the run starts, not during it.
- **Report the shape once.** When the run starts, post one Inbox message (thread `<epic>: workflow`) that lists the PR groups in order and states which work waits on which. Post again only for questions and draft PRs.
- **Review rounds have no small cap.** A bead agent fixes concrete review findings and reviews again for as long as each round brings new fixes. It never asks Tal whether to integrate after a fix round. It asks only when the review says NEEDS HUMAN, a finding needs a decision (`autonomy.md`), or a fixed finding comes back or two rounds contradict each other. Hard limit: 6 fix rounds. If the review has not approved by then and the last fixes pass the gate, integrate, add the label `needs-human`, list the unreviewed fix commits in the bead comment, and return `integrated`.
