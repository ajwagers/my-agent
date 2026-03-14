"""Tests for stt.py — conftest.py stubs faster_whisper before import."""
import numpy as np
import pytest

# conftest.py has already installed the faster_whisper stub.
from stt import WhisperSTT


def make_pcm_48k(duration_sec: float) -> bytes:
    n_samples = int(48000 * duration_sec)
    return (np.zeros(n_samples, dtype=np.int16)).tobytes()


def make_segment(text: str):
    from unittest.mock import MagicMock
    seg = MagicMock()
    seg.text = text
    return seg


# ── 1. transcribe() returns a string ───────────────────────────────────────

def test_transcribe_returns_string(whisper_model_instance):
    whisper_model_instance.transcribe.return_value = (
        [make_segment("Hello world")], {}
    )
    stt = WhisperSTT()
    result = stt.transcribe(make_pcm_48k(1.0))
    assert isinstance(result, str)
    assert result == "Hello world"


# ── 2. Resampling: 48kHz input → ~16kHz output ─────────────────────────────

def test_resampling_reduces_samples(whisper_model_instance):
    captured = {}

    def capture_transcribe(audio_f32, **kwargs):
        captured["n_samples"] = len(audio_f32)
        return [], {}

    whisper_model_instance.transcribe.side_effect = capture_transcribe
    stt = WhisperSTT()
    stt.transcribe(make_pcm_48k(1.0))

    expected = 16000
    assert abs(captured["n_samples"] - expected) <= 10


# ── 3. Multiple segments joined with spaces ─────────────────────────────────

def test_multiple_segments_joined(whisper_model_instance):
    whisper_model_instance.transcribe.return_value = (
        [make_segment("Hello"), make_segment("world")], {}
    )
    stt = WhisperSTT()
    result = stt.transcribe(make_pcm_48k(1.0))
    assert result == "Hello world"


# ── 4. Empty segments list → empty string ──────────────────────────────────

def test_empty_segments_returns_empty_string(whisper_model_instance):
    whisper_model_instance.transcribe.return_value = ([], {})
    stt = WhisperSTT()
    result = stt.transcribe(make_pcm_48k(1.0))
    assert result == ""


# ── 5. Segments with extra whitespace are stripped ─────────────────────────

def test_segment_whitespace_stripped(whisper_model_instance):
    whisper_model_instance.transcribe.return_value = (
        [make_segment("  trim me  "), make_segment(" and me ")], {}
    )
    stt = WhisperSTT()
    result = stt.transcribe(make_pcm_48k(1.0))
    assert result == "trim me and me"
