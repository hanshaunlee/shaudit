import itertools
import random

from shaudit import closure
from shaudit.auditor import AuditConfig, audit
from shaudit.belief import HYPOTHESES, Belief, LikelihoodModel, lik_given_sample
from shaudit.minimize import ddmin
from shaudit.model import Implementation
from shaudit.observe import PROFILES
from shaudit.probes import build, candidates
from shaudit.workloads import INVOICE, TEMPLATES

TUNED = {"alpha": 2.0, "beta": 0.02}


def test_drop_likelihood_is_a_distribution():
    items = {"E:A:x": None, "D:A:y": 1, "J:A:z": 3}
    droppable = ["D:A:y", "J:A:z"]
    total = 0.0
    for k in range(3):
        for sub in itertools.combinations(droppable, k):
            total += lik_given_sample(tuple(sorted(("E:A:x",) + sub)), items, 0.3)
    assert abs(total - 1.0) < 1e-9
    assert lik_given_sample(("E:A:q",), items, 0.3) == 0.0


def test_belief_concentrates_on_the_true_mechanism():
    t = TEMPLATES["invoice_mixed_chain"]
    truth = Implementation(frozenset({"worker_attested"}))
    model = LikelihoodModel(t, PROFILES["full"], n_samples=8)
    b = Belief(kinds={p.kind for p in candidates(t)})
    from shaudit.observe import observe, signature
    from shaudit.runtime import run

    for i, p in enumerate(candidates(t)):
        sig = signature(observe(run(build(p, t, jitter_seed=900 + i), truth), PROFILES["full"]), PROFILES["full"])
        b.update(model, p, frozenset(), sig)
    best = int(b.p.argmax())
    assert HYPOTHESES[best] == frozenset({"worker_attested"})
    assert b.marginals()["worker_attested"] > 0.9


def test_ddmin_is_one_minimal():
    rng = random.Random(3)
    items = list(range(20))
    need = set(rng.sample(items, 3))

    def test(sub):
        return need <= set(sub)

    out = ddmin(items, test)
    assert set(out) == need


def test_invoice_audit_finds_and_contains_the_queued_send():
    rep = audit(INVOICE, Implementation(frozenset({"enqueue_only"})), AuditConfig(**TUNED))
    assert rep.first_hit is not None and rep.first_hit <= 3
    assert rep.counterexamples and "enqueue_only" in rep.counterexamples[0].explanation
    full = Implementation(frozenset({"enqueue_only"}), rep.K)
    assert closure.check(INVOICE.name, full).closed
    assert "blanket_shutdown" not in rep.K


def test_cache_flaw_containment_preserves_independent_reuse():
    from shaudit.experiments import evaluate_containment

    impl = Implementation(frozenset({"enqueue_only", "cache_carries_auth"}))
    rep = audit(INVOICE, impl, AuditConfig(**TUNED))
    ev = evaluate_containment(INVOICE.name, impl, rep.K)
    assert ev["closed"] and ev["legit_completion"] == 1.0
    assert "strip_cache_auth" in rep.K


def test_reference_deployment_yields_no_counterexample():
    rep = audit(TEMPLATES["invoice_queued_pair"], Implementation(), AuditConfig(**TUNED))
    assert rep.first_hit is None and not rep.K
    assert rep.claim in ("no_counterexample_within_budget", "residual_risk")


def test_withheld_mechanism_is_reported_as_insufficient_evidence():
    t = TEMPLATES["payroll_fanout_queue"]
    rep = audit(t, Implementation(frozenset({"batch_leader"})), AuditConfig(**TUNED))
    assert rep.counterexamples and all(c.unexplained for c in rep.counterexamples)
    assert rep.claim == "insufficient_evidence"
    assert "split_batches" in rep.K


def test_whitebox_claim_matches_ground_truth():
    impl = Implementation(frozenset({"retry_system"}))
    rep = audit(TEMPLATES["invoice_queued_send"], impl, AuditConfig(whitebox=True, **TUNED))
    cl = closure.check("invoice_queued_send", Implementation(impl.flaws, rep.K))
    assert rep.claim == ("closed_within_bound" if cl.closed else "counterexample_within_bound")
