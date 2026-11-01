import pytest

from shaudit import closure
from shaudit.model import KNOWN_MECHANISMS, REASON_MECHANISM, REFERENCE, Implementation, Scenario
from shaudit.observe import PROFILES, attributable, observe, signature
from shaudit.probes import build, candidates
from shaudit.runtime import run
from shaudit.workloads import INVOICE, TEMPLATES, role_a, role_b, role_c, role_d

FIX = {
    "enqueue_only": "commit_check",
    "child_survives": "cascade_revoke",
    "cache_carries_auth": "strip_cache_auth",
    "retry_system": "retry_lineage",
    "worker_attested": "commit_check",
    "async_gateway": "sync_revocation",
    "batch_leader": "split_batches",
}


def test_thirty_templates():
    assert len([t for t in TEMPLATES.values() if t.split in ("dev", "gen", "hard")]) == 30


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_reference_implementation_is_closed(name):
    assert closure.check(name, REFERENCE).closed


@pytest.mark.parametrize("mech", sorted(FIX))
def test_every_mechanism_is_reachable_and_fixed_by_its_intervention(mech):
    reachable = False
    for name in TEMPLATES:
        cl = closure.check(name, Implementation(frozenset({mech})))
        assert {REASON_MECHANISM[t] for t in cl.tags} <= {mech}
        reachable |= not cl.closed
        assert closure.check(name, Implementation(frozenset({mech}), frozenset({FIX[mech]}))).closed, name
    assert reachable


def test_queued_send_survives_enqueue_only_check():
    sc = Scenario(INVOICE.name, (role_a(INVOICE),), ((3, "A"),))
    ok = run(sc, REFERENCE)
    bad = run(sc, Implementation(frozenset({"enqueue_only"})))
    assert not ok.violations
    assert [(e.step, e.tag) for e in bad.violations] == [("send_invoice", "trusted_enqueue")]


def test_knowledge_versus_authority_on_cache_reuse():
    sc = Scenario(INVOICE.name, (role_a(INVOICE), role_b(INVOICE, 4)), ((3, "A"),))
    # Correct cache: B reuses the plan under its own authority.
    assert run(sc, REFERENCE).legit_completion(("B",)) == (2, 2)
    # Flawed cache: B's effects rely on A's revoked approvals.
    flawed = Implementation(frozenset({"cache_carries_auth"}))
    r = run(sc, flawed)
    assert r.legit_completion(("B",)) == (0, 2)
    assert {e.tag for e in r.violations if e.role == "B"} == {"cached_decision"}
    # A commit-time check alone blocks the carried permission but also B's work.
    r = run(sc, Implementation(flawed.flaws, frozenset({"commit_check"})))
    assert not r.violations and r.legit_completion(("B",)) == (0, 2)
    # Stripping authorization from the cache keeps the plan and drops the permission.
    r = run(sc, Implementation(flawed.flaws, frozenset({"strip_cache_auth"})))
    assert not r.violations and r.legit_completion(("B",)) == (2, 2)


def test_send_less_task_cannot_send_unless_cache_carries_approval():
    sc = build(next(p for p in candidates(INVOICE) if p.kind == "revoked_plan_reuse"), INVOICE)
    vis = PROFILES["sparse"]
    clean = signature(observe(run(sc, REFERENCE), vis), vis)
    leaky = signature(observe(run(sc, Implementation(frozenset({"cache_carries_auth"}))), vis), vis)
    assert attributable(clean, sc) == []
    assert attributable(leaky, sc) == [("C", "send_invoice")]


def test_blanket_shutdown_destroys_unrelated_work():
    sc = Scenario(INVOICE.name, (role_a(INVOICE), role_b(INVOICE, 4), role_d(1)), ((3, "A"),))
    r = run(sc, Implementation(frozenset(), frozenset({"blanket_shutdown"})))
    assert not r.violations
    done, expected = r.legit_completion()
    assert expected == 4 and done < 2


def test_runs_are_deterministic():
    p = candidates(TEMPLATES["invoice_mixed_chain"])[4]
    sc = build(p, TEMPLATES["invoice_mixed_chain"], jitter_seed=17)
    impl = Implementation(frozenset({"enqueue_only", "worker_attested"}))
    a, b = run(sc, impl), run(sc, impl)
    assert [(e.t, e.kind, e.role, e.step) for e in a.events] == [(e.t, e.kind, e.role, e.step) for e in b.events]


def test_sparse_visibility_hides_internals():
    sc = Scenario(INVOICE.name, (role_a(INVOICE), role_c(INVOICE)), ((3, "A"),))
    obs = observe(run(sc, REFERENCE), PROFILES["sparse"])
    assert {e.kind for e in obs} <= {"revoke", "effect", "crash", "restart"}


@pytest.mark.parametrize("mech", KNOWN_MECHANISMS)
def test_probe_findings_are_inside_the_closure_bound(mech):
    """Whatever a jittered probe exposes, the bounded grid also exposes."""
    for name, t in TEMPLATES.items():
        impl = Implementation(frozenset({mech}))
        found = False
        for p in candidates(t):
            for seed in range(4):
                r = run(build(p, t, jitter_seed=seed), impl)
                found |= bool(r.violations)
        if found:
            assert not closure.check(name, impl).closed, (name, mech)
