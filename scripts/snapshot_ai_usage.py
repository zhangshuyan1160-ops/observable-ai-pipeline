"""Extract usage counters from this Codex chat without exporting conversation text.

Usage: python scripts/snapshot_ai_usage.py --output docs/evidence/ai-usage-planning.json
An explicit --session JSONL path may be used when CODEX_THREAD_ID is unavailable.
This records one cumulative snapshot; it does not sum cumulative log events.
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.session:
        paths = [args.session.expanduser()]
    else:
        thread = os.environ.get("CODEX_THREAD_ID")
        if not thread:
            parser.error("No current thread ID. Supply --session; no other chats are inspected.")
        paths = list((Path.home() / ".codex/sessions").rglob("*" + thread + "*.jsonl"))
    if len(paths) != 1:
        parser.error("Expected one current-chat log; supply an explicit --session path.")

    latest = None
    count = 0
    with paths[0].open(encoding="utf-8") as source:
        for line in source:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # A live log may end with an incomplete line.
            payload = record.get("payload", {})
            info = payload.get("info")
            if payload.get("type") == "token_count" and isinstance(info, dict):
                total = info.get("total_token_usage")
                if isinstance(total, dict):
                    latest = (record.get("timestamp"), total)
                    count += 1
    if latest is None:
        parser.error("No usage counters found in the selected session.")

    timestamp, total = latest
    inputs = total.get("input_tokens")
    outputs = total.get("output_tokens")
    cached = total.get("cached_input_tokens")
    if not all(isinstance(value, int) and value >= 0 for value in (inputs, outputs, cached)):
        parser.error("Unexpected usage schema: required counters are missing or invalid.")
    if cached > inputs or total.get("total_tokens") != inputs + outputs:
        parser.error("Inconsistent usage counters; inspect schema before reporting totals.")

    local_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(
        timezone(timedelta(hours=8))
    )
    result = {
        "scope": "one current Codex chat, cumulative through the recorded snapshot; not final project usage",
        "source": "local Codex token_count total_token_usage; conversation text and local path omitted",
        "snapshot_at_utc": timestamp,
        "snapshot_at_shanghai": local_time.isoformat(),
        "usage_event_count": count,
        "input_tokens": inputs,
        "cached_input_tokens_subset": cached,
        "output_tokens": outputs,
        "reasoning_output_tokens_reported_subset": total.get("reasoning_output_tokens"),
        "total_tokens_input_plus_output": inputs + outputs,
        "uncached_input_plus_output": inputs - cached + outputs,
        "note": "Cached input is already included in input. This is not a cost estimate or human-work timer.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
