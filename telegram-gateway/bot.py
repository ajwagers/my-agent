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
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
YOUR_CHAT_ID = int(os.getenv("CHAT_ID", "0"))  # Set in .env

# Redis connection for approval pub/sub
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

MAX_TG_LEN = 4096  # Hard Telegram limit.

RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}

CONTENT_PREVIEW_LIMIT = 500

# Redis queue keys for serialising chat requests
QUEUE_KEY = "queue:chat"
QUEUE_ACTIVE_KEY = "queue:chat:active"


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


async def _notification_subscriber(application):
    """Subscribe to Redis notifications:agent channel and forward to owner."""
    pubsub = redis_client.pubsub()
    pubsub.subscribe("notifications:agent")
    logger.info("Notification subscriber started")

    try:
        while True:
            msg = pubsub.get_message(timeout=0)
            if msg and msg["type"] == "message":
                try:
                    data = json.loads(msg["data"])
                    text = data.get("text", "")
                    if text:
                        await application.bot.send_message(
                            chat_id=YOUR_CHAT_ID,
                            text=text,
                            parse_mode="Markdown",
                        )
                except Exception:
                    logger.exception("Failed to process agent notification")
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pubsub.close()
        return


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
• Ollama: ✅ phi4-mini loaded
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

    # Start background workers
    asyncio.create_task(_approval_subscriber(application))
    asyncio.create_task(_notification_subscriber(application))
    asyncio.create_task(_queue_worker(application))

    # Catch up on any pending approvals from before restart
    await _catch_up_pending(application)


def _sync_call_agent(message: str, user_id: str) -> str:
    """Blocking agent HTTP call — run via asyncio.to_thread so it doesn't block the event loop."""
    try:
        resp = requests.post(
            f"{AGENT_URL}/chat",
            json={"message": message, "user_id": user_id, "channel": "telegram"},
            headers={"X-Api-Key": AGENT_API_KEY},
            timeout=None,
        )
        resp.raise_for_status()
        return resp.json()["response"]
    except Exception as e:
        logger.exception("Agent call failed")
        return f"❌ Error: {e}"


async def _queue_worker(application) -> None:
    """Process chat jobs from Redis queue one at a time."""
    logger.info("Queue worker started")
    try:
        while True:
            raw = redis_client.rpop(QUEUE_KEY)
            if raw is None:
                await asyncio.sleep(0.5)
                continue

            job = json.loads(raw)
            chat_id = job["chat_id"]
            message_id = job.get("message_id")

            redis_client.set(QUEUE_ACTIVE_KEY, "1", ex=600)
            typing_task = asyncio.create_task(_typing_loop(chat_id, application.bot))
            try:
                reply_text = await asyncio.to_thread(
                    _sync_call_agent, job["message"], job["user_id"]
                )
            finally:
                typing_task.cancel()
                redis_client.delete(QUEUE_ACTIVE_KEY)

            for chunk in _split_message(reply_text, MAX_TG_LEN):
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        reply_to_message_id=message_id,
                    )
                except Exception:
                    # Fallback if original message was deleted
                    await application.bot.send_message(chat_id=chat_id, text=chunk)

    except asyncio.CancelledError:
        return


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Filter: only your chat ID
    if YOUR_CHAT_ID and update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Push job onto queue
    redis_client.lpush(QUEUE_KEY, json.dumps({
        "chat_id": chat_id,
        "user_id": str(chat_id),
        "message": user_message,
        "message_id": update.message.message_id,
    }))

    # Compute queue position: items waiting + 1 if a job is actively running
    depth = redis_client.llen(QUEUE_KEY)
    is_busy = bool(redis_client.exists(QUEUE_ACTIVE_KEY))
    position = depth + (1 if is_busy else 0)

    if position > 1:
        ack = f"⏳ Got it — model is busy, you're #{position} in queue. I'll reply when ready."
    else:
        ack = "⏳ On it..."

    await update.message.reply_text(ack)


async def _typing_loop(chat_id: int, bot) -> None:
    """Keep typing status alive until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
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

