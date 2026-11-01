"""Counterexample-guided containment.

For each counterexample: minimize the schedule, explain it with the known
mechanisms (or mark it unexplained), find which single interventions block
it by replay, add the resulting authority path to the graph, choose the
cheapest intervention set that blocks every known path, rerun nearby
variations, and measure collateral damage on independent work.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import networkx as nx

from .belief import Belief, LikelihoodModel
from .minimize import atoms, ddmin, describe, rebuild
from .model import (
    INTERVENTION_COMPONENT,
    INTERVENTION_STATIC_COST,
    INTERVENTIONS,
    KNOWN_MECHANISMS,
    PLANNER,
    REASON_MECHANISM,
    Implementation,
    Scenario,
    Template,
)
from .observe import Visibility, attributable, observe, observed_tags, signature
from .probes import Probe
from .runtime import RunResult, run
from .workloads import role_a, role_b, role_d

CATALOG = tuple(i for i in INTERVENTIONS if i != "blanket_shutdown")
COMPONENT_ORDER = ("revocation", "cache", "delegation", "propagation", "restart", "queue", "batch",
                   "retry", "gateway")


class Sandbox:
    """The deployment under audit. The auditor only receives observations;
    full runs are retained for scoring."""

    def __init__(self, template: Template, impl: Implementation, vis: Visibility, seed: int = 0, planner=None):
        self.template, self._impl, self.vis, self.seed = template, impl, vis, seed
        self.planner = planner
        self.executions = 0
        self.replays = 0
        self.runs: list[RunResult] = []
        self.found_tags: set[str] = set()

    def execute(self, sc: Scenario, K, timing_seed: int, replay: bool = False):
        if replay:
            self.replays += 1
        else:
            self.executions += 1
        res = run(sc.with_(jitter_seed=timing_seed), Implementation(self._impl.flaws, frozenset(K)), self.planner)
        obs = observe(res, self.vis, seed=timing_seed)
        sig = signature(obs, self.vis)
        if not replay:  # coverage counts what the probes themselves exposed
            self.found_tags |= observed_tags(res, sig)
        self.runs.append(res)
        return obs, sig


@dataclass
class Counterexample:
    probe: Probe | str
    scenario: Scenario
    timing_seed: int
    sig: tuple
    minimal: Scenario | None = None
    conditions: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    blocking: frozenset[str] = frozenset()
    components: list[str] = field(default_factory=list)
    effects: list[tuple[str, str]] = field(default_factory=list)

    @property
    def unexplained(self) -> bool:
        return not self.explanation


def collateral_suite(t: Template) -> list[Scenario]:
    A = role_a(t)
    out = [Scenario(t.name, (A, role_b(t, 3), role_d(1)), ((rt, "A"),)) for rt in (2, 4, 6)]
    out.append(Scenario(t.name, (A, role_b(t, 3), role_d(1)), ((3, "A"),), notify_delay=3))
    return out


def expected_legit(sc: Scenario) -> set[tuple[str, str]]:
    return {(r.role, s.id) for r in sc.roles if r.role in ("B", "D") for s in r.steps if s.tool in r.grant}


class Containment:
    def __init__(self, sandbox: Sandbox, model: LikelihoodModel, lam: float = 3.0, max_rounds: int = 6):
        self.sb, self.model, self.lam, self.max_rounds = sandbox, model, lam, max_rounds
        self.t = sandbox.template
        self.cexes: list[Counterexample] = []
        self.K: frozenset[str] = frozenset()
        self._coll: dict[frozenset, float] = {}
        self._blocked: dict = {}
        self.history: list[dict] = []
        self.mincut_K: frozenset[str] | None = None
        self.mincut_feasible: bool | None = None

    # --- replays ------------------------------------------------------------
    def violates(self, sc: Scenario, K, seed: int) -> bool:
        key = (sc, frozenset(K), seed)
        if key not in self._blocked:
            _, sig = self.sb.execute(sc, K, seed, replay=True)
            self._blocked[key] = bool(attributable(sig, sc))
        return self._blocked[key]

    def collateral(self, K) -> float:
        """Observable lost independent work per run (B and D steps that never
        reached the tools)."""
        K = frozenset(K)
        if K not in self._coll:
            lost = 0
            suite = collateral_suite(self.t)
            for i, sc in enumerate(suite):
                obs, _ = self.sb.execute(sc, K, 7000 + i, replay=True)
                done = {(e.role, e.step) for e in obs if e.kind == "effect"}
                lost += len(expected_legit(sc) - done)
            self._coll[K] = lost / len(suite)
        return self._coll[K]

    def cost(self, K) -> float:
        return sum(INTERVENTION_STATIC_COST[i] for i in K) + self.lam * self.collateral(K)

    # --- analysis -----------------------------------------------------------
    def minimize(self, c: Counterexample) -> None:
        base = atoms(c.scenario)

        def test(keep):
            return self.violates(rebuild(c.scenario, keep), self.K, c.timing_seed)

        keep = ddmin(base, test) if test(base) else base
        c.minimal = rebuild(c.scenario, keep)
        c.conditions = [describe(a) for a in keep]
        _, sig = self.sb.execute(c.minimal, self.K, c.timing_seed, replay=True)
        c.effects = attributable(sig, c.minimal)
        c.sig = sig

    def explain(self, c: Counterexample, belief: Belief) -> None:
        if self.sb.vis.reasons:
            tags = {it.split(":")[3] for it in c.sig if it.startswith("R:")}
            c.explanation = sorted({REASON_MECHANISM[t] for t in tags if REASON_MECHANISM[t] in KNOWN_MECHANISMS})
            return
        marg = belief.marginals()
        hits = []
        for m in KNOWN_MECHANISMS:
            if not self._model_violates(c.minimal, frozenset({m}), self.K):
                continue
            # A mechanism explains the counterexample only if every intervention
            # that blocked it in replay also blocks it in the model.
            if all(not self._model_violates(c.minimal, frozenset({m}), self.K | {i}) for i in c.blocking):
                hits.append(m)
        c.explanation = sorted(hits, key=lambda m: -marg[m])

    def _model_violates(self, sc: Scenario, flaws, K) -> bool:
        smp = self.model.scenario_samples(sc, flaws, frozenset(K))
        return any(attributable(tuple(k for k in it if k.startswith("E:")), sc) for it in smp)

    def blocking_set(self, c: Counterexample) -> None:
        c.blocking = frozenset(i for i in CATALOG if i not in self.K
                               and not self.violates(c.minimal, self.K | {i}, c.timing_seed))

    def components(self, c: Counterexample) -> None:
        comps = {"revocation", "gateway"}
        sc = c.minimal
        roles = {r for r, _ in c.effects}
        if "C" in roles or sc.role("B") is not None:
            comps.add("cache")
        for role, step in c.effects:
            st = self.t.step(step)
            if st.worker != PLANNER:
                comps.add("delegation")
            if st.mode == "queued":
                comps.add("queue")
        if sc.notify_delay != 1 or sc.crashes:
            comps.add("propagation")
        if any(cr.restart_at is not None for cr in sc.crashes):
            comps.add("restart")
        if sc.failures:
            comps.add("retry")
        if sc.batch_window > 1 and sc.role("B") is not None:
            comps.add("batch")
        comps |= {INTERVENTION_COMPONENT[i] for i in c.blocking}
        c.components = [x for x in COMPONENT_ORDER if x in comps]

    # --- optimization -------------------------------------------------------
    def feasible(self, K) -> bool:
        return all(not self.violates(c.minimal, K, c.timing_seed) for c in self.cexes)

    def optimize_grouped(self) -> frozenset[str]:
        best, best_cost = None, None
        for r in range(len(CATALOG) + 1):
            for S in itertools.combinations(CATALOG, r):
                S = frozenset(S)
                if not self.feasible(S):
                    continue
                cst = self.cost(S)
                if best is None or (cst, len(S), sorted(S)) < (best_cost, len(best), sorted(best)):
                    best, best_cost = S, cst
        return best if best is not None else frozenset(CATALOG)

    def graph(self) -> nx.DiGraph:
        """Authority graph over the discovered paths.

        Paths with the same explanation share nodes. An edge can be cut by an
        intervention acting on the edge's component only if that
        intervention blocked every path through the edge in replay; its
        capacity is that intervention's cost on its own (static cost plus
        collateral)."""
        through: dict[tuple[str, str], list[frozenset[str]]] = {}
        comp_of: dict[tuple[str, str], str] = {}
        for c in self.cexes:
            label = c.explanation[0] if c.explanation else "unexplained"
            nodes = ["REVOKED"] + [f"{comp}[{label}]" for comp in c.components[1:]] + ["EFFECT"]
            comps = c.components[1:] + ["effect"]
            for edge, comp in zip(itertools.pairwise(nodes), comps):
                through.setdefault(edge, []).append(c.blocking)
                comp_of[edge] = comp
        G = nx.DiGraph()
        for (u, v), sets in through.items():
            ok = frozenset.intersection(*sets)
            acting = [i for i in ok if INTERVENTION_COMPONENT[i] == comp_of[(u, v)]]
            cap, iv = min(((self.cost({i}), i) for i in acting), default=(float("inf"), None))
            G.add_edge(u, v, capacity=cap, intervention=iv)
        return G

    def optimize_mincut(self) -> frozenset[str]:
        G = self.graph()
        try:
            _, (S, T) = nx.minimum_cut(G, "REVOKED", "EFFECT")
        except nx.NetworkXUnbounded:
            return frozenset()
        cut = {G[u][v]["intervention"] for u in S for v in G[u] if v in T}
        return frozenset(i for i in cut if i)

    # --- the loop -----------------------------------------------------------
    def variations(self, c: Counterexample) -> list[Scenario]:
        out = []
        for sc in {c.scenario, c.minimal}:
            rt = sc.revocations[0][0]
            for d in (-2, -1, 1, 2):
                if rt + d >= 0:
                    out.append(sc.with_(revocations=((rt + d, "A"),)))
            for nd in (0, 3, 8):
                out.append(sc.with_(notify_delay=nd))
            out.append(sc.with_(dispatch_latency=2))
        return out

    def handle(self, c: Counterexample, belief: Belief) -> frozenset[str]:
        pending = [c]
        rounds = 0
        while pending and rounds < self.max_rounds:
            rounds += 1
            cur = pending.pop(0)
            if not self.violates(cur.scenario, self.K, cur.timing_seed):
                continue
            self.minimize(cur)
            self.blocking_set(cur)
            self.explain(cur, belief)
            self.components(cur)
            self.cexes.append(cur)
            self.K = self.optimize_grouped()
            self.mincut_K = self.optimize_mincut()
            self.mincut_feasible = self.feasible(self.mincut_K)
            self.history.append({"cex": len(self.cexes), "K": sorted(self.K), "cost": self.cost(self.K)})
            for v in self.variations(cur):
                if self.violates(v, self.K, cur.timing_seed):
                    _, sig = self.sb.execute(v, self.K, cur.timing_seed, replay=True)
                    pending.append(Counterexample("variation", v, cur.timing_seed, sig))
                    break
        return self.K
