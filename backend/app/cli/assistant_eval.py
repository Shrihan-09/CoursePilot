"""Run the assistant's golden eval (Prompt 11 Part B, B4).

    python -m app.cli.assistant_eval                      # fake provider (CI)
    python -m app.cli.assistant_eval --provider openai    # real models; needs OPENAI_API_KEY

Runs every golden conversation twice - default model only, and with the
escalation model - and reports pass rate, average tokens and estimated cost
per conversation for each. Reports are written to a NEW folder under the
system temp directory (never the repository or OneDrive); `--out` must also
be outside both.

The API key is read from the environment / .env by Settings and handed to the
provider; it is never printed, logged or written to a report.
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
from app.services.assistant.eval.runner import fake_factory, markdown, run_all, summarize

EXAMPLES = ["g11", "g22", "g37", "g26"]


def openai_factory(settings):
    from app.llm.providers.openai import OpenAIProvider

    def make(case: dict):
        return (OpenAIProvider(api_key=settings.openai_api_key, model=settings.assistant_model,
                               timeout_seconds=settings.assistant_timeout_seconds,
                               max_retries=settings.assistant_provider_retries),
                OpenAIProvider(api_key=settings.openai_api_key,
                               model=settings.assistant_escalation_model,
                               timeout_seconds=settings.assistant_timeout_seconds,
                               max_retries=settings.assistant_provider_retries))
    return make


async def _run(args) -> dict:
    settings = get_settings()
    if args.provider == "openai":
        if not settings.openai_api_key:
            raise SystemExit("OPENAI_API_KEY is not set (environment or .env).")
        make, alias = openai_factory(settings), None
    else:
        make = fake_factory
        alias = {"fake-assistant": settings.assistant_model,
                 "fake-assistant_escalation": settings.assistant_escalation_model}
    ids = set(args.cases.split(",")) if args.cases else None
    report: dict = {"provider": args.provider, "run_at": datetime.now(UTC).isoformat(),
                    "default_model": settings.assistant_model,
                    "escalation_model": settings.assistant_escalation_model, "modes": {}}
    transcripts = {}
    for mode, escalation in (("default_only", False), ("with_escalation", True)):
        if args.mode not in ("both", mode):
            continue
        results = await run_all(make, escalation_mode=escalation, ids=ids)
        report["modes"][mode] = summarize(results, prices=settings.assistant_prices,
                                          alias=alias)
        transcripts[mode] = {"md": markdown(results, EXAMPLES),
                             "json": [{"id": r.id, "passed": r.passed, "notes": r.notes,
                                       "transcript": r.transcript} for r in results]}
    if args.provider == "fake":
        report["note"] = ("Fake provider: token counts are estimated from characters "
                          "(chars/4) and costs priced as the configured models.")
    return report | {"_transcripts": transcripts}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--provider", choices=["fake", "openai"], default="fake")
    p.add_argument("--mode", choices=["both", "default_only", "with_escalation"],
                   default="both")
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
    for mode, t in transcripts.items():
        (out / f"transcripts_{mode}.md").write_text(t["md"], encoding="utf-8")
        (out / f"conversations_{mode}.json").write_text(json.dumps(t["json"], indent=2,
                                                                   default=str),
                                                        encoding="utf-8")
    for mode, s in report["modes"].items():
        cost = s["avg_cost_usd"]
        print(f"{mode}: {s['passed']}/{s['conversations']} passed ({s['pass_rate']:.1%}); "
              f"no auto-apply {s['no_auto_apply_rate']:.0%}; no invented courses "
              f"{s['no_invented_courses_rate']:.0%}; avg tokens {s['avg_input_tokens']:.0f} in / "
              f"{s['avg_output_tokens']:.0f} out; avg cost "
              f"{'n/a' if cost is None else f'${cost:.5f}'} per conversation")
        for f in s["failed"]:
            print(f"  FAIL {f['id']}: {'; '.join(f['notes'])[:300]}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
