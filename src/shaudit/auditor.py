"""The audit loop: choose a probe, run it, update the belief, and contain
whatever surviving authority it exposes."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

from . import closure
from .belief import Belief, LikelihoodModel, predictive, probe_scores, residual_risk
from .contain import Containment, Counterexample, Sandbox
from .model import Implementation, Template
from .observe import PROFILES, attributable
from .probes import CHECKLIST_ORDER, Probe, build, candidates, cost

POLICIES = ("active", "lookahead", "random", "checklist")


@dataclass
class AuditConfig:
    policy: str = "active"
    visibility: str = "standard"
    budget: int = 10
    alpha: float = 1.0  # weight on expected violation discovery
    beta: float = 0.1  # weight on execution cost
    gamma: float = 0.5  # weight on the second step (lookahead policy only)
    n_samples: int = 16
    contain: bool = True
    tau: float = 0.05  # residual-risk threshold for a "no counterexample" claim
    other_prior: float = 0.1  # prior mass on behaviour outside the known mechanisms
    other_tau: float = 0.3  # OTHER mass above which evidence is called insufficient
    lam: float = 3.0  # weight on lost independent work in intervention cost
    whitebox: bool = False  # allow a bounded exhaustive check of the deployment
    seed: int = 0


@dataclass
class ProbeRecord:
    index: int
    probe: str
    cost: float
    hit: bool
    sig: tuple
    eig: float | None
    p_violation: float | None
    K: list[str]
    top: list
    other: float


@dataclass
class AuditReport:
    template: str
    config: AuditConfig
    probes: list[ProbeRecord]
    first_hit: int | None
    counterexamples: list[Counterexample]
    K: frozenset[str]
    mincut_K: frozenset[str] | None
    mincut_feasible: bool | None
    cost: float
    claim: str
    residual: float
    other: float
    marginals: dict[str, float]
    executions: int
    replays: int
    probe_cost: float
    found_tags: set[str] = field(default_factory=set)
    history: list[dict] = field(default_factory=list)


_MODELS: dict = {}


def get_model(t: Template, vis_name: str, n: int) -> LikelihoodModel:
    key = (t.name, vis_name, n)
    if key not in _MODELS:
        _MODELS[key] = LikelihoodModel(t, PROFILES[vis_name], n_samples=n)
    return _MODELS[key]


def _value(s: dict, c: float, cfg: AuditConfig) -> float:
    return s["eig"] + cfg.alpha * s["p_violation"] - cfg.beta * c


def _posterior(belief: Belief, P: np.ndarray, j: int) -> Belief:
    b = belief.copy()
    post = b.p * P[:, j]
    b.p = post / post.sum() if post.sum() > 0 else b.p
    return b


def choose(cfg, belief, model, cands, scen, K, rng, state) -> tuple[Probe, dict | None]:
    if cfg.policy == "random":
        return rng.choice(cands), None
    if cfg.policy == "checklist":
        order = sorted(cands, key=lambda p: (CHECKLIST_ORDER.index(p.kind), p.step or ""))
        p = order[state["ptr"] % len(order)]
        state["ptr"] += 1
        return p, None
    scores = {p: probe_scores(belief, model, p, K, scen[p]) for p in cands}
    vals = {p: _value(scores[p], cost(p, scen[p]), cfg) for p in cands}
    if cfg.policy == "lookahead":
        top = sorted(cands, key=lambda p: -vals[p])[:4]
        best, best_v = None, -1e9
        for p in top:
            _, P = predictive(belief, model, p, K)
            po = (belief.p[:, None] * P).sum(0)
            nxt = 0.0
            for j in range(P.shape[1]):
                if po[j] < 0.02:
                    continue
                b2 = _posterior(belief, P, j)
                nxt += po[j] * max(_value(probe_scores(b2, model, q, K, scen[q]), cost(q, scen[q]), cfg)
                                   for q in cands)
            v = vals[p] + cfg.gamma * nxt
            if v > best_v:
                best, best_v = p, v
        return best, scores[best]
    best = max(cands, key=lambda p: (vals[p], -cands.index(p)))
    return best, scores[best]


def audit(template: Template, impl: Implementation, cfg: AuditConfig, planner=None) -> AuditReport:
    vis = PROFILES[cfg.visibility]
    sb = Sandbox(template, impl, vis, cfg.seed, planner)
    model = get_model(template, cfg.visibility, cfg.n_samples)
    cands = candidates(template)
    belief = Belief(other_prior=cfg.other_prior, kinds={p.kind for p in cands})
    cont = Containment(sb, model, lam=cfg.lam)
    scen = {p: build(p, template) for p in cands}
    rng = random.Random(cfg.seed)
    state = {"ptr": 0}
    K: frozenset[str] = frozenset()
    records: list[ProbeRecord] = []
    first_hit = None
    spent = 0.0
    for i in range(cfg.budget):
        p, sc_scores = choose(cfg, belief, model, cands, scen, K, rng, state)
        sc = scen[p]
        seed = 1_000_003 * (cfg.seed + 1) + 7919 * i
        _, sig = sb.execute(sc, K, seed)
        c = cost(p, sc)
        spent += c
        belief.update(model, p, K, sig)
        hits = attributable(sig, sc)
        records.append(ProbeRecord(i + 1, str(p), c, bool(hits), sig,
                                   sc_scores["eig"] if sc_scores else None,
                                   sc_scores["p_violation"] if sc_scores else None,
                                   sorted(K), belief.top(3), belief.other))
        if hits:
            if first_hit is None:
                first_hit = i + 1
            if cfg.contain:
                K = cont.handle(Counterexample(p, sc, seed, sig), belief)

    residual = residual_risk(belief, model, cands, K)
    unexplained = any(c.unexplained for c in cont.cexes)
    if cfg.whitebox:
        cl = closure.check(template.name, Implementation(impl.flaws, K))
        claim = "closed_within_bound" if cl.closed else "counterexample_within_bound"
    elif belief.other > cfg.other_tau or unexplained:
        claim = "insufficient_evidence"
    elif residual < cfg.tau:
        claim = "no_counterexample_within_budget"
    else:
        claim = "residual_risk"
    return AuditReport(
        template=template.name, config=cfg, probes=records, first_hit=first_hit,
        counterexamples=cont.cexes, K=K, mincut_K=cont.mincut_K, mincut_feasible=cont.mincut_feasible,
        cost=cont.cost(K) if cfg.contain else 0.0, claim=claim, residual=residual,
        other=belief.other, marginals=belief.marginals(), executions=sb.executions,
        replays=sb.replays, probe_cost=spent, found_tags=set(sb.found_tags), history=cont.history,
    )
