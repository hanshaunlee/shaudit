"""Delta debugging over the conditions of a failing schedule.

A schedule is decomposed into removable atoms (extra tasks, crashes,
restarts, injected failures, timing knobs, workflow steps).  ddmin finds a
1-minimal subset that still reproduces an attributable post-revocation
effect: removing any single remaining atom makes the failure disappear.
"""

from __future__ import annotations

from dataclasses import replace

from .model import Crash, Scenario

DEFAULTS = {"notify_delay": 1, "dispatch_latency": 1, "batch_window": 1, "retry_backoff": 2}


def atoms(sc: Scenario) -> list[tuple]:
    out: list[tuple] = []
    for k, d in DEFAULTS.items():
        v = getattr(sc, k)
        if v != d:
            out.append(("knob", k, v))
    for c in sc.crashes:
        out.append(("crash", c.worker, c.at))
        if c.restart_at is not None:
            out.append(("restart", c.worker, c.restart_at))
    for f in sc.failures:
        out.append(("failure",) + tuple(f))
    for r in sc.roles:
        if r.role != "A":
            out.append(("role", r.role))
    a = sc.role("A")
    for s in a.steps:
        out.append(("step", s.id))
    return out


def rebuild(sc: Scenario, keep: list[tuple]) -> Scenario:
    ks = set(keep)
    knobs = {k: (v if ("knob", k, v) in ks else DEFAULTS[k]) for k, v in
             ((k, getattr(sc, k)) for k in DEFAULTS)}
    crashes = []
    for c in sc.crashes:
        if ("crash", c.worker, c.at) in ks:
            r = c.restart_at if c.restart_at is not None and ("restart", c.worker, c.restart_at) in ks else None
            crashes.append(Crash(c.worker, c.at, r))
    failures = tuple(f for f in sc.failures if ("failure",) + tuple(f) in ks)
    roles = []
    for r in sc.roles:
        if r.role == "A":
            roles.append(replace(r, steps=tuple(s for s in r.steps if ("step", s.id) in ks)))
        elif ("role", r.role) in ks:
            roles.append(r)
    return replace(sc, roles=tuple(roles), crashes=tuple(crashes), failures=failures, **knobs)


def ddmin(items: list, test) -> list:
    """Zeller's ddmin. `test(subset) -> True` when the failure reproduces."""
    memo: dict = {}

    def t(sub):
        key = tuple(sub)
        if key not in memo:
            memo[key] = bool(test(list(sub)))
        return memo[key]

    n = 2
    cur = list(items)
    while len(cur) >= 2:
        size = max(1, len(cur) // n)
        chunks = [cur[i:i + size] for i in range(0, len(cur), size)]
        reduced = False
        for ch in chunks:
            if t(ch):
                cur, n, reduced = ch, 2, True
                break
        if not reduced:
            for i in range(len(chunks)):
                comp = [x for j, c in enumerate(chunks) if j != i for x in c]
                if comp and t(comp):
                    cur, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(cur):
                break
            n = min(len(cur), 2 * n)
    if len(cur) == 1 and t([]):
        return []
    return cur


def describe(atom: tuple) -> str:
    kind = atom[0]
    if kind == "knob":
        return f"{atom[1]}={atom[2]}"
    if kind == "crash":
        return f"crash {atom[1]} at t={atom[2]}"
    if kind == "restart":
        return f"restart {atom[1]} at t={atom[2]}"
    if kind == "failure":
        return f"transient failure x{atom[3]} on {atom[1]}.{atom[2]}"
    if kind == "role":
        return {"B": "independent task B reuses the plan", "C": "send-less task C reuses the plan",
                "D": "unrelated task D shares infrastructure"}.get(atom[1], f"role {atom[1]}")
    if kind == "step":
        return f"step {atom[1]}"
    return str(atom)
