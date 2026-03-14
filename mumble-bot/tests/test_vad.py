"""Tests for vad.py — run without audio hardware via time mocking."""
import time
from unittest.mock import patch
import pytest

# conftest.py stubs webrtcvad before this file is imported.
from vad import (
    UserBuffer, VADTracker,
    FRAME_BYTES, FRAME_MS, MIN_SPEECH_SEC, SILENCE_TIMEOUT,
)

SILENT_FRAME = b"\x00" * FRAME_BYTES
VOICED_FRAME = b"\x10\x00" * (FRAME_BYTES // 2)


def make_silent_frames(n: int) -> bytes:
    return SILENT_FRAME * n


def make_voiced_frames(n: int) -> bytes:
    return VOICED_FRAME * n


# ── 1. Silent audio → no utterance emitted ─────────────────────────────────

def test_silent_audio_no_utterance(vad_instance_mock):
    vad_instance_mock.is_speech.return_value = False
    buf = UserBuffer()
    result = buf.add_chunk(make_silent_frames(100))
    assert result is None
    assert not buf.is_speaking


# ── 2. Voiced frames → silence timeout → utterance returned ────────────────

def test_voiced_then_silence_emits_utterance(vad_instance_mock):
    vad_instance_mock.is_speech.return_value = True
    buf = UserBuffer()
    t0 = 1000.0
    with patch("vad.time.monotonic", return_value=t0):
        buf.add_chunk(make_voiced_frames(50))
    assert buf.is_speaking

    vad_instance_mock.is_speech.return_value = False
    silence_time = t0 + SILENCE_TIMEOUT + 0.1
    with patch("vad.time.monotonic", return_value=silence_time):
        result = buf.add_chunk(make_silent_frames(5))

    assert result is not None
    assert len(result) > 0
    assert not buf.is_speaking


# ── 3. Speech too short (< MIN_SPEECH_SEC) → None ──────────────────────────

def test_speech_too_short_returns_none(vad_instance_mock):
    buf = UserBuffer()
    vad_instance_mock.is_speech.return_value = True
    t0 = 2000.0
    with patch("vad.time.monotonic", return_value=t0):
        buf.add_chunk(make_voiced_frames(1))  # 1 frame = 20ms < 300ms

    vad_instance_mock.is_speech.return_value = False
    silence_time = t0 + SILENCE_TIMEOUT + 0.1
    with patch("vad.time.monotonic", return_value=silence_time):
        result = buf.add_chunk(make_silent_frames(5))

    assert result is None


# ── 4. Two sessions tracked independently ──────────────────────────────────

def test_two_sessions_independent(vad_instance_mock):
    tracker = VADTracker()
    t0 = 3000.0

    vad_instance_mock.is_speech.return_value = True
    with patch("vad.time.monotonic", return_value=t0):
        tracker.add_audio(1, "Alice", make_voiced_frames(50))
    assert tracker._buffers[1].is_speaking

    vad_instance_mock.is_speech.return_value = False
    with patch("vad.time.monotonic", return_value=t0):
        tracker.add_audio(2, "Bob", make_silent_frames(5))
    assert not tracker._buffers[2].is_speaking


# ── 5. Partial frame accumulates in remainder ──────────────────────────────

def test_partial_frame_accumulates(vad_instance_mock):
    vad_instance_mock.is_speech.return_value = False
    buf = UserBuffer()
    half = FRAME_BYTES // 2
    buf.add_chunk(SILENT_FRAME[:half])
    assert buf.remainder == SILENT_FRAME[:half]
    assert not buf.is_speaking


# ── 6. Remainder joined with next chunk → full frame processed ─────────────

def test_remainder_joined_with_next_chunk(vad_instance_mock):
    vad_instance_mock.is_speech.return_value = False
    buf = UserBuffer()
    half = FRAME_BYTES // 2
    buf.add_chunk(SILENT_FRAME[:half])
    assert len(buf.remainder) == half
    buf.add_chunk(SILENT_FRAME[half:] + SILENT_FRAME)
    assert len(buf.remainder) < FRAME_BYTES


# ── 7. remove_session clears buffer ────────────────────────────────────────

def test_remove_session_clears_buffer(vad_instance_mock):
    vad_instance_mock.is_speech.return_value = False
    tracker = VADTracker()
    tracker.add_audio(99, "Ghost", make_silent_frames(1))
    assert 99 in tracker._buffers
    tracker.remove_session(99)
    assert 99 not in tracker._buffers


# ── 8. remove_session on unknown session is safe ───────────────────────────

def test_remove_unknown_session_safe():
    tracker = VADTracker()
    tracker.remove_session(999)  # should not raise


# ── 9. VAD exception is caught gracefully ──────────────────────────────────

def test_vad_exception_handled(vad_instance_mock):
    vad_instance_mock.is_speech.side_effect = Exception("vad error")
    buf = UserBuffer()
    result = buf.add_chunk(SILENT_FRAME)
    assert result is None


# ── 10. Multiple utterances from same user buffer ──────────────────────────

def test_multiple_utterances_same_buffer(vad_instance_mock):
    buf = UserBuffer()
    for i in range(2):
        vad_instance_mock.is_speech.return_value = True
        t0 = float(5000 + i * 10)
        with patch("vad.time.monotonic", return_value=t0):
            buf.add_chunk(make_voiced_frames(50))

        vad_instance_mock.is_speech.return_value = False
        silence_time = t0 + SILENCE_TIMEOUT + 0.1
        with patch("vad.time.monotonic", return_value=silence_time):
            buf.add_chunk(make_silent_frames(5))

        assert not buf.is_speaking
