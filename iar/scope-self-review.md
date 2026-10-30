## Scope and self-review (required)

Scope is a contract. Implement exactly what the task's acceptance criteria and the specification require.
- Do not refactor, reformat or move code the task does not need.
- Do not add general mechanisms, registries or extension points for a single use.
- If you believe a change outside the listed files is required, make the smallest change that works and write one line in your notes saying why.

Before you finish the task, review your own work:
1. Run `git diff --stat` against the commit you started from, then read the full diff.
2. Remove unintended or unrelated changes: formatting churn, moved helpers, generated fixtures the criteria do not need, debug code.
3. Fix unclear names, loose types (`Any`) and dead code in what you wrote.
4. Rerun the task's tests and confirm they pass before you commit.
