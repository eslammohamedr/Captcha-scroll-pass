"""Telegram Bot & In-App Notification Engine for OPPO Sales Activation Monitor.

Dispatches instant activation alerts and attaches the two batch Excel report
sheets (Full Run Sheet + Newly Activated Sheet) directly to Telegram.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

import database

logger = logging.getLogger("oppo_notifier")


def get_telegram_config() -> tuple[str, str, bool, bool]:
    """Retrieve telegram configuration from database settings.
    Returns (token, chat_id, is_enabled, send_sheets).
    """
    settings = database.get_all_settings()
    token = settings.get("telegram_bot_token", "").strip()
    chat_id = settings.get("telegram_chat_id", "").strip()
    enabled = settings.get("telegram_notifications_enabled", "0") in ("1", "true", "True")
    send_sheets = settings.get("telegram_send_sheets", "1") in ("1", "true", "True")
    return token, chat_id, enabled, send_sheets


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    timeout: float = 15.0,
) -> bool:
    """Send a formatted text message to a Telegram chat using Bot API."""
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.error("Telegram sendMessage error [%s]: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def send_telegram_document(
    token: str,
    chat_id: str,
    file_path: Path | str,
    caption: str = "",
    timeout: float = 40.0,
) -> bool:
    """Send a file (e.g., Excel/CSV sheet) to a Telegram chat."""
    if not token or not chat_id:
        return False

    p = Path(file_path).resolve()
    if not p.exists() or p.stat().st_size == 0:
        logger.warning("Telegram send_document skipped: file not found or empty (%s)", p)
        return False

    url = f"https://api.telegram.org/bot{token}/sendDocument"

    try:
        with httpx.Client(timeout=timeout) as client:
            with open(p, "rb") as f:
                files = {"document": (p.name, f, "application/octet-stream")}
                data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
                resp = client.post(url, data=data, files=files)
                if resp.status_code == 200:
                    return True
                logger.error("Telegram sendDocument error [%s]: %s", resp.status_code, resp.text)
                return False
    except Exception as exc:
        logger.error("Failed to send Telegram document %s: %s", p.name, exc)
        return False


def notify_activation(device: dict[str, Any], previous_status: str = "Unknown") -> bool:
    """
    Triggered the moment a serial transitions to 'Active' (was not active before).
    Dispatches instant notification to Telegram with phone specs and activation details.
    """
    token, chat_id, enabled, _ = get_telegram_config()
    if not enabled or not token or not chat_id:
        return False

    sn = device.get("serial", "")
    model = device.get("model_name") or "OPPO Smartphone"
    group = device.get("group_name") or "Default"
    specs = device.get("specs") or ""
    color = device.get("color") or ""
    specs_str = f" ({specs} - {color})" if (specs or color) else ""
    act_date = device.get("activation_date") or "Activated"
    act_comment = device.get("activation_comment") or ""
    comment_line = f"\n💬 <b>Comment:</b> {act_comment}" if act_comment and act_comment != act_date else ""
    act_time = device.get("activated_at") or "Just Now"

    prev_badge = previous_status if previous_status else "Not Active"

    message = (
        "🚨 <b>OPPO DEVICE ACTIVATED!</b>\n\n"
        f"📱 <b>Model:</b> {model}{specs_str}\n"
        f"🔢 <b>Serial / IMEI:</b> <code>{sn}</code>\n"
        f"🏷️ <b>Group:</b> {group}\n"
        f"📅 <b>Activation Date:</b> {act_date}{comment_line}\n"
        f"🕒 <b>Detected At:</b> {act_time}\n"
        f"⚡ <b>Transition:</b> {prev_badge} ➔ <b>ACTIVE (Yes)</b>\n\n"
        "<i>🔔 OPPO Sales Activation Monitor</i>"
    )

    success = send_telegram_message(token, chat_id, message)
    if success and sn:
        database.mark_device_notified(sn)
    return success


def notify_run_completion(
    summary: dict[str, Any],
    full_sheet_path: Path | str | None = None,
    newly_activated_sheet_path: Path | str | None = None,
) -> bool:
    """
    Triggered when a full batch run or scheduled run completes.
    Sends summary statistics and automatically sends the TWO Excel sheets requested.
    """
    token, chat_id, enabled, send_sheets = get_telegram_config()
    if not enabled or not token or not chat_id:
        return False

    run_type = summary.get("run_type", "Manual Run")
    group_str = f" | Group: {summary.get('target_group')}" if summary.get("target_group") else ""
    total = summary.get("total_checked", 0)
    newly_act = summary.get("newly_activated", 0)
    active_cnt = summary.get("active_count", 0)
    inactive_cnt = summary.get("inactive_count", 0)
    errors_cnt = summary.get("error_count", 0)
    time_str = summary.get("elapsed_str", "")

    header_icon = "🎉" if newly_act > 0 else "📊"
    newly_line = f"🔥 <b>Newly Activated:</b> {newly_act} devices\n" if newly_act > 0 else "ℹ️ <b>Newly Activated:</b> 0 devices\n"

    message = (
        f"{header_icon} <b>OPPO Verification Run Completed</b>\n\n"
        f"🎯 <b>Mode:</b> {run_type}{group_str}\n"
        f"⏱ <b>Duration:</b> {time_str}\n"
        f"🔢 <b>Total Processed:</b> {total} devices\n"
        f"✅ <b>Total Active:</b> {active_cnt}\n"
        f"{newly_line}"
        f"⏳ <b>Still Inactive:</b> {inactive_cnt}\n"
        f"⚠️ <b>Errors / Unresolved:</b> {errors_cnt}\n\n"
        "📎 <i>Full reports attached below.</i>"
    )

    # 1. Send Summary text
    send_telegram_message(token, chat_id, message)

    # 2. Send both requested sheets if enabled
    if send_sheets:
        # Sheet 1: Full Run Sheet
        if full_sheet_path and Path(full_sheet_path).exists():
            send_telegram_document(
                token,
                chat_id,
                full_sheet_path,
                caption=f"📄 <b>Full Run Sheet</b> - {total} devices checked ({Path(full_sheet_path).name})",
            )

        # Sheet 2: Newly Activated Sheet
        if newly_activated_sheet_path and Path(newly_activated_sheet_path).exists():
            send_telegram_document(
                token,
                chat_id,
                newly_activated_sheet_path,
                caption=f"🔥 <b>Newly Activated Sheet</b> - Devices activated in this batch ({Path(newly_activated_sheet_path).name})",
            )

    return True


def test_telegram_connection(token: str, chat_id: str) -> tuple[bool, str]:
    """Test Telegram Bot credentials by fetching bot info and sending a test ping."""
    token = token.strip()
    chat_id = chat_id.strip()

    if not token or not chat_id:
        return False, "Bot token and Chat ID are both required."

    # Step 1: verify token via getMe
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code != 200:
                return False, f"Invalid Bot Token: {resp.json().get('description', 'Unauthorized')}"
            bot_data = resp.json().get("result", {})
            bot_name = bot_data.get("first_name", "Bot")
            bot_username = bot_data.get("username", "")
    except Exception as exc:
        return False, f"Connection error: {exc}"

    # Step 2: send test message to chat_id
    test_msg = (
        f"👋 <b>Connection Test Successful!</b>\n\n"
        f"🤖 Bot: <b>{bot_name}</b> (@{bot_username})\n"
        f"✅ OPPO Sales Activation Monitor is connected to this chat.\n"
        f"Instant activation alerts and run report sheets will be delivered here."
    )
    ok = send_telegram_message(token, chat_id, test_msg)
    if ok:
        return True, f"Success! Connected to bot @{bot_username} and test message sent to chat {chat_id}."
    return False, "Bot token is valid, but could not send message to Chat ID. Ensure you have started the bot in Telegram (/start)."
