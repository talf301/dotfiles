"""Seeded data-generation planner runs, written as small per-seed digests for a refactor equivalence check.

Mirrors data_gen/plans.py _produce_output_plan: config.sample(rng=Random(seed)), compile, then
run_debug(plan_limit=1, randomize_actions=True, random_seed=100_000 + seed, node_budget=...).
Each <label>_<seed>.txt holds: outcome + node count, a sha256 of the sampled plan spec, a sha256 of the
full run output, then the full action traces. Prolog variable names are masked before hashing.
usage: datagen_digest.py <out_dir> <seed> [<seed> ...]
"""

import dataclasses
import hashlib
import itertools
import re
import sys
from pathlib import Path
from random import Random

from sc.hydra.benchmarks import sampler
from sc.hydra.domains.tau_retail.data_gen.config import TauRetailDataGenConfig
from sc.hydra.domains.tau_retail.domain_models import email as email_module
from sc.hydra.domains.tau_retail.domain_models import user_model
from sc.hydra.domains.tau_retail.workflows.auth import builders as auth_builders
from sc.hydra.planning.compiler import compile
from sc.hydra.planning.engine import run_debug

# Prolog variable names and function addresses differ run to run.
VAR = re.compile(r"\b_(G)?[0-9]+\b| at 0x[0-9a-f]+")
# These modules fall back to an unseeded Random() (auth template wording, user and email sampling).
UNSEEDED_RANDOM_MODULES = (auth_builders, user_model, email_module)


def seed_unseeded_randoms(seed: int) -> None:
    counter = itertools.count()

    def seeded_random(*args):
        return Random(*args) if args else Random(seed * 1_000 + next(counter))

    for module in UNSEEDED_RANDOM_MODULES:
        module.Random = seeded_random


NODE_BUDGET = 4000
CONFIGS = {
    "default": TauRetailDataGenConfig(
        name="eq_default", num_plan_specs=1, num_plans=1, intent_count_min=1, intent_count_max=3
    ),
    "stress": sampler._stress_config(),
    # The default reference_probability is 0, which never reaches the referential address/payment paths.
    "referential": TauRetailDataGenConfig(
        name="eq_referential",
        num_plan_specs=1,
        num_plans=1,
        intent_count_min=1,
        intent_count_max=3,
        task_sampling_config={"reference_probability": 0.5},
    ),
}


def sha(text: str) -> str:
    return hashlib.sha256(VAR.sub("_V", text).encode()).hexdigest()[:16]


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for seed in map(int, sys.argv[2:]):
    for label, config in CONFIGS.items():
        key = f"{label}_{seed:04d}"
        seed_unseeded_randoms(seed)
        try:
            sampled = config.sample(rng=Random(seed))
        except Exception as e:
            text = f"SAMPLE_ERROR {type(e).__name__}: {str(e)[:300]}"
        else:
            spec = dataclasses.replace(sampled.plan_spec, name=f"{sampled.plan_spec.name}_eq_{key}")
            head = f"spec_sha={sha(repr(spec))}"
            try:
                r = run_debug(
                    compile(spec),
                    plan_limit=1,
                    randomize_actions=True,
                    random_seed=100_000 + seed,
                    node_budget=NODE_BUDGET,
                )
                status = "plan" if r.plans else ("budget" if r.node_budget_exhausted else "no_plan")
                full = "\n".join(
                    repr(x)
                    for x in (r.plans, r.state_snapshots, r.final_database_states, r.plan_markers, r.snapshot_markers)
                )
                traces = "\n".join(repr(t) for p in r.plans for t in p.action_traces)
                text = f"{status} nodes={r.expanded_nodes}\n{head}\nfull_sha={sha(full)}\n{VAR.sub('_V', traces)}"
            except Exception as e:
                text = f"ERROR {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}\n{head}"
        (out / f"{key}.txt").write_text(text)
        print(key, text.split("\n", 1)[0].split(":")[0], flush=True)
