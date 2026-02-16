import logging
import os
import requests
import datetime
import asyncio
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from zoneinfo import ZoneInfo

# Config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
YOUR_CHAT_ID = int(os.getenv("CHAT_ID", "0"))

MAX_TG_LEN = 4096  # Hard Telegram limit

async def post_init(application):
    """Send a greeting when the bot starts up."""
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour

    if 5 <= hour < 12:
        greeting = "Good Morning"
    elif 12 <= hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    uptime_msg = f"""
**{greeting}!**

**Agent Stack Online:**
- Ollama: phi3:latest loaded
- CLI: `agent chat` ready
- Telegram: Private responses
- RAG: ChromaDB healthy (if enabled)

**Boot:** {now.strftime('%Y-%m-%d %H:%M:%S EST')}
"""

    await application.bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=uptime_msg,
        parse_mode="Markdown"
    )
    logger.info(f"Sent {greeting} message")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only respond to your chat ID
    if YOUR_CHAT_ID and update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Show typing indicator while waiting
    typing_task = asyncio.create_task(_typing_loop(chat_id, context))

    try:
        resp = requests.post(
            f"{AGENT_URL}/chat",
            json={"message": user_message, "model": "phi3:latest"},
            timeout=None,
        )
        resp.raise_for_status()
        reply_text = resp.json()["response"]
    except requests.exceptions.Timeout:
        reply_text = "Agent timed out (took too long)."
    except Exception as e:
        logger.exception("Agent error")
        reply_text = f"Error: {e}"
    finally:
        typing_task.cancel()

    # Send in chunks instead of truncating
    for chunk in _split_message(reply_text, MAX_TG_LEN):
        await update.message.reply_text(chunk)


async def _typing_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep typing status alive until cancelled."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return

def _split_message(text: str, max_len: int):
    """Yield chunks <= max_len, splitting on line breaks or spaces."""
    if len(text) <= max_len:
        yield text
        return

    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
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

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))

    logger.info("Telegram bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
