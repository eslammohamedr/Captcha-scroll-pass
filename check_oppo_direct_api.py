#!/usr/bin/env python3
"""Direct API checker for OPPO serial numbers and IMEIs (Approach 4).

Queries backend database APIs (51gcc, 3023 Data, Sickw, Custom) directly:
- 0 CAPTCHAs
- 0 Browsers / Headless overhead
- Millisecond response times
- Real-time progress bar via tqdm
- Formatted Excel output matching check_oppo.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Ensure real-time console streaming
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass


COLUMNS = [
    ("serial", "Serial / IMEI"),
    ("is_active", "Active?"),
    ("activation_date", "Activation Date"),
    ("model_name", "Model Name"),
    ("specs", "RAM/ROM"),
    ("color", "Color"),
    ("certified_model", "Certified Model"),
    ("region", "Region"),
    ("warranty_end", "Warranty End"),
    ("status", "Check Status"),
    ("checked_at", "Last Checked"),
]


def read_identifiers(file_path: Path) -> list[str]:
    """Read serial numbers or IMEIs from text, csv, or excel."""
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    ext = file_path.suffix.lower()
    identifiers: list[str] = []

    if ext in [".xlsx", ".xls"] and HAS_PANDAS:
        df = pd.read_excel(file_path)
        first_col = df.columns[0]
        for val in df[first_col].dropna():
            s = str(val).strip()
            if s and not s.startswith("#"):
                identifiers.append(s)
        return identifiers

    for line in file_path.read_text(encoding="utf-8-sig").splitlines():
        val = line.strip()
        if val and not val.startswith("#"):
            identifiers.append(val)
    return identifiers


# =====================================================================
# Provider Parsers & Query Handlers
# =====================================================================

def query_51gcc(serial: str, api_key: str, is_demo: bool = False) -> dict[str, Any]:
    """Query 51gcc API (API 38 for OPPO Device Details)."""
    url = "https://open-api.51gcc.com"
    payload = {
        "sn": serial,
        "api_id": "38",
        "api_key": api_key,
        "api_test": "1" if is_demo else "0",
    }
    resp = requests.post(url, data=payload, timeout=20)
    data = resp.json()

    code = data.get("code")
    if code != 200:
        msg = data.get("msg") or f"Error code {code}"
        return {
            "serial": serial,
            "is_active": "Error",
            "activation_date": "",
            "model_name": "",
            "specs": "",
            "color": "",
            "certified_model": "",
            "region": "",
            "warranty_end": "",
            "status": "API Error",
            "raw_msg": msg,
        }

    raw_items = data.get("data", {}).get("result", [])
    kv: dict[str, str] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict) and "key" in item:
                kv[str(item["key"]).lower()] = str(item.get("value") or "").strip()

    # Determine activation status
    active_val = kv.get("active_status", "") or kv.get("activation_status", "")
    act_date = kv.get("active_time", "") or kv.get("activation_date", "") or kv.get("reg_date", "")
    
    if "未激活" in active_val or "not" in active_val.lower() or not act_date:
        is_active = "No"
        act_display = act_date if act_date else "Not Activated"
    elif "已激活" in active_val or "active" in active_val.lower() or act_date:
        is_active = "Yes"
        act_display = act_date
    else:
        is_active = "Unknown"
        act_display = act_date

    ram = kv.get("ram", "")
    rom = kv.get("rom", "") or kv.get("memory", "")
    specs = f"{ram}+{rom}" if ram and rom else (rom or ram)

    return {
        "serial": serial,
        "is_active": is_active,
        "activation_date": act_display,
        "model_name": kv.get("model", "") or kv.get("marketing_name", ""),
        "specs": specs,
        "color": kv.get("color", ""),
        "certified_model": kv.get("certified_model", "") or kv.get("device_model", ""),
        "region": kv.get("region", "") or kv.get("country", ""),
        "warranty_end": kv.get("warranty_end", "") or kv.get("warranty_time", ""),
        "status": "Found",
        "raw_msg": json.dumps(kv, ensure_ascii=False),
    }


def query_3023(serial: str, api_key: str) -> dict[str, Any]:
    """Query 3023data API (OPPO Coverage endpoint)."""
    url = f"https://api.3023data.com/oppo/coverage?sn={serial}"
    headers = {"key": api_key, "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=20)
    data = resp.json()

    code = data.get("code")
    if code != 0:
        msg = data.get("msg") or f"Error code {code}"
        return {
            "serial": serial,
            "is_active": "Error",
            "activation_date": "",
            "model_name": "",
            "specs": "",
            "color": "",
            "certified_model": "",
            "region": "",
            "warranty_end": "",
            "status": "API Error",
            "raw_msg": msg,
        }

    d = data.get("data") or {}
    act_date = str(d.get("active_date") or d.get("regDate") or "").strip()
    act_status = str(d.get("active_status") or "").strip().lower()

    if "not" in act_status or not act_date or act_date in ["-", "0"]:
        is_active = "No"
        act_display = act_date if act_date else "Not Activated"
    else:
        is_active = "Yes"
        act_display = act_date

    ram = str(d.get("productRam") or "").strip()
    rom = str(d.get("productRom") or d.get("memory") or "").strip()
    specs = f"{ram}+{rom}" if ram and rom else (rom or ram)

    return {
        "serial": serial,
        "is_active": is_active,
        "activation_date": act_display,
        "model_name": str(d.get("model") or d.get("marketingModelName") or "").strip(),
        "specs": specs,
        "color": str(d.get("color") or d.get("productColor") or "").strip(),
        "certified_model": str(d.get("certifiedModel") or "").strip(),
        "region": str(d.get("region") or "").strip(),
        "warranty_end": str(d.get("warranty_date") or d.get("warrantyEndTime") or "").strip(),
        "status": "Found",
        "raw_msg": json.dumps(d, ensure_ascii=False),
    }


def query_sickw(serial: str, api_key: str, service_id: str = "1") -> dict[str, Any]:
    """Query Sickw IMEI API."""
    url = f"https://sickw.com/api.php?format=json&key={api_key}&service={service_id}&imei={serial}"
    resp = requests.get(url, timeout=25)
    data = resp.json()

    status = data.get("status")
    if status != "success":
        msg = data.get("result") or data.get("message") or "Sickw lookup failed"
        return {
            "serial": serial,
            "is_active": "Error",
            "activation_date": "",
            "model_name": "",
            "specs": "",
            "color": "",
            "certified_model": "",
            "region": "",
            "warranty_end": "",
            "status": "API Error",
            "raw_msg": str(msg),
        }

    res = data.get("result", {})
    if isinstance(res, str):
        # Plain text result format
        res = {"raw": res}

    act_date = res.get("Activation Date") or res.get("Estimated Purchase Date") or ""
    if "Not Activated" in act_date or not act_date:
        is_active = "No"
        act_display = "Not Activated"
    else:
        is_active = "Yes"
        act_display = act_date

    return {
        "serial": serial,
        "is_active": is_active,
        "activation_date": act_display,
        "model_name": res.get("Model") or res.get("Model Description") or "",
        "specs": res.get("Capacity") or "",
        "color": res.get("Color") or "",
        "certified_model": res.get("Model Number") or "",
        "region": res.get("Country") or "",
        "warranty_end": res.get("Warranty End Date") or res.get("Warranty Status") or "",
        "status": "Found",
        "raw_msg": json.dumps(res, ensure_ascii=False),
    }


def query_mock(serial: str) -> dict[str, Any]:
    """Simulate instantaneous response for testing without consuming API credits."""
    # Deterministic mock based on serial digits
    last_digit = int(serial[-1]) if serial[-1].isdigit() else 0
    is_active = "Yes" if last_digit % 2 == 1 else "No"
    act_date = "2025-11-20 14:32:00" if is_active == "Yes" else "Not Activated"

    return {
        "serial": serial,
        "is_active": is_active,
        "activation_date": act_date,
        "model_name": "OPPO A6",
        "specs": "8GB+256GB",
        "color": "Aurora Gold" if last_digit < 5 else "Starry Black",
        "certified_model": "CPH2817",
        "region": "UA",
        "warranty_end": "2026-11-20" if is_active == "Yes" else "",
        "status": "Found (Simulated)",
        "raw_msg": "Demo mode simulated check",
    }


# =====================================================================
# Excel Spreadsheet Manager
# =====================================================================

def save_to_excel(results: list[dict[str, Any]], output_path: Path, quiet: bool = False) -> None:
    """Save or merge results into formatted Excel spreadsheet."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_rows: dict[str, dict[str, Any]] = {}

    if output_path.exists() and HAS_OPENPYXL:
        try:
            wb_old = openpyxl.load_workbook(output_path)
            ws_old = wb_old.active
            headers = [cell.value for cell in ws_old[1]]
            header_map = {col_label: col_key for col_key, col_label in COLUMNS}

            for row in ws_old.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                row_dict = {}
                for idx, cell_val in enumerate(row):
                    if idx < len(headers) and headers[idx] in header_map:
                        row_dict[header_map[headers[idx]]] = cell_val
                sn = str(row[0]).strip()
                if sn:
                    existing_rows[sn] = row_dict
        except Exception as e:
            if not quiet:
                print(f"[Notice] Could not parse existing Excel: {e}")

    for r in results:
        sn = str(r["serial"]).strip()
        new_status = str(r.get("status") or "")
        old_status = str(existing_rows.get(sn, {}).get("status") or "")
        if sn in existing_rows and "Found" in old_status and "Found" not in new_status:
            continue
        row_entry = {**r, "checked_at": timestamp}
        existing_rows[sn] = row_entry

    final_rows = list(existing_rows.values())

    if HAS_OPENPYXL:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "OPPO Status"

        # OPPO Green Header
        header_fill = PatternFill(start_color="107C41", end_color="107C41", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        border_side = Side(border_style="thin", color="D9D9D9")
        cell_border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")

        ws.row_dimensions[1].height = 28

        for col_idx, (_, col_title) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align

        for row_idx, data_row in enumerate(final_rows, start=2):
            ws.row_dimensions[row_idx].height = 22
            is_active_val = data_row.get("is_active", "")

            status_fill = None
            if is_active_val == "Yes":
                status_fill = PatternFill(start_color="E2F0D9", end_color="E2F0D9", fill_type="solid")
            elif is_active_val == "No":
                status_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
            elif is_active_val in ["Not Found", "Error", "API Error"]:
                status_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")

            for col_idx, (col_key, _) in enumerate(COLUMNS, start=1):
                val = data_row.get(col_key, "")
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = cell_border

                if col_key in ["serial", "model_name", "color"]:
                    cell.alignment = left_align
                else:
                    cell.alignment = center_align

                if col_key == "is_active" and status_fill:
                    cell.fill = status_fill
                    cell.font = Font(name="Calibri", size=10, bold=True)

        for col_idx in range(1, len(COLUMNS) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = max(len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(1, len(final_rows) + 2))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

        wb.save(output_path)
        if not quiet:
            print(f"Saved {len(final_rows)} records to Excel: {output_path}")
    elif HAS_PANDAS:
        df = pd.DataFrame(final_rows)
        df.to_excel(output_path, index=False)
        if not quiet:
            print(f"Saved {len(final_rows)} records to Excel: {output_path}")


# =====================================================================
# Main CLI Interface
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Direct API IMEI/Serial checker for OPPO devices (0 CAPTCHAs, fast HTTP queries)."
    )
    parser.add_argument(
        "--provider",
        choices=["51gcc", "3023", "sickw", "mock"],
        default="51gcc",
        help="API provider to query (default: 51gcc)",
    )
    parser.add_argument(
        "--key",
        type=str,
        default=None,
        help="API Key for the provider. Can also be set via OPPO_API_KEY environment variable.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("serials.txt"),
        help="Path to input serials file (default: serials.txt)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("oppo_results.xlsx"),
        help="Path to output Excel file (default: oppo_results.xlsx)",
    )
    parser.add_argument(
        "--service-id",
        type=str,
        default="1",
        help="Service ID (used for Sickw, default: 1)",
    )
    parser.add_argument(
        "--skip-found",
        action="store_true",
        help="Skip serials already successfully verified in output Excel",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo/test mode (free testing endpoint or simulated response)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Delay between API queries in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of devices to process",
    )

    args = parser.parse_args()

    identifiers = read_identifiers(args.input)
    if args.limit:
        identifiers = identifiers[: args.limit]

    # Resolve API Key
    api_key = args.key
    if not api_key:
        env_map = {
            "51gcc": "OPPO_51GCC_API_KEY",
            "3023": "OPPO_3023_API_KEY",
            "sickw": "SICKW_API_KEY",
        }
        env_var = env_map.get(args.provider, "OPPO_API_KEY")
        api_key = os.environ.get(env_var) or os.environ.get("OPPO_API_KEY", "")

    if not api_key and not args.demo and args.provider != "mock":
        print(f"\n[Warning] No API key provided for '{args.provider}'.", file=sys.stderr)
        print("To use live queries, pass your key via --key YOUR_KEY or set environment variable.", file=sys.stderr)
        print(f"Example: python check_oppo_direct_api.py --provider {args.provider} --key YOUR_KEY", file=sys.stderr)
        print("\nTip: To test the workflow immediately without a key, add --demo flag.\n", file=sys.stderr)
        return 1

    # Filter out already verified if --skip-found
    if args.skip_found and args.output.exists() and HAS_OPENPYXL:
        try:
            wb_check = openpyxl.load_workbook(args.output)
            ws_check = wb_check.active
            headers = [cell.value for cell in ws_check[1]]
            status_idx = headers.index("Check Status") if "Check Status" in headers else -1
            sn_idx = headers.index("Serial / IMEI") if "Serial / IMEI" in headers else 0
            found_sns = set()
            for row in ws_check.iter_rows(min_row=2, values_only=True):
                if row and status_idx != -1 and len(row) > status_idx and "Found" in str(row[status_idx]):
                    found_sns.add(str(row[sn_idx]).strip())
            initial_count = len(identifiers)
            identifiers = [s for s in identifiers if s not in found_sns]
            if initial_count != len(identifiers):
                print(f"Skipping {initial_count - len(identifiers)} already verified serial(s). {len(identifiers)} remaining to check.")
        except Exception:
            pass

    if not identifiers:
        print(f"All serials in {args.input} are already verified in {args.output}!")
        return 0

    mode_label = "Demo/Simulated" if args.demo or args.provider == "mock" else f"Live ({args.provider})"
    print(f"Checking {len(identifiers)} device(s) via Direct API [{mode_label}]...")
    print(f"Target Excel output: {args.output}\n")

    results: list[dict[str, Any]] = []

    def run_query(sn: str) -> dict[str, Any]:
        if args.demo and args.provider == "51gcc" and api_key:
            return query_51gcc(sn, api_key, is_demo=True)
        elif args.demo or args.provider == "mock":
            time.sleep(0.08)  # simulate network latency
            return query_mock(sn)
        elif args.provider == "51gcc":
            return query_51gcc(sn, api_key, is_demo=False)
        elif args.provider == "3023":
            return query_3023(sn, api_key)
        elif args.provider == "sickw":
            return query_sickw(sn, api_key, service_id=args.service_id)
        else:
            return query_mock(sn)

    if HAS_TQDM:
        pbar = tqdm(
            identifiers,
            desc="OPPO Direct API Check",
            unit="device",
            ncols=95,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}",
        )
        for idx, sn in enumerate(pbar, start=1):
            pbar.set_postfix_str(f"{sn}: Querying...")
            res = run_query(sn)
            results.append(res)

            save_to_excel(results, args.output, quiet=True)

            status_line = f"[{idx}/{len(identifiers)}] {sn} -> Active: {res['is_active']} | Model: {res['model_name']} | Color: {res['color']}"
            tqdm.write(status_line)
            pbar.set_postfix_str(f"Last: {res['is_active']}")

            if idx < len(identifiers):
                time.sleep(args.delay)
        pbar.close()
    else:
        for idx, sn in enumerate(identifiers, start=1):
            pct = int((idx / len(identifiers)) * 100)
            bar_str = "[" + "=" * (pct // 5) + " " * (20 - pct // 5) + "]"
            print(f"{bar_str} {pct}% ({idx}/{len(identifiers)}) Querying: {sn} ...", flush=True)

            res = run_query(sn)
            results.append(res)
            save_to_excel(results, args.output, quiet=True)

            print(f"   Result: Active={res['is_active']} | Model={res['model_name']} | Color={res['color']}", flush=True)
            if idx < len(identifiers):
                time.sleep(args.delay)

    save_to_excel(results, args.output, quiet=False)
    print("\n" + "=" * 60)
    print(f"Done! Checked {len(results)} device(s) in seconds. Results saved to {args.output}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
