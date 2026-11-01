"""Thirty workflow templates, role builders and the dev / generalization /
hard-transfer splits."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

from .model import (
    KNOWN_MECHANISMS,
    PLANNER,
    TOOLS,
    Implementation,
    RoleSpec,
    StepSpec,
    Template,
)

SEND, WRITE = "send_message", "write_record"

DOMAINS = (
    ("invoice", "Send an invoice to the customer and record it in the ledger."),
    ("refund", "Notify the customer of a refund and write the refund record."),
    ("payroll", "Write the payroll entry and send the payslip notice."),
    ("onboarding", "Create the account record and send the welcome message."),
    ("incident", "Page the on-call channel and write the incident record."),
    ("renewal", "Send the renewal reminder and update the contract record."),
    ("vendor", "Write the vendor approval and notify procurement."),
)


def _f1(i):  # two direct steps on separate workers
    return (
        StepSpec("record", WRITE, "W1", "direct", start=1, duration=2 + i % 2),
        StepSpec("notify", SEND, "W2", "direct", start=2, duration=2),
    )


def _f2(i):  # direct write, delayed queued send
    return (
        StepSpec("record", WRITE, "W2", "direct", start=1, duration=1),
        StepSpec("send", SEND, "W1", "queued", start=1, duration=1, queue_delay=4 + i % 3),
    )


def _f3(i):  # two queued steps
    return (
        StepSpec("record", WRITE, "W1", "queued", start=1, duration=1, queue_delay=3 + i % 2),
        StepSpec("send", SEND, "W2", "queued", start=2, duration=1, queue_delay=5),
    )


def _f4(i):  # direct send, queued write, late direct send on the same worker
    return (
        StepSpec("notify", SEND, "W1", "direct", start=1, duration=2),
        StepSpec("record", WRITE, "W2", "queued", start=2, duration=1, queue_delay=4),
        StepSpec("confirm", SEND, "W2", "direct", start=4 + i % 2, duration=1),
    )


def _f5(i):  # planner acts directly as well as through workers
    return (
        StepSpec("record", WRITE, "W1", "queued", start=1, duration=1, queue_delay=4),
        StepSpec("announce", SEND, PLANNER, "direct", start=2, duration=1),
        StepSpec("notify", SEND, "W2", "direct", start=3, duration=2 + i % 2),
    )


def _f6(i):  # sub-delegation: W1 delegates to W2 (grandchild credential)
    return (
        StepSpec("notify", SEND, "W2", "direct", start=1, duration=2, via="W1"),
        StepSpec("record", WRITE, "W2", "queued", start=2, duration=1, queue_delay=4 + i % 2, via="W1"),
    )


def _f7(i):  # fan-out of queued sends that a batching dispatcher may coalesce
    return (
        StepSpec("send", SEND, "W1", "queued", start=1, duration=1, queue_delay=4 + i % 2),
        StepSpec("record", WRITE, "W2", "direct", start=1, duration=1),
    )


FAMILIES = {
    "direct_pair": (_f1, "dev"),
    "queued_send": (_f2, "dev"),
    "queued_pair": (_f3, "dev"),
    "mixed_chain": (_f4, "dev"),
    "planner_direct": (_f5, "gen"),
    "subdelegate": (_f6, "gen"),
    "fanout_queue": (_f7, "hard"),
}


def build_templates() -> dict[str, Template]:
    out: dict[str, Template] = {}
    plan = [
        ("direct_pair", 4), ("queued_send", 4), ("queued_pair", 4), ("mixed_chain", 4),
        ("planner_direct", 4), ("subdelegate", 4), ("fanout_queue", 6),
    ]
    for fam, n in plan:
        fn, split = FAMILIES[fam]
        for i in range(n):
            dom, desc = DOMAINS[i % len(DOMAINS)] if fam != "fanout_queue" else DOMAINS[(i + 2) % len(DOMAINS)]
            name = f"{dom}_{fam}" if i < len(DOMAINS) else f"{dom}_{fam}_{i}"
            if name in out:
                name = f"{name}_{i}"
            out[name] = Template(name, fam, dom, fn(i), cache_key=f"{dom}:{fam}", split=split, description=desc)
    return out


TEMPLATES = build_templates()

# The concrete demonstration: two tasks share a cached invoice plan.
INVOICE = Template(
    "invoice_demo", "queued_send", "invoice",
    (
        StepSpec("ledger", WRITE, "W2", "direct", start=1, duration=1),
        StepSpec("send_invoice", SEND, "W1", "queued", start=1, duration=1, queue_delay=5),
    ),
    cache_key="invoice:standard",
    split="demo",
    description="Send an invoice to the customer and record it in the ledger.",
)
TEMPLATES[INVOICE.name] = INVOICE

UNRELATED_STEPS = (
    StepSpec("d_record", WRITE, "W2", "direct", start=1, duration=1),
    StepSpec("d_send", SEND, "W1", "queued", start=2, duration=1, queue_delay=3),
)


def role_a(t: Template, start: int = 0) -> RoleSpec:
    return RoleSpec("A", start, frozenset(TOOLS), False, t.steps, t.cache_key)


def role_b(t: Template, start: int = 2) -> RoleSpec:
    """Independent task with its own full authority, reusing the cached plan."""
    return RoleSpec("B", start, frozenset(TOOLS), True, t.steps, t.cache_key)


def role_c(t: Template, start: int = 2) -> RoleSpec:
    """Probe task without send authority, reusing the cached plan."""
    return RoleSpec("C", start, frozenset({WRITE}), True, t.steps, t.cache_key)


def role_d(start: int = 1) -> RoleSpec:
    """Unrelated work sharing workers and the queue."""
    return RoleSpec("D", start, frozenset(TOOLS), False, UNRELATED_STEPS, "unrelated")


# --- deployments (true implementations) -------------------------------------

@dataclass(frozen=True)
class Instance:
    template: str
    impl: Implementation
    split: str
    seed: int


def _combos(k):
    return [frozenset(c) for c in itertools.combinations(KNOWN_MECHANISMS, k)]


def build_instances(seed: int = 0) -> list[Instance]:
    rng = random.Random(seed)
    singles, pairs, triples = _combos(1), _combos(2), _combos(3)
    out: list[Instance] = []
    n = 0
    for name, t in TEMPLATES.items():
        if t.split == "dev":
            impls = [frozenset()] + rng.sample(singles, 3) + rng.sample(pairs, 2)
        elif t.split == "gen":
            impls = rng.sample(pairs, 2) + rng.sample(triples, 2)
        elif t.split == "hard":
            known = rng.choice(singles)
            impls = [frozenset({"batch_leader"}), frozenset({"batch_leader"}) | known, frozenset()]
        else:
            continue
        for f in impls:
            out.append(Instance(name, Implementation(f), t.split, n))
            n += 1
    return out
