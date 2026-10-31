#!/usr/bin/env python3
"""gate-hook: PreToolUse backstop that asks Tal before a Bash command provisions compute or cloud
resources (org rule: never create AWS or Baseten resources without asking, each time).

  gate-hook.py               read the hook JSON on stdin; print an `ask` decision or nothing (pass)
  gate-hook.py --self-test   table of commands -> expected decision, run through the real hook

Gated: AWS create/run verbs (create-*, run-*, request-*, allocate-*, purchase-*, submit-*,
`s3 mb`, `cloudformation deploy`), `sky launch`, `sky jobs launch`, `sky serve up`,
`terraform apply`, `pulumi up`, and Baseten `truss push|deploy`, `baseten push|deploy`.
The hook tokenizes with shlex and looks through `env`/`sudo`/`nohup`/`timeout`/`xargs` wrappers,
VAR=value prefixes, `sh|bash|zsh -c` and `eval` strings, `aws --profile/--region/...` global
options, and `&&`, `||`, `;`, `|`, `&`, `$( )` segments. It returns `ask`, never deny: a budget
block or Tal's answer can still allow the command.

What it CANNOT see (the permissions.ask rules in settings.json and the org rule in CLAUDE.md stay
the primary controls):
- scripts or programs that call cloud SDKs (boto3, the Baseten or SkyPilot Python API, Makefiles,
  `python train.py --launch`)
- shell aliases and functions (`alias up='sky launch'`)
- binaries behind symlinks or renamed copies (`ln -s $(which aws) a`)
- commands built at run time (`$VERB`, `$(echo sky) launch`, base64 | sh)
- tools other than Bash (MCP servers, PowerShell)

Registered in ~/.claude/settings.json by iar/settings-patch.jq with onFailure "block". Any
internal error returns `ask`, not a pass.
"""

import json
import os
import re
import shlex
import subprocess
import sys

SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")", "{", "}", "!", "then", "do", "else"}
WRAPPERS = {"env", "sudo", "command", "exec", "nohup", "time", "nice", "xargs", "timeout",
            "caffeinate", "uvx", "npx", "pipx", "builtin"}
WRAPPER_VALUE_OPTS = {"-u", "-g", "-C", "-n", "-s", "-k", "-P", "-I", "-L"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
AWS_VALUE_OPTS = {"--profile", "--region", "--output", "--endpoint-url", "--query", "--color",
                  "--ca-bundle", "--cli-read-timeout", "--cli-connect-timeout",
                  "--cli-binary-format"}
AWS_VERB = re.compile(r"^(create|run|request|allocate|purchase|submit)-")
AWS_PAIRS = {("s3", "mb"), ("cloudformation", "deploy")}
GATED = {  # program -> set of gated subcommand tuples
    "sky": {("launch",), ("jobs", "launch"), ("serve", "up")},
    "terraform": {("apply",)},
    "pulumi": {("up",)},
    "truss": {("push",), ("deploy",)},
    "baseten": {("push",), ("deploy",)},
}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def tokenize(cmd):
    cmd = HEREDOC.sub(r"\3", cmd)  # heredoc bodies are data (commit messages), not commands
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes (often a heredoc): split crudely, still look for verbs
        return re.findall(r"&&|\|\||[;|&()]|[^\s;|&()'\"]+", cmd)


def segments(tokens):
    seg = []
    for t in tokens + [";"]:
        if t in SEPARATORS or (t and set(t) <= set("&|;()")):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)


def strip_wrappers(argv):
    """Drop VAR=value prefixes and wrapper programs with their options."""
    i = 0
    while i < len(argv):
        t, base = argv[i], os.path.basename(argv[i])
        if ASSIGN.match(t):
            i += 1
        elif base in WRAPPERS:
            i += 1
            if base == "pipx" and i < len(argv) and argv[i] == "run":
                i += 1
            while i < len(argv) and (argv[i].startswith("-") or ASSIGN.match(argv[i])
                                     or (base == "timeout" and re.match(r"^\d", argv[i]))):
                i += 2 if argv[i] in WRAPPER_VALUE_OPTS else 1
        elif base == "uv" and i + 1 < len(argv) and argv[i + 1] == "run":
            i += 2
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
        else:
            break
    return argv[i:]


def check_argv(argv, depth):
    """Return a reason string if argv is gated, else None."""
    argv = strip_wrappers(argv)
    if not argv:
        return None
    prog, rest = os.path.basename(argv[0]), argv[1:]
    if prog in SHELLS:
        for j, t in enumerate(rest):
            if re.match(r"^-[a-z]*c[a-z]*$", t) and j + 1 < len(rest):
                return check(rest[j + 1], depth + 1)
        return None
    if prog == "eval":
        return check(" ".join(rest), depth + 1)
    if prog == "aws":
        words, i = [], 0
        while i < len(rest) and len(words) < 2:
            t = rest[i]
            if t.startswith("--"):
                i += 2 if t in AWS_VALUE_OPTS else 1
            else:
                words.append(t)
                i += 1
        if len(words) == 2 and (AWS_VERB.match(words[1]) or tuple(words) in AWS_PAIRS):
            return f"aws {words[0]} {words[1]}"
        return None
    words = [t for t in rest if not t.startswith("-")]
    for sub in GATED.get(prog, ()):  # allow one option value before the verb (pulumi -C dir up)
        n = len(sub)
        if sub in (tuple(words[:n]), tuple(words[1:n + 1])):
            return " ".join((prog,) + sub)
    return None


def check(cmd, depth=0):
    if depth > 5:
        return "nested too deep to check"
    for seg in segments(tokenize(cmd)):
        reason = check_argv(seg, depth)
        if reason:
            return reason
    return None


def ask(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"gate-hook: `{reason}` creates or provisions paid resources. "
                                    "Org rule: Tal approves this each time (iar/autonomy.md, gated)."}}


def main():
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") != "Bash":
            return 0
        reason = check(event.get("tool_input", {}).get("command", ""))
    except Exception as e:  # fail closed, but as ask, not block
        reason = f"hook error {type(e).__name__}: {e}"
    if reason:
        print(json.dumps(ask(reason)))
    return 0


CASES = [  # (command, expected)
    ("aws ec2 run-instances --image-id ami-1 --count 1", "ask"),
    ("aws --profile ml --region us-east-2 sagemaker create-training-job --cli-input-json file://j", "ask"),
    ("aws --profile=ml ec2 run-instances", "ask"),
    ("AWS_PROFILE=ml aws ecs run-task --cluster c", "ask"),
    ("env AWS_REGION=us-east-2 X=1 aws s3 mb s3://new-bucket", "ask"),
    ("sh -c 'aws ec2 create-volume --size 10'", "ask"),
    ("bash -lc \"cd /tmp && sky launch -c gpu task.yaml\"", "ask"),
    ("git status && aws batch submit-job --job-name j", "ask"),
    ("echo hi; /opt/homebrew/bin/aws s3api create-bucket --bucket b", "ask"),
    ("sky launch task.yaml", "ask"),
    ("sky launch -c mycluster --gpus A100:8 train.yaml", "ask"),
    ("sky jobs launch -n run1 train.yaml", "ask"),
    ("nohup sky serve up svc.yaml &", "ask"),
    ("terraform -chdir=infra apply -auto-approve", "ask"),
    ("cd deploy && pulumi up --yes", "ask"),
    ("truss push --publish", "ask"),
    ("uvx truss push", "ask"),
    ("sudo -u tal aws cloudformation deploy --template-file t.yml --stack-name s", "ask"),
    ("eval 'aws ec2 run-instances'", "ask"),
    ("cat x | xargs aws ec2 allocate-address", "ask"),
    ("echo $(aws ec2 run-instances)", "ask"),
    ("sky exec mycluster train.yaml", "pass"),
    ("sky status", "pass"),
    ("aws s3 ls s3://com.scaledcognition.model-eval-results/", "pass"),
    ("aws sso login --profile ml", "pass"),
    ("aws --profile ml ec2 describe-instances", "pass"),
    ("git push origin u/tfriedman/x", "pass"),
    ("terraform plan", "pass"),
    ("git commit -m 'aws ec2 run-instances is gated'", "pass"),
    ("grep -rn 'sky launch' docs/", "pass"),
    ("echo \"terraform apply\"", "pass"),
    ("pulumi -C deploy up", "ask"),
    ("git commit -F - <<'EOF'\nsky launch is now gated\naws ec2 run-instances too\nEOF\n", "pass"),
    ("cat <<EOF > x.sh\nhello\nEOF\nsky launch t.yaml", "ask"),
]


def self_test():
    fails = 0
    for cmd, want in CASES:
        out = subprocess.run([sys.executable, os.path.abspath(__file__)], text=True, capture_output=True,
                             input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}))
        got = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] if out.stdout else "pass"
        ok = got == want and out.returncode == 0
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {want:4} {got:4} {cmd!r}")
    other = subprocess.run([sys.executable, os.path.abspath(__file__)], text=True, capture_output=True,
                           input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}))
    if other.stdout:
        fails += 1
        print("FAIL non-Bash tool must pass")
    print(f"{len(CASES) + 1 - fails}/{len(CASES) + 1} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else main())
