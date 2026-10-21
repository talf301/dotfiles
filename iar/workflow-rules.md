## Workflow scripts for unattended work

Rules for a Workflow script that implements beads while Tal is away. A script is written for one run and then discarded, so these rules live here, not in any script.

- **A question stops only the work that depends on its answer.** When a bead agent needs Tal, it posts one Inbox question (`escalation-rules.md`, `inbox-etiquette.md`) and then:
  - If the answer cannot change an interface, file or behavior that a later bead builds on: continue with its recommended answer, say so in the question ("I went on with <recommendation>; answer to change it"), add the label `needs-human` to the bead, and return `integrated`. Tal checks it on the group's draft PR.
  - Otherwise return `blocked`. The script skips only the beads and PR groups that depend on that bead. Independent beads and groups keep running.
- **Answers come from the Inbox, not from script edits.** A blocked agent saves its Inbox thread id in a bead comment. On resume, the bead agent reads that thread (`read_thread`) and acts on Tal's reply. Resume with `resumeFromRunId` and the unchanged script, so finished agents replay from cache. Edit the script only to change the plan itself.
- **Gate before launch.** Before starting the run, check every bead against the readiness gate in `~/.claude/skills/dispatch-bead/SKILL.md` step 2. Ask Tal about the beads that fail it before the run starts, not during it.
- **Report the shape once.** When the run starts, post one Inbox message (thread `<epic>: workflow`) that lists the PR groups in order and states which work waits on which. Post again only for questions and draft PRs.
