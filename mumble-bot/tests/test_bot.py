"""Tests for bot.py — all external dependencies mocked via conftest.py."""
import json
from unittest.mock import MagicMock, patch
import pytest

# conftest.py has already installed all stubs. Just import bot.
import bot


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_bot_state(monkeypatch):
    """Reset module-level mutable state before each test."""
    monkeypatch.setattr(bot, "_speaking", False)
    monkeypatch.setattr(bot, "_pending_approval_id", None)

    mock_redis = MagicMock()
    monkeypatch.setattr(bot, "redis_client", mock_redis)

    mock_mumble = MagicMock()
    mock_mumble.users.myself_session = 99
    monkeypatch.setattr(bot, "mumble", mock_mumble)

    monkeypatch.setattr(bot, "vad_tracker", MagicMock())

    yield mock_mumble, mock_redis


def make_soundchunk(pcm=b"\x00\x00" * 960):
    sc = MagicMock()
    sc.pcm = pcm
    return sc


def make_text_message(actor=1, text="hello"):
    msg = MagicMock()
    msg.actor = actor
    msg.message = text
    return msg


def make_users_mock(users_by_session: dict, myself_session: int = 99) -> MagicMock:
    """Create a MagicMock that acts as both a dict and has .myself_session."""
    m = MagicMock()
    m.myself_session = myself_session
    m.__getitem__ = MagicMock(side_effect=lambda k: users_by_session[k])
    m.__contains__ = MagicMock(side_effect=lambda k: k in users_by_session)
    return m


# ── 1. text_received pushes JSON job to Redis ──────────────────────────────

def test_text_received_pushes_job(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state
    mock_mumble.users = make_users_mock({1: {"name": "Alice"}}, myself_session=99)

    msg = make_text_message(actor=1, text="what time is it")
    bot.text_received_cb(msg)

    mock_redis.lpush.assert_called_once()
    key, raw = mock_redis.lpush.call_args.args
    assert key == bot.QUEUE_KEY
    job = json.loads(raw)
    assert job["type"] == "text"
    assert job["message"] == "what time is it"
    assert job["username"] == "Alice"


# ── 2. Own message is ignored ──────────────────────────────────────────────

def test_own_message_ignored(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state
    mock_mumble.users.myself_session = 5

    msg = make_text_message(actor=5, text="I said this")
    bot.text_received_cb(msg)

    mock_redis.lpush.assert_not_called()


# ── 3. HTML stripped from text messages ────────────────────────────────────

def test_html_stripped(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state
    mock_mumble.users = make_users_mock({1: {"name": "Bob"}}, myself_session=99)

    msg = make_text_message(actor=1, text="<b>bold</b> <i>italic</i>")
    bot.text_received_cb(msg)

    _, raw = mock_redis.lpush.call_args.args
    job = json.loads(raw)
    assert job["message"] == "bold italic"


# ── 4. Voice received + VAD emits → voice job pushed ──────────────────────

def test_voice_received_pushes_job(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state
    pcm_data = b"\x10\x00" * 960
    bot.vad_tracker.add_audio.return_value = pcm_data

    user = {"session": 1, "name": "Carol"}
    sc = make_soundchunk(pcm=pcm_data)
    bot.sound_received_cb(user, sc)

    mock_redis.lpush.assert_called_once()
    key, raw = mock_redis.lpush.call_args.args
    job = json.loads(raw)
    assert job["type"] == "voice"
    assert job["username"] == "Carol"
    assert job["pcm"] == pcm_data.hex()


# ── 5. _speaking=True suppresses sound_received processing ─────────────────

def test_speaking_suppresses_sound(reset_bot_state, monkeypatch):
    mock_mumble, mock_redis = reset_bot_state
    monkeypatch.setattr(bot, "_speaking", True)

    user = {"session": 1, "name": "Dave"}
    sc = make_soundchunk()
    bot.sound_received_cb(user, sc)

    bot.vad_tracker.add_audio.assert_not_called()
    mock_redis.lpush.assert_not_called()


# ── 6. "yes" text resolves pending approval ────────────────────────────────

def test_yes_resolves_approval(reset_bot_state, monkeypatch):
    mock_mumble, mock_redis = reset_bot_state
    monkeypatch.setattr(bot, "_pending_approval_id", "abc123")
    mock_mumble.users = make_users_mock({1: {"name": "Owner"}}, myself_session=99)

    msg = make_text_message(actor=1, text="yes")
    bot.text_received_cb(msg)

    mock_redis.hset.assert_called_once()
    call_str = str(mock_redis.hset.call_args)
    assert "approval:abc123" in call_str
    assert bot._pending_approval_id is None


# ── 7. "no" text denies pending approval ──────────────────────────────────

def test_no_denies_approval(reset_bot_state, monkeypatch):
    mock_mumble, mock_redis = reset_bot_state
    monkeypatch.setattr(bot, "_pending_approval_id", "xyz789")
    mock_mumble.users = make_users_mock({1: {"name": "Owner"}}, myself_session=99)

    msg = make_text_message(actor=1, text="no")
    bot.text_received_cb(msg)

    mock_redis.hset.assert_called_once()
    call_str = str(mock_redis.hset.call_args)
    assert "denied" in call_str
    assert bot._pending_approval_id is None


# ── 8. _call_agent POSTs with channel="mumble" ────────────────────────────

def test_call_agent_posts_with_mumble_channel():
    with patch("bot.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"response": "hi"}
        result = bot._call_agent("hello", "user1")

    mock_post.assert_called_once()
    sent_json = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
    assert sent_json["channel"] == "mumble"
    assert result == "hi"


# ── 9. Queue worker text job: agent called → send_text_message called ──────

def test_queue_worker_text_job(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state

    jobs = [json.dumps({"type": "text", "username": "Alice", "message": "hi"}), None]
    mock_redis.rpop.side_effect = lambda key: jobs.pop(0) if jobs else None

    mock_stt = MagicMock()
    mock_tts = MagicMock()

    with patch("bot._call_agent", return_value="hello back") as mock_agent, \
         patch("bot.time.sleep", side_effect=StopIteration):
        try:
            bot.queue_worker(mock_stt, mock_tts)
        except StopIteration:
            pass

    mock_agent.assert_called_once_with("hi", "Alice")
    mock_mumble.my_channel().send_text_message.assert_called_with("hello back")


# ── 10. Queue worker voice job: STT + agent + TTS all called ───────────────

def test_queue_worker_voice_job(reset_bot_state):
    mock_mumble, mock_redis = reset_bot_state

    pcm = b"\x10\x00" * 960
    jobs = [json.dumps({"type": "voice", "username": "Bob", "session": 2, "pcm": pcm.hex()}), None]
    mock_redis.rpop.side_effect = lambda key: jobs.pop(0) if jobs else None

    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = "turn on the light"

    mock_tts = MagicMock()
    mock_tts.synthesize.return_value = b"\x00\x00" * 100

    with patch("bot._call_agent", return_value="Lights on") as mock_agent, \
         patch("bot._play_and_wait") as mock_play, \
         patch("bot.time.sleep", side_effect=StopIteration):
        try:
            bot.queue_worker(mock_stt, mock_tts)
        except StopIteration:
            pass

    mock_stt.transcribe.assert_called_once_with(pcm)
    mock_agent.assert_called_once_with("turn on the light", "Bob")
    mock_play.assert_called_once_with(mock_tts, "Lights on")
    calls = mock_mumble.my_channel().send_text_message.call_args_list
    assert any("turn on the light" in str(c) for c in calls)
    assert any("Lights on" in str(c) for c in calls)
