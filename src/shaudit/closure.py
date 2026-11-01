"""Bounded exhaustive reachability.

Enumerates every schedule in a finite grid (revocation tick, notification
delay, dispatch latency, task mix, crash/restart and transient-failure
placements; timing jitter off) and checks ground truth on each run.  A
"closed within bound" result is a statement about this grid of the simulator
model only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import WORKERS, Crash, Implementation, Scenario
from .probes import timeline
from .runtime import behaviour_key, run
from .workloads import TEMPLATES, role_a, role_b, role_c, role_d

NOTIFY = (0, 1, 6)
LATENCY = (1, 2)


def schedule_space(template_name: str) -> list[Scenario]:
    t = TEMPLATES[template_name]
    tl = timeline(t.name)
    end = max(max(x.commit or 0 for x in tl.values()), max(x.dequeue or 0 for x in tl.values())) + 2
    A = role_a(t)
    configs = [
        ((A,), 1),
        ((A, role_c(t, 2)), 1),
        ((A, role_b(t, 3), role_d(1)), 1),
        ((role_a(t, 1), role_b(t, 0)), 3),
    ]
    out = []
    for roles, window in configs:
        shift = roles[0].start
        for rt in range(end + shift + 1):
            perturb: list[tuple] = [((), ())]
            for w in WORKERS:
                perturb.append(((Crash(w, rt, rt + 1),), ()))
                perturb.append(((Crash(w, rt + 1, None),), ()))
            for s in t.steps:
                perturb.append(((), (("A", s.id, 1),)))
                if s.mode == "queued" and s.worker in WORKERS and tl[s.id].enqueue is not None:
                    perturb.append(((Crash(s.worker, tl[s.id].enqueue + shift + 1, None),), (("A", s.id, 1),)))
            for nd in NOTIFY:
                for lat in LATENCY:
                    for crashes, fails in perturb:
                        out.append(Scenario(t.name, roles, ((rt, "A"),), notify_delay=nd, crashes=crashes,
                                            failures=fails, dispatch_latency=lat, batch_window=window,
                                            retry_backoff=3))
    return out


@dataclass
class ClosureResult:
    closed: bool
    schedules: int
    violating: int
    tags: set[str] = field(default_factory=set)
    counterexample: Scenario | None = None
    max_latency: int = 0

    @property
    def violation_rate(self) -> float:
        return self.violating / self.schedules if self.schedules else 0.0


_CACHE: dict[tuple, ClosureResult] = {}


def check(template_name: str, impl: Implementation) -> ClosureResult:
    """Ground-truth closure of `impl` on the template's bounded schedule grid."""
    key = (template_name, behaviour_key(impl))
    if key not in _CACHE:
        _CACHE[key] = _check(template_name, impl)
    return _CACHE[key]


def _check(template_name: str, impl: Implementation) -> ClosureResult:
    res = ClosureResult(True, 0, 0)
    for sc in schedule_space(template_name):
        r = run(sc, impl)
        res.schedules += 1
        v = r.violations
        if v:
            res.violating += 1
            res.closed = False
            res.tags |= r.tags()
            res.max_latency = max(res.max_latency, r.revocation_latency())
            if res.counterexample is None:
                res.counterexample = sc
    return res

