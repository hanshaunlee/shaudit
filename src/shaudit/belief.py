"""Belief over interpretable implementation hypotheses.

H ranges over every subset of the six known mechanisms (64 hypotheses) plus
catch-all hypotheses for behaviour none of them explains.  The catch-all is
split by experiment type: OTHER:k says "the deployment behaves like some known
hypothesis, except that experiments of kind k can trigger something unmodeled".
Running kind k without surprise lowers OTHER:k only, so untested conditions keep
their share of doubt and remain worth testing.  Likelihoods come
from simulating each probe under each hypothesis (Monte Carlo over timing
jitter) and treating telemetry loss exactly: each exported internal event is
dropped independently, so the probability of an observed outcome given a
simulated run is a product over its outcome items.
"""

from __future__ import annotations

import itertools
import math
import random

import numpy as np

from .model import KNOWN_MECHANISMS, Implementation, Scenario, Template
from .observe import ALWAYS, Visibility, attributable
from .probes import PROBE_KINDS, Probe, build
from .runtime import RunResult, behaviour_key, run

HYPOTHESES: list[frozenset[str]] = [
    frozenset(c) for k in range(len(KNOWN_MECHANISMS) + 1)
    for c in itertools.combinations(KNOWN_MECHANISMS, k)
]
N_H = len(HYPOTHESES)
N_OTHER = len(PROBE_KINDS)
N_ALL = N_H + N_OTHER  # OTHER:k lives at index N_H + PROBE_KINDS.index(k)

ETA = 0.02  # model-misspecification smoothing on known hypotheses
EPS = 0.05  # pseudo-probability of an arbitrary outcome
NOVEL = 0.7  # chance that OTHER:k shows unmodeled behaviour on a kind-k experiment


def outcome_items(res: RunResult, vis: Visibility) -> dict[str, int | None]:
    """Outcome items before telemetry loss, with the number of droppable
    events that could reveal each one (None: always visible)."""
    rev = res.revoke_seq.get("A")
    items: dict[str, int | None] = {}
    if rev is None:
        return items
    for e in res.events:
        if e.seq < rev:
            continue
        if e.kind == "effect":
            items[f"E:{e.role}:{e.step}"] = None
            continue
        if vis.kinds is not None and e.kind not in vis.kinds:
            continue
        if e.kind == "gateway_deny":
            key = f"D:{e.role}:{e.step}"
        elif e.kind == "job_cancelled":
            key = f"J:{e.role}:{e.step}"
        elif e.kind == "gateway_allow" and vis.reasons and e.data.get("tag"):
            key = f"R:{e.role}:{e.step}:{e.data['tag']}"
        else:
            continue
        if e.kind in ALWAYS or not vis.drop:
            items[key] = None
        elif key not in items:
            items[key] = 1
        elif items[key] is not None:
            items[key] += 1
    return items


def lik_given_sample(sig: tuple[str, ...], items: dict[str, int | None], drop: float) -> float:
    s = set(sig)
    if not s.issubset(items):
        return 0.0
    p = 1.0
    for it, k in items.items():
        vis_p = 1.0 if k is None else 1.0 - drop ** k
        p *= vis_p if it in s else (1.0 - vis_p)
        if p == 0.0:
            return 0.0
    return p


def draw_sig(items: dict[str, int | None], drop: float, rng: random.Random) -> tuple[str, ...]:
    out = []
    for it, k in items.items():
        if k is None or rng.random() < 1.0 - drop ** k:
            out.append(it)
    return tuple(sorted(out))


class LikelihoodModel:
    """Simulation-backed observation model for one workflow template."""

    def __init__(self, template: Template, vis: Visibility, n_samples: int = 16, seed_base: int = 50_000):
        self.t, self.vis, self.n = template, vis, n_samples
        self.seed_base = seed_base
        self._samples: dict = {}
        self._tables: dict = {}
        self.sims = 0

    def samples(self, probe: Probe, flaws: frozenset[str], K: frozenset[str]) -> list[dict]:
        impl = Implementation(flaws, K)
        key = (probe, behaviour_key(impl))
        got = self._samples.get(key)
        if got is None:
            got = []
            for s in range(self.n):
                sc = build(probe, self.t, jitter_seed=self.seed_base + s)
                got.append(outcome_items(run(sc, impl), self.vis))
            self.sims += self.n
            self._samples[key] = got
        return got

    def scenario_samples(self, sc: Scenario, flaws: frozenset[str], K: frozenset[str]) -> list[dict]:
        impl = Implementation(flaws, K)
        out = []
        for s in range(self.n):
            out.append(outcome_items(run(sc.with_(jitter_seed=self.seed_base + s), impl), self.vis))
        self.sims += self.n
        return out

    def lik(self, probe: Probe, flaws, K, sig) -> float:
        smp = self.samples(probe, flaws, K)
        return sum(lik_given_sample(sig, it, self.vis.drop) for it in smp) / len(smp)

    def outcome_table(self, probe: Probe, K: frozenset[str]):
        """Outcomes reachable under any known hypothesis, and the raw
        likelihood matrix P[h, o] with a final column for anything else."""
        key = (probe, frozenset(K))
        if key in self._tables:
            return self._tables[key]
        rng = random.Random(f"o:{probe}:{sorted(K)}")
        outcomes: dict[tuple, None] = {}
        by_bk: dict = {}
        for h in range(N_H):
            bk = behaviour_key(Implementation(HYPOTHESES[h], K))
            if bk in by_bk:
                continue
            smp = self.samples(probe, HYPOTHESES[h], K)
            by_bk[bk] = smp
            for it in smp:
                for _ in range(2 if self.vis.drop else 1):
                    outcomes[draw_sig(it, self.vis.drop, rng)] = None
        O = list(outcomes)
        rows = {}
        for bk, smp in by_bk.items():
            rows[bk] = np.array([sum(lik_given_sample(o, it, self.vis.drop) for it in smp) / len(smp) for o in O])
        P = np.zeros((N_H, len(O) + 1))
        for h in range(N_H):
            row = rows[behaviour_key(Implementation(HYPOTHESES[h], K))]
            P[h, :-1] = row
            P[h, -1] = max(0.0, 1.0 - row.sum())
        self._tables[key] = (O, P)
        return O, P


class Belief:
    def __init__(self, prior_p: float = 0.3, other_prior: float = 0.05, kinds=PROBE_KINDS):
        """`kinds`: experiment types applicable to the workflow; OTHER mass is
        spread over those only."""
        w = np.array([
            math.prod(prior_p if m in h else 1 - prior_p for m in KNOWN_MECHANISMS) for h in HYPOTHESES
        ])
        w = w / w.sum() * (1 - other_prior)
        kinds = set(kinds)
        o = np.array([1.0 if k in kinds else 0.0 for k in PROBE_KINDS])
        self.p = np.append(w, other_prior * o / o.sum())

    def copy(self) -> Belief:
        b = Belief.__new__(Belief)
        b.p = self.p.copy()
        return b

    def support(self, eps: float = 1e-6) -> list[int]:
        return [i for i in range(N_H) if self.p[i] > eps]

    def marginals(self) -> dict[str, float]:
        return {m: float(sum(self.p[i] for i, h in enumerate(HYPOTHESES) if m in h)) for m in KNOWN_MECHANISMS}

    @property
    def other(self) -> float:
        return float(self.p[N_H:].sum())

    def top(self, k: int = 3) -> list[tuple[str, float]]:
        idx = np.argsort(-self.p)[:k]
        return [(f"OTHER:{PROBE_KINDS[i - N_H]}" if i >= N_H else ("+".join(sorted(HYPOTHESES[i])) or "reference"),
                 float(self.p[i])) for i in idx]

    def entropy(self) -> float:
        p = self.p[self.p > 0]
        return float(-(p * np.log2(p)).sum())

    def update(self, model: LikelihoodModel, probe: Probe, K, sig) -> None:
        lk = np.zeros(N_ALL)
        for i in self.support():
            lk[i] = (1 - ETA) * model.lik(probe, HYPOTHESES[i], K, sig) + ETA * EPS
        known_mean = float((self.p[:N_H] * lk[:N_H]).sum() / max(self.p[:N_H].sum(), 1e-12))
        lk[N_H:] = known_mean
        lk[N_H + PROBE_KINDS.index(probe.kind)] = (1 - NOVEL) * known_mean + NOVEL * EPS
        post = self.p * lk
        self.p = post / post.sum()


def predictive(belief: Belief, model: LikelihoodModel, probe: Probe, K) -> tuple[list, np.ndarray]:
    O, Pk = model.outcome_table(probe, K)
    Pk = (1 - ETA) * Pk + ETA / Pk.shape[1]
    known = belief.p[:N_H]
    mix = (known[:, None] * Pk).sum(0) / max(known.sum(), 1e-12)
    P = np.vstack([Pk, np.tile(mix, (N_OTHER, 1))])
    j = N_H + PROBE_KINDS.index(probe.kind)
    P[j] = (1 - NOVEL) * mix
    P[j, -1] += NOVEL
    return O, P


def probe_scores(belief: Belief, model: LikelihoodModel, probe: Probe, K, scenario: Scenario) -> dict:
    """Expected information gain (bits) and probability that the probe
    exposes an attributable post-revocation effect."""
    O, P = predictive(belief, model, probe, K)
    joint = belief.p[:, None] * P
    po = joint.sum(0)
    h0 = belief.entropy()
    eh = 0.0
    for j in range(P.shape[1]):
        if po[j] <= 0:
            continue
        q = joint[:, j] / po[j]
        q = q[q > 0]
        eh += po[j] * float(-(q * np.log2(q)).sum())
    # Unmodeled behaviour is what the audit is hunting for, so the novel
    # outcome counts as a potential violation under OTHER.
    viol = np.array([1.0 if attributable(o, scenario) else 0.0 for o in O] + [0.0])
    pv = float((po * viol).sum()) + float(belief.p[N_H + PROBE_KINDS.index(probe.kind)]) * NOVEL
    return {"eig": max(0.0, h0 - eh), "p_violation": pv, "outcomes": O, "P": P, "po": po}


def residual_risk(belief: Belief, model: LikelihoodModel, probes: list[Probe], K, threshold: float = 0.01) -> float:
    """Posterior mass on hypotheses under which some known probe still
    exposes revoked authority despite the interventions K, plus all OTHER mass."""
    risky = 0.0
    scen = {p: build(p, model.t) for p in probes}
    verdict: dict = {}
    for i in belief.support():
        bk = behaviour_key(Implementation(HYPOTHESES[i], K))
        if bk not in verdict:
            hit = False
            for p in probes:
                smp = model.samples(p, HYPOTHESES[i], K)
                frac = sum(1 for it in smp if attributable(tuple(k for k in it if k.startswith("E:")), scen[p])) / len(smp)
                if frac > threshold:
                    hit = True
                    break
            verdict[bk] = hit
        if verdict[bk]:
            risky += belief.p[i]
    return float(risky + belief.p[N_H:].sum())

