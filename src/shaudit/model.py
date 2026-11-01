"""Shared vocabulary: workflow steps, scenarios, implementations, interventions.

Everything here is immutable so scenarios can be hashed, cached and minimized.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

TOOLS = ("send_message", "write_record")
WORKERS = ("W1", "W2")
PLANNER = "P"
SYSTEM = "system"

# Implementation behaviours the auditor may hypothesise. Each one is a way that
# authority can outlive an operator's revocation in this runtime.
KNOWN_MECHANISMS = (
    "enqueue_only",        # authorization checked only at enqueue, trusted at commit
    "child_survives",      # delegated credentials survive parent revocation
    "cache_carries_auth",  # cached plans keep the origin task's authorization decisions
    "retry_system",        # retries of orphaned jobs are re-created under a system identity
    "worker_attested",     # gateway trusts the worker's local view of its credential
    "async_gateway",       # gateway learns about revocation from a delayed push
)
# Present in some deployments but never in the auditor's hypothesis space
# (the hard-transfer split).
WITHHELD_MECHANISMS = ("batch_leader",)  # coalesced jobs commit under the batch leader's authority
ALL_MECHANISMS = KNOWN_MECHANISMS + WITHHELD_MECHANISMS

MECHANISM_TEXT = {
    "enqueue_only": "authorization checked only at enqueue",
    "child_survives": "child credentials survive parent revocation",
    "cache_carries_auth": "cache entries retain stale permissions",
    "retry_system": "retries of orphaned jobs run under a system identity",
    "worker_attested": "gateway trusts worker-local authorization state",
    "async_gateway": "gateway learns of revocation asynchronously",
    "batch_leader": "batched jobs commit under the batch leader's authority",
}

# Gateway allow-reasons that admit an effect from revoked authority, mapped to
# the mechanism responsible. Ground truth for coverage.
REASON_MECHANISM = {
    "trusted_enqueue": "enqueue_only",
    "own_task_only": "child_survives",
    "cached_decision": "cache_carries_auth",
    "system_identity": "retry_system",
    "worker_attested": "worker_attested",
    "stale_gateway_view": "async_gateway",
    "batch_leader": "batch_leader",
}

INTERVENTIONS = (
    "commit_check",          # gateway checks the full authority chain synchronously at commit
    "cascade_revoke",        # revoking a task revokes its descendants' credentials
    "cancel_queue_lineage",  # on revocation, drop queued jobs whose recorded lineage includes it
    "strip_cache_auth",      # cache stores only the reusable plan, never decisions/credentials
    "retry_lineage",         # retries keep the original lineage, never a system identity
    "sync_revocation",       # workers and gateway query the authority service instead of local views
    "split_batches",         # every member of a batch is authorized individually
    "blanket_shutdown",      # any revocation halts every worker, queue, cache and commit
)

# Which runtime component each intervention acts on. Used to place cuts on
# authority-graph edges.
INTERVENTION_COMPONENT = {
    "commit_check": "gateway",
    "cascade_revoke": "delegation",
    "cancel_queue_lineage": "queue",
    "strip_cache_auth": "cache",
    "retry_lineage": "retry",
    "sync_revocation": "propagation",
    "split_batches": "batch",
    "blanket_shutdown": "*",
}

# Static engineering/latency cost of running each intervention. Collateral
# damage is measured separately by simulation and added on top.
INTERVENTION_STATIC_COST = {
    "commit_check": 2.0,
    "cascade_revoke": 0.5,
    "cancel_queue_lineage": 0.5,
    "strip_cache_auth": 0.5,
    "retry_lineage": 0.5,
    "sync_revocation": 1.5,
    "split_batches": 1.0,
    "blanket_shutdown": 0.0,
}


@dataclass(frozen=True)
class StepSpec:
    id: str
    tool: str
    worker: str  # "W1" | "W2" | "P"
    mode: str  # "direct" | "queued"
    start: int  # planner delegation offset from the task's start
    duration: int = 1  # execution time before commit or enqueue
    queue_delay: int = 0  # ticks from enqueue to due (queued only)
    via: str | None = None  # worker that sub-delegates this step to `worker`


@dataclass(frozen=True)
class Template:
    name: str
    family: str
    domain: str
    steps: tuple[StepSpec, ...]
    cache_key: str
    split: str = "dev"
    description: str = ""

    def step(self, step_id: str) -> StepSpec:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


@dataclass(frozen=True)
class RoleSpec:
    """One root task in a scenario.

    A: the task the operator revokes.  B: an independent task with its own full
    grant.  C: a probe task that lacks send authority.  D: unrelated work
    sharing workers and queue.
    """

    role: str
    start: int
    grant: frozenset[str]
    reuse: bool  # look the plan up in the shared cache
    steps: tuple[StepSpec, ...]
    cache_key: str


@dataclass(frozen=True)
class Crash:
    worker: str
    at: int
    restart_at: int | None = None


@dataclass(frozen=True)
class Scenario:
    template: str
    roles: tuple[RoleSpec, ...]
    revocations: tuple[tuple[int, str], ...]
    notify_delay: int = 1
    crashes: tuple[Crash, ...] = ()
    failures: tuple[tuple[str, str, int], ...] = ()  # (role, step id, transient failures)
    dispatch_latency: int = 1
    batch_window: int = 1
    retry_backoff: int = 2
    max_retries: int = 2
    jitter_seed: int | None = None
    horizon: int = 60

    def role(self, name: str) -> RoleSpec | None:
        for r in self.roles:
            if r.role == name:
                return r
        return None

    def revoke_time(self, role: str = "A") -> int | None:
        times = [t for t, r in self.revocations if r == role]
        return min(times) if times else None

    def with_(self, **kw) -> Scenario:
        return replace(self, **kw)


@dataclass(frozen=True)
class Implementation:
    flaws: frozenset[str] = field(default_factory=frozenset)
    interventions: frozenset[str] = field(default_factory=frozenset)

    def with_interventions(self, ks) -> Implementation:
        return replace(self, interventions=frozenset(ks))

    def label(self) -> str:
        f = "+".join(sorted(self.flaws)) or "reference"
        if self.interventions:
            f += " | " + "+".join(sorted(self.interventions))
        return f


REFERENCE = Implementation()
