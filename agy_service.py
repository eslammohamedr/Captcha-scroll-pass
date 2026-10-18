"""AGY (Antigravity CLI) Remote Execution Engine.

Executes `agy` in the current workspace with `--dangerously-skip-permissions`
(YOLO mode), streams live terminal stdout/stderr to the web interface via SSE
or polling, and sends notifications upon completion.
"""

from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Generator

import notifier

logger = logging.getLogger("agy_service")

WORKSPACE_DIR = Path("/home/dell/Desktop/automation/Captcha-scroll-pass").resolve()
AGY_BIN = "/home/dell/.local/bin/agy"


class AgyRunner:
    """Manages asynchronous agy execution with live streaming."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.is_running: bool = False
        self.current_prompt: str = ""
        self.started_at: str = ""
        self.output_buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=2000)
        self.subscribers: list[queue.Queue[dict[str, Any]]] = []
        self.exit_code: int | None = None
        self.last_run_summary: dict[str, Any] = {}
        self._recent_broadcasts: collections.deque[str] = collections.deque(maxlen=200)

    def get_status(self) -> dict[str, Any]:
        """Return live status of the AGY process."""
        with self.lock:
            return {
                "is_running": self.is_running,
                "current_prompt": self.current_prompt,
                "started_at": self.started_at,
                "exit_code": self.exit_code,
                "total_log_lines": len(self.output_buffer),
                "last_run": self.last_run_summary,
            }

    def get_recent_logs(self, since_index: int = 0) -> list[dict[str, Any]]:
        """Retrieve logs starting from a specific index."""
        with self.lock:
            logs = list(self.output_buffer)
            if 0 <= since_index < len(logs):
                return logs[since_index:]
            return logs

    def run_prompt(self, prompt: str, mode: str = "auto", send_telegram: bool = True) -> tuple[bool, str]:
        """Start an AGY turn in YOLO mode."""
        prompt = prompt.strip()
        if not prompt:
            return False, "Prompt cannot be empty."

        with self.lock:
            if self.is_running:
                return False, f"AGY is currently busy executing: '{self.current_prompt[:40]}...'"

            self.is_running = True
            self.current_prompt = prompt
            self.started_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.exit_code = None
            self.output_buffer.clear()
            self._recent_broadcasts.clear()

        self._broadcast_log("system", f"🚀 Starting AGY (YOLO Mode) in {WORKSPACE_DIR}")
        self._broadcast_log("system", f"Prompt: {prompt}")

        # Start execution in background worker thread
        t = threading.Thread(
            target=self._worker_loop,
            args=(prompt, mode, send_telegram),
            daemon=True,
        )
        t.start()
        return True, "AGY execution launched in YOLO mode."

    def stop(self) -> bool:
        """Terminate the active AGY process."""
        with self.lock:
            if not self.is_running or not self.process:
                return False
            try:
                self.process.terminate()
                self._broadcast_log("warning", "🛑 Process termination signal sent.")
                return True
            except Exception as e:
                logger.error("Failed to terminate AGY process: %s", e)
                return False

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        """Subscribe to live output stream."""
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _broadcast_log(self, stream: str, text: str) -> None:
        """Append log line to circular buffer and broadcast to all live subscribers."""
        if not text:
            return
        now = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {
            "time": now,
            "stream": stream,
            "text": text,
            "index": len(self.output_buffer),
        }
        with self.lock:
            dedup_key = f"{stream}:{text}"
            if dedup_key in self._recent_broadcasts:
                return
            self._recent_broadcasts.append(dedup_key)

            self.output_buffer.append(entry)
            for q in list(self.subscribers):
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass

    def _tail_transcript_loop(self, prompt: str, start_time: float) -> None:
        """Tail transcript.jsonl of the active AGY run to broadcast real-time step progress."""
        transcript_file: Path | None = None
        brain_dir = Path("/home/dell/.gemini/antigravity-cli/brain")

        # Wait up to 15 seconds for transcript file to appear
        search_start = time.time()
        while time.time() - search_start < 15 and self.is_running:
            if brain_dir.exists():
                candidates = []
                for p in brain_dir.glob("*/.system_generated/logs/transcript.jsonl"):
                    try:
                        mtime = p.stat().st_mtime
                        if mtime >= start_time - 3.0:
                            candidates.append((mtime, p))
                    except OSError:
                        pass
                candidates.sort(key=lambda x: x[0], reverse=True)
                for _, p in candidates:
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as f:
                            first = f.readline()
                            if prompt[:20].lower() in first.lower():
                                transcript_file = p
                                break
                    except Exception:
                        pass
                if transcript_file:
                    break
            time.sleep(1)

        if not transcript_file:
            logger.info("No matching transcript.jsonl found for active AGY run.")
            return

        conv_id = transcript_file.parent.parent.parent.name
        self._broadcast_log("system", f"🔍 Tracking agent session: {conv_id[:8]}...")

        # Tail the file
        last_heartbeat = time.time()
        last_step_idx = -1
        try:
            with open(transcript_file, "r", encoding="utf-8", errors="ignore") as f:
                while self.is_running or (self.process and self.process.poll() is None):
                    line = f.readline()
                    if line:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            step_idx = entry.get("step_index", 0)
                            entry_type = entry.get("type", "")

                            if "tool_calls" in entry and entry["tool_calls"]:
                                for tc in entry["tool_calls"]:
                                    name = tc.get("name", "tool")
                                    args = tc.get("args", {})
                                    action = args.get("toolAction") or args.get("toolSummary") or name
                                    action_clean = str(action).strip('"\'')
                                    self._broadcast_log("step", f"⚡ [Step {step_idx}] {action_clean}")
                                    last_step_idx = step_idx
                                    last_heartbeat = time.time()
                            elif entry_type == "PLANNER_RESPONSE" and entry.get("content"):
                                content = str(entry.get("content", "")).strip()
                                if content:
                                    for cline in content.splitlines():
                                        if cline.strip():
                                            self._broadcast_log("stdout", cline)
                                    last_step_idx = step_idx
                                    last_heartbeat = time.time()
                        except Exception:
                            pass
                    else:
                        now = time.time()
                        if now - last_heartbeat >= 8.0:
                            elapsed = int(now - start_time)
                            step_str = f"at Step {last_step_idx}" if last_step_idx >= 0 else "planning initial actions"
                            self._broadcast_log("system", f"⏳ AGY is actively working ({step_str}, {elapsed}s elapsed)...")
                            last_heartbeat = now
                        time.sleep(0.5)

                # Final flush
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") == "PLANNER_RESPONSE" and entry.get("content"):
                            content = str(entry.get("content", "")).strip()
                            for cline in content.splitlines():
                                if cline.strip():
                                    self._broadcast_log("stdout", cline)
                    except Exception:
                        pass
        except Exception as e:
            logger.error("Error reading transcript: %s", e)

    def _worker_loop(self, prompt: str, mode: str, send_telegram: bool) -> None:
        """Subprocess execution loop capturing stdout and stderr."""
        agy_executable = AGY_BIN if Path(AGY_BIN).exists() else "agy"
        cmd = [
            agy_executable,
            "--dangerously-skip-permissions",
        ]
        if mode and mode != "auto":
            cmd.extend(["--mode", mode])
        cmd.extend(["-p", prompt])

        env = os.environ.copy()
        env["PATH"] = f"/home/dell/.local/bin:/usr/local/bin:{env.get('PATH', '')}"
        env["PYTHONUNBUFFERED"] = "1"
        env["HOME"] = "/home/dell"
        env["USER"] = "dell"

        start_time = time.time()
        final_response_lines: list[str] = []

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(WORKSPACE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
            )

            # Launch transcript watcher thread for live turn streaming
            watcher_thread = threading.Thread(
                target=self._tail_transcript_loop,
                args=(prompt, start_time),
                daemon=True,
            )
            watcher_thread.start()

            if self.process.stdout:
                for line in iter(self.process.stdout.readline, ""):
                    clean_line = line.rstrip()
                    if clean_line:
                        final_response_lines.append(clean_line)
                        self._broadcast_log("stdout", clean_line)

            self.process.wait()
            ret_code = self.process.returncode
        except Exception as exc:
            ret_code = -1
            self._broadcast_log("error", f"Execution error: {exc}")

        elapsed = round(time.time() - start_time, 2)
        status_tag = "COMPLETED" if ret_code == 0 else f"EXITED with code {ret_code}"
        self._broadcast_log("system", f"🏁 AGY process {status_tag} in {elapsed}s.")

        with self.lock:
            self.is_running = False
            self.exit_code = ret_code
            self.last_run_summary = {
                "prompt": prompt,
                "elapsed_seconds": elapsed,
                "exit_code": ret_code,
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "lines_count": len(final_response_lines),
            }

        # Telegram notification if enabled
        if send_telegram:
            try:
                token, chat_id, enabled, send_sheets, notify_runs = notifier.get_telegram_config()
                if enabled and token and chat_id:
                    tail = "\n".join(final_response_lines[-10:]) if final_response_lines else "No output."
                    if len(tail) > 1000:
                        tail = tail[:1000] + "..."
                    msg = (
                        f"💻 <b>AGY Remote Command Finished</b>\n\n"
                        f"📝 <b>Prompt:</b> <code>{prompt}</code>\n"
                        f"⏱ <b>Time:</b> {elapsed}s | <b>Status:</b> {status_tag}\n\n"
                        f"📄 <b>Output:</b>\n<pre>{tail}</pre>"
                    )
                    notifier.send_telegram_message(token, chat_id, msg)
            except Exception as e_tg:
                logger.error("Failed to dispatch AGY Telegram alert: %s", e_tg)


# Singleton Runner
agy_runner = AgyRunner()
