"""Run the assistant's golden eval (Prompt 11 Part B, B4).

    python -m app.cli.assistant_eval                      # fake provider (CI)
    python -m app.cli.assistant_eval --provider openai    # real models; needs OPENAI_API_KEY

A run is a list of CONFIGURATIONS. `default` runs with no escalation;
`default>escalation` escalates 2+-change turns and failed replies. The
default list (from config) is:

    <ASSISTANT_MODEL>                                  e.g. gpt-6-luna
    <ASSISTANT_MODEL>><ASSISTANT_ESCALATION_MODEL>     e.g. gpt-6-luna>gpt-6-sol
    <each --compare model> and <it>><ESCALATION_MODEL> e.g. gpt-5.6-luna, gpt-5.6-luna>gpt-6-sol

Each configuration reports pass rate, the two 100% rates, average tokens and
estimated cost per conversation. Real runs use the Responses API (native tool
calling) at ASSISTANT_REASONING_EFFORT, and first check that every model id
exists, so a typo costs nothing.

Reports go to a NEW folder under the system temp directory (never the
repository or OneDrive; `--out` must also be outside both). The API key is
read by Settings and handed to the provider; it is never printed, logged or
written to a report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import tempfile
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.test_output_guard import check_basetemp
from app.services.assistant.eval.runner import markdown, run_all, summarize

EXAMPLES = ["g11", "g22", "g37", "g26"]


def configs(settings, compare: list[str], explicit: str | None) -> list[tuple[str, str | None]]:
    if explicit:
        out = []
        for c in explicit.split(","):
            default, _, esc = c.strip().partition(">")
            out.append((default, esc or None))
        return out
    esc = settings.assistant_escalation_model
    out = [(settings.assistant_model, None), (settings.assistant_model, esc)]
    for m in compare:
        out += [(m, None), (m, esc)]
    return out


def _label(default: str, esc: str | None) -> str:
    return f"{default} -> {esc}" if esc else f"{default} only"


def openai_factory(settings, default: str, esc: str | None):
    from app.llm.providers.openai import OpenAIProvider

    def build(model: str):
        return OpenAIProvider(api_key=settings.openai_api_key, model=model,
                              timeout_seconds=settings.assistant_timeout_seconds,
                              max_retries=settings.assistant_provider_retries,
                              reasoning_effort=settings.assistant_reasoning_effort)

    return lambda case: (build(default), build(esc) if esc else None)


def fake_factory_for(default: str, esc: str | None):
    from app.services.assistant.fake import FakeAssistantProvider

    return lambda case: (FakeAssistantProvider(default, script=case.get("script")),
                         FakeAssistantProvider(esc) if esc else None)


async def _check_models(settings, models: set[str]) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30, max_retries=1)
    missing = []
    for m in sorted(models):
        try:
            await client.models.retrieve(m)
        except Exception as exc:  # noqa: BLE001 - only the class name is reported
            missing.append(f"{m} ({type(exc).__name__})")
    if missing:
        raise SystemExit("These model ids are not available to this key: " + ", ".join(missing))


async def _run(args) -> dict:
    settings = get_settings()
    runs = configs(settings, [m for m in (args.compare or "").split(",") if m], args.configs)
    if args.provider == "openai":
        if not settings.openai_api_key:
            raise SystemExit("OPENAI_API_KEY is not set (environment or .env).")
        await _check_models(settings, {m for d, e in runs for m in (d, e) if m})
    ids = set(args.cases.split(",")) if args.cases else None
    report: dict = {"provider": args.provider, "run_at": datetime.now(UTC).isoformat(),
                    "api": "responses" if args.provider == "openai" else "fake (native tools)",
                    "reasoning_effort": settings.assistant_reasoning_effort, "configs": {}}
    transcripts = {}
    for default, esc in runs:
        label = _label(default, esc)
        if args.provider == "openai":
            make, alias = openai_factory(settings, default, esc), None
        else:
            make = fake_factory_for(f"fake-{default}", f"fake-{esc}" if esc else None)
            alias = {f"fake-{default}": default, **({f"fake-{esc}": esc} if esc else {})}
        results = await run_all(make, escalation_mode=esc is not None, ids=ids)
        report["configs"][label] = summarize(results, prices=settings.assistant_prices,
                                             alias=alias)
        transcripts[label] = {"md": markdown(results, EXAMPLES),
                              "json": [{"id": r.id, "passed": r.passed, "notes": r.notes,
                                        "transcript": r.transcript} for r in results]}
    if args.provider == "fake":
        report["note"] = ("Fake provider: token counts are estimated from characters "
                          "(chars/4) and costs priced as the named models.")
    return report | {"_transcripts": transcripts}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--provider", choices=["fake", "openai"], default="fake")
    p.add_argument("--compare", default="gpt-5.6-luna",
                   help="extra default models to compare, comma-separated ('' for none)")
    p.add_argument("--configs", help="explicit list, e.g. 'gpt-6-luna,gpt-6-luna>gpt-6-sol'")
    p.add_argument("--cases", help="comma-separated case ids (default: all)")
    p.add_argument("--out", type=pathlib.Path, help="report folder (outside repo and OneDrive)")
    args = p.parse_args(argv)
    if args.out:
        check_basetemp(args.out)
        out = args.out
        out.mkdir(parents=True, exist_ok=False)
    else:
        out = pathlib.Path(tempfile.mkdtemp(prefix="coursepilot-assistant-eval-"))
    report = asyncio.run(_run(args))
    transcripts = report.pop("_transcripts")
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str),
                                     encoding="utf-8")
    for i, (label, t) in enumerate(transcripts.items()):
        (out / f"transcripts_{i}.md").write_text(f"# {label}\n\n{t['md']}", encoding="utf-8")
        (out / f"conversations_{i}.json").write_text(json.dumps(
            {"config": label, "conversations": t["json"]}, indent=2, default=str),
            encoding="utf-8")
    for label, s in report["configs"].items():
        cost = s["avg_cost_usd"]
        print(f"{label}: {s['passed']}/{s['conversations']} passed ({s['pass_rate']:.1%}); "
              f"no auto-apply {s['no_auto_apply_rate']:.0%}; no invented courses "
              f"{s['no_invented_courses_rate']:.0%}; avg tokens {s['avg_input_tokens']:.0f} in / "
              f"{s['avg_output_tokens']:.0f} out; avg cost "
              f"{'n/a' if cost is None else f'${cost:.5f}'} per conversation; "
              f"escalated {s['conversations_escalated']}")
        for f in s["failed"]:
            print(f"  FAIL {f['id']}: {'; '.join(f['notes'])[:300]}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
