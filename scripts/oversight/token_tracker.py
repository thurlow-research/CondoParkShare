#!/usr/bin/env python3
"""
token_tracker.py — track and report external CLI token usage across oversight runs.

Appends usage records to .claudetmp/oversight/token-usage.jsonl so you can see
cumulative spend across a full build. Each record is one external CLI invocation.

Usage (from shell scripts):
  # Record a usage event:
  python3 scripts/oversight/token_tracker.py record \
    --vendor agy \
    --stage second-review \
    --step 3 \
    --prompt-chars 12400 \
    --output-chars 3200 \
    [--actual-prompt-tokens N]    # from CLI output if available
    [--actual-output-tokens N]

  # Print report for current session:
  python3 scripts/oversight/token_tracker.py report

  # Print report for all time:
  python3 scripts/oversight/token_tracker.py report --all

Estimation: 1 token ≈ 4 characters (English prose/code).
When actual counts are available from the CLI JSON output, those are used instead.

Subscription awareness:
  agy (Gemini):   subscription CLI. Tracks usage vs. known quota.
  codex (OpenAI): subscription CLI. Reserve reviewer — tracks premium request usage.
  claude:         Internal agents. Tracked for awareness; covered by the Claude subscription.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

CHARS_PER_TOKEN = 4.0
USAGE_LOG = Path(".claudetmp/oversight/token-usage.jsonl")

# Rough monthly quota estimates (conservative — actual quotas vary)
MONTHLY_QUOTA_ESTIMATES = {
    "agy-20": {"tokens": 2_000_000, "label": "Gemini (baseline tier)"},
    "agy-100": {"tokens": 10_000_000, "label": "Gemini (upgraded tier)"},
    "codex-20": {"tokens": 500_000, "label": "ChatGPT (~25 large calls)"},
}


def estimate_tokens(chars: int) -> int:
    return max(1, round(chars / CHARS_PER_TOKEN))


def record(args: argparse.Namespace) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)

    # REQ-255-25/26: review-event path — no token count, outcome required.
    is_review_event = getattr(args, "review_event", False)
    if is_review_event:
        outcome = getattr(args, "outcome", None)
        if not outcome:
            print(
                "error: --outcome is required with --review-event",
                file=__import__("sys").stderr,
            )
            __import__("sys").exit(1)
        step_val = args.step
        try:
            step_int = int(step_val) if step_val else 0
        except (ValueError, TypeError):
            step_int = 0
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "vendor": args.vendor,
            "stage": args.stage,
            "step": step_int,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated": False,
            "review_event": True,
            "outcome": outcome,
        }
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
        print(
            f"  Review event recorded: {args.vendor} {args.stage} "
            f"step={step_int} outcome={outcome}"
        )
        return

    # Standard token-count path (unchanged).
    prompt_tokens = args.actual_prompt_tokens or estimate_tokens(args.prompt_chars or 0)
    output_tokens = args.actual_output_tokens or estimate_tokens(args.output_chars or 0)
    total_tokens = prompt_tokens + output_tokens
    estimated = not (args.actual_prompt_tokens or args.actual_output_tokens)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "vendor": args.vendor,
        "stage": args.stage,
        "step": args.step,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated": estimated,
    }

    with open(USAGE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    flag = " [estimated]" if estimated else " [actual]"
    print(
        f"  Token usage recorded: {args.vendor} {args.stage} "
        f"step={args.step} total={total_tokens:,}{flag}"
    )


def report(args: argparse.Namespace) -> None:
    if not USAGE_LOG.exists():
        print("No token usage recorded yet.")
        return

    entries = []
    with open(USAGE_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not entries:
        print("No token usage recorded yet.")
        return

    # Filter to current session (today) unless --all
    if not getattr(args, "all", False):
        today = datetime.now(timezone.utc).date().isoformat()
        entries = [e for e in entries if e["ts"].startswith(today)]
        if not entries:
            print("No token usage recorded today. Use --all for all-time report.")
            return

    # Aggregate by vendor
    by_vendor: dict[str, dict] = {}
    for e in entries:
        v = e["vendor"]
        if v not in by_vendor:
            by_vendor[v] = {
                "prompt": 0, "output": 0, "total": 0, "calls": 0, "estimated": 0,
                "review_events": 0,  # REQ-255-27: count review-event records separately
            }
        if e.get("review_event"):
            # Review-event records MUST NOT contribute to token totals (REQ-255-27).
            by_vendor[v]["review_events"] += 1
        else:
            by_vendor[v]["prompt"] += e["prompt_tokens"]
            by_vendor[v]["output"] += e["output_tokens"]
            by_vendor[v]["total"] += e["total_tokens"]
            by_vendor[v]["calls"] += 1
            if e.get("estimated"):
                by_vendor[v]["estimated"] += 1

    # Aggregate by stage — review-event records do not contribute to token totals (REQ-255-27).
    by_stage: dict[str, int] = {}
    for e in entries:
        if e.get("review_event"):
            continue
        s = e["stage"]
        by_stage[s] = by_stage.get(s, 0) + e["total_tokens"]

    total_all = sum(e["total_tokens"] for e in entries if not e.get("review_event"))
    any_estimated = any(e.get("estimated") for e in entries if not e.get("review_event"))

    print("")
    print("╔══════════════════════════════════════════════════════╗")
    print("║  Token Usage Report — Human Oversight System        ║")
    scope = "all-time" if getattr(args, "all", False) else "today"
    print(f"║  Scope: {scope:<45}║")
    print("╚══════════════════════════════════════════════════════╝")
    print("")
    print("By vendor:")
    for vendor, data in sorted(by_vendor.items()):
        # REQ-255-27: vendors with only review-event records show event count, not token total.
        if data["review_events"] > 0 and data["calls"] == 0:
            print(f"  {vendor:<12}  (review events: {data['review_events']})")
        else:
            est_note = f"  ({data['estimated']}/{data['calls']} estimated)" if data["estimated"] else ""
            rev_note = f"  +{data['review_events']} review event(s)" if data["review_events"] else ""
            print(
                f"  {vendor:<12}  {data['total']:>8,} tokens  "
                f"({data['calls']} calls, {data['prompt']:,} in / {data['output']:,} out)"
                f"{est_note}{rev_note}"
            )

    print("")
    print("By pipeline stage:")
    for stage, tokens in sorted(by_stage.items(), key=lambda x: x[1], reverse=True):
        bar_len = min(30, round(tokens / max(by_stage.values()) * 30))
        bar = "█" * bar_len
        print(f"  {stage:<22}  {tokens:>8,}  {bar}")

    print("")
    print(f"  Total: {total_all:,} tokens across {len(entries)} calls")

    if any_estimated:
        print("")
        print("  ⚠ Some counts are estimated (1 token ≈ 4 chars).")
        print("    Actual counts shown when CLI output includes usage data.")

    # Subscription guidance
    agy_total = by_vendor.get("agy", {}).get("total", 0)
    codex_total = by_vendor.get("codex", {}).get("total", 0)

    if agy_total > 0 or codex_total > 0:
        print("")
        print("Subscription impact:")
        if agy_total > 0:
            pct_20 = round(agy_total / MONTHLY_QUOTA_ESTIMATES["agy-20"]["tokens"] * 100, 1)
            pct_100 = round(agy_total / MONTHLY_QUOTA_ESTIMATES["agy-100"]["tokens"] * 100, 1)
            print(
                f"  agy (Gemini):   ~{agy_total:,} tokens  "
                f"= ~{pct_20}% of $20/mo | ~{pct_100}% of $100/mo"
            )
            if pct_20 > 50:
                print("    ⚠ Consider $100/mo upgrade if nearing quota")
        if codex_total > 0:
            pct = round(codex_total / MONTHLY_QUOTA_ESTIMATES["codex-20"]["tokens"] * 100, 1)
            print(f"  codex (OpenAI): ~{codex_total:,} tokens  = ~{pct}% of $20/mo reserve")
            if pct > 60:
                print("    ⚠ Approaching reserve quota — codex fires at HIGH+ only by design")

    claude_total = by_vendor.get("claude", {}).get("total", 0)
    if claude_total > 0:
        print(f"  claude (internal): ~{claude_total:,} tokens  (Claude subscription — awareness only)")

    print("")


def main() -> None:
    parser = argparse.ArgumentParser(description="Track oversight token usage")
    sub = parser.add_subparsers(dest="cmd")

    rec = sub.add_parser("record")
    rec.add_argument("--vendor", required=True, choices=["agy", "codex", "claude", "copilot"])
    rec.add_argument("--stage", required=True)
    rec.add_argument("--step", default="")
    rec.add_argument("--prompt-chars", type=int, default=0)
    rec.add_argument("--output-chars", type=int, default=0)
    rec.add_argument("--actual-prompt-tokens", type=int, default=0)
    rec.add_argument("--actual-output-tokens", type=int, default=0)
    # REQ-255-25/26: review-event path (no token count; outcome required).
    rec.add_argument(
        "--review-event", action="store_true",
        help="record a review event (no token count); requires --outcome",
    )
    rec.add_argument(
        "--outcome",
        choices=["approved", "changes_requested", "commented", "timeout", "skipped", "dismissed"],
        help="review outcome (required with --review-event)",
    )

    rep = sub.add_parser("report")
    rep.add_argument("--all", action="store_true")

    args = parser.parse_args()
    if args.cmd == "record":
        record(args)
    elif args.cmd == "report":
        report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
# end of file — importable as a module by shell scripts
