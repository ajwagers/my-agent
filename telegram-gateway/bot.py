import logging
import os
import json
import time
import requests
import datetime
import asyncio
import redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes,
)
from zoneinfo import ZoneInfo

# Config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
YOUR_CHAT_ID = int(os.getenv("CHAT_ID", "0"))  # Set in .env

# Redis connection for approval pub/sub
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

MAX_TG_LEN = 4096  # Hard Telegram limit.

RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}


CONTENT_PREVIEW_LIMIT = 500


def _build_approval_message(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Build the Telegram message text and inline keyboard for an approval request."""
    risk = data.get("risk_level", "medium")
    emoji = RISK_EMOJI.get(risk, "⚪")
    approval_id = data.get("approval_id") or data.get("id", "unknown")
    text = (
        f"{emoji} **Approval Request**\n\n"
        f"**Action:** {data.get('action', 'unknown')}\n"
        f"**Zone:** {data.get('zone', 'unknown')}\n"
        f"**Risk:** {risk}\n"
        f"**Description:** {data.get('description', 'N/A')}\n"
        f"**Target:** {data.get('target', 'N/A')}\n"
        f"**ID:** `{approval_id}`"
    )

    # Include content preview for proposals (e.g., bootstrap writes)
    proposed_content = data.get("proposed_content")
    if proposed_content:
        preview = proposed_content[:CONTENT_PREVIEW_LIMIT]
        if len(proposed_content) > CONTENT_PREVIEW_LIMIT:
            preview += "\n... (truncated)"
        text += f"\n\n**Proposed Content:**\n```\n{preview}\n```"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{approval_id}"),
        ]
    ])
    return text, keyboard


async def _approval_subscriber(application):
    """Subscribe to Redis approvals:pending channel and send inline keyboards."""
    pubsub = redis_client.pubsub()
    pubsub.subscribe("approvals:pending")
    logger.info("Approval subscriber started")

    try:
        while True:
            msg = pubsub.get_message(timeout=0)
            if msg and msg["type"] == "message":
                try:
                    data = json.loads(msg["data"])
                    text, keyboard = _build_approval_message(data)
                    await application.bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                    logger.info(f"Sent approval request {data.get('approval_id')}")
                except Exception:
                    logger.exception("Failed to process approval notification")
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pubsub.close()
        return


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Approve/Deny button presses from inline keyboards."""
    query = update.callback_query
    if not query or not query.data:
        return

    # Only owner can approve
    if query.from_user.id != YOUR_CHAT_ID:
        await query.answer("Unauthorized", show_alert=True)
        return

    parts = query.data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("approve", "deny"):
        await query.answer("Invalid callback")
        return

    action, approval_id = parts
    status = "approved" if action == "approve" else "denied"

    # Write resolution to Redis hash
    key = f"approval:{approval_id}"
    current = redis_client.hgetall(key)

    if not current:
        await query.answer("Approval not found", show_alert=True)
        return

    if current.get("status") != "pending":
        await query.answer(f"Already {current.get('status')}", show_alert=True)
        return

    redis_client.hset(key, mapping={
        "status": status,
        "resolved_at": str(time.time()),
        "resolved_by": f"telegram:{query.from_user.id}",
    })

    emoji = "✅" if status == "approved" else "❌"
    await query.answer(f"{emoji} {status.capitalize()}")

    # Edit the original message to show the decision
    await query.edit_message_text(
        text=f"{emoji} **{status.upper()}** — {current.get('description', 'N/A')}\n"
             f"ID: `{approval_id}`",
        parse_mode="Markdown",
    )
    logger.info(f"Approval {approval_id} → {status}")


async def _catch_up_pending(application):
    """On startup, check for any pending approvals missed during downtime."""
    try:
        keys = redis_client.keys("approval:*")
        for key in keys:
            data = redis_client.hgetall(key)
            if data and data.get("status") == "pending":
                text, keyboard = _build_approval_message(data)
                await application.bot.send_message(
                    chat_id=YOUR_CHAT_ID,
                    text=f"📋 **Pending (from before restart)**\n\n{text}",
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                logger.info(f"Caught up pending approval {data.get('id')}")
    except Exception:
        logger.exception("Failed to catch up on pending approvals")


async def post_init(application):
    """Smart wake-up on boot"""
    now = datetime.datetime.now(ZoneInfo("America/New_York"))  # EST
    hour = now.hour

    if 5 <= hour < 12:
        greeting = "Good Morning"
    elif 12 <= hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    titles = ["Andy", "Dr. Wagers", "Sir", "Boss", "Chief Data Engineer"]
    title = titles[now.minute % len(titles)]  # Rotate every minute

    uptime_msg = f"""
🟢 **{greeting}, {title}!**

**Agent Stack Online:**
• Ollama: ✅ phi3:latest loaded
• CLI: ✅ `agent chat` ready
• Telegram: ✅ Private responses
• RAG: ✅ ChromaDB healthy (if enabled)
• Policy Engine: ✅ Guardrails active

**Boot:** {now.strftime('%Y-%m-%d %H:%M:%S EST')}
"""

    await application.bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=uptime_msg,
        parse_mode="Markdown"
    )
    logger.info(f"Sent {greeting} message to {title}")

    # Start approval subscriber as background task
    asyncio.create_task(_approval_subscriber(application))

    # Catch up on any pending approvals from before restart
    await _catch_up_pending(application)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Filter: only your chat ID
    if YOUR_CHAT_ID and update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Continuous typing loop (runs ~2min max, safe)
    typing_task = asyncio.create_task(_typing_loop(chat_id, context))

    try:
        resp = requests.post(
            f"{AGENT_URL}/chat",
            json={
                "message": user_message,
                "user_id": str(chat_id),
                "channel": "telegram",
            },
            timeout=None,
        )
        resp.raise_for_status()
        reply_text = resp.json()["response"]
    except requests.exceptions.Timeout:
        reply_text = "❌ Agent timed out (took too long)."
    except Exception as e:
        logger.exception("Agent error")
        reply_text = f"Error: {e}"
    finally:
        # Always stop typing and reply
        typing_task.cancel()
        
    # Send in chunks instead of truncating
    for chunk in _split_message(reply_text, MAX_TG_LEN):
        await update.message.reply_text(chunk)


async def _typing_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep typing status alive until cancelled."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)  # < 5s so it stays continuous [web:52][web:56][web:59]
    except asyncio.CancelledError:
        return

def _split_message(text: str, max_len: int):
    """Yield chunks <= max_len, try to split on line breaks or spaces."""
    if len(text) <= max_len:
        yield text
        return

    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
        # try break at last newline/space before end
        split_pos = text.rfind("\n", start, end)
        if split_pos == -1:
            split_pos = text.rfind(" ", start, end)
        if split_pos == -1 or split_pos <= start:
            split_pos = end
        yield text[start:split_pos]
        start = split_pos


def main():
    """Non-async main - run_polling handles event loop"""
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN env var required")

    # Build app
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # Handler: your chat ID + text only
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))

    # Handler: approval inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(handle_approval_callback))
    
    # Start polling (NO asyncio.run, NO await)
    logger.info("Telegram bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

