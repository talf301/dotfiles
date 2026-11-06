---
name: review
description: Independently review a plan, diff, or completed change against its objective, evidence, safety boundaries, and repository rules.
---

# Review

1. Identify the fixed revision, artifact, or file set under review. Read the objective and applicable repository instructions. Read the requested outputs: PR, results, or both.
2. Inspect the complete relevant source, not only the summary or changed lines.
3. Check correctness, safety, maintainability, tests, recovery behavior, and whether the change contains unnecessary complexity.
4. Distinguish observed facts, inferred risks, and recommendations. Do not treat an agent plan, historical example, or confidence score as human authorization.
5. Report findings by severity with exact file or artifact references. If there are no findings, state the checks performed and the remaining uncertainty.
6. For a plan or visual artifact, use Plannotator when human review is needed. For a code change, run the relevant checks and preserve unrelated work. Review is read-only: report findings; do not edit, merge, auto-merge, or approve Tal's pull request.

## Independent review

Use an independent reviewer for every substantive final revision, normally through the other configured default provider. The reviewer must receive the fixed revision, objective, constraints, and evidence needed to assess the change, but must not silently broaden scope or grant new resources. Review results for methods, controls, analysis, reproducibility, and claims supported by evidence. After fixes, inspect the new fixed revision and affected context in a targeted re-review. Record the actual reviewer model; no silent model upgrade.

## Completion

A review is complete only when every relevant finding is classified, evidence is recorded, and the owner has a clear next action or an explicit acceptance of the remaining risk. Record the verdict, findings, checks, and reviewed revision in Linear.

