"""Mumble bot — connects to Murmur, relays voice/text to agent-core."""
import json
import os
import re
import threading
import time

import pymumble_py3 as pymumble
from pymumble_py3.constants import PYMUMBLE_CLBK_SOUNDRECEIVED, PYMUMBLE_CLBK_TEXTMESSAGERECEIVED
import redis
import requests

from vad import VADTracker
from stt import WhisperSTT
from tts import PiperTTS

# ── Config ──────────────────────────────────────────────────────────────────
MUMBLE_HOST = os.environ.get("MUMBLE_HOST", "mumble-server")
MUMBLE_PORT = int(os.environ.get("MUMBLE_PORT", "64738"))
MUMBLE_PASSWORD = os.environ.get("MUMBLE_PASSWORD", "")
MUMBLE_BOT_NAME = os.environ.get("MUMBLE_BOT_NAME", "Agent")
MUMBLE_CHANNEL = os.environ.get("MUMBLE_CHANNEL", "General")
AGENT_URL = os.environ.get("AGENT_URL", "http://agent-core:8000")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")

QUEUE_KEY = "queue:mumble"
QUEUE_ACTIVE_KEY = "queue:mumble:active"
BRAIN_URL = os.environ.get("BRAIN_URL", "http://open-brain-mcp:8002")

# ── Owner identity / channel trust ───────────────────────────────────────────
#
# Two-layer trust model:
#
#   1. Certificate hash (primary — cryptographically unforgeable):
#      Each Mumble client auto-generates an SSL certificate on first connect.
#      Set MUMBLE_OWNER_CERT_HASH to the owner's cert fingerprint to enable.
#      Run `docker compose logs mumble-bot` after first connect to see it.
#
#   2. Username allowlist (fallback — weaker, requires server password to be set):
#      If MUMBLE_OWNER_CERT_HASH is empty, trust any user whose display name
#      is in MUMBLE_OWNER_USERNAMES. Anyone with the server password could
#      connect using a matching name, so keep MUMBLE_SERVER_PASSWORD set.
#
# Trusted users → channel="mumble_owner" (full private-channel access, same as Telegram).
# Untrusted users → channel="mumble" (restricted to public/business info only).

MUMBLE_OWNER_CERT_HASH: str = os.environ.get("MUMBLE_OWNER_CERT_HASH", "").strip()
MUMBLE_OWNER_USERNAMES: frozenset = frozenset(
    u.strip() for u in os.environ.get("MUMBLE_OWNER_USERNAMES", "Andy").split(",")
    if u.strip()
)


def _get_channel(username: str, cert_hash: str = "") -> str:
    """Return the agent channel string for this Mumble user.

    "mumble_owner" is a private channel with the same access as Telegram.
    "mumble" is a restricted public channel (no personal data).
    """
    # Primary: cert hash — unforgeable, doesn't depend on username
    if MUMBLE_OWNER_CERT_HASH:
        return "mumble_owner" if cert_hash == MUMBLE_OWNER_CERT_HASH else "mumble"
    # Fallback: username allowlist (weaker)
    return "mumble_owner" if username in MUMBLE_OWNER_USERNAMES else "mumble"

# ── Module-level state ───────────────────────────────────────────────────────
mumble = None           # set in main() after connect
_speaking = False       # True while playing TTS → suppress STT
_pending_approval_id = None
_last_bot_response: str = ""  # last response spoken — captured by "save that"

_SAVE_THAT_RE = re.compile(
    r"^(save that|remember that|save this|remember this)[\.\!]?$",
    re.IGNORECASE,
)

vad_tracker = VADTracker()
redis_client = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_agent(message: str, user_id: str, channel: str = "mumble") -> str:
    resp = requests.post(
        f"{AGENT_URL}/chat",
        json={"message": message, "user_id": user_id, "channel": channel},
        headers={"X-Api-Key": AGENT_API_KEY},
        timeout=None,
    )
    return resp.json()["response"]


def _capture_to_brain(content: str) -> bool:
    """Directly capture a thought to brain memory. Returns True on success."""
    try:
        resp = requests.post(
            f"{BRAIN_URL}/tools/capture_thought",
            json={"content": content, "type": "note"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _strip_for_speech(text: str) -> str:
    """Strip markdown/HTML so TTS produces clean spoken prose."""
    # Remove fenced code blocks entirely — not useful when spoken
    text = re.sub(r"```[\s\S]*?```", "code block omitted.", text)
    # Inline code — just keep the content
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold / italic markers
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    # Markdown headers → plain text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bullet / numbered list items — strip the marker
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _play_and_wait(tts: PiperTTS, text: str):
    global _speaking
    _speaking = True
    try:
        pcm = tts.synthesize(text)
        mumble.sound_output.add_sound(pcm)
        time.sleep(len(pcm) / 96000 + 0.5)  # 96000 = 48kHz × 2 bytes/sample
    finally:
        _speaking = False


def _resolve_approval(resolution: str):
    global _pending_approval_id
    redis_client.hset(
        f"approval:{_pending_approval_id}",
        mapping={
            "status": resolution,
            "resolved_at": str(time.time()),
            "resolved_by": "mumble_owner",
        },
    )
    redis_client.publish(
        "approvals:resolved",
        json.dumps({"approval_id": _pending_approval_id, "status": resolution}),
    )
    approval_id = _pending_approval_id
    _pending_approval_id = None
    mumble.my_channel().send_text_message(f"✓ {resolution.capitalize()}.")


# ── Callbacks ────────────────────────────────────────────────────────────────

def sound_received_cb(user, soundchunk):
    if _speaking:
        return
    session = user.get("session")
    username = user.get("name", str(session))
    cert_hash = user.get("hash", "")
    # Log cert hash on first occurrence so owner can copy it to .env
    if not MUMBLE_OWNER_CERT_HASH and username in MUMBLE_OWNER_USERNAMES and cert_hash:
        print(
            f"[auth] Cert hash for '{username}': {cert_hash}\n"
            f"[auth] Add MUMBLE_OWNER_CERT_HASH={cert_hash} to .env for stronger auth.",
            flush=True,
        )
    utterance = vad_tracker.add_audio(session, username, soundchunk.pcm)
    if utterance is not None:
        _push_voice_job(username, session, utterance, cert_hash)


def _push_voice_job(username: str, session: int, utterance: bytes, cert_hash: str = ""):
    duration_ms = len(utterance) / 1920 * 20
    print(f"[VAD] utterance from {username}: {duration_ms:.0f}ms", flush=True)
    job = {
        "type": "voice",
        "username": username,
        "session": session,
        "pcm": utterance.hex(),
        "cert_hash": cert_hash,
    }
    redis_client.lpush(QUEUE_KEY, json.dumps(job))


def vad_flush_worker():
    """Periodically flush VAD buffers for users who stopped transmitting (PTT released)."""
    while True:
        time.sleep(0.3)
        try:
            for username, utterance in vad_tracker.flush_stale():
                print(f"[VAD] flush (stream ended) from {username}: {len(utterance)/1920*20:.0f}ms", flush=True)
                _push_voice_job(username, 0, utterance)
        except Exception as e:
            print(f"VAD flush error: {e}", flush=True)


def text_received_cb(message):
    global _pending_approval_id
    # Skip own messages and server system messages (actor=0)
    if message.actor == mumble.users.myself_session or message.actor == 0:
        return
    text = re.sub(r"<[^>]+>", "", message.message).strip()
    if not text:
        return

    actor = message.actor
    user_info = mumble.users[actor] if actor in mumble.users else {}
    username = user_info.get("name", str(actor))
    cert_hash = user_info.get("hash", "")

    # Log cert hash on first occurrence so owner can copy it to .env
    if not MUMBLE_OWNER_CERT_HASH and username in MUMBLE_OWNER_USERNAMES and cert_hash:
        print(
            f"[auth] Cert hash for '{username}': {cert_hash}\n"
            f"[auth] Add MUMBLE_OWNER_CERT_HASH={cert_hash} to .env for stronger auth.",
            flush=True,
        )

    # Approval flow
    if _pending_approval_id is not None:
        if re.match(r"^(yes|approve|y)$", text, re.IGNORECASE):
            _resolve_approval("approved")
            return
        if re.match(r"^(no|deny|n)$", text, re.IGNORECASE):
            _resolve_approval("denied")
            return

    job = {"type": "text", "username": username, "cert_hash": cert_hash, "message": text}
    redis_client.lpush(QUEUE_KEY, json.dumps(job))


# ── Worker threads ────────────────────────────────────────────────────────────

# Tick intervals in seconds: quick check early, then space out as reasoning deepens
PROGRESS_INTERVALS = [30, 60, 90]  # last value repeats indefinitely


def _call_agent_with_progress(message: str, user_id: str, channel: str = "mumble") -> str:
    """Call agent and send periodic progress ticks, spacing out over time."""
    stop_event = threading.Event()

    def _reporter():
        elapsed = 0
        intervals = PROGRESS_INTERVALS
        idx = 0
        while True:
            interval = intervals[min(idx, len(intervals) - 1)]
            if stop_event.wait(timeout=interval):
                break  # agent finished
            elapsed += interval
            idx += 1
            try:
                mumble.my_channel().send_text_message(f"<i>⏳ Still working... ({elapsed}s)</i>")
            except Exception:
                pass

    t = threading.Thread(target=_reporter, daemon=True)
    t.start()
    try:
        return _call_agent(message, user_id, channel)
    finally:
        stop_event.set()
        t.join(timeout=1)


def queue_worker(stt: WhisperSTT, tts: PiperTTS):
    global _last_bot_response
    while True:
        raw = redis_client.rpop(QUEUE_KEY)
        if raw is None:
            time.sleep(0.2)
            continue
        redis_client.set(QUEUE_ACTIVE_KEY, "1", ex=600)
        job = json.loads(raw)
        try:
            username = job.get("username", "")
            cert_hash = job.get("cert_hash", "")
            channel = _get_channel(username, cert_hash)

            if job["type"] == "text":
                text = job["message"]
                mumble.my_channel().send_text_message(f'<i>⏳ On it, {username}...</i>')
                if _SAVE_THAT_RE.match(text.strip()):
                    if _last_bot_response:
                        ok = _capture_to_brain(_last_bot_response)
                        response = "📝 Saved to memory." if ok else "❌ Couldn't save to memory."
                    else:
                        response = "Nothing to save yet — I haven't responded this session."
                    mumble.my_channel().send_text_message(response)
                else:
                    response = _call_agent_with_progress(text, username, channel)
                    _last_bot_response = response
                    mumble.my_channel().send_text_message(response)
            elif job["type"] == "voice":
                pcm_bytes = bytes.fromhex(job["pcm"])
                transcript = stt.transcribe(pcm_bytes)
                print(f"[STT] '{transcript}' from {username} (channel={channel})", flush=True)
                if not transcript.strip():
                    continue
                mumble.my_channel().send_text_message(f'<i>"{transcript}"</i>')
                if _SAVE_THAT_RE.match(transcript.strip()):
                    if _last_bot_response:
                        ok = _capture_to_brain(_last_bot_response)
                        response = "Saved to memory." if ok else "Couldn't save to memory."
                    else:
                        response = "Nothing to save yet."
                    mumble.my_channel().send_text_message(response)
                    _play_and_wait(tts, response)
                    continue
                response = _call_agent_with_progress(transcript, username, channel)
                _last_bot_response = response
                mumble.my_channel().send_text_message(response)
                speech_text = _strip_for_speech(response)
                print(f"[TTS] synthesizing response ({len(speech_text)} chars)", flush=True)
                _play_and_wait(tts, speech_text)
                print(f"[TTS] playback complete", flush=True)
        except Exception as e:
            print(f"Queue worker error: {e}", flush=True)
            import traceback; traceback.print_exc()
        finally:
            redis_client.delete(QUEUE_ACTIVE_KEY)


def approval_subscriber():
    global _pending_approval_id
    pubsub = redis_client.pubsub()
    pubsub.subscribe("approvals:pending")
    for msg in pubsub.listen():
        if msg["type"] != "message":
            continue
        try:
            data = json.loads(msg["data"])
            _pending_approval_id = data.get("approval_id")
            description = data.get("description", "(no description)")
            mumble.my_channel().send_text_message(
                f"⚠️ Approval Required\n{description}\nReply yes to approve or no to deny."
            )
        except Exception as e:
            print(f"Approval subscriber error: {e}")


def notification_subscriber():
    pubsub = redis_client.pubsub()
    pubsub.subscribe("notifications:agent")
    for msg in pubsub.listen():
        if msg["type"] != "message":
            continue
        try:
            data = json.loads(msg["data"])
            text = data.get("text", str(msg["data"]))
            mumble.my_channel().send_text_message(text)
        except Exception as e:
            print(f"Notification subscriber error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global mumble, redis_client

    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    print("Loading STT model...")
    stt = WhisperSTT()
    print("Loading TTS model...")
    tts = PiperTTS()

    threading.Thread(target=queue_worker, args=(stt, tts), daemon=True).start()
    threading.Thread(target=vad_flush_worker, daemon=True).start()
    threading.Thread(target=approval_subscriber, daemon=True).start()
    threading.Thread(target=notification_subscriber, daemon=True).start()

    mumble = pymumble.Mumble(
        MUMBLE_HOST, MUMBLE_BOT_NAME,
        password=MUMBLE_PASSWORD,
        port=MUMBLE_PORT,
        reconnect=True,
    )
    mumble.set_receive_sound(True)
    mumble.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, sound_received_cb)
    mumble.callbacks.set_callback(PYMUMBLE_CLBK_TEXTMESSAGERECEIVED, text_received_cb)
    mumble.start()
    mumble.is_ready()  # blocks until handshake complete
    try:
        mumble.channels.find_by_name(MUMBLE_CHANNEL).move_in()
    except Exception:
        print(f"Channel '{MUMBLE_CHANNEL}' not found, staying in Root")
        mumble.channels.find_by_name("Root").move_in()
    print(f"Connected to {MUMBLE_HOST}:{MUMBLE_PORT} as {MUMBLE_BOT_NAME}")

    while True:
        mumble.ping()
        time.sleep(5)


if __name__ == "__main__":
    main()
