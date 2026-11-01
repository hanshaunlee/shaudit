"""Benchmark: probe policies, observability, and containment baselines across
the dev / generalization / hard-transfer splits."""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean

from . import closure
from .auditor import AuditConfig, audit
from .contain import collateral_suite, expected_legit
from .model import INTERVENTION_STATIC_COST, REASON_MECHANISM, REFERENCE, Implementation
from .runtime import run
from .workloads import TEMPLATES, Instance, build_instances

METHODS = {
    "random": AuditConfig(policy="random"),
    "checklist": AuditConfig(policy="checklist"),
    "active": AuditConfig(policy="active"),
    "lookahead": AuditConfig(policy="lookahead"),
    "oracle(full-obs)": AuditConfig(policy="active", visibility="full"),
}

BASELINE_K = {
    "parent_only": frozenset(),
    "static_lineage": frozenset({"cascade_revoke", "cancel_queue_lineage"}),
    "blanket": frozenset({"blanket_shutdown"}),
}

LAM = 3.0


def reachable_mechanisms(template: str, impl: Implementation) -> set[str]:
    """Mechanisms that can admit a post-revocation effect in this workflow:
    either they do so in the deployment itself, or they would on their own."""
    R = {REASON_MECHANISM[t] for t in closure.check(template, impl).tags}
    for f in impl.flaws:
        if any(REASON_MECHANISM[t] == f for t in closure.check(template, Implementation(frozenset({f}))).tags):
            R.add(f)
    return R


def evaluate_containment(template: str, impl: Implementation, K) -> dict:
    """Ground-truth effect of running intervention set K on this deployment."""
    full = Implementation(impl.flaws, frozenset(K))
    cl = closure.check(template, full)
    done = expected = lost = 0
    suite = collateral_suite(TEMPLATES[template])
    for sc in suite:
        r = run(sc, full)
        d, e = r.legit_completion()
        done, expected = done + d, expected + e
        happened = {(x.role, x.step) for x in r.effects}
        lost += len(expected_legit(sc) - happened)
    cost = sum(INTERVENTION_STATIC_COST[i] for i in K) + LAM * lost / len(suite)
    return {
        "closed": cl.closed,
        "violation_rate": cl.violation_rate,
        "legit_completion": done / expected if expected else 1.0,
        "latency": cl.max_latency,
        "cost": cost,
    }


def run_instance(job: tuple[Instance, dict[str, AuditConfig], int]) -> list[dict]:
    inst, methods, budget = job
    t = TEMPLATES[inst.template]
    R = reachable_mechanisms(inst.template, inst.impl)
    base = {"split": inst.split, "template": inst.template, "family": t.family,
            "flaws": sorted(inst.impl.flaws), "reachable": sorted(R), "instance": inst.seed}
    rows = []
    for name, cfg in methods.items():
        rep = audit(t, inst.impl, replace(cfg, seed=inst.seed, budget=budget))
        ev = evaluate_containment(inst.template, inst.impl, rep.K)
        found = {REASON_MECHANISM[x] for x in rep.found_tags}
        mc = evaluate_containment(inst.template, inst.impl, rep.mincut_K) if rep.mincut_K is not None else None
        claimed = rep.claim == "no_counterexample_within_budget"
        rows.append({
            **base, "kind": "audit", "method": name,
            "first_hit": rep.first_hit,
            "coverage": len(found & R) / len(R) if R else None,
            "claim": rep.claim, "claimed_contained": claimed, "false_claim": claimed and not ev["closed"],
            "K": sorted(rep.K), "mincut_K": sorted(rep.mincut_K) if rep.mincut_K is not None else None,
            "mincut_cost": mc["cost"] if mc else None, "mincut_closed": mc["closed"] if mc else None,
            "mincut_blocks_known": rep.mincut_feasible,
            "counterexamples": len(rep.counterexamples),
            "unexplained": sum(1 for c in rep.counterexamples if c.unexplained),
            "executions": rep.executions, "replays": rep.replays, "probe_cost": rep.probe_cost,
            "other": rep.other, "residual": rep.residual, **ev,
        })
    for name, K in BASELINE_K.items():
        rows.append({**base, "kind": "baseline", "method": name, "K": sorted(K),
                     **evaluate_containment(inst.template, inst.impl, K)})
    rows.append({**base, "kind": "baseline", "method": "reference_impl", "K": [],
                 **evaluate_containment(inst.template, REFERENCE, frozenset())})
    return rows


def run_all(instances: list[Instance], methods: dict[str, AuditConfig], budget: int, workers: int | None = None):
    jobs = [(i, methods, budget) for i in instances]
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    rows: list[dict] = []
    if workers == 1:
        for j in jobs:
            rows.extend(run_instance(j))
        return rows
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for out in ex.map(run_instance, jobs, chunksize=1):
            rows.extend(out)
    return rows


def _m(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def summarize(rows: list[dict], budget: int) -> list[dict]:
    out = []
    keys = sorted({(r["split"], r["kind"], r["method"]) for r in rows},
                  key=lambda k: (["dev", "gen", "hard"].index(k[0]), k[1], k[2]))
    for split, kind, method in keys:
        rs = [r for r in rows if r["split"] == split and r["method"] == method and r["kind"] == kind]
        s = {"split": split, "kind": kind, "method": method, "n": len(rs)}
        if kind == "audit":
            vuln = [r for r in rs if r["reachable"]]
            s["hit_rate"] = _m([1.0 if r["first_hit"] else 0.0 for r in vuln])
            s["probes_to_first"] = _m([r["first_hit"] or budget + 1 for r in vuln])
            s["coverage"] = _m([r["coverage"] for r in vuln])
            claims = [r for r in rs if r["claimed_contained"]]
            s["claim_rate"] = len(claims) / len(rs)
            s["false_claim_rate"] = (sum(r["false_claim"] for r in claims) / len(claims)) if claims else None
            s["insufficient_evidence"] = _m([1.0 if r["claim"] == "insufficient_evidence" else 0.0 for r in rs])
            s["replays"] = _m([r["replays"] for r in rs])
            withK = [r for r in rs if r["K"]]
            s["n_contained"] = len(withK)
            s["grouped_cost"] = _m([r["cost"] for r in withK])
            s["grouped_closed"] = _m([1.0 if r["closed"] else 0.0 for r in withK])
            s["mincut_cost"] = _m([r["mincut_cost"] for r in withK])
            s["mincut_closed"] = _m([1.0 if r["mincut_closed"] else 0.0 for r in withK])
            s["mincut_blocks_known"] = _m([1.0 if r["mincut_blocks_known"] else 0.0 for r in withK])
            s["mincut_differs"] = _m([1.0 if r["mincut_K"] != r["K"] else 0.0 for r in withK])
        s["closed"] = _m([1.0 if r["closed"] else 0.0 for r in rs])
        s["violation_rate"] = _m([r["violation_rate"] for r in rs])
        s["legit_completion"] = _m([r["legit_completion"] for r in rs])
        s["cost"] = _m([r["cost"] for r in rs])
        s["latency"] = _m([r["latency"] for r in rs])
        out.append(s)
    return out


def fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def markdown(summary: list[dict]) -> str:
    lines = []
    audit_cols = ["n", "hit_rate", "probes_to_first", "coverage", "claim_rate", "false_claim_rate",
                  "insufficient_evidence", "closed", "legit_completion", "cost", "replays"]
    base_cols = ["n", "closed", "violation_rate", "legit_completion", "cost", "latency"]
    for split in ("dev", "gen", "hard"):
        rs = [s for s in summary if s["split"] == split]
        if not rs:
            continue
        lines.append(f"\n### {split}\n")
        lines.append("| probe policy | " + " | ".join(audit_cols) + " |")
        lines.append("|---" * (len(audit_cols) + 1) + "|")
        for s in rs:
            if s["kind"] == "audit":
                lines.append(f"| {s['method']} | " + " | ".join(fmt(s.get(c)) for c in audit_cols) + " |")
        lines.append("")
        lines.append("| containment | " + " | ".join(base_cols) + " |")
        lines.append("|---" * (len(base_cols) + 1) + "|")
        for s in rs:
            if s["kind"] == "audit" and s["method"] == "active":
                lines.append("| CEGC (active) | " + " | ".join(fmt(s.get(c)) for c in base_cols) + " |")
        for s in rs:
            if s["kind"] == "baseline":
                lines.append(f"| {s['method']} | " + " | ".join(fmt(s.get(c)) for c in base_cols) + " |")
        opt_cols = ["n_contained", "grouped_cost", "grouped_closed", "mincut_cost", "mincut_blocks_known",
                    "mincut_closed", "mincut_differs"]
        lines.append("")
        lines.append("| optimizer (same discovered paths) | " + " | ".join(opt_cols) + " |")
        lines.append("|---" * (len(opt_cols) + 1) + "|")
        for s in rs:
            if s["kind"] == "audit" and s["method"] == "active":
                lines.append("| active policy | " + " | ".join(fmt(s.get(c)) for c in opt_cols) + " |")
    return "\n".join(lines) + "\n"


def tune(budget: int = 10, workers: int | None = None) -> dict:
    """Pick alpha/beta for the active policy on the dev split only."""
    dev = [i for i in build_instances() if i.split == "dev"]
    grid = {f"a{a}_b{b}": AuditConfig(policy="active", alpha=a, beta=b)
            for a in (0.0, 0.5, 1.0, 2.0) for b in (0.0, 0.02, 0.05, 0.1)}
    rows = run_all(dev, grid, budget, workers)
    scores = {}
    for name in grid:
        rs = [r for r in rows if r["method"] == name and r["kind"] == "audit" and r["reachable"]]
        ptf = mean(r["first_hit"] or budget + 1 for r in rs) / budget
        cov = mean(r["coverage"] for r in rs)
        allr = [r for r in rows if r["method"] == name and r["kind"] == "audit"]
        claims = [r for r in allr if r["claimed_contained"]]
        fc = sum(r["false_claim"] for r in claims) / len(claims) if claims else 0.0
        scores[name] = {"probes_to_first/budget": ptf, "coverage": cov, "false_claim_rate": fc,
                        "score": ptf + (1 - cov) + fc}
    best = min(scores, key=lambda k: scores[k]["score"])
    cfg = grid[best]
    return {"best": best, "alpha": cfg.alpha, "beta": cfg.beta, "scores": scores}


def bench(out_dir: str = "results", budget: int = 10, workers: int | None = None, splits=("dev", "gen", "hard"),
          limit: int | None = None) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    methods = dict(METHODS)
    tuned = out / "tuned.json"
    if tuned.exists():
        t = json.loads(tuned.read_text())
        methods = {k: replace(v, alpha=t["alpha"], beta=t["beta"]) if v.policy in ("active", "lookahead") else v
                   for k, v in methods.items()}
    instances = [i for i in build_instances() if i.split in splits]
    if limit:
        instances = instances[:limit]
    rows = run_all(instances, methods, budget, workers)
    (out / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summary = summarize(rows, budget)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "summary.md").write_text(
        f"Budget {budget} probes per audit. Methods: {json.dumps({k: asdict(v) for k, v in methods.items()})}\n"
        + markdown(summary))
    return summary


def llm_subset(planner, n_templates: int = 4, out_dir: str = "results") -> dict:
    """Real planning and cache adaptation on a representative subset.

    For each template: does the LLM adapter carry a cached approval into a
    task that reuses a revoked task's plan, and does the auditor reach the
    same containment as with deterministic planning?"""
    from .auditor import AuditConfig, audit
    from .observe import PROFILES, attributable, observe, signature
    from .probes import Probe, build
    from .runtime import DeterministicPlanner

    picks = [TEMPLATES["invoice_demo"]] + [t for t in TEMPLATES.values() if t.split == "dev"][: max(0, n_templates - 1)]
    flawed = Implementation(frozenset({"cache_carries_auth"}))
    report = {"provider": getattr(planner, "provider", None), "model": getattr(planner, "model", None),
              "templates": []}
    for t in picks:
        row = {"template": t.name}
        for kind in ("revoked_plan_reuse", "independent_reuse"):
            sc = build(Probe(kind), t)
            for label, pl in (("llm", planner), ("deterministic", DeterministicPlanner())):
                r = run(sc, flawed, pl)
                sig = signature(observe(r, PROFILES["full"]), PROFILES["full"])
                row[f"{kind}/{label}"] = {
                    "violations": len(r.violations),
                    "attributable": len(attributable(sig, sc)),
                }
        rep_llm = audit(t, flawed, AuditConfig(budget=6), planner)
        rep_det = audit(t, flawed, AuditConfig(budget=6))
        row["audit"] = {"llm_K": sorted(rep_llm.K), "deterministic_K": sorted(rep_det.K),
                        "agree": rep_llm.K == rep_det.K}
        report["templates"].append(row)
    adapt = getattr(planner, "adaptations", [])
    offered = [a for a in adapt if a["offered"]]
    report["adaptations_with_cached_approvals"] = len(offered)
    report["adaptations_that_carried_approvals"] = sum(1 for a in offered if a["carried"])
    report["adaptations_that_rewrote_approvals"] = sum(1 for a in offered if a.get("rewritten"))
    report["adaptations_that_dropped_all"] = sum(1 for a in offered if not a["carried"] and not a.get("rewritten"))
    report["approval_examples"] = adapt[:6]
    report["llm_calls"] = getattr(planner, "llm_calls", None)
    report["refusals"] = getattr(planner, "refusals", None)
    report["fallbacks_to_template"] = getattr(planner, "fallbacks", None)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "llm_subset.json").write_text(json.dumps(report, indent=1))
    return report
