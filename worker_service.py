"""Background Worker Service & Scheduled Automation Engine for OPPO Monitor.

Coordinates Playwright OpenCV worker threads, periodic scheduler polling,
state-transition detection (Inactive -> Active), and dispatching notifications
with the two generated report sheets.
"""

from __future__ import annotations

import datetime
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

import database
import notifier
import reports
from check_oppo_cv import check_oppo_serial

logger = logging.getLogger("oppo_worker")


class WorkerService:
    _instance: WorkerService | None = None
    _lock = threading.Lock()

    def __new__(cls) -> WorkerService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_service()
            return cls._instance

    def _init_service(self) -> None:
        self.is_running = False
        self.stop_requested = threading.Event()
        self.current_run_id: int | None = None
        self.run_type = ""
        self.target_group = ""
        self.total_items = 0
        self.completed_items = 0
        self.current_serial = ""
        self.newly_activated_count = 0
        self.start_timestamp: float = 0.0
        self.logs: list[dict[str, Any]] = []
        self._logs_lock = threading.Lock()
        self.last_run_summary: dict[str, Any] | None = None

        # Scheduler state
        self.last_scheduled_check_time: float = time.time()
        self.scheduler_thread: threading.Thread | None = None
        self._start_scheduler_daemon()

    def add_log(self, level: str, message: str) -> None:
        """Add a timestamped log entry to the in-memory ring buffer (max 100 items)."""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": now_str, "level": level, "message": message}
        with self._logs_lock:
            self.logs.append(entry)
            if len(self.logs) > 100:
                self.logs.pop(0)
        logger.info("[%s] %s: %s", now_str, level.upper(), message)

    def get_next_run_info(self) -> dict[str, Any]:
        """Compute the next scheduled run time and countdown."""
        settings = database.get_all_settings()
        enabled = settings.get("schedule_enabled", "0") in ("1", "true", "True")
        try:
            interval_min = int(settings.get("schedule_interval_minutes", "60"))
        except ValueError:
            interval_min = 60

        if not enabled or interval_min <= 0:
            return {
                "enabled": False,
                "interval_minutes": interval_min,
                "next_run_timestamp": None,
                "next_run_clock": "",
                "remaining_seconds": 0,
                "remaining_str": "Paused / Disabled",
                "display_text": "Scheduler Paused",
            }

        interval_seconds = interval_min * 60
        now = time.time()
        next_time = self.last_scheduled_check_time + interval_seconds

        if next_time < now and not self.is_running:
            remaining = 0
            rem_str = "due now"
        elif next_time < now and self.is_running:
            remaining = interval_seconds
            next_time = now + interval_seconds
            m_rem = remaining // 60
            s_rem = remaining % 60
            rem_str = f"in {m_rem}m {s_rem}s" if m_rem > 0 else f"in {s_rem}s"
        else:
            remaining = max(0, int(next_time - now))
            m_rem = remaining // 60
            s_rem = remaining % 60
            rem_str = f"in {m_rem}m {s_rem}s" if m_rem > 0 else f"in {s_rem}s"

        dt_next = datetime.datetime.fromtimestamp(next_time)
        next_run_clock = dt_next.strftime("%H:%M:%S")

        return {
            "enabled": True,
            "interval_minutes": interval_min,
            "next_run_timestamp": int(next_time),
            "next_run_clock": next_run_clock,
            "remaining_seconds": remaining,
            "remaining_str": rem_str,
            "display_text": f"at {next_run_clock} ({rem_str})",
        }

    def get_status(self) -> dict[str, Any]:
        """Return the current execution state for live UI updates."""
        elapsed = 0.0
        if self.is_running and self.start_timestamp > 0:
            elapsed = time.time() - self.start_timestamp

        pct = int((self.completed_items / max(1, self.total_items)) * 100) if self.total_items > 0 else 0

        with self._logs_lock:
            recent_logs = list(self.logs[-25:])

        return {
            "is_running": self.is_running,
            "run_id": self.current_run_id,
            "run_type": self.run_type,
            "target_group": self.target_group,
            "total": self.total_items,
            "completed": self.completed_items,
            "pct": min(100, pct),
            "current_serial": self.current_serial,
            "newly_activated": self.newly_activated_count,
            "elapsed_seconds": int(elapsed),
            "next_run": self.get_next_run_info(),
            "logs": recent_logs,
            "last_summary": self.last_run_summary,
        }

    def start_run(
        self,
        mode: str = "all",
        group_name: str = "",
        selected_serials: list[str] | None = None,
        pending_only: bool = False,
    ) -> tuple[bool, str]:
        """
        Start a new verification run.
        Modes: 'all', 'group', 'selected', 'pending_only'.
        """
        if self.is_running:
            return False, "A verification run is already in progress. Please wait or stop it first."

        serials = database.get_serials_to_check(
            mode=mode,
            group_name=group_name,
            selected_serials=selected_serials,
            pending_only=pending_only,
        )

        if not serials:
            return False, "No devices match the specified criteria or all are already active."

        self.is_running = True
        self.stop_requested.clear()
        self.run_type = mode
        self.target_group = group_name
        self.total_items = len(serials)
        self.completed_items = 0
        self.current_serial = ""
        self.newly_activated_count = 0
        self.start_timestamp = time.time()

        # Record in database run history
        self.current_run_id = database.create_run_history_record(
            run_type=mode,
            target_group=group_name,
            total=len(serials),
        )

        self.add_log("info", f"Started verification run [{mode}]. Target serials: {len(serials)}")

        # Launch background executor thread
        t = threading.Thread(
            target=self._execute_batch,
            args=(serials, mode, group_name),
            daemon=True,
        )
        t.start()
        return True, f"Started verification for {len(serials)} device(s)."

    def stop_run(self) -> bool:
        """Signal running workers to stop execution."""
        if not self.is_running:
            return False
        self.stop_requested.set()
        self.add_log("warning", "Stop requested by user. Finishing active browser requests...")
        return True

    def _execute_batch(self, serials: list[str], mode: str, group_name: str) -> None:
        """Coordinator thread executing Playwright worker pool and handling results."""
        settings = database.get_all_settings()
        req_threads = int(settings.get("threads_count", "2"))
        delay = float(settings.get("delay_seconds", "2.0"))
        offset = float(settings.get("fine_tune_offset", "0.0"))
        headful = settings.get("headful_mode", "0") in ("1", "true")
        auto_recover = settings.get("auto_recover_failures", "1") in ("1", "true")

        num_workers = max(1, min(req_threads, len(serials)))
        work_queue: queue.Queue[tuple[int, str]] = queue.Queue()
        for idx, sn in enumerate(serials, start=1):
            work_queue.put((idx, sn))

        newly_activated_devices: list[dict[str, Any]] = []
        newly_act_lock = threading.Lock()
        checked_serials_order = list(serials)
        results_cache: dict[str, dict[str, Any]] = {}
        cache_lock = threading.Lock()

        def _worker_thread(w_id: int) -> None:
            try:
                with sync_playwright() as p:
                    launch_args = ["--start-maximized"] if headful else []
                    browser = None
                    try:
                        browser = p.chromium.launch(headless=not headful, args=launch_args)
                    except Exception:
                        try:
                            browser = p.chromium.launch(channel="msedge", headless=not headful, args=launch_args)
                        except Exception:
                            try:
                                browser = p.chromium.launch(channel="chrome", headless=not headful, args=launch_args)
                            except Exception as e_br:
                                self.add_log("error", f"[Worker #{w_id}] Browser launch error: {e_br}")
                                return

                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        viewport={"width": 1280, "height": 820},
                    )
                    page = context.new_page()

                    while not self.stop_requested.is_set():
                        try:
                            idx, sn = work_queue.get_nowait()
                        except queue.Empty:
                            break

                        self.current_serial = sn
                        w_tag = f"[W#{w_id}]"

                        try:
                            res = check_oppo_serial(page, sn, fine_tune_offset=offset)
                        except Exception as exc:
                            res = {
                                "serial": sn,
                                "is_active": "Unknown",
                                "activation_date": "",
                                "activation_comment": "",
                                "model_name": "",
                                "specs": "",
                                "color": "",
                                "certified_model": "",
                                "region": "",
                                "warranty_end": "",
                                "status": "Error",
                                "raw_msg": str(exc),
                            }

                        # Auto-retry once on transient Captcha Failure
                        if res.get("status") in ["Captcha Failed", "Error"] and not self.stop_requested.is_set():
                            time.sleep(2.0)
                            try:
                                res_retry = check_oppo_serial(page, sn, fine_tune_offset=offset)
                                if res_retry.get("status") == "Found":
                                    res = res_retry
                            except Exception:
                                pass

                        # Update database & check for activation transition
                        updated_dev, is_newly_activated = database.update_device_result(sn, res)

                        with cache_lock:
                            results_cache[sn] = updated_dev

                        if is_newly_activated:
                            with newly_act_lock:
                                newly_activated_devices.append(updated_dev)
                                self.newly_activated_count = len(newly_activated_devices)
                            self.add_log("success", f"🚨 ACTIVATED: {sn} ({updated_dev.get('model_name') or 'OPPO'})!")
                            # Send instant alert to Telegram!
                            try:
                                notifier.notify_activation(updated_dev, previous_status="Inactive")
                            except Exception as e_notif:
                                logger.error("Notification dispatch error: %s", e_notif)
                        else:
                            act_val = res.get("is_active")
                            model_str = res.get("model_name") or ""
                            self.add_log("info", f"{w_tag} [{idx}/{len(serials)}] {sn} -> Active: {act_val} {model_str}")

                        self.completed_items += 1
                        work_queue.task_done()

                        if not self.stop_requested.is_set() and delay > 0:
                            time.sleep(delay)

                    try:
                        browser.close()
                    except Exception:
                        pass
            except Exception as e_w:
                self.add_log("error", f"[Worker #{w_id}] Thread error: {e_w}")

        # Spawn workers
        threads: list[threading.Thread] = []
        for w_idx in range(1, num_workers + 1):
            if self.stop_requested.is_set():
                break
            t = threading.Thread(target=_worker_thread, args=(w_idx,), daemon=True)
            threads.append(t)
            t.start()
            if w_idx < num_workers:
                time.sleep(1.0)

        for t in threads:
            t.join()

        # Automatic Recovery Phase
        if auto_recover and not self.stop_requested.is_set():
            failed_serials = [
                sn for sn in serials
                if sn not in results_cache or str(results_cache[sn].get("status") or "").strip() != "Found"
            ]
            if failed_serials:
                self.add_log("warning", f"🔄 [Auto-Recovery] Found {len(failed_serials)} failed device(s). Cooling down 4s before retry...")
                time.sleep(4.0)

                rec_queue: queue.Queue[tuple[int, str]] = queue.Queue()
                for i_rec, s_rec in enumerate(failed_serials, start=1):
                    rec_queue.put((i_rec, s_rec))

                def _rec_thread(r_id: int) -> None:
                    try:
                        with sync_playwright() as p:
                            browser = p.chromium.launch(headless=not headful)
                            context = browser.new_context(
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                                viewport={"width": 1280, "height": 820},
                            )
                            page = context.new_page()

                            while not self.stop_requested.is_set():
                                try:
                                    r_idx, sn = rec_queue.get_nowait()
                                except queue.Empty:
                                    break

                                try:
                                    res_new = check_oppo_serial(page, sn, fine_tune_offset=offset)
                                except Exception as exc:
                                    res_new = {"serial": sn, "status": "Error", "raw_msg": str(exc), "is_active": "Unknown"}

                                updated_dev, is_newly = database.update_device_result(sn, res_new)
                                with cache_lock:
                                    results_cache[sn] = updated_dev

                                if is_newly:
                                    with newly_act_lock:
                                        newly_activated_devices.append(updated_dev)
                                        self.newly_activated_count = len(newly_activated_devices)
                                    self.add_log("success", f"🚨 [Recovery] ACTIVATED: {sn}!")
                                    notifier.notify_activation(updated_dev, previous_status="Inactive")
                                else:
                                    stat_str = res_new.get("status")
                                    self.add_log("info", f"[Recovery #{r_id}] {sn} -> {stat_str}")

                                rec_queue.task_done()
                                time.sleep(max(2.0, delay))

                            browser.close()
                    except Exception as e_r:
                        self.add_log("error", f"[Recovery #{r_id}] Error: {e_r}")

                rec_t = threading.Thread(target=_rec_thread, args=(1,), daemon=True)
                rec_t.start()
                rec_t.join()

        # Build full devices list in original order
        final_devices_list: list[dict[str, Any]] = []
        for sn in checked_serials_order:
            dev = results_cache.get(sn)
            if not dev:
                devs, _ = database.get_devices(search=sn, limit=1)
                dev = devs[0] if devs else {"serial": sn, "is_active": "Unknown", "status": "Not Checked"}
            final_devices_list.append(dev)

        # Generate the TWO distinct Excel & CSV report sheets requested
        custom_tag = group_name if group_name else mode
        full_sheet_xlsx, full_sheet_csv = reports.generate_full_run_sheet(final_devices_list, custom_tag=custom_tag)
        new_sheet_xlsx, new_sheet_csv = reports.generate_newly_activated_sheet(newly_activated_devices, custom_tag=custom_tag)

        # Calculate final summary metrics
        total_time = max(0.0, time.time() - self.start_timestamp)
        hours = int(total_time // 3600)
        minutes = int((total_time % 3600) // 60)
        seconds = int(total_time % 60)
        elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        active_cnt = sum(1 for d in final_devices_list if d.get("is_active") == "Yes")
        inactive_cnt = sum(1 for d in final_devices_list if d.get("is_active") == "No")
        error_cnt = sum(1 for d in final_devices_list if d.get("status") in ["Error", "Captcha Failed", "No Response"])

        summary = {
            "run_id": self.current_run_id,
            "run_type": mode,
            "target_group": group_name,
            "total_checked": len(final_devices_list),
            "newly_activated": len(newly_activated_devices),
            "active_count": active_cnt,
            "inactive_count": inactive_cnt,
            "error_count": error_cnt,
            "elapsed_seconds": int(total_time),
            "elapsed_str": elapsed_str,
            "full_sheet_excel": full_sheet_xlsx.name,
            "full_sheet_csv": full_sheet_csv.name,
            "newly_activated_excel": new_sheet_xlsx.name,
            "newly_activated_csv": new_sheet_csv.name,
        }
        self.last_run_summary = summary

        # Complete run history row in database
        if self.current_run_id:
            import json
            final_status = "stopped" if self.stop_requested.is_set() else "completed"
            database.update_run_history_record(
                run_id=self.current_run_id,
                status=final_status,
                total_checked=len(final_devices_list),
                newly_activated=len(newly_activated_devices),
                full_sheet=str(full_sheet_xlsx),
                newly_activated_sheet=str(new_sheet_xlsx),
                summary_json=json.dumps(summary),
            )

        self.add_log(
            "success",
            f"Run finished in {elapsed_str}! Processed: {len(final_devices_list)} | Newly Activated: {len(newly_activated_devices)}",
        )

        # Notify Telegram with summary and attach BOTH sheets!
        try:
            notifier.notify_run_completion(
                summary=summary,
                full_sheet_path=full_sheet_xlsx,
                newly_activated_sheet_path=new_sheet_xlsx,
            )
        except Exception as e_tg:
            logger.error("Error sending run completion Telegram notification: %s", e_tg)

        self.is_running = False

    def _start_scheduler_daemon(self) -> None:
        """Start background daemon checking recurring intervals every 30 seconds."""
        def _scheduler_loop() -> None:
            while True:
                time.sleep(30.0)
                try:
                    settings = database.get_all_settings()
                    enabled = settings.get("schedule_enabled", "0") in ("1", "true", "True")
                    interval_min = int(settings.get("schedule_interval_minutes", "60"))

                    if not enabled or interval_min <= 0:
                        continue

                    # If not currently running, check if elapsed interval has passed
                    interval_seconds = interval_min * 60
                    now = time.time()
                    if (now - self.last_scheduled_check_time) >= interval_seconds:
                        if not self.is_running:
                            self.last_scheduled_check_time = now
                            self.add_log("info", f"⏰ [Scheduler] Triggering recurring check (every {interval_min}m)...")
                            # Check only unactivated / pending devices
                            self.start_run(mode="all", pending_only=True)
                except Exception as exc:
                    logger.error("Scheduler daemon loop error: %s", exc)

        self.scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        self.scheduler_thread.start()


# Global Singleton
worker_service = WorkerService()
