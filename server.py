"""FastAPI REST API & Application Server for OPPO Device Verification & Sales Monitor.

Provides endpoints for mobile PWA frontend, live device management, group filtering,
scheduler controls, and report downloads.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import json
import queue
import time

import openpyxl
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agy_service
import database
import notifier
import reports
from worker_service import worker_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR / "reports"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="OPPO Device Verification & Activation Monitor API",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request Models & Authentication ---

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


def get_current_user_optional(request: Request) -> dict[str, Any] | None:
    """Extract and validate session token from cookies or Authorization header."""
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        return None
    return database.get_session(token)


def require_user(request: Request) -> dict[str, Any]:
    """Ensure request is authenticated with an active session."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required. Please sign in.")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    """Ensure request is authenticated and has admin privileges."""
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access forbidden: Admin access required for this action."
        )
    return user


class AddDeviceRequest(BaseModel):
    serial: str = Field(..., min_length=2)
    group_name: str = Field(default="Default")
    notes: str = Field(default="")


class BulkAddDevicesRequest(BaseModel):
    serials_text: str = Field(...)
    group_name: str = Field(default="Default")


class BulkDeleteRequest(BaseModel):
    serials: list[str]


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = Field(default="")


class RunJobRequest(BaseModel):
    mode: str = Field(default="all")  # 'all', 'group', 'selected', 'pending_only', 'errors'
    group_name: str = Field(default="")
    serials: list[str] = Field(default_factory=list)
    pending_only: bool = Field(default=False)
    errors_only: bool = Field(default=False)


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]


class TelegramTestRequest(BaseModel):
    token: str
    chat_id: str


class AgyRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    mode: str = Field("auto")
    send_telegram: bool = Field(True)


# --- Authentication Endpoints ---

@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    """Authenticate user credentials and issue 30-day session cookie."""
    user = database.authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = database.create_session(user["username"], user["role"])
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        },
    }


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    """Revoke user session token and clear cookie."""
    token = request.cookies.get("session_token")
    if token:
        database.delete_session(token)
    response.delete_cookie(key="session_token", path="/")
    return {"success": True}


@app.get("/api/auth/me")
def get_auth_me(request: Request) -> dict[str, Any]:
    """Return currently active authenticated user profile."""
    user = get_current_user_optional(request)
    if not user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    """Serve login page if unauthenticated, or redirect to dashboard."""
    user = get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    login_file = STATIC_DIR / "login.html"
    if login_file.exists():
        return HTMLResponse(content=login_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Login page not found</h1>")


# --- API Routes ---

@app.get("/api/stats")
def get_stats(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Retrieve high-level dashboard metrics."""
    stats = database.get_dashboard_stats()
    w_status = worker_service.get_status()
    stats["worker"] = w_status
    stats["next_run"] = w_status.get("next_run", {})
    return stats


@app.get("/api/devices/filter-options")
def get_device_filter_options(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Retrieve distinct models, colors, specs, and groups for device filtering."""
    return database.get_device_filter_options()


@app.get("/api/devices")
def list_devices(
    search: str = Query("", description="Search term for serial, model, color, specs"),
    group: str = Query("", description="Filter by group name"),
    status: str = Query("", description="Filter: Active, Inactive, Pending, Error, Not Run"),
    model: str = Query("", description="Filter by device model"),
    color: str = Query("", description="Filter by color"),
    specs: str = Query("", description="Filter by specs"),
    activation_date: str = Query("", description="Filter by activation date (DD-MM-YYYY or YYYY-MM-DD)"),
    newly_activated: bool = Query(False, description="Filter newly activated devices in last 24h"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """List devices with search, group filter, status filter, category filters, and pagination."""
    offset = (page - 1) * limit
    devices, total = database.get_devices(
        search=search,
        group_name=group,
        status_filter=status,
        model=model,
        color=color,
        specs=specs,
        activation_date=activation_date,
        newly_activated_only=newly_activated,
        limit=limit,
        offset=offset,
    )
    return {
        "devices": devices,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@app.post("/api/devices")
def add_device(req: AddDeviceRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Add or update a single serial."""
    device = database.upsert_device(req.serial, req.group_name, req.notes)
    return {"success": True, "device": device}


@app.post("/api/devices/bulk")
def bulk_add_devices(req: BulkAddDevicesRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Add multiple serials by pasting text."""
    lines = req.serials_text.replace(",", "\n").replace(";", "\n").splitlines()
    serials = [ln.strip() for ln in lines if ln.strip()]
    if not serials:
        raise HTTPException(status_code=400, detail="No valid serials found in input.")

    added, total = database.bulk_upsert_devices(serials, req.group_name)
    return {
        "success": True,
        "added": added,
        "total": total,
        "group": req.group_name,
        "message": f"Successfully loaded {added} new device(s) into group '{req.group_name}'.",
    }


def detect_serial_column_xlsx(ws: Any) -> tuple[list[dict[str, Any]], int]:
    """Analyze Excel sheet headers and rows to identify candidate serial/IMEI columns."""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], 0

    headers = [str(c or "").strip() for c in rows[0]]
    max_cols = max(len(r) for r in rows[:15]) if rows else 0
    columns_info = []
    candidate_col = -1

    keywords = ["serial", "imei", "sn", "serial number", "serial_no", "imei1", "imei2", "device_sn", "barcode", "serials"]

    for col_idx in range(max_cols):
        col_letter = openpyxl.utils.get_column_letter(col_idx + 1)
        h_name = headers[col_idx] if col_idx < len(headers) else f"Column {col_letter}"

        samples = []
        for r in rows[1:10]:
            if col_idx < len(r) and r[col_idx] is not None:
                val = str(r[col_idx]).strip()
                if val and val.lower() != "none":
                    samples.append(val)

        is_kw = any(kw in h_name.lower() for kw in keywords)
        has_imei = any(s.isdigit() and len(s) in (14, 15, 16) for s in samples)
        has_alphanumeric = any(len(s) >= 8 and s.isalnum() for s in samples)
        is_candidate = is_kw or has_imei or (has_alphanumeric and not any(" " in s for s in samples))

        columns_info.append({
            "index": col_idx,
            "letter": col_letter,
            "header": h_name or f"Column {col_letter}",
            "samples": samples[:3],
            "is_candidate": is_candidate,
        })

        if candidate_col == -1 and (is_kw or has_imei):
            candidate_col = col_idx

    if candidate_col == -1:
        for c in columns_info:
            if c["is_candidate"]:
                candidate_col = c["index"]
                break
        if candidate_col == -1:
            candidate_col = 0

    return columns_info, candidate_col


@app.post("/api/devices/upload-preview")
async def preview_upload_file(file: UploadFile = File(...), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Inspect an uploaded Excel or CSV/text file and return columns with samples."""
    contents = await file.read()
    filename = (file.filename or "").lower()

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
            ws = wb.active
            cols, detected = detect_serial_column_xlsx(ws)
            total_rows = max(0, ws.max_row - 1)
            return {
                "success": True,
                "is_excel": True,
                "filename": file.filename,
                "sheet_name": ws.title,
                "total_rows": total_rows,
                "columns": cols,
                "detected_index": detected,
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to inspect Excel file: {e}")
    else:
        try:
            text = contents.decode("utf-8-sig", errors="ignore")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return {
                "success": True,
                "is_excel": False,
                "filename": file.filename,
                "sheet_name": "Plain Text / CSV",
                "total_rows": len(lines),
                "columns": [{"index": 0, "letter": "A", "header": "Text Lines", "samples": lines[:3], "is_candidate": True}],
                "detected_index": 0,
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to inspect text file: {e}")


@app.post("/api/devices/upload")
async def upload_devices_file(
    file: UploadFile = File(...),
    group_name: str = Query("Default"),
    serial_column: str = Query("", description="Selected column index, letter (e.g. 'A', 'C'), or header name"),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Upload a .txt, .csv, or .xlsx file to bulk import serials from a targeted column."""
    contents = await file.read()
    filename = (file.filename or "").lower()
    serials: list[str] = []

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                raise HTTPException(status_code=400, detail="Excel file is empty.")

            cols, detected_idx = detect_serial_column_xlsx(ws)
            headers = [str(c or "").strip() for c in rows[0]]

            # Determine target column index
            target_idx = detected_idx
            if serial_column.strip():
                sc = serial_column.strip()
                if sc.isdigit():
                    target_idx = int(sc)
                elif len(sc) in (1, 2) and sc.isalpha():
                    target_idx = openpyxl.utils.column_index_from_string(sc.upper()) - 1
                else:
                    # Match by header name
                    for i, h in enumerate(headers):
                        if h.lower() == sc.lower():
                            target_idx = i
                            break

            # Extract serials starting from row 2 (skipping header)
            start_row = 1 if len(rows) > 1 else 0
            for row in rows[start_row:]:
                if not row or target_idx >= len(row):
                    continue
                cell_val = row[target_idx]
                if cell_val is None:
                    continue
                val = str(cell_val).strip()
                # Skip header-like keywords if present in data
                if val and len(val) >= 4 and not val.lower().startswith("serial") and not val.lower().startswith("imei"):
                    serials.append(val)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {e}")
    else:
        # Plain text or CSV
        try:
            text = contents.decode("utf-8-sig", errors="ignore")
            lines = text.splitlines()
            if lines and ("," in lines[0] or ";" in lines[0] or "\t" in lines[0]):
                delim = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else ",")
                headers = [h.strip() for h in lines[0].split(delim)]
                target_idx = 0
                if serial_column.strip() and serial_column.strip().isdigit():
                    target_idx = int(serial_column.strip())
                else:
                    for i, h in enumerate(headers):
                        if any(kw in h.lower() for kw in ["serial", "imei", "sn"]):
                            target_idx = i
                            break
                for ln in lines[1:]:
                    parts = ln.split(delim)
                    if target_idx < len(parts):
                        val = parts[target_idx].strip()
                        if val and len(val) >= 4 and not val.lower().startswith("serial"):
                            serials.append(val)
            else:
                for ln in lines:
                    val = ln.strip()
                    if val and len(val) >= 4 and not val.lower().startswith("serial") and not val.lower().startswith("imei"):
                        serials.append(val)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse text file: {e}")

    if not serials:
        raise HTTPException(status_code=400, detail="No valid serial numbers found in the selected column.")

    added, total = database.bulk_upsert_devices(serials, group_name)
    return {
        "success": True,
        "added": added,
        "total": total,
        "group": group_name,
        "filename": file.filename,
        "message": f"Successfully imported {added} new device(s) ({total} read) into group '{group_name}'.",
    }


@app.delete("/api/devices/{serial}")
def delete_device(serial: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Delete a single serial."""
    ok = database.delete_device(serial)
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found.")
    return {"success": True, "serial": serial}


@app.post("/api/devices/bulk-delete")
def bulk_delete_devices(req: BulkDeleteRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Delete multiple serials."""
    deleted_cnt = database.delete_multiple_devices(req.serials)
    return {"success": True, "deleted": deleted_cnt}


@app.get("/api/groups")
def list_groups(user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
    """List all device groups with metrics."""
    return database.get_groups()


@app.post("/api/groups")
def create_group(req: GroupCreateRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Create a new device group."""
    ok = database.create_group(req.name, req.description)
    if not ok:
        raise HTTPException(status_code=400, detail="Group name is invalid or already exists.")
    return {"success": True, "name": req.name}


@app.delete("/api/groups/{name}")
def delete_group(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Delete a group (reassigns devices to 'Default')."""
    ok = database.delete_group(name)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete Default group or group not found.")
    return {"success": True, "deleted": name}


@app.post("/api/run")
def trigger_run(req: RunJobRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """
    Trigger a verification check run.
    Can run:
    - 'all': check all devices
    - 'group': check devices in a specific group
    - 'selected': check only selected serials
    - 'pending_only': check only unactivated/pending devices
    - 'errors': check only devices with errors/captcha failures
    """
    ok, msg = worker_service.start_run(
        mode=req.mode,
        group_name=req.group_name,
        selected_serials=req.serials,
        pending_only=req.pending_only,
        errors_only=req.errors_only or (req.mode == "errors"),
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.post("/api/run/errors")
def trigger_run_errors(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Trigger a verification check run targeting only failed / error devices."""
    ok, msg = worker_service.start_run(mode="errors", errors_only=True)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.post("/api/run/stop")
def stop_run(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Request the running worker to stop."""
    ok = worker_service.stop_run()
    return {"success": ok, "message": "Stop signal sent." if ok else "No job currently running."}


@app.get("/api/run/status")
def run_status(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Get live worker execution status."""
    return worker_service.get_status()


@app.get("/api/settings")
def get_settings(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Retrieve system configuration settings (Telegram credentials masked for non-admin)."""
    settings = database.get_all_settings()
    if user.get("role") != "admin":
        if "telegram_bot_token" in settings and settings["telegram_bot_token"]:
            settings["telegram_bot_token"] = "••••••••••••••••"
        if "telegram_chat_id" in settings and settings["telegram_chat_id"]:
            settings["telegram_chat_id"] = "••••••••"
    return settings


@app.post("/api/settings")
def update_settings(req: UpdateSettingsRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Update system settings (intervals, telegram tokens, worker threads) - Admin Only."""
    database.set_multiple_settings(req.settings)
    return {"success": True, "settings": database.get_all_settings()}


@app.post("/api/telegram/test")
def test_telegram(req: TelegramTestRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Test Telegram Bot credentials - Admin Only."""
    ok, msg = notifier.test_telegram_connection(req.token, req.chat_id)
    return {"success": ok, "message": msg}


@app.get("/api/reports/history")
def run_history(limit: int = Query(15, ge=1, le=50), user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
    """List recent runs with links to download the 2 report sheets."""
    runs = database.get_recent_runs(limit=limit)
    for r in runs:
        if r.get("full_sheet_path"):
            r["full_sheet_name"] = Path(r["full_sheet_path"]).name
        if r.get("newly_activated_sheet_path"):
            r["newly_activated_sheet_name"] = Path(r["newly_activated_sheet_path"]).name
    return runs


@app.get("/api/reports/download/{filename}")
def download_report(filename: str, user: dict[str, Any] = Depends(require_user)) -> FileResponse:
    """Download any generated Excel or CSV report."""
    safe_filename = Path(filename).name
    file_path = REPORTS_DIR / safe_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found.")

    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if safe_filename.endswith(".csv"):
        media_type = "text/csv"

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type=media_type,
    )


@app.get("/api/database/info")
def get_database_info(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Retrieve detailed database engine, storage statistics, and device metrics."""
    return database.get_database_info()


@app.get("/api/database/download")
def download_database(admin: dict[str, Any] = Depends(require_admin)) -> FileResponse:
    """Download the raw SQLite database file as a full backup - Admin Only."""
    db_path = database.get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found.")
    return FileResponse(
        path=str(db_path),
        filename="oppo_monitor_backup.db",
        media_type="application/x-sqlite3",
    )


@app.post("/api/database/import-default")
def import_default_sheet(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Import or reset database from the default 696-serial sheet - Admin Only."""
    imported = database.import_sheet_to_database("oppo_results_20261017_1718.csv", group_name="Default", clear_test=True)
    return {
        "success": True,
        "imported": imported,
        "message": f"Successfully loaded {imported} serials from default sheet into database.",
        "info": database.get_database_info(),
    }


@app.get("/api/database/export-excel")
def export_database_excel(user: dict[str, Any] = Depends(require_user)) -> FileResponse:
    """Export the entire database of devices as a beautifully styled Excel (.xlsx) spreadsheet."""
    devices = database.get_all_devices_for_export()
    excel_path, _ = reports.generate_full_run_sheet(devices, custom_tag="database_export")
    return FileResponse(
        path=str(excel_path),
        filename=excel_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/database/export-csv")
def export_database_csv(user: dict[str, Any] = Depends(require_user)) -> FileResponse:
    """Export the entire database of devices as a CSV file."""
    devices = database.get_all_devices_for_export()
    _, csv_path = reports.generate_full_run_sheet(devices, custom_tag="database_export")
    return FileResponse(
        path=str(csv_path),
        filename=csv_path.name,
        media_type="text/csv",
    )


@app.get("/api/devices/export-excel")
def export_devices_excel(
    search: str = Query("", description="Search term"),
    group: str = Query("", description="Filter by group name"),
    status: str = Query("", description="Filter by status (Active, Inactive, Pending, Error, Not Run)"),
    model: str = Query("", description="Filter by model name"),
    color: str = Query("", description="Filter by color"),
    specs: str = Query("", description="Filter by specs"),
    activation_date: str = Query("", description="Filter by activation date"),
    newly_activated: bool = Query(False, description="Filter newly activated devices"),
    category_name: str = Query("", description="Optional custom category tag for filename"),
    user: dict[str, Any] = Depends(require_user),
) -> FileResponse:
    """Export devices matching category filters as a beautifully styled Excel (.xlsx) spreadsheet."""
    devices = database.get_filtered_devices_for_export(
        search=search,
        group_name=group,
        status_filter=status,
        model=model,
        color=color,
        specs=specs,
        activation_date=activation_date,
        newly_activated_only=newly_activated,
    )
    tag = category_name.strip()
    if not tag:
        parts = []
        if model and model.lower() != "all":
            parts.append(model)
        if group and group.lower() != "all":
            parts.append(f"group_{group}")
        if status and status.lower() != "all":
            parts.append(f"status_{status}")
        if color and color.lower() != "all":
            parts.append(f"color_{color}")
        if specs and specs.lower() != "all":
            parts.append(f"specs_{specs}")
        if newly_activated:
            parts.append("newly_activated")
        if search:
            parts.append(f"search_{search[:15]}")
        tag = "_".join(parts) if parts else "all_devices"

    excel_path, _ = reports.generate_filtered_devices_sheet(devices, custom_tag=tag, sheet_title=tag[:30])
    return FileResponse(
        path=str(excel_path),
        filename=excel_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/devices/export-categorized-excel")
def export_categorized_excel(
    by: str = Query("model", description="Category field to group by: 'model', 'group', 'status', 'specs', 'color'"),
    group: str = Query("", description="Optional group scope"),
    user: dict[str, Any] = Depends(require_user),
) -> FileResponse:
    """Export a multi-sheet Excel workbook with a Summary sheet and separate tabs for each category."""
    categorized = database.get_devices_categorized(category_field=by, group_name=group)
    label_map = {
        "model": "Model",
        "group": "Group",
        "status": "Status",
        "specs": "Specs",
        "color": "Color",
    }
    cat_label = label_map.get(by.lower(), by.title())
    excel_path, _ = reports.generate_categorized_workbook(categorized, category_label=cat_label)
    return FileResponse(
        path=str(excel_path),
        filename=excel_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/devices/export-csv")
def export_devices_csv(
    search: str = Query(""),
    group: str = Query(""),
    status: str = Query(""),
    model: str = Query(""),
    color: str = Query(""),
    specs: str = Query(""),
    activation_date: str = Query(""),
    newly_activated: bool = Query(False),
    category_name: str = Query(""),
    user: dict[str, Any] = Depends(require_user),
) -> FileResponse:
    """Export devices matching category filters as a CSV spreadsheet."""
    devices = database.get_filtered_devices_for_export(
        search=search,
        group_name=group,
        status_filter=status,
        model=model,
        color=color,
        specs=specs,
        activation_date=activation_date,
        newly_activated_only=newly_activated,
    )
    tag = category_name.strip() or (model if model and model.lower() != "all" else "filtered_devices")
    _, csv_path = reports.generate_filtered_devices_sheet(devices, custom_tag=tag)
    return FileResponse(
        path=str(csv_path),
        filename=csv_path.name,
        media_type="text/csv",
    )



# --- AGY Remote Terminal YOLO Engine Routes (Admin Only) ---

@app.post("/api/terminal/agy/run")
def run_agy_command(req: AgyRunRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Execute an AGY turn in YOLO mode (skip all permissions) - Admin Only."""
    ok, msg = agy_service.agy_runner.run_prompt(
        prompt=req.prompt,
        mode=req.mode,
        send_telegram=req.send_telegram,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.get("/api/terminal/agy/status")
def get_agy_status(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Get live execution state and metadata - Admin Only."""
    return agy_service.agy_runner.get_status()


@app.get("/api/terminal/agy/logs")
def get_agy_logs(since: int = Query(0, ge=0), admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Poll logs since a given index - Admin Only."""
    logs = agy_service.agy_runner.get_recent_logs(since_index=since)
    status = agy_service.agy_runner.get_status()
    return {"logs": logs, "status": status}


@app.get("/api/terminal/agy/stream")
async def stream_agy_output(request: Request, admin: dict[str, Any] = Depends(require_admin)):
    """Server-Sent Events (SSE) live stream of terminal output - Admin Only."""
    def event_generator():
        q = agy_service.agy_runner.subscribe()
        try:
            initial_logs = agy_service.agy_runner.get_recent_logs()
            for log in initial_logs[-50:]:
                yield f"event: line\ndata: {json.dumps(log)}\n\n"

            while True:
                try:
                    log = q.get(timeout=1.0)
                    yield f"event: line\ndata: {json.dumps(log)}\n\n"
                except queue.Empty:
                    status = agy_service.agy_runner.get_status()
                    yield f"event: status\ndata: {json.dumps(status)}\n\n"
                    if not status["is_running"] and q.empty():
                        time.sleep(0.5)
                        if not agy_service.agy_runner.is_running and q.empty():
                            yield f"event: done\ndata: {json.dumps(status)}\n\n"
                            break
        finally:
            agy_service.agy_runner.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/terminal/agy/stop")
def stop_agy_command(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Stop running AGY process - Admin Only."""
    ok = agy_service.agy_runner.stop()
    return {"success": ok}


@app.get("/api/launcher/download")
def download_launcher(
    request: Request,
    platform: str = Query("windows", description="'windows' for .url or 'linux' for .desktop"),
    user: dict[str, Any] = Depends(require_user),
) -> Response:
    """Generate and return a ready-to-use desktop shortcut for 1-tap launching."""
    host = request.headers.get("host") or "localhost:8000"
    scheme = "https" if request.url.scheme == "https" else "http"
    base_url = f"{scheme}://{host}/"

    if platform == "linux":
        content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=OPPO Sales Monitor\n"
            "Comment=OPPO Sales Activation Monitor\n"
            f"Exec=xdg-open {base_url}\n"
            f"Icon={base_url}static/icon-192.png\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
        )
        return Response(
            content=content,
            media_type="application/x-desktop",
            headers={"Content-Disposition": 'attachment; filename="OPPO_Sales_Monitor.desktop"'}
        )
    else:
        # Windows .url Internet Shortcut
        content = (
            "[InternetShortcut]\r\n"
            f"URL={base_url}\r\n"
            "IconIndex=0\r\n"
            f"IconFile={base_url}static/icon-192.png\r\n"
        )
        return Response(
            content=content,
            media_type="application/x-mswinurl",
            headers={"Content-Disposition": 'attachment; filename="OPPO_Sales_Monitor.url"'}
        )


# --- Static Files & Root View ---

@app.get("/", response_class=HTMLResponse)
def index_html(request: Request) -> Response:
    """Serve mobile PWA homepage if authenticated, else redirect to login."""
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>OPPO Activation Monitor Server is running!</h1><p>Visit /api/docs for API documentation.</p>")


# Mount static directory for JS, CSS, manifest, icons
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
