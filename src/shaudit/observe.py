"""What the auditor gets to see, and the discrete outcome it reasons over.

The auditor owns the test harness, so it always sees its own revocations and
every effect at the simulated tools.  Internal telemetry is partial: some
kinds of events are never exported, sensitive fields are stripped, and
exported events can be lost.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .model import Scenario
from .runtime import Event, RunResult

ALWAYS = frozenset({"revoke", "effect"})


@dataclass(frozen=True)
class Visibility:
    name: str
    kinds: frozenset[str] | None  # None means every kind
    hidden_fields: frozenset[str]
    drop: float  # probability an exported internal event is lost
    reasons: bool  # gateway allow reasons and ground-truth tags exported


PROFILES = {
    "full": Visibility("full", None, frozenset(), 0.0, True),
    "standard": Visibility(
        "standard",
        frozenset({"gateway_deny", "gateway_allow", "crash", "restart", "enqueue", "dequeue",
                   "cache_hit", "retry_created", "job_cancelled"}),
        frozenset({"presented", "reason", "tag", "identity", "orphaned", "carried", "revoked", "task"}),
        0.3,
        False,
    ),
    "sparse": Visibility("sparse", frozenset({"crash", "restart"}), frozenset(), 0.0, False),
}


def observe(res: RunResult, vis: Visibility, seed: int = 0) -> list[Event]:
    rng = random.Random(f"drop:{seed}")
    out = []
    for e in res.events:
        if e.kind not in ALWAYS:
            if vis.kinds is not None and e.kind not in vis.kinds:
                continue
            if vis.drop and rng.random() < vis.drop:
                continue
        data = {k: v for k, v in e.data.items() if k not in vis.hidden_fields}
        out.append(Event(e.seq, e.t, e.kind, e.role, e.step, data))
    return out


def signature(obs: list[Event], vis: Visibility) -> tuple[str, ...]:
    """Discrete outcome of one experiment: what happened after revocation."""
    rev = min((e.seq for e in obs if e.kind == "revoke" and e.role == "A"), default=None)
    items: set[str] = set()
    for e in obs:
        if rev is None or e.seq < rev:
            continue
        if e.kind == "effect":
            items.add(f"E:{e.role}:{e.step}")
        elif e.kind == "gateway_deny":
            items.add(f"D:{e.role}:{e.step}")
        elif e.kind == "job_cancelled":
            items.add(f"J:{e.role}:{e.step}")
        elif e.kind == "gateway_allow" and vis.reasons and e.data.get("tag"):
            items.add(f"R:{e.role}:{e.step}:{e.data['tag']}")
    return tuple(sorted(items))


def attributable(sig: tuple[str, ...], scenario: Scenario) -> list[tuple[str, str]]:
    """Effects the auditor can attribute to revoked authority without seeing
    lineage: anything task A did after its revocation, and anything probe
    task C did that its own grant does not allow."""
    out = []
    c = scenario.role("C")
    for item in sig:
        if not item.startswith("E:"):
            continue
        _, role, step = item.split(":", 2)
        if role == "A":
            out.append((role, step))
        elif role == "C" and c is not None:
            tool = next(s.tool for s in c.steps if s.id == step)
            if tool not in c.grant:
                out.append((role, step))
    return out


def observed_tags(res: RunResult, sig: tuple[str, ...]) -> set[str]:
    """Ground-truth mechanism tags behind the attributable effects (for
    scoring coverage; never shown to a partially observing auditor)."""
    hits = set(attributable(sig, res.scenario))
    return {e.tag for e in res.effects if e.violation and e.tag and (e.role, e.step) in hits}
