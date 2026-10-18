"""FastAPI REST API & Application Server for OPPO Device Verification & Sales Monitor.

Provides endpoints for mobile PWA frontend, live device management, group filtering,
scheduler controls, and report downloads.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import openpyxl
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


# --- Request Models ---

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
    mode: str = Field(default="all")  # 'all', 'group', 'selected', 'pending_only'
    group_name: str = Field(default="")
    serials: list[str] = Field(default_factory=list)
    pending_only: bool = Field(default=False)


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]


class TelegramTestRequest(BaseModel):
    token: str
    chat_id: str


# --- API Routes ---

@app.get("/api/stats")
def get_stats() -> dict[str, Any]:
    """Retrieve high-level dashboard metrics."""
    stats = database.get_dashboard_stats()
    w_status = worker_service.get_status()
    stats["worker"] = w_status
    stats["next_run"] = w_status.get("next_run", {})
    return stats


@app.get("/api/devices")
def list_devices(
    search: str = Query("", description="Search term for serial, model, color"),
    group: str = Query("", description="Filter by group name"),
    status: str = Query("", description="Filter: Active, Inactive, Pending, Error"),
    newly_activated: bool = Query(False, description="Filter newly activated devices in last 24h"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """List devices with search, group filter, status filter, and pagination."""
    offset = (page - 1) * limit
    devices, total = database.get_devices(
        search=search,
        group_name=group,
        status_filter=status,
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
def add_device(req: AddDeviceRequest) -> dict[str, Any]:
    """Add or update a single serial."""
    device = database.upsert_device(req.serial, req.group_name, req.notes)
    return {"success": True, "device": device}


@app.post("/api/devices/bulk")
def bulk_add_devices(req: BulkAddDevicesRequest) -> dict[str, Any]:
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


@app.post("/api/devices/upload")
async def upload_devices_file(
    file: UploadFile = File(...),
    group_name: str = Query("Default"),
) -> dict[str, Any]:
    """Upload a .txt, .csv, or .xlsx file to bulk import serials."""
    contents = await file.read()
    filename = (file.filename or "").lower()
    serials: list[str] = []

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                if not row:
                    continue
                for cell in row:
                    val = str(cell).strip()
                    if val and len(val) >= 4 and not val.lower().startswith("serial") and not val.lower().startswith("imei"):
                        serials.append(val)
                        break  # Take first valid cell per row
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {e}")
    else:
        # Plain text or CSV
        try:
            text = contents.decode("utf-8-sig", errors="ignore")
            lines = text.replace(",", "\n").replace(";", "\n").splitlines()
            for ln in lines:
                val = ln.strip()
                if val and not val.lower().startswith("serial") and not val.lower().startswith("imei"):
                    serials.append(val)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse text file: {e}")

    if not serials:
        raise HTTPException(status_code=400, detail="No valid serial numbers found in uploaded file.")

    added, total = database.bulk_upsert_devices(serials, group_name)
    return {
        "success": True,
        "added": added,
        "total": total,
        "group": group_name,
        "filename": file.filename,
        "message": f"Imported {added} new device(s) from {file.filename}.",
    }


@app.delete("/api/devices/{serial}")
def delete_device(serial: str) -> dict[str, Any]:
    """Delete a single serial."""
    ok = database.delete_device(serial)
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found.")
    return {"success": True, "serial": serial}


@app.post("/api/devices/bulk-delete")
def bulk_delete_devices(req: BulkDeleteRequest) -> dict[str, Any]:
    """Delete multiple serials."""
    deleted_cnt = database.delete_multiple_devices(req.serials)
    return {"success": True, "deleted": deleted_cnt}


@app.get("/api/groups")
def list_groups() -> list[dict[str, Any]]:
    """List all device groups with metrics."""
    return database.get_groups()


@app.post("/api/groups")
def create_group(req: GroupCreateRequest) -> dict[str, Any]:
    """Create a new device group."""
    ok = database.create_group(req.name, req.description)
    if not ok:
        raise HTTPException(status_code=400, detail="Group name is invalid or already exists.")
    return {"success": True, "name": req.name}


@app.delete("/api/groups/{name}")
def delete_group(name: str) -> dict[str, Any]:
    """Delete a group (reassigns devices to 'Default')."""
    ok = database.delete_group(name)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete Default group or group not found.")
    return {"success": True, "deleted": name}


@app.post("/api/run")
def trigger_run(req: RunJobRequest) -> dict[str, Any]:
    """
    Trigger a verification check run.
    Can run:
    - 'all': check all devices
    - 'group': check devices in a specific group
    - 'selected': check only selected serials
    - 'pending_only': check only unactivated/pending devices
    """
    ok, msg = worker_service.start_run(
        mode=req.mode,
        group_name=req.group_name,
        selected_serials=req.serials,
        pending_only=req.pending_only,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.post("/api/run/stop")
def stop_run() -> dict[str, Any]:
    """Request the running worker to stop."""
    ok = worker_service.stop_run()
    return {"success": ok, "message": "Stop signal sent." if ok else "No job currently running."}


@app.get("/api/run/status")
def run_status() -> dict[str, Any]:
    """Get live worker execution status."""
    return worker_service.get_status()


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Retrieve system configuration settings."""
    return database.get_all_settings()


@app.post("/api/settings")
def update_settings(req: UpdateSettingsRequest) -> dict[str, Any]:
    """Update system settings (intervals, telegram tokens, worker threads)."""
    database.set_multiple_settings(req.settings)
    return {"success": True, "settings": database.get_all_settings()}


@app.post("/api/telegram/test")
def test_telegram(req: TelegramTestRequest) -> dict[str, Any]:
    """Test Telegram Bot credentials."""
    ok, msg = notifier.test_telegram_connection(req.token, req.chat_id)
    return {"success": ok, "message": msg}


@app.get("/api/reports/history")
def run_history(limit: int = Query(15, ge=1, le=50)) -> list[dict[str, Any]]:
    """List recent runs with links to download the 2 report sheets."""
    runs = database.get_recent_runs(limit=limit)
    for r in runs:
        if r.get("full_sheet_path"):
            r["full_sheet_name"] = Path(r["full_sheet_path"]).name
        if r.get("newly_activated_sheet_path"):
            r["newly_activated_sheet_name"] = Path(r["newly_activated_sheet_path"]).name
    return runs


@app.get("/api/reports/download/{filename}")
def download_report(filename: str) -> FileResponse:
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
def get_database_info() -> dict[str, Any]:
    """Retrieve detailed database engine, storage statistics, and device metrics."""
    return database.get_database_info()


@app.get("/api/database/download")
def download_database() -> FileResponse:
    """Download the raw SQLite database file as a full backup."""
    db_path = database.get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found.")
    return FileResponse(
        path=str(db_path),
        filename="oppo_monitor_backup.db",
        media_type="application/x-sqlite3",
    )


@app.post("/api/database/import-default")
def import_default_sheet() -> dict[str, Any]:
    """Import or reset database from the default 696-serial sheet."""
    imported = database.import_sheet_to_database("oppo_results_20261017_1718.csv", group_name="Default", clear_test=True)
    return {
        "success": True,
        "imported": imported,
        "message": f"Successfully loaded {imported} serials from default sheet into database.",
        "info": database.get_database_info(),
    }


@app.get("/api/database/export-excel")
def export_database_excel() -> FileResponse:
    """Export the entire database of devices as a beautifully styled Excel (.xlsx) spreadsheet."""
    devices = database.get_all_devices_for_export()
    excel_path, _ = reports.generate_full_run_sheet(devices, custom_tag="database_export")
    return FileResponse(
        path=str(excel_path),
        filename=excel_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/database/export-csv")
def export_database_csv() -> FileResponse:
    """Export the entire database of devices as a CSV file."""
    devices = database.get_all_devices_for_export()
    _, csv_path = reports.generate_full_run_sheet(devices, custom_tag="database_export")
    return FileResponse(
        path=str(csv_path),
        filename=csv_path.name,
        media_type="text/csv",
    )


# --- Static Files & Root View ---

@app.get("/", response_class=HTMLResponse)
def index_html() -> HTMLResponse:
    """Serve mobile PWA homepage."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>OPPO Activation Monitor Server is running!</h1><p>Visit /api/docs for API documentation.</p>")


# Mount static directory for JS, CSS, manifest, icons
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
