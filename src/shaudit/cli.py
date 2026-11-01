"""Command line entry point: `shaudit <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import ALL_MECHANISMS, INTERVENTIONS, MECHANISM_TEXT, Implementation


def _flaws(xs):
    bad = [x for x in xs if x not in ALL_MECHANISMS]
    if bad:
        raise SystemExit(f"unknown mechanism(s): {bad}; choose from {list(ALL_MECHANISMS)}")
    return xs


def _load_dotenv(path: str = ".env") -> None:
    """Pick up API keys from a local .env without overriding the environment."""
    import os

    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip("'\""))
    if not os.environ.get("OPENAI_API_KEY"):
        # The workspace keeps one OpenAI key in the macOS Keychain (bin/set-openai-key).
        import subprocess

        try:
            key = subprocess.run(
                ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s",
                 "writ-openai-api-key", "-w"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            key = ""
        if key:
            os.environ["OPENAI_API_KEY"] = key


def _planner(args):
    if not getattr(args, "llm", False):
        return None
    from .llm import LLMPlanner

    return LLMPlanner(model=args.model, effort=args.effort, memo_path=args.memo, provider=args.provider)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="shaudit", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def llm_opts(p):
        p.add_argument("--llm", action="store_true", help="plan and adapt with a real LLM")
        p.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
        p.add_argument("--model", help="default: claude-opus-5-5 (anthropic) or gpt-5 (openai)")
        p.add_argument("--effort", default="low")
        p.add_argument("--memo", default="results/llm_memo.json", help="prompt-keyed response cache")

    p = sub.add_parser("demo", help="the shared invoice-plan demonstration")
    p.add_argument("--flaws", nargs="*", default=["enqueue_only"])
    p.add_argument("--visibility", default="standard", choices=["full", "standard", "sparse"])
    p.add_argument("--budget", type=int, default=10)
    p.add_argument("--out")
    llm_opts(p)

    p = sub.add_parser("audit", help="audit one workflow template against a hidden deployment")
    p.add_argument("--template", required=True)
    p.add_argument("--flaws", nargs="*", default=[])
    p.add_argument("--policy", default="active", choices=["active", "lookahead", "random", "checklist"])
    p.add_argument("--visibility", default="standard", choices=["full", "standard", "sparse"])
    p.add_argument("--budget", type=int, default=10)
    p.add_argument("--whitebox", action="store_true", help="finish with a bounded exhaustive check")
    p.add_argument("--seed", type=int, default=0)
    llm_opts(p)

    p = sub.add_parser("closure", help="bounded exhaustive reachability check")
    p.add_argument("--template", required=True)
    p.add_argument("--flaws", nargs="*", default=[])
    p.add_argument("--interventions", nargs="*", default=[], choices=list(INTERVENTIONS))

    p = sub.add_parser("bench", help="full benchmark across splits, policies and baselines")
    p.add_argument("--budget", type=int, default=10)
    p.add_argument("--workers", type=int)
    p.add_argument("--out", default="results")
    p.add_argument("--splits", nargs="*", default=["dev", "gen", "hard"])

    p = sub.add_parser("tune", help="tune alpha/beta on the dev split")
    p.add_argument("--budget", type=int, default=10)
    p.add_argument("--workers", type=int)
    p.add_argument("--out", default="results/tuned.json")

    p = sub.add_parser("llm-subset", help="real LLM planning/adaptation on a representative subset")
    p.add_argument("--n", type=int, default=4)
    p.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    p.add_argument("--model", help="default: claude-opus-5-5 (anthropic) or gpt-5 (openai)")
    p.add_argument("--effort", default="low")
    p.add_argument("--memo", default="results/llm_memo.json")

    sub.add_parser("templates", help="list workflow templates and mechanisms")

    args = ap.parse_args(argv)
    _load_dotenv()

    if args.cmd == "demo":
        from .demo import main as demo

        demo(_flaws(args.flaws), args.visibility, args.budget, _planner(args), args.out)
    elif args.cmd == "audit":
        from .auditor import AuditConfig, audit
        from .workloads import TEMPLATES

        t = TEMPLATES[args.template]
        cfg = AuditConfig(policy=args.policy, visibility=args.visibility, budget=args.budget,
                          whitebox=args.whitebox, seed=args.seed)
        rep = audit(t, Implementation(frozenset(_flaws(args.flaws))), cfg, _planner(args))
        for pr in rep.probes:
            print(f"{pr.index:>2} {pr.probe:34s} {'HIT' if pr.hit else '-':3s} {pr.sig} K={pr.K}")
        for c in rep.counterexamples:
            print("cex:", c.conditions, "| explained by", c.explanation or "UNEXPLAINED",
                  "| blocked by", sorted(c.blocking), "| path", c.components)
        print(json.dumps({"K": sorted(rep.K), "mincut_K": sorted(rep.mincut_K or []), "cost": rep.cost,
                          "claim": rep.claim, "residual": rep.residual, "other": rep.other,
                          "probes": rep.executions, "replays": rep.replays}, indent=1))
    elif args.cmd == "closure":
        from . import closure

        r = closure.check(args.template, Implementation(frozenset(_flaws(args.flaws)), frozenset(args.interventions)))
        print(json.dumps({"closed_within_bound": r.closed, "schedules": r.schedules, "violating": r.violating,
                          "tags": sorted(r.tags), "max_latency": r.max_latency}, indent=1))
        if r.counterexample:
            print("first counterexample:", r.counterexample)
    elif args.cmd == "bench":
        from .experiments import bench

        bench(args.out, args.budget, args.workers, tuple(args.splits))
        print(Path(args.out, "summary.md").read_text())
    elif args.cmd == "tune":
        from .experiments import tune

        r = tune(args.budget, args.workers)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(r, indent=1))
        print(f"best: {r['best']} (alpha={r['alpha']}, beta={r['beta']})")
    elif args.cmd == "llm-subset":
        from .experiments import llm_subset
        from .llm import LLMPlanner

        planner = LLMPlanner(model=args.model, effort=args.effort, memo_path=args.memo, provider=args.provider)
        r = llm_subset(planner, args.n, out_dir=f"results/llm_{args.provider}")
        print(json.dumps(r, indent=1))
    elif args.cmd == "templates":
        from .workloads import TEMPLATES

        for name, t in TEMPLATES.items():
            steps = ", ".join(f"{s.id}:{s.tool}@{s.worker}/{s.mode}" for s in t.steps)
            print(f"{t.split:5s} {name:28s} {steps}")
        print()
        for m, txt in MECHANISM_TEXT.items():
            print(f"{m:20s} {txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
