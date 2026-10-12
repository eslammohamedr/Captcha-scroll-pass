#!/usr/bin/env python3
"""Check OPPO IMEI/SN values with a configured third-party API.

No requests are made unless --live is supplied. 3023 lookups may incur a fee;
they also require the additional --confirm-paid flag.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def read_identifiers(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return values


def post_form(url: str, fields: dict[str, str]) -> dict:
    body = urlencode(fields).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, key: str) -> dict:
    req = Request(url, headers={"key": key, "Accept": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def flatten_3023(payload: dict) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in payload.items():
        if key != "data" and not isinstance(value, (dict, list)):
            row[f"api_{key}"] = str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                for subkey, subvalue in value.items():
                    row[f"{key}_{subkey}"] = json.dumps(subvalue, ensure_ascii=False) if isinstance(subvalue, (dict, list)) else str(subvalue)
            else:
                row[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return row


def flatten_51gcc(payload: dict) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in payload.items():
        if key not in {"data"} and not isinstance(value, (dict, list)):
            row[f"api_{key}"] = str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for item in data.get("result", []):
            if isinstance(item, dict) and item.get("key"):
                value = item.get("value", "")
                row[str(item["key"])] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if data.get("other_result"):
            row["other_result"] = json.dumps(data["other_result"], ensure_ascii=False)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("3023", "51gcc"), required=True)
    parser.add_argument("--input", type=Path, default=Path("serials.txt"))
    parser.add_argument("--output", type=Path, default=Path("oppo_results.csv"))
    parser.add_argument("--limit", type=int, default=1, help="Maximum identifiers per run (default: 1).")
    parser.add_argument("--live", action="store_true", help="Send real identifiers to the selected provider.")
    parser.add_argument("--confirm-paid", action="store_true", help="Required for 3023 because its API is pay-per-query.")
    parser.add_argument("--demo", action="store_true", help="For 51gcc, request its documented simulated response instead of a real lookup.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests; default 1.")
    args = parser.parse_args()

    if args.limit < 1 or args.delay < 0:
        parser.error("--limit must be positive and --delay cannot be negative")
    if args.provider == "3023" and args.demo:
        parser.error("--demo is documented only for 51gcc")
    if args.provider == "3023" and args.live and not args.confirm_paid:
        parser.error("3023 charges per lookup; add --confirm-paid to run paid requests")
    if args.demo and args.live:
        parser.error("Use either --demo or --live, not both")

    ids = read_identifiers(args.input)[:args.limit]
    if not ids:
        print(f"No identifiers found in {args.input}", file=sys.stderr)
        return 2

    key_name = "OPPO_3023_API_KEY" if args.provider == "3023" else "OPPO_51GCC_API_KEY"
    api_key = os.environ.get(key_name, "")
    if (args.live or args.demo) and not api_key:
        print(f"Set {key_name} in the environment first.", file=sys.stderr)
        return 2

    rows: list[dict[str, str]] = []
    for index, identifier in enumerate(ids):
        base = {"identifier": identifier, "provider": args.provider}
        if not args.live and not args.demo:
            base.update({"result": "not_sent", "note": "Preview only. Add --live to query the API."})
            rows.append(base)
            continue
        try:
            if args.provider == "3023":
                query = urlencode({"sn": identifier})
                payload = get_json(f"https://api.3023data.com/oppo/coverage?{query}", api_key)
                base.update({"result": "ok" if payload.get("code") == 0 else "api_error"})
                base.update(flatten_3023(payload))
            else:
                payload = post_form("https://open-api.51gcc.com", {
                    "sn": identifier,
                    "api_id": "38",
                    "api_test": "1" if args.demo else "0",
                    "api_key": api_key,
                })
                base.update({"result": "ok" if payload.get("code") == 200 else "api_error"})
                base.update(flatten_51gcc(payload))
        except HTTPError as exc:
            base.update({"result": "http_error", "note": f"HTTP {exc.code}"})
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            base.update({"result": "request_error", "note": str(exc)})
        except Exception as exc:
            base.update({"result": "error", "note": str(exc)})
        rows.append(base)
        print(f"{index + 1}/{len(ids)}: {base['result']}")
        if index + 1 < len(ids) and (args.live or args.demo):
            time.sleep(args.delay)

    columns = list(dict.fromkeys(key for row in rows for key in row))
    with args.output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} row(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
