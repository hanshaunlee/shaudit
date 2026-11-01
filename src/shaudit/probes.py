"""Controlled experiments the auditor can run against a workflow.

Each probe is a recipe that turns a workflow template into a concrete event
schedule (a :class:`Scenario`), timed against the template's undisturbed
timeline so that the perturbation lands where it is meant to.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from .model import PLANNER, REFERENCE, Crash, Scenario, Template
from .runtime import run
from .workloads import TEMPLATES, role_a, role_b, role_c

PROBE_KINDS = (
    "revoke_early",          # revoke before anything is delegated (control)
    "revoke_before_commit",  # revoke the parent at the tick a worker commits
    "delay_notification",    # revoke while a step runs; revocation notices are slow
    "restart_stale",         # revoke, then crash and restart the worker holding the step
    "revoke_after_enqueue",  # revoke while a job waits in the queue
    "revoke_inflight",       # revoke between dequeue and commit
    "retry_after_stop",      # orphan a queued job, fail its first attempt, then revoke
    "revoked_plan_reuse",    # a task without send authority reuses the revoked task's plan
    "independent_reuse",     # an independently authorized task reuses the same plan
    "mixed_batch",           # A's and B's queued jobs are due together; revoke A in flight
)

CHECKLIST_ORDER = (
    "revoke_early", "revoke_before_commit", "revoke_after_enqueue", "delay_notification",
    "restart_stale", "retry_after_stop", "revoked_plan_reuse", "independent_reuse",
    "revoke_inflight", "mixed_batch",
)


@dataclass(frozen=True)
class Probe:
    kind: str
    step: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}({self.step})" if self.step else self.kind


@dataclass(frozen=True)
class StepTimes:
    worker: str
    start: int
    commit: int | None
    enqueue: int | None
    dequeue: int | None


@cache
def timeline(template_name: str) -> dict[str, StepTimes]:
    t = TEMPLATES[template_name]
    res = run(Scenario(t.name, (role_a(t),), ()), REFERENCE)
    out = {}
    for s in t.steps:
        evs = [e for e in res.events if e.role == "A" and e.step == s.id]

        def first(kind, evs=evs):
            ts = [e.t for e in evs if e.kind == kind]
            return ts[-1] if kind == "step_start" and ts else (ts[0] if ts else None)

        start = first("step_start")
        out[s.id] = StepTimes(
            s.worker, start if start is not None else s.start, first("effect"),
            first("enqueue"), first("dequeue"),
        )
    return out


def candidates(template: Template) -> list[Probe]:
    out = [Probe("revoke_early")]
    direct = [s for s in template.steps if s.mode == "direct"]
    queued = [s for s in template.steps if s.mode == "queued"]
    for s in direct:
        out.append(Probe("revoke_before_commit", s.id))
        out.append(Probe("delay_notification", s.id))
        if s.worker != PLANNER:
            out.append(Probe("restart_stale", s.id))
            out.append(Probe("retry_after_stop", s.id))
    for s in queued:
        out.append(Probe("revoke_after_enqueue", s.id))
        out.append(Probe("revoke_inflight", s.id))
        if s.worker != PLANNER:
            out.append(Probe("retry_after_stop", s.id))
    out.append(Probe("revoked_plan_reuse"))
    out.append(Probe("independent_reuse"))
    if queued:
        out.append(Probe("mixed_batch", queued[0].id))
    return out


def build(probe: Probe, template: Template, jitter_seed: int | None = None) -> Scenario:
    tl = timeline(template.name)
    A = role_a(template)
    base = {"template": template.name, "jitter_seed": jitter_seed}
    k = probe.kind
    st = tl.get(probe.step) if probe.step else None
    if k == "revoke_early":
        return Scenario(roles=(A,), revocations=((1, "A"),), **base)
    if k == "revoke_before_commit":
        return Scenario(roles=(A,), revocations=((st.commit, "A"),), notify_delay=1, **base)
    if k == "delay_notification":
        return Scenario(roles=(A,), revocations=((st.start + 1, "A"),), notify_delay=8, **base)
    if k == "restart_stale":
        return Scenario(roles=(A,), revocations=((st.start + 1, "A"),), notify_delay=0,
                        crashes=(Crash(st.worker, st.commit, st.commit + 1),), **base)
    if k == "revoke_after_enqueue":
        t = st.enqueue + 1 if st.enqueue + 1 < st.dequeue else st.enqueue
        return Scenario(roles=(A,), revocations=((t, "A"),), **base)
    if k == "revoke_inflight":
        return Scenario(roles=(A,), revocations=((st.dequeue + 1, "A"),), dispatch_latency=2, **base)
    if k == "retry_after_stop" and template.step(probe.step).mode == "direct":
        # The step fails at commit and is handed to the retry queue; the
        # worker then stops and the parent is revoked before the retry.
        return Scenario(roles=(A,), revocations=((st.commit + 1, "A"),),
                        crashes=(Crash(st.worker, st.commit + 1, None),),
                        failures=(("A", probe.step, 2),), retry_backoff=3, **base)
    if k == "retry_after_stop":
        # Two transient failures keep the job retrying across the revocation
        # even when timing jitter moves the first attempt.
        attempt = st.dequeue + 1
        return Scenario(roles=(A,), revocations=((attempt + 2, "A"),),
                        crashes=(Crash(st.worker, st.enqueue + 1, None),),
                        failures=(("A", probe.step, 2),), retry_backoff=3, **base)
    if k == "revoked_plan_reuse":
        return Scenario(roles=(A, role_c(template, 2)), revocations=((1, "A"),), **base)
    if k == "independent_reuse":
        return Scenario(roles=(A, role_b(template, 2)), revocations=((1, "A"),), **base)
    if k == "mixed_batch":
        # B starts first so its job leads any batch; A's identical job is due
        # about a tick later, inside the batching window despite jitter.
        A2 = role_a(template, start=1)
        B = role_b(template, start=0)
        return Scenario(roles=(A2, B), revocations=((st.dequeue + 1, "A"),),
                        dispatch_latency=2, batch_window=3, **base)
    raise ValueError(k)


def cost(probe: Probe, scenario: Scenario) -> float:
    """Execution cost: one run, plus extra planning tasks, crashes and restarts."""
    c = 1.0 + 0.5 * (len(scenario.roles) - 1)
    for cr in scenario.crashes:
        c += 0.5 + (0.25 if cr.restart_at is not None else 0.0)
    return c
