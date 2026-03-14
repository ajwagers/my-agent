"""Tests for tts.py — conftest.py stubs piper before import."""
import io
import wave
import numpy as np
import pytest

# conftest.py has already installed the piper stub.
from tts import PiperTTS, OUTPUT_RATE


def _setup_synthesize(piper_voice_inst, duration_sec: float, native_rate: int = 22050):
    """Configure the mock voice to write PCM when synthesize() is called."""
    n_samples = int(native_rate * duration_sec)
    pcm_data = b"\x10\x00" * n_samples

    def fake_synthesize(text, wav_file):
        wav_file.writeframes(pcm_data)

    piper_voice_inst.synthesize.side_effect = fake_synthesize
    piper_voice_inst.config.sample_rate = native_rate
    return n_samples


# ── 1. synthesize() returns bytes ──────────────────────────────────────────

def test_synthesize_returns_bytes(piper_voice_inst):
    _setup_synthesize(piper_voice_inst, 0.5)
    tts = PiperTTS()
    result = tts.synthesize("Hello")
    assert isinstance(result, bytes)
    assert len(result) > 0


# ── 2. Output is resampled to 48kHz ────────────────────────────────────────

def test_output_resampled_to_48k(piper_voice_inst):
    duration_sec = 1.0
    _setup_synthesize(piper_voice_inst, duration_sec, native_rate=22050)
    tts = PiperTTS()
    result = tts.synthesize("test")

    output_samples = len(result) // 2
    expected_samples = int(OUTPUT_RATE * duration_sec)
    assert abs(output_samples - expected_samples) < expected_samples * 0.02


# ── 3. No resampling when native_rate == OUTPUT_RATE ───────────────────────

def test_no_resample_when_rate_matches(piper_voice_inst):
    n_samples = _setup_synthesize(piper_voice_inst, 0.5, native_rate=OUTPUT_RATE)
    tts = PiperTTS()
    tts.native_rate = OUTPUT_RATE
    result = tts.synthesize("test")
    expected_bytes = n_samples * 2
    assert len(result) == expected_bytes


# ── 4. Resampling increases sample count from 22050 to 48000 ───────────────

def test_resampling_ratio(piper_voice_inst):
    duration_sec = 0.5
    native_samples = _setup_synthesize(piper_voice_inst, duration_sec, native_rate=22050)
    tts = PiperTTS()
    result = tts.synthesize("test")

    output_samples = len(result) // 2
    assert output_samples > native_samples


# ── 5. Empty text still returns bytes ─────────────────────────────────────

def test_empty_text_returns_bytes(piper_voice_inst):
    _setup_synthesize(piper_voice_inst, 0.1)
    tts = PiperTTS()
    result = tts.synthesize("")
    assert isinstance(result, bytes)
