"""Excel & CSV Report Generator for OPPO Device Verification & Sales Monitoring.

Generates the TWO distinct report sheets required after every run:
1. Full Run Sheet (All checked serials with full device specifications)
2. Newly Activated Sheet (Only devices detected as active in this batch)
"""

from __future__ import annotations

import csv
import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _apply_sheet_styling(ws: Any, is_newly_activated_sheet: bool = False) -> None:
    """Apply professional colors, headers, borders, and auto-column widths."""
    header_fill = PatternFill(
        start_color="1E4620" if is_newly_activated_sheet else "1F2428",
        end_color="1E4620" if is_newly_activated_sheet else "1F2428",
        fill_type="solid",
    )
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")

    thin_border = Border(
        left=Side(style="thin", color="E1E4E8"),
        right=Side(style="thin", color="E1E4E8"),
        top=Side(style="thin", color="E1E4E8"),
        bottom=Side(style="thin", color="E1E4E8"),
    )

    active_fill = PatternFill(start_color="E6FFEC", end_color="E6FFEC", fill_type="solid")
    active_font = Font(name="Segoe UI", size=10, bold=True, color="1A7F37")

    inactive_fill = PatternFill(start_color="FFF8C5", end_color="FFF8C5", fill_type="solid")
    inactive_font = Font(name="Segoe UI", size=10, bold=True, color="9A6700")

    pending_fill = PatternFill(start_color="F2EAFA", end_color="F2EAFA", fill_type="solid")
    pending_font = Font(name="Segoe UI", size=10, bold=True, color="6B21A8")

    err_fill = PatternFill(start_color="FFEBE9", end_color="FFEBE9", fill_type="solid")
    err_font = Font(name="Segoe UI", size=10, bold=True, color="CF222E")

    # Format header row
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[1].height = 28

    # Format data rows
    headers = [ws.cell(row=1, column=col).value for col in range(1, ws.max_column + 1)]
    active_col_idx = headers.index("Active?") + 1 if "Active?" in headers else -1

    for row_idx in range(2, ws.max_row + 1):
        ws.row_dimensions[row_idx].height = 20
        is_active_val = ws.cell(row=row_idx, column=active_col_idx).value if active_col_idx != -1 else ""

        for col_idx in range(1, ws.max_column + 1):
            c = ws.cell(row=row_idx, column=col_idx)
            c.border = thin_border
            c.font = Font(name="Segoe UI", size=10)
            c.alignment = Alignment(vertical="center")

            # Center-align specific columns
            header_name = headers[col_idx - 1]
            if header_name in ["Active?", "Serial / IMEI", "Group", "Last Checked", "Activated At", "Check Status"]:
                c.alignment = Alignment(horizontal="center", vertical="center")

            # Highlight Active column
            if col_idx == active_col_idx:
                act_str = str(is_active_val).strip()
                if act_str == "Yes":
                    c.fill = active_fill
                    c.font = active_font
                elif act_str == "No":
                    c.fill = inactive_fill
                    c.font = inactive_font
                elif act_str in ["Pending", "Pending Sync", "Syncing"]:
                    c.fill = pending_fill
                    c.font = pending_font
                elif act_str in ["Error", "Captcha Failed", "Unknown", "No Response"]:
                    c.fill = err_fill
                    c.font = err_font

    # Auto-adjust column widths
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)


def generate_full_run_sheet(devices: list[dict[str, Any]], custom_tag: str = "") -> tuple[Path, Path]:
    """
    Generate Sheet #1: Full Run Report with all processed serials.
    Returns (excel_path, csv_path).
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_part = f"_{custom_tag.strip()}" if custom_tag.strip() else ""
    filename_base = f"oppo_full_run{tag_part}_{timestamp}"

    excel_path = REPORTS_DIR / f"{filename_base}.xlsx"
    csv_path = REPORTS_DIR / f"{filename_base}.csv"

    columns = [
        ("serial", "Serial / IMEI"),
        ("is_active", "Active?"),
        ("activation_date", "Activation Date"),
        ("activation_comment", "Activation Comment"),
        ("model_name", "Model Name"),
        ("specs", "RAM / Storage"),
        ("color", "Color"),
        ("certified_model", "Certified Model"),
        ("region", "Region"),
        ("group_name", "Group"),
        ("warranty_end", "Warranty End"),
        ("status", "Check Status"),
        ("last_checked_at", "Last Checked"),
        ("activated_at", "Activated At"),
    ]

    # 1. Create Excel Workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Full Run Report"

    # Header
    ws.append([col_title for _, col_title in columns])

    # Rows
    for dev in devices:
        row_vals = [dev.get(col_key, "") for col_key, _ in columns]
        ws.append(row_vals)

    _apply_sheet_styling(ws, is_newly_activated_sheet=False)
    wb.save(excel_path)

    # 2. Create Companion CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col_title for _, col_title in columns])
        for dev in devices:
            writer.writerow([dev.get(col_key, "") for col_key, _ in columns])

    return excel_path, csv_path


def generate_newly_activated_sheet(
    newly_activated_devices: list[dict[str, Any]],
    custom_tag: str = "",
) -> tuple[Path, Path]:
    """
    Generate Sheet #2: Newly Activated Devices Report.
    Only contains devices detected as active in this batch.
    Returns (excel_path, csv_path).
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_part = f"_{custom_tag.strip()}" if custom_tag.strip() else ""
    filename_base = f"oppo_newly_activated{tag_part}_{timestamp}"

    excel_path = REPORTS_DIR / f"{filename_base}.xlsx"
    csv_path = REPORTS_DIR / f"{filename_base}.csv"

    columns = [
        ("serial", "Serial / IMEI"),
        ("is_active", "Active?"),
        ("activation_date", "Activation Date"),
        ("activation_comment", "Activation Comment"),
        ("model_name", "Model Name"),
        ("specs", "RAM / Storage"),
        ("color", "Color"),
        ("group_name", "Group"),
        ("activated_at", "Detected Active At"),
        ("status", "Check Status"),
    ]

    # 1. Create Excel Workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Newly Activated"

    # Header
    ws.append([col_title for _, col_title in columns])

    if newly_activated_devices:
        for dev in newly_activated_devices:
            row_vals = [dev.get(col_key, "") for col_key, _ in columns]
            ws.append(row_vals)
    else:
        # Informative empty row
        empty_row = ["No newly activated devices detected in this run", "—", "—", "—", "—", "—", "—", "—", "—", "—"]
        ws.append(empty_row)

    _apply_sheet_styling(ws, is_newly_activated_sheet=True)
    wb.save(excel_path)

    # 2. Create Companion CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([col_title for _, col_title in columns])
        if newly_activated_devices:
            for dev in newly_activated_devices:
                writer.writerow([dev.get(col_key, "") for col_key, _ in columns])
        else:
            writer.writerow(["No newly activated devices detected in this run"] + ["—"] * 9)

    return excel_path, csv_path
