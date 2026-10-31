# settings-patch.jq: factory changes to ~/.claude/settings.json (epic precog-am8w). Idempotent.
# Apply (from ~/dotfiles, after merge):
#   jq --argjson clusters "$(python3 -c 'import sys; sys.path.insert(0, "iar"); import json, workflow; print(json.dumps(workflow.load()[0]["compute_allowlist"]))')" \
#     -f iar/settings-patch.jq ~/.claude/settings.json > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
# $clusters is iar/WORKFLOW.md compute_allowlist. Re-apply when that list changes.

def add_unique($xs): (. // []) + ($xs - (. // []));

# W6: hard gates. Ask (never deny) for provisioning; iar/gate-hook.py is the backstop for forms
# these prefix rules miss (env/sh -c/--profile/chained). No hard_deny entries.
.permissions.ask |= add_unique([
  "Bash(aws * create-*)",
  "Bash(aws * run-*)",
  "Bash(aws * request-*)",
  "Bash(aws * allocate-*)",
  "Bash(aws * purchase-*)",
  "Bash(aws * submit-*)",
  "Bash(aws s3 mb *)",
  "Bash(aws cloudformation deploy *)",
  "Bash(sky launch *)",
  "Bash(sky jobs launch *)",
  "Bash(sky serve up *)",
  "Bash(terraform apply *)",
  "Bash(pulumi up *)",
  "Bash(truss push *)",
  "Bash(truss deploy *)",
  "Bash(baseten push *)",
  "Bash(baseten deploy *)"
])
| .hooks.PreToolUse |= ((. // []) | map(select(any(.hooks[]?; (.command // "") | endswith("iar/gate-hook.py")) | not))
    + [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/Users/tal/dotfiles/iar/gate-hook.py",
        "args": [], "onFailure": "block", "timeout": 10}]}])
| .autoMode.environment |= ((. // []) | map(select(startswith("**Routine compute (budget-covered)**") | not))
    + ["**Routine compute (budget-covered)**: these existing clusters are routine for runs inside a bead's valid `budget:` block (iar/budget.py exits 0): "
       + (if ($clusters | length) > 0 then ($clusters | join(", ")) else "none yet" end)
       + ". Creating or provisioning compute (sky launch, AWS create/run, Baseten push) is never routine: ask every time."])
# W15: log every PermissionDenied (auto-mode denial) to ~/.local/state/iar/denials.jsonl for the
# digest's "Decided without you" section and iar/ratchet. Diagnostic; never blocks.
| .hooks.PermissionDenied |= ((. // []) | map(select(any(.hooks[]?; (.command // "") | endswith("iar/denial-hook.sh")) | not))
    + [{"hooks": [{"type": "command", "command": "/Users/tal/dotfiles/iar/denial-hook.sh", "timeout": 10}]}])
