"""Deterministic discrete-event runtime.

A planner per root task, two workers, a shared plan cache, a delayed retrying
job queue, an authority service and a commit gateway in front of two simulated
consequential tools.  Implementation flaws and containment interventions are
flags on :class:`~shaudit.model.Implementation`.

Ground truth is exact: every external effect records the full set of tasks
whose authority it actually relied on, so whether it survived a revocation is
a lookup, not an inference.
"""

from __future__ import annotations

import heapq
import random
from collections import deque
from dataclasses import dataclass, field, replace

from .model import PLANNER, SYSTEM, TOOLS, WORKERS, Implementation, RoleSpec, Scenario, StepSpec

PRIO_REVOKE, PRIO_NOTIFY, PRIO_WORK = 0, 1, 2


@dataclass(slots=True)
class Event:
    seq: int
    t: int
    kind: str
    role: str | None = None
    step: str | None = None
    data: dict = field(default_factory=dict)


@dataclass(slots=True)
class Effect:
    seq: int
    t: int
    role: str
    step: str
    tool: str
    args: dict
    presented: str
    lineage: frozenset[str]
    reason: str
    violation: bool
    tag: str | None  # mechanism-level reason when this is a violation


@dataclass(slots=True)
class Request:
    role: str
    step: str
    tool: str
    args: dict
    presented: str  # task whose authority is presented to the gateway
    lineage_true: frozenset[str]  # every task this action actually relies on
    lineage_record: frozenset[str]  # what the runtime records about its origin
    source: str  # direct | queued | retry | enqueue
    enqueue_ok: bool = False
    attestation: bool | None = None
    decision: str | None = None  # task whose cached decision is being reused
    owner: str | None = None


@dataclass(slots=True)
class Job:
    id: int
    req: Request
    due: int
    attempts: int = 0
    cancelled: bool = False
    dispatched: bool = False


class Authority:
    """Tasks, delegation, grants and revocation. Single source of truth."""

    def __init__(self, cascade: bool):
        self.cascade = cascade
        self.parent: dict[str, str | None] = {SYSTEM: None}
        self.grant: dict[str, frozenset[str]] = {SYSTEM: frozenset(TOOLS)}
        self.revoked: set[str] = set()  # implementation state
        self.op_revoked: set[str] = set()  # what the operator revoked (ground truth)

    def create(self, tid: str, parent: str | None, grant: frozenset[str]) -> None:
        if tid in self.parent:
            return
        self.parent[tid] = parent
        self.grant[tid] = grant if parent is None else grant & self.grant[parent]
        if self.cascade and parent is not None and not self.status_ok(parent, "chain"):
            self.revoked.add(tid)

    def chain(self, tid: str) -> list[str]:
        out = []
        cur: str | None = tid
        while cur is not None:
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def descendants(self, tid: str) -> set[str]:
        out, frontier = set(), [tid]
        while frontier:
            cur = frontier.pop()
            for k, p in self.parent.items():
                if p == cur and k not in out:
                    out.add(k)
                    frontier.append(k)
        return out

    def revoke(self, tid: str) -> set[str]:
        self.op_revoked.add(tid)
        ids = {tid} | (self.descendants(tid) if self.cascade else set())
        self.revoked |= ids
        return ids

    def status_ok(self, tid: str, mode: str, view: set[str] | None = None) -> bool:
        revoked = self.revoked if view is None else view
        tasks = self.chain(tid) if mode == "chain" else [tid]
        return all(t not in revoked for t in tasks)

    def allows(self, tid: str, tool: str) -> bool:
        return tool in self.grant.get(tid, frozenset())

    def truly_revoked(self, lineage) -> bool:
        return any(c in self.op_revoked for t in lineage for c in self.chain(t))


class Worker:
    def __init__(self, sim: Sim, name: str):
        self.sim, self.name = sim, name
        self.alive = True
        self.busy = False
        self.epoch = 0
        self.inbox: deque = deque()
        self.suspended: list = []
        self.current = None
        self.local: dict[str, bool] = {}
        self.snapshot: dict[str, bool] = {}

    def grant_credential(self, task: str) -> None:
        self.snapshot[task] = True
        if self.alive and task not in self.local:
            self.local[task] = True

    def view_ok(self, task: str) -> bool:
        if self.sim.sync:
            return self.sim.authority.status_ok(task, self.sim.mode)
        return self.local.get(task, False)

    def deliver(self, item) -> None:
        self.inbox.append(item)
        if self.alive:
            self.process()

    def process(self) -> None:
        sim = self.sim
        while self.alive and not self.busy and self.inbox:
            item = self.inbox.popleft()
            role, step, task = item[0], item[1], item[2]
            if not self.view_ok(task):
                self.suspended.append(item)
                sim.log("suspend", role, step.id, worker=self.name, task=task)
                continue
            self.busy, self.current = True, item
            dur = 1 if item[4] else sim.duration(role, step)
            sim.log("step_start", role, step.id, worker=self.name, task=task)
            sim.at(sim.now + dur, PRIO_WORK, self.complete, self.epoch)

    def complete(self, epoch: int) -> None:
        if epoch != self.epoch or not self.alive:
            return
        sim = self.sim
        item = self.current
        self.busy, self.current = False, None
        role, step, task, decision, forward = item
        if not self.view_ok(task):
            self.suspended.append(item)
            sim.log("suspend", role, step.id, worker=self.name, task=task)
        elif forward:
            sim.subdelegate(role, step, task, decision)
        else:
            req = sim.make_request(role, step, task, decision, owner=self.name)
            req.attestation = self.view_ok(task)
            if step.mode == "queued":
                sim.enqueue(req, sim.queue_delay(role, step))
            else:
                res = sim.commit(req)
                if res == "fail":
                    sim.schedule_retry(Job(sim.next_job_id(), req, sim.now))
        self.process()

    def notify(self, ids: set[str]) -> None:
        if not self.alive:
            return
        for tid in ids:
            if tid in self.local:
                self.local[tid] = False
        self.sim.log("notify_worker", worker=self.name, revoked=sorted(ids))

    def crash(self) -> None:
        if not self.alive:
            return
        self.alive = False
        self.epoch += 1
        if self.current is not None:
            self.inbox.appendleft(self.current)
        self.busy, self.current = False, None
        self.local = {}
        self.sim.log("crash", worker=self.name)

    def restart(self) -> None:
        if self.alive or self.sim.halted:
            return
        self.alive = True
        self.local = dict(self.snapshot)  # persisted credential state, possibly stale
        for item in reversed(self.suspended):
            self.inbox.appendleft(item)
        self.suspended = []
        self.sim.log("restart", worker=self.name)
        self.process()


class DeterministicPlanner:
    """Plans come from the workflow template; adaptation copies whatever the
    cache entry holds, including any carried authorization decisions."""

    llm_calls = 0

    def plan(self, role: RoleSpec, template: str) -> dict[str, dict]:
        return {s.id: default_args(template, role.role, s) for s in role.steps}

    def adapt(self, entry: dict, role: RoleSpec, template: str) -> tuple[dict[str, dict], dict[str, str]]:
        args = {s.id: default_args(template, role.role, s) for s in role.steps}
        return args, dict(entry.get("decisions", {}))


def default_args(template: str, role: str, step: StepSpec) -> dict:
    return {"workflow": template, "role": role, "ref": f"{role}-{step.id}"}


def behaviour_key(impl: Implementation) -> tuple:
    """Effective runtime behaviour of flaws + interventions. Implementations
    with equal keys are indistinguishable by any experiment."""
    F, I = impl.flaws, impl.interventions
    return (
        "own" if "child_survives" in F else "chain",
        "child_survives" not in F or "cascade_revoke" in I,
        "commit_check" in I,
        "sync_revocation" in I,
        "async_gateway" in F and "sync_revocation" not in I,
        "worker_attested" in F,
        "enqueue_only" in F,
        "cache_carries_auth" in F and "strip_cache_auth" not in I,
        "retry_system" in F and "retry_lineage" not in I,
        "batch_leader" in F,
        "split_batches" in I,
        "cancel_queue_lineage" in I,
        "blanket_shutdown" in I,
    )


class Sim:
    def __init__(self, scenario: Scenario, impl: Implementation, planner=None):
        self.sc = scenario
        F, I = impl.flaws, impl.interventions
        self.impl = impl
        self.mode = "own" if "child_survives" in F else "chain"
        self.cascade = "child_survives" not in F or "cascade_revoke" in I
        self.commit_check = "commit_check" in I
        self.sync = "sync_revocation" in I
        self.gw_async = "async_gateway" in F and not self.sync
        self.attested = "worker_attested" in F
        self.enqueue_only = "enqueue_only" in F
        self.cache_auth = "cache_carries_auth" in F and "strip_cache_auth" not in I
        self.retry_system = "retry_system" in F and "retry_lineage" not in I
        self.batching = "batch_leader" in F
        self.split_batches = "split_batches" in I
        self.cancel_queue = "cancel_queue_lineage" in I
        self.blanket = "blanket_shutdown" in I

        self.planner = planner or DeterministicPlanner()
        self.authority = Authority(self.cascade)
        self.workers = {w: Worker(self, w) for w in WORKERS}
        self.queue: list[Job] = []
        self.cache: dict[str, dict] = {}
        self.gw_known: set[str] = set()
        self.planner_stopped: set[str] = set()
        self.halted = False
        self.now = 0
        self._seq = 0
        self._job = 0
        self._heap: list = []
        self.events: list[Event] = []
        self.effects: list[Effect] = []
        self.attempts: dict[tuple[str, str], int] = {}
        self.failures = {(r, s): n for r, s, n in scenario.failures}
        self.revoke_seq: dict[str, int] = {}
        self._jit = scenario.jitter_seed
        self._args: dict[tuple[str, str], dict] = {}

    # --- engine -----------------------------------------------------------
    def at(self, t: int, prio: int, fn, *args) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (t, prio, self._seq, fn, args))

    def log(self, kind: str, role: str | None = None, step: str | None = None, **data) -> Event:
        self._seq += 1
        ev = Event(self._seq, self.now, kind, role, step, data)
        self.events.append(ev)
        return ev

    def next_job_id(self) -> int:
        self._job += 1
        return self._job

    def _rng(self, *key) -> random.Random:
        return random.Random(":".join(map(str, (self._jit,) + key)))

    def duration(self, role: str, step: StepSpec) -> int:
        if self._jit is None:
            return step.duration
        return step.duration + self._rng("d", role, step.id).choice((0, 0, 1))

    def queue_delay(self, role: str, step: StepSpec) -> int:
        if self._jit is None:
            return step.queue_delay
        return max(1, step.queue_delay + self._rng("q", role, step.id).choice((-1, 0, 0, 1)))

    def run(self) -> RunResult:
        sc = self.sc
        for r in sc.roles:
            self.authority.create(r.role, None, r.grant)
            self.at(r.start, PRIO_WORK, self.role_start, r)
        for t, role in sc.revocations:
            self.at(t, PRIO_REVOKE, self.on_revoke, role)
        for c in sc.crashes:
            self.at(c.at, PRIO_NOTIFY, self.workers[c.worker].crash)
            if c.restart_at is not None:
                self.at(c.restart_at, PRIO_NOTIFY, self.workers[c.worker].restart)
        while self._heap:
            t, _, _, fn, args = heapq.heappop(self._heap)
            if t > sc.horizon:
                break
            self.now = t
            fn(*args)
        return RunResult(sc, self.impl, self.events, self.effects, self.revoke_seq, self.planner_llm_calls())

    def planner_llm_calls(self) -> int:
        return getattr(self.planner, "llm_calls", 0)

    # --- planner ----------------------------------------------------------
    def role_start(self, role: RoleSpec) -> None:
        if role.role in self.planner_stopped or self.halted:
            return
        key = role.cache_key
        carried: dict[str, str] = {}
        if role.reuse and key in self.cache:
            entry = self.cache[key]
            args, carried = self.planner.adapt(entry, role, self.sc.template)
            self.log("cache_hit", role.role, key=key, origin=entry["origin"], carried=sorted(carried))
        else:
            args = self.planner.plan(role, self.sc.template)
            self.log("plan", role.role, key=key)
        own: dict[str, str] = {}
        for step in role.steps:
            if step.id in carried:
                continue
            if self.authority.allows(role.role, step.tool):
                own[step.id] = role.role
            else:
                self.log("not_authorized", role.role, step.id, tool=step.tool)
        if key not in self.cache:
            entry = {"origin": role.role, "steps": [s.id for s in role.steps], "args": args}
            if self.cache_auth:
                entry["decisions"] = dict(own)
            self.cache[key] = entry
            self.log("cache_put", role.role, key=key, with_auth=self.cache_auth)
        for step in role.steps:
            if step.id not in carried and step.id not in own:
                continue
            self._args[(role.role, step.id)] = args.get(step.id, {})
            self.at(role.start + step.start, PRIO_WORK, self.delegate, role, step, carried.get(step.id))

    def delegate(self, role: RoleSpec, step: StepSpec, decision: str | None) -> None:
        if role.role in self.planner_stopped or self.halted:
            self.log("not_delegated", role.role, step.id)
            return
        if step.worker == PLANNER:
            self.at(self.now + self.duration(role.role, step), PRIO_WORK, self.planner_commit, role, step, decision)
            return
        first = step.via or step.worker
        child = f"{role.role}/{first}"
        self.authority.create(child, role.role, role.grant)
        w = self.workers[first]
        w.grant_credential(child)
        self.log("delegate", role.role, step.id, worker=first, task=child)
        w.deliver((role.role, step, child, decision, step.via is not None))

    def subdelegate(self, role: str, step: StepSpec, task: str, decision: str | None) -> None:
        grand = f"{task}/{step.worker}"
        self.authority.create(grand, task, self.authority.grant[task])
        self.log("delegate", role, step.id, worker=step.worker, task=grand)
        target = self.workers[step.worker]
        target.grant_credential(grand)
        self.at(self.now + 1, PRIO_WORK, target.deliver, (role, step, grand, decision, False))

    def planner_commit(self, role: RoleSpec, step: StepSpec, decision: str | None) -> None:
        if role.role in self.planner_stopped or self.halted:
            return
        req = self.make_request(role.role, step, role.role, decision, owner=PLANNER)
        if step.mode == "queued":
            self.enqueue(req, self.queue_delay(role.role, step))
        elif self.commit(req) == "fail":
            self.schedule_retry(Job(self.next_job_id(), req, self.now))

    def make_request(self, role: str, step: StepSpec, task: str, decision: str | None, owner: str) -> Request:
        lineage = frozenset(self.authority.chain(task))
        if decision is not None:
            lineage |= frozenset(self.authority.chain(decision))
        return Request(
            role=role, step=step.id, tool=step.tool, args=self._args.get((role, step.id), {}),
            presented=task, lineage_true=lineage, lineage_record=lineage, source="direct",
            decision=decision, owner=owner,
        )

    # --- revocation -------------------------------------------------------
    def on_revoke(self, role: str) -> None:
        if role not in self.authority.parent:
            return
        ids = self.authority.revoke(role)
        self.planner_stopped.add(role)
        ev = self.log("revoke", role, revoked=sorted(ids))
        self.revoke_seq.setdefault(role, ev.seq)
        if self.blanket:
            self.halted = True
            self.planner_stopped |= {r.role for r in self.sc.roles}
            for j in self.queue:
                j.cancelled = True
            self.cache.clear()
            for w in self.workers.values():
                w.crash()
            self.log("blanket_shutdown")
            return
        if self.cancel_queue:
            for j in self.queue:
                if not j.dispatched and not j.cancelled and role in j.req.lineage_record:
                    j.cancelled = True
                    self.log("job_cancelled", j.req.role, j.req.step, job=j.id)
        self.at(self.now + self.sc.notify_delay, PRIO_NOTIFY, self.deliver_notice, ids)

    def deliver_notice(self, ids: set[str]) -> None:
        self.gw_known |= ids
        self.log("notify_gateway", revoked=sorted(ids))
        for w in self.workers.values():
            w.notify(ids)

    # --- gateway ----------------------------------------------------------
    def decide(self, req: Request) -> tuple[bool, str]:
        a = self.authority
        if self.halted:
            return False, "blanket"
        if self.commit_check:
            basis = [req.presented] + ([req.decision] if req.decision else [])
            ok = all(a.status_ok(t, "chain") and a.allows(t, req.tool) for t in basis)
            return ok, "commit_check"
        if req.source in ("queued", "retry") and self.enqueue_only and req.enqueue_ok:
            return True, "trusted_enqueue"
        if req.decision is not None:
            return True, "cached_decision"
        if req.presented == SYSTEM:
            return True, "system_identity"
        if self.attested and req.attestation is not None:
            return bool(req.attestation), "worker_attested"
        view = self.gw_known if self.gw_async else None
        return a.status_ok(req.presented, self.mode, view) and a.allows(req.presented, req.tool), "checked"

    def commit(self, req: Request, leader: Request | None = None) -> str:
        if leader is None:
            ok, reason = self.decide(req)
        else:
            ok, reason = self.decide(leader)
        if not ok:
            self.log("gateway_deny", req.role, req.step, tool=req.tool, source=req.source,
                     presented=req.presented, reason=reason)
            return "deny"
        key = (req.role, req.step)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.attempts[key] <= self.failures.get(key, 0):
            self.log("tool_fail", req.role, req.step, tool=req.tool, source=req.source)
            return "fail"
        violation = self.authority.truly_revoked(req.lineage_true)
        tag = None
        if violation:
            if leader is not None and leader is not req:
                tag = "batch_leader"
            elif req.presented == SYSTEM:
                tag = "system_identity"
            elif reason in ("checked", "commit_check"):
                stale = self.gw_async and not self.authority.status_ok(req.presented, self.mode)
                tag = "stale_gateway_view" if stale else "own_task_only"
            else:
                tag = reason
        self.log("gateway_allow", req.role, req.step, tool=req.tool, source=req.source,
                 presented=req.presented, reason=reason, tag=tag)
        ev = self.log("effect", req.role, req.step, tool=req.tool, args=req.args)
        self.effects.append(Effect(ev.seq, self.now, req.role, req.step, req.tool, req.args,
                                   req.presented, req.lineage_true, reason, violation, tag))
        return "ok"

    # --- queue ------------------------------------------------------------
    def enqueue(self, req: Request, delay: int) -> None:
        if self.halted:
            return
        ok, _ = self.decide(replace(req, source="enqueue"))
        if not ok:
            self.log("enqueue_denied", req.role, req.step)
            return
        req.source, req.enqueue_ok = "queued", True
        job = Job(self.next_job_id(), req, self.now + delay)
        self.queue.append(job)
        self.log("enqueue", req.role, req.step, job=job.id, due=job.due)
        self.at(job.due, PRIO_WORK, self.on_due, job)

    def on_due(self, job: Job) -> None:
        if job.cancelled or job.dispatched or self.halted:
            return
        if self.batching:
            members = [j for j in self.queue if not j.dispatched and not j.cancelled
                       and j.req.tool == job.req.tool and j.due <= self.now + self.sc.batch_window]
            members.sort(key=lambda j: j.id)
        else:
            members = [job]
        for m in members:
            m.dispatched = True
        self.queue = [j for j in self.queue if not j.dispatched]
        self.log("dequeue", job.req.role, job.req.step, jobs=[m.id for m in members],
                 batch=len(members) > 1)
        self.at(self.now + self.sc.dispatch_latency, PRIO_WORK, self.commit_jobs, members)

    def commit_jobs(self, members: list[Job]) -> None:
        if self.halted:
            return
        if len(members) == 1 or self.split_batches:
            for m in members:
                self.after_attempt(m, self.commit(m.req))
            return
        leader = members[0].req
        ok, reason = self.decide(leader)
        for m in members:
            res = self.commit(m.req, leader=leader) if ok else self._deny_member(m, reason)
            self.after_attempt(m, res)

    def _deny_member(self, m: Job, reason: str) -> str:
        self.log("gateway_deny", m.req.role, m.req.step, tool=m.req.tool, source=m.req.source,
                 presented=m.req.presented, reason=reason)
        return "deny"

    def after_attempt(self, job: Job, res: str) -> None:
        if res == "fail":
            self.schedule_retry(job)

    def schedule_retry(self, job: Job) -> None:
        job.attempts += 1
        if job.attempts > self.sc.max_retries or self.halted:
            self.log("retry_exhausted", job.req.role, job.req.step)
            return
        req = replace(job.req, source="retry")
        owner = self.workers.get(job.req.owner) if job.req.owner else None
        orphaned = owner is not None and not owner.alive
        if self.retry_system and orphaned:
            req = replace(req, presented=SYSTEM, lineage_record=frozenset({SYSTEM}),
                          attestation=None, decision=None)
        retry = Job(self.next_job_id(), req, self.now + self.sc.retry_backoff, attempts=job.attempts)
        self.queue.append(retry)
        self.log("retry_created", req.role, req.step, job=retry.id, identity=req.presented,
                 orphaned=orphaned)
        self.at(retry.due, PRIO_WORK, self.on_due, retry)


@dataclass
class RunResult:
    scenario: Scenario
    impl: Implementation
    events: list[Event]
    effects: list[Effect]
    revoke_seq: dict[str, int]
    llm_calls: int = 0

    @property
    def violations(self) -> list[Effect]:
        return [e for e in self.effects if e.violation]

    def tags(self) -> set[str]:
        return {e.tag for e in self.effects if e.violation and e.tag}

    def legit_completion(self, roles=("B", "D")) -> tuple[int, int]:
        """(legitimately completed, expected) consequential steps of the
        independent roles."""
        expected = done = 0
        for r in self.scenario.roles:
            if r.role not in roles:
                continue
            for s in r.steps:
                if s.tool in r.grant:
                    expected += 1
                    if any(e.role == r.role and e.step == s.id and not e.violation for e in self.effects):
                        done += 1
        return done, expected

    def revocation_latency(self, role: str = "A") -> int:
        """Ticks from revocation to the last effect that relied on the revoked task."""
        t0 = self.scenario.revoke_time(role)
        if t0 is None:
            return 0
        late = [e.t for e in self.effects if e.violation and any(x.split("/")[0] == role for x in e.lineage)]
        return max(late) - t0 if late else 0


def run(scenario: Scenario, impl: Implementation, planner=None) -> RunResult:
    return Sim(scenario, impl, planner).run()
