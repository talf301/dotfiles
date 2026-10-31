# gnhf runs in Precog

One command per run. Write a prompt, start it in a Rex tab, read the walkthrough in the Plannotator Inbox later.

```bash
gnhf-run <name> <prompt.md> [--base <ref>] [--quota-floor <percent>] [gnhf flags...]
# example
gnhf-run intent-boilerplate ~/prompts/intent-boilerplate.md \
  --max-iterations 30 --max-tokens 80000000 --max-rate-limit-wait 6h \
  --stop-when "identity names derive from one per-intent descriptor"
```

- It leases a Precog treehouse slot as `agent:gnhf:<name>` (about 4 min) and creates branch `u/tfriedman/gnhf-<name>` from `--base` (default `origin/main`).
- It writes `build/gnhf/env.sh` (main-checkout `$PY` and a slot-correct `PYTHONPATH`; the slot venv cannot load libswipl) and `build/gnhf/base` (the base SHA). `build/` is gitignored.
- If the prompt names `hydra-check`, it freezes the check baseline first (about 5 min, cached per base SHA in `~/.local/share/precog/gnhf/`).
- It runs `gnhf --agent claude --current-branch <your flags> "<prompt>"` in the foreground. A watchdog sends one Ctrl+C when the weekly Claude quota falls to the floor (default 60% remaining). Its log is `build/gnhf/watchdog.log`.
- When gnhf exits (done, failures, Ctrl+C), it starts the background session `gnhf walkthrough: <name>` with the `gnhf-walkthrough` skill. The walkthrough arrives in the Inbox and asks what to keep.
- It never pushes, and the slot stays leased. Return it with `treehouse return <slot>` after you decide; returning resets the slot.

To stop: one Ctrl+C stops after the current iteration; two Ctrl+C roll back the current iteration. To rerun only the walkthrough, run the same `~/dotfiles/bin/bg --new` command `gnhf-run` prints at its end. `bin/bg` creates a bead (label `adhoc`) for each walkthrough.

`--max-tokens` counts cache reads: one iteration is about 1.1–1.9M tokens. Use it as a backstop above `--max-iterations`; the watchdog is the real cap.

## The Hydra check

`~/dotfiles/gnhf/hydra-check.sh` accepts or rejects one iteration. Run it from anywhere in the slot. It takes about 5 min and prints one line, `CHECK PASS ...` or `CHECK FAIL [reasons] ...`, with exit 0 only on pass. It passes only when:

- the hydra pytest suite passes (`benchmarks/harness_test.py` is excluded: it is load-sensitive);
- `pyright@1.1.414` is clean on `hydra/`;
- seeded data generation (160 seeds × default, stress and referential configs: sampled spec, full planner output, every rendered utterance) is byte-identical to the baseline from the base commit;
- the collected test count is at least the baseline's.

It also prints `net_lines`, the net production lines in `hydra/` against the base commit (`*_test.py` and `conftest.py` excluded), and `net_test_lines`. Logs are in `build/gnhf/logs/`; on a digest failure `digest-first-diff.txt` shows the first differing seed.

Use it only for behaviour-preserving work (refactors, speedups). A change that is meant to change data generation fails the digest by design.

## Writing a run prompt

gnhf suits work with an automatic accept/reject check and, ideally, a scalar metric. Give the prompt these parts:

1. **Goal:** what to change, with file paths, and the order to do it in.
2. **Environment:** `source build/gnhf/env.sh` sets `$PY` and `PYTHONPATH`. Fast feedback: `$PY -m pytest <files> -q -p no:cacheprovider`. Pyright: `npx -y pyright@1.1.414 --pythonpath "$PWD/.venv/bin/python" <paths>`.
3. **The gate:** "Run `~/dotfiles/gnhf/hydra-check.sh` at the end of every iteration. Commit only on `CHECK PASS`; otherwise fix or revert and report `success: false`. Never edit the check, `env.sh`, the baseline or test assertions to make it pass."
4. **Metric:** say which number ranks passing changes (for example `net_lines`, lower is better), and that a shorter but less clear change is not a win.
5. **Rules:** one coherent change per iteration; no formatting-only changes; no deleted tests unless moved into a parameterized table; the structures you rule out (registries, extension points, borrow-subclassing); do not re-bundle a patch from a rejected iteration; comments describe the current code; start `summary` with `[Hydra] <what changed>` (gnhf uses it in the commit subject); do not push, open PRs or change Beads.

Pass a `--stop-when` condition that the agent can judge from the code, so the run ends when the goal is met.
