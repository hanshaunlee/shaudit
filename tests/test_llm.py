import json
from types import SimpleNamespace

from shaudit.llm import MODEL, LLMPlanner
from shaudit.model import Implementation
from shaudit.probes import Probe, build
from shaudit.runtime import run
from shaudit.workloads import INVOICE


class FakeMessages:
    """Stands in for client.beta.messages; echoes the cached plan back,
    optionally keeping its approval refs."""

    def __init__(self, keep_approvals: bool, refuse: bool = False):
        self.keep, self.refuse = keep_approvals, refuse
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if self.refuse:
            return SimpleNamespace(stop_reason="refusal", content=[])
        prompt = kw["messages"][0]["content"]
        if "Cached plan:" in prompt:
            cached = json.loads(prompt.split("Cached plan:\n", 1)[1].split("\n\nReturn", 1)[0])
            steps = [{"id": s["id"], "fields": [{"key": "to", "value": "new-customer"}],
                      "approval_ref": s["approval_ref"] if self.keep else None} for s in cached["steps"]]
        else:
            steps = [{"id": sid, "fields": [{"key": "to", "value": "customer"}], "approval_ref": None}
                     for sid in ("ledger", "send_invoice")]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"steps": steps}))])


def client(**kw):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(**kw)))


SC = build(Probe("revoked_plan_reuse"), INVOICE)
FLAWED = Implementation(frozenset({"cache_carries_auth"}))


def test_request_shape():
    c = client(keep_approvals=False)
    run(SC, FLAWED, LLMPlanner(client=c))
    kw = c.beta.messages.calls[0]
    assert kw["model"] == MODEL
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert "thinking" not in kw and "budget_tokens" not in json.dumps(kw)


def test_adapter_that_carries_approvals_reproduces_the_cache_flaw():
    pl = LLMPlanner(client=client(keep_approvals=True))
    r = run(SC, FLAWED, pl)
    assert any(e.role == "C" and e.step == "send_invoice" and e.violation for e in r.effects)
    assert pl.adaptations[-1]["carried"] == ["ledger", "send_invoice"]
    assert {e.args.get("to") for e in r.effects if e.role == "C"} == {"new-customer"}


def test_adapter_that_drops_approvals_leaves_permission_behind():
    pl = LLMPlanner(client=client(keep_approvals=False))
    r = run(SC, FLAWED, pl)
    assert not any(e.role == "C" and e.step == "send_invoice" for e in r.effects)
    assert pl.adaptations[-1]["offered"] and pl.adaptations[-1]["carried"] == []


def test_refusal_falls_back_to_template_plan():
    pl = LLMPlanner(client=client(keep_approvals=False, refuse=True))
    r = run(SC, FLAWED, pl)
    assert pl.refusals >= 1 and pl.fallbacks >= 1
    assert r.effects  # the workflow still ran on the template plan


def test_responses_are_memoized(tmp_path):
    c = client(keep_approvals=False)
    memo = tmp_path / "memo.json"
    pl = LLMPlanner(client=c, memo_path=str(memo))
    run(SC, FLAWED, pl)
    n = len(c.beta.messages.calls)
    run(SC, FLAWED, pl)
    assert len(c.beta.messages.calls) == n
    pl2 = LLMPlanner(client=client(keep_approvals=False), memo_path=str(memo))
    run(SC, FLAWED, pl2)
    assert pl2.llm_calls == 0


class FakeCompletions:
    """Stands in for client.chat.completions on the OpenAI backend."""

    def __init__(self, keep_approvals: bool, refuse: bool = False):
        self.inner = FakeMessages(keep_approvals, refuse)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.inner.create(messages=kw["messages"])
        if r.stop_reason == "refusal":
            msg = SimpleNamespace(content=None, refusal="no")
        else:
            msg = SimpleNamespace(content=r.content[0].text, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def openai_client(**kw):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**kw)))


def test_openai_backend_request_shape_and_carry_forward():
    c = openai_client(keep_approvals=True)
    pl = LLMPlanner(client=c, provider="openai")
    r = run(SC, FLAWED, pl)
    kw = c.chat.completions.calls[0]
    assert kw["model"] == "gpt-5" and kw["reasoning_effort"] == "low"
    assert kw["response_format"]["json_schema"]["strict"] is True
    assert any(e.role == "C" and e.step == "send_invoice" and e.violation for e in r.effects)


def test_openai_refusal_falls_back():
    pl = LLMPlanner(client=openai_client(keep_approvals=False, refuse=True), provider="openai")
    run(SC, FLAWED, pl)
    assert pl.refusals >= 1 and pl.fallbacks >= 1
