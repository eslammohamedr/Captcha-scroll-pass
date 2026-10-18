"""SQLite Database Manager for OPPO Device Verification & Sales Activation Monitor.

Thread-safe SQLite connection manager with WAL mode, device tracking,
group segmentation, run history, and dynamic user settings.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

DB_DIR = Path(__file__).resolve().parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "oppo_monitor.db"

_db_lock = threading.Lock()


def get_db_path() -> Path:
    return DB_PATH


@contextmanager
def get_db_cursor() -> Generator[sqlite3.Cursor, None, None]:
    """Provide a thread-safe SQLite connection cursor with Row factory and WAL mode."""
    with _db_lock:
        conn = sqlite3.connect(
            str(DB_PATH),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Initialize database tables, indexes, and default settings."""
    with get_db_cursor() as cur:
        # Devices table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial TEXT UNIQUE NOT NULL,
                group_name TEXT NOT NULL DEFAULT 'Default',
                is_active TEXT NOT NULL DEFAULT 'Unknown',
                activation_date TEXT DEFAULT '',
                activation_comment TEXT DEFAULT '',
                model_name TEXT DEFAULT '',
                specs TEXT DEFAULT '',
                color TEXT DEFAULT '',
                certified_model TEXT DEFAULT '',
                region TEXT DEFAULT '',
                warranty_end TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                raw_msg TEXT DEFAULT '',
                first_added_at TEXT NOT NULL,
                last_checked_at TEXT DEFAULT '',
                activated_at TEXT DEFAULT '',
                notified_activation INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT ''
            );
        """)

        # Indexes for fast querying & filtering
        cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_group ON devices(group_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_is_active ON devices(is_active);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_activated_at ON devices(activated_at);")

        # Groups table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                description TEXT DEFAULT ''
            );
        """)
        # Insert Default group
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT OR IGNORE INTO groups (name, created_at, description) VALUES (?, ?, ?);", ("Default", now, "Standard default group"))

        # Run history table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                target_group TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT DEFAULT '',
                total_checked INTEGER NOT NULL DEFAULT 0,
                newly_activated_count INTEGER NOT NULL DEFAULT 0,
                full_sheet_path TEXT DEFAULT '',
                newly_activated_sheet_path TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                summary_json TEXT DEFAULT '{}'
            );
        """)

        # Settings table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT DEFAULT ''
            );
        """)

        # Default configuration settings
        default_settings = [
            ("schedule_interval_minutes", "60", "How often to check unactivated serials automatically (in minutes, 0=disabled)"),
            ("schedule_enabled", "1", "1 if auto-scheduler is enabled, 0 if paused"),
            ("threads_count", "2", "Number of parallel browser worker threads (1-4)"),
            ("delay_seconds", "2.0", "Delay between serial checks in seconds"),
            ("fine_tune_offset", "0.0", "Slider offset adjust in pixels"),
            ("headful_mode", "0", "Launch visible browser windows on server (0=headless, 1=visible)"),
            ("auto_recover_failures", "1", "Automatically run recovery pass for errors at end of batch"),
            ("telegram_bot_token", "", "Telegram Bot Token from @BotFather"),
            ("telegram_chat_id", "", "Telegram Chat ID or Group ID to send alerts to"),
            ("telegram_notifications_enabled", "0", "1 to enable instant Telegram alerts, 0 to disable"),
            ("telegram_send_sheets", "1", "1 to automatically attach both Excel sheets to Telegram upon run completion, 0 to disable"),
        ]

        for k, v, d in default_settings:
            cur.execute("""
                INSERT OR IGNORE INTO settings (key, value, description)
                VALUES (?, ?, ?);
            """, (k, v, d))


# --- Settings Helpers ---

def get_setting(key: str, default: str = "") -> str:
    """Retrieve setting value by key."""
    with get_db_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?;", (key,))
        row = cur.fetchone()
        return str(row["value"]) if row else default


def get_all_settings() -> dict[str, str]:
    """Retrieve all system settings as a dictionary."""
    with get_db_cursor() as cur:
        cur.execute("SELECT key, value FROM settings;")
        return {str(r["key"]): str(r["value"]) for r in cur.fetchall()}


def set_setting(key: str, value: str) -> None:
    """Update or insert a system setting."""
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        """, (key, str(value)))


def set_multiple_settings(settings_dict: dict[str, Any]) -> None:
    """Bulk update multiple settings."""
    with get_db_cursor() as cur:
        for k, v in settings_dict.items():
            cur.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (k, str(v)))


# --- Group Helpers ---

def get_groups() -> list[dict[str, Any]]:
    """Get all groups along with counts of total and active devices."""
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT 
                g.name,
                g.created_at,
                g.description,
                COUNT(d.id) AS total_devices,
                SUM(CASE WHEN d.is_active = 'Yes' THEN 1 ELSE 0 END) AS active_devices,
                SUM(CASE WHEN d.is_active != 'Yes' OR d.is_active IS NULL THEN 1 ELSE 0 END) AS pending_devices
            FROM groups g
            LEFT JOIN devices d ON g.name = d.group_name
            GROUP BY g.name
            ORDER BY g.name ASC;
        """)
        return [dict(r) for r in cur.fetchall()]


def create_group(name: str, description: str = "") -> bool:
    """Create a new device group."""
    name = name.strip()
    if not name:
        return False
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT OR IGNORE INTO groups (name, created_at, description)
            VALUES (?, ?, ?);
        """, (name, now, description.strip()))
        return cur.rowcount > 0


def delete_group(name: str) -> bool:
    """Delete a group and reassign its devices to 'Default'."""
    name = name.strip()
    if name.lower() == "default":
        return False
    with get_db_cursor() as cur:
        cur.execute("UPDATE devices SET group_name = 'Default' WHERE group_name = ?;", (name,))
        cur.execute("DELETE FROM groups WHERE name = ?;", (name,))
        return cur.rowcount > 0


# --- Device Helpers ---

def upsert_device(serial: str, group_name: str = "Default", notes: str = "") -> dict[str, Any]:
    """Add a new serial or update its group."""
    serial = str(serial).strip().upper()
    group_name = str(group_name).strip() or "Default"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db_cursor() as cur:
        # Ensure group exists
        cur.execute("INSERT OR IGNORE INTO groups (name, created_at) VALUES (?, ?);", (group_name, now))

        cur.execute("""
            INSERT INTO devices (serial, group_name, first_added_at, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(serial) DO UPDATE SET
                group_name = CASE WHEN excluded.group_name != 'Default' THEN excluded.group_name ELSE devices.group_name END,
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE devices.notes END;
        """, (serial, group_name, now, notes))

        cur.execute("SELECT * FROM devices WHERE serial = ?;", (serial,))
        row = cur.fetchone()
        return dict(row) if row else {}


def bulk_upsert_devices(serials: list[str], group_name: str = "Default") -> tuple[int, int]:
    """Bulk insert serials into a group. Returns (added_count, total_provided)."""
    group_name = str(group_name).strip() or "Default"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    clean_serials = [s.strip().upper() for s in serials if s and s.strip()]

    with get_db_cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO groups (name, created_at) VALUES (?, ?);", (group_name, now))
        for sn in clean_serials:
            cur.execute("""
                INSERT INTO devices (serial, group_name, first_added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(serial) DO NOTHING;
            """, (sn, group_name, now))
            if cur.rowcount > 0:
                added += 1

    return added, len(clean_serials)


def update_device_result(serial: str, res: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """
    Update device verification result in database.
    Detects if the device transitioned from not active/unknown -> Active ('Yes').
    Returns (updated_device_dict, is_newly_activated).
    """
    serial = str(serial).strip().upper()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM devices WHERE serial = ?;", (serial,))
        existing = cur.fetchone()

        prev_is_active = existing["is_active"] if existing else "Unknown"
        new_is_active = str(res.get("is_active") or "Unknown").strip()

        # Activation transition detection:
        # If it was NOT active before (or Unknown/Pending) and now is "Yes"
        is_newly_activated = (prev_is_active != "Yes" and new_is_active == "Yes")

        activated_at = existing["activated_at"] if existing and existing["activated_at"] else ""
        if is_newly_activated:
            activated_at = now

        cur.execute("""
            UPDATE devices SET
                is_active = ?,
                activation_date = ?,
                activation_comment = ?,
                model_name = ?,
                specs = ?,
                color = ?,
                certified_model = ?,
                region = ?,
                warranty_end = ?,
                status = ?,
                raw_msg = ?,
                last_checked_at = ?,
                activated_at = ?
            WHERE serial = ?;
        """, (
            new_is_active,
            str(res.get("activation_date") or ""),
            str(res.get("activation_comment") or ""),
            str(res.get("model_name") or ""),
            str(res.get("specs") or ""),
            str(res.get("color") or ""),
            str(res.get("certified_model") or ""),
            str(res.get("region") or ""),
            str(res.get("warranty_end") or ""),
            str(res.get("status") or ""),
            str(res.get("raw_msg") or ""),
            now,
            activated_at,
            serial,
        ))

        cur.execute("SELECT * FROM devices WHERE serial = ?;", (serial,))
        updated_row = cur.fetchone()
        return dict(updated_row) if updated_row else {}, is_newly_activated


def mark_device_notified(serial: str) -> None:
    """Mark device as having had its activation notification sent."""
    with get_db_cursor() as cur:
        cur.execute("UPDATE devices SET notified_activation = 1 WHERE serial = ?;", (serial.strip().upper(),))


def get_devices(
    search: str = "",
    group_name: str = "",
    status_filter: str = "",
    newly_activated_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve devices with filtering, pagination, and total count."""
    query = "SELECT * FROM devices WHERE 1=1"
    count_query = "SELECT COUNT(*) AS cnt FROM devices WHERE 1=1"
    params: list[Any] = []

    if search.strip():
        search_term = f"%{search.strip().upper()}%"
        query += " AND (serial LIKE ? OR model_name LIKE ? OR color LIKE ?)"
        count_query += " AND (serial LIKE ? OR model_name LIKE ? OR color LIKE ?)"
        params.extend([search_term, search_term, search_term])

    if group_name.strip() and group_name.strip().lower() != "all":
        query += " AND group_name = ?"
        count_query += " AND group_name = ?"
        params.append(group_name.strip())

    if status_filter.strip() and status_filter.strip().lower() != "all":
        stat = status_filter.strip()
        if stat == "Active":
            query += " AND is_active = 'Yes'"
            count_query += " AND is_active = 'Yes'"
        elif stat == "Inactive":
            query += " AND is_active = 'No'"
            count_query += " AND is_active = 'No'"
        elif stat == "Pending":
            query += " AND (is_active = 'Unknown' OR status = 'Pending')"
            count_query += " AND (is_active = 'Unknown' OR status = 'Pending')"
        elif stat == "Error":
            query += " AND (status IN ('Error', 'Captcha Failed', 'No Response'))"
            count_query += " AND (status IN ('Error', 'Captcha Failed', 'No Response'))"

    if newly_activated_only:
        # Activated in the last 24 hours
        yesterday = (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        query += " AND is_active = 'Yes' AND activated_at >= ?"
        count_query += " AND is_active = 'Yes' AND activated_at >= ?"
        params.append(yesterday)

    query += " ORDER BY id DESC LIMIT ? OFFSET ?;"

    with get_db_cursor() as cur:
        # Get total matching count
        cur.execute(count_query, params[:-2] if newly_activated_only and len(params) > 1 else params)
        total_cnt = cur.fetchone()["cnt"]

        # Get rows
        cur.execute(query, params + [limit, offset])
        rows = [dict(r) for r in cur.fetchall()]

    return rows, total_cnt


def get_serials_to_check(
    mode: str = "all",
    group_name: str = "",
    selected_serials: list[str] | None = None,
    pending_only: bool = False,
) -> list[str]:
    """Retrieve list of serials matching specific run criteria."""
    query = "SELECT serial FROM devices WHERE 1=1"
    params: list[Any] = []

    if mode == "selected" and selected_serials:
        clean = [s.strip().upper() for s in selected_serials if s.strip()]
        placeholders = ",".join("?" for _ in clean)
        query += f" AND serial IN ({placeholders})"
        params.extend(clean)
    elif mode == "group" and group_name.strip():
        query += " AND group_name = ?"
        params.append(group_name.strip())

    if pending_only:
        # Exclude already verified active devices
        query += " AND is_active != 'Yes'"

    query += " ORDER BY id ASC;"

    with get_db_cursor() as cur:
        cur.execute(query, params)
        return [str(r["serial"]) for r in cur.fetchall()]


def delete_device(serial: str) -> bool:
    """Delete a single device by serial."""
    with get_db_cursor() as cur:
        cur.execute("DELETE FROM devices WHERE serial = ?;", (serial.strip().upper(),))
        return cur.rowcount > 0


def delete_multiple_devices(serials: list[str]) -> int:
    """Delete multiple devices by serials."""
    clean = [s.strip().upper() for s in serials if s.strip()]
    if not clean:
        return 0
    placeholders = ",".join("?" for _ in clean)
    with get_db_cursor() as cur:
        cur.execute(f"DELETE FROM devices WHERE serial IN ({placeholders});", clean)
        return cur.rowcount


def get_dashboard_stats() -> dict[str, Any]:
    """Compute high-level metrics for dashboard cards."""
    now = datetime.datetime.now()
    today_start = now.strftime("%Y-%m-%d 00:00:00")

    with get_db_cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_active = 'Yes' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN is_active = 'No' THEN 1 ELSE 0 END) AS inactive_count,
                SUM(CASE WHEN is_active = 'Unknown' OR status = 'Pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status IN ('Error', 'Captcha Failed', 'No Response') THEN 1 ELSE 0 END) AS error_count,
                SUM(CASE WHEN is_active = 'Yes' AND activated_at >= ? THEN 1 ELSE 0 END) AS activated_today_count
            FROM devices;
        """, (today_start,))
        row = cur.fetchone()

        cur.execute("SELECT COUNT(*) AS cnt FROM groups;")
        groups_cnt = cur.fetchone()["cnt"]

        return {
            "total": row["total"] or 0,
            "active": row["active_count"] or 0,
            "inactive": row["inactive_count"] or 0,
            "pending": row["pending_count"] or 0,
            "errors": row["error_count"] or 0,
            "activated_today": row["activated_today_count"] or 0,
            "groups_count": groups_cnt or 0,
        }


# --- Run History Helpers ---

def create_run_history_record(run_type: str, target_group: str = "", total: int = 0) -> int:
    """Create a new run history row when a verification job starts."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO run_history (run_type, target_group, started_at, total_checked, status)
            VALUES (?, ?, ?, ?, 'running');
        """, (run_type, target_group, now, total))
        return cur.lastrowid or 0


def update_run_history_record(
    run_id: int,
    status: str,
    total_checked: int,
    newly_activated: int,
    full_sheet: str = "",
    newly_activated_sheet: str = "",
    summary_json: str = "{}",
) -> None:
    """Complete a run history row."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_cursor() as cur:
        cur.execute("""
            UPDATE run_history SET
                status = ?,
                finished_at = ?,
                total_checked = ?,
                newly_activated_count = ?,
                full_sheet_path = ?,
                newly_activated_sheet_path = ?,
                summary_json = ?
            WHERE id = ?;
        """, (status, now, total_checked, newly_activated, full_sheet, newly_activated_sheet, summary_json, run_id))


def get_recent_runs(limit: int = 15) -> list[dict[str, Any]]:
    """Get recent verification runs."""
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM run_history ORDER BY id DESC LIMIT ?;", (limit,))
        return [dict(r) for r in cur.fetchall()]


# Initialize on import
init_db()
