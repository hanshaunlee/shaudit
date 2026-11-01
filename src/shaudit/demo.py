"""The invoice demonstration.

Two tasks share a cached invoice-processing plan.  Task A delegates an
invoice send, its worker queues it, and then A is revoked.  Task B
independently retrieves the same plan for another invoice.  The auditor
discovers that A's queued send still commits, synthesizes a containment
scoped to A's authority, and shows that A's send is blocked while B
completes.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import closure
from .auditor import AuditConfig, audit
from .experiments import evaluate_containment
from .model import MECHANISM_TEXT, Implementation, Scenario
from .runtime import RunResult, run
from .workloads import INVOICE, role_a, role_b

SHOW = {"plan", "cache_put", "cache_hit", "delegate", "enqueue", "revoke", "dequeue", "gateway_allow",
        "gateway_deny", "effect", "job_cancelled", "blanket_shutdown", "retry_created"}


def demo_scenario() -> Scenario:
    return Scenario(INVOICE.name, (role_a(INVOICE), role_b(INVOICE, 4)), ((3, "A"),))


def timeline(res: RunResult) -> list[str]:
    out = []
    eff = {(e.seq): e for e in res.effects}
    for e in res.events:
        if e.kind not in SHOW:
            continue
        who = f"{e.role or '':1s}"
        what = e.kind
        extra = ""
        if e.kind == "effect":
            x = next(v for k, v in eff.items() if k == e.seq)
            flag = "  <-- SURVIVING AUTHORITY (relies on revoked task)" if x.violation else ""
            extra = f"{x.tool} {e.step}{flag}"
        elif e.kind in ("gateway_allow", "gateway_deny"):
            extra = f"{e.step} presented={e.data.get('presented')} reason={e.data.get('reason')}"
        elif e.kind == "revoke":
            extra = f"operator revokes {e.role} (also voids {', '.join(x for x in e.data['revoked'] if x != e.role) or 'nothing else'})"
        elif e.kind == "cache_hit":
            extra = f"reuses plan from {e.data.get('origin')}; carried approvals={e.data.get('carried')}"
        elif e.step:
            extra = e.step
        out.append(f"  t={e.t:>2}  {who:1s}  {what:14s} {extra}")
    return out


def summary_line(res: RunResult) -> str:
    a_after = [e for e in res.effects if e.role == "A" and e.violation]
    b_done, b_exp = res.legit_completion(("B",))
    return (f"A effects after revocation: {len(a_after)}   "
            f"B completed legitimately: {b_done}/{b_exp}")


def main(flaws: list[str] | None = None, visibility: str = "standard", budget: int = 10, planner=None,
         out: str | None = None) -> dict:
    flaws = flaws if flaws is not None else ["enqueue_only"]
    impl = Implementation(frozenset(flaws))
    sc = demo_scenario()
    tuned = Path("results/tuned.json")
    ab = json.loads(tuned.read_text()) if tuned.exists() else {"alpha": 2.0, "beta": 0.02}
    print("=" * 78)
    print("Invoice demo: two tasks share a cached invoice plan; task A is revoked")
    print("Deployment under audit (hidden from the auditor):", impl.label())
    for f in flaws:
        print(f"  - {f}: {MECHANISM_TEXT[f]}")
    print("=" * 78)

    print("\n[1] Operator revokes A with parent-only shutdown (what the deployment does today)")
    before = run(sc, impl, planner)
    print("\n".join(timeline(before)))
    print("  " + summary_line(before))

    print(f"\n[2] Auditor runs ({visibility} telemetry, {budget}-probe budget, active probe selection)")
    cfg = AuditConfig(policy="active", visibility=visibility, budget=budget, alpha=ab["alpha"], beta=ab["beta"])
    rep = audit(INVOICE, impl, cfg, planner)
    for p in rep.probes:
        top = ", ".join(f"{h} {w:.2f}" for h, w in p.top[:2])
        print(f"  probe {p.index:>2}: {p.probe:32s} {'COUNTEREXAMPLE' if p.hit else 'no violation':15s}"
              f" K={p.K or '-'}  belief: {top}")
    for i, c in enumerate(rep.counterexamples, 1):
        print(f"\n  counterexample {i} (from {c.probe}):")
        print(f"    minimal failure conditions: {'; '.join(c.conditions) or '(none beyond the revocation)'}")
        print(f"    surviving effects: {', '.join(f'{r}.{s}' for r, s in c.effects)}")
        print(f"    explained by: {', '.join(c.explanation) or 'UNEXPLAINED by known mechanisms'}")
        print(f"    authority path: {' -> '.join(c.components)} -> external effect")
        print(f"    single interventions that block it in replay: {', '.join(sorted(c.blocking)) or 'none'}")
    for h in rep.history:
        print(f"  after counterexample {h['cex']}: chose {h['K']} (cost {h['cost']:.2f})")
    print(f"\n  selected containment (grouped optimum): {sorted(rep.K)}  cost {rep.cost:.2f}")
    print(f"  weighted min-cut on the authority graph:  {sorted(rep.mincut_K or [])}")
    print(f"  auditor's report: {rep.claim.replace('_', ' ')} (residual risk {rep.residual:.3f})")
    print(f"  experiments: {rep.executions} probes, {rep.replays} replays for minimization/validation/collateral")

    print("\n[3] Same revocation with the selected containment")
    after = run(sc, Implementation(impl.flaws, rep.K), planner)
    print("\n".join(timeline(after)))
    print("  " + summary_line(after))

    print("\n[4] Comparison on the demo schedule")
    blanket = run(sc, Implementation(impl.flaws, frozenset({"blanket_shutdown"})), planner)
    for name, r in (("parent-only", before), ("blanket shutdown", blanket), ("selected containment", after)):
        print(f"  {name:22s} {summary_line(r)}")

    cl = closure.check(INVOICE.name, Implementation(impl.flaws, rep.K))
    ev = evaluate_containment(INVOICE.name, impl, rep.K)
    print(f"\n[5] Bounded exhaustive check of deployment + containment over {cl.schedules} schedules: "
          f"{'CLOSED within the bound' if cl.closed else f'{cl.violating} violating schedules remain'}")
    print(f"    legitimate independent work completed across the collateral suite: {ev['legit_completion']:.0%}")
    result = {
        "flaws": flaws, "K": sorted(rep.K), "mincut_K": sorted(rep.mincut_K or []), "claim": rep.claim,
        "before": summary_line(before), "after": summary_line(after), "closed": cl.closed,
        "counterexamples": [{"probe": str(c.probe), "conditions": c.conditions, "explanation": c.explanation,
                             "blocking": sorted(c.blocking), "path": c.components} for c in rep.counterexamples],
    }
    if out:
        Path(out).write_text(json.dumps(result, indent=1))
    return result
