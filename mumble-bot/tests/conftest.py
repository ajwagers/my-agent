"""
Centralized sys.modules stubs — loaded by pytest BEFORE any test module.
All test files share these same mock objects so module caching doesn't interfere.
"""
import sys
from unittest.mock import MagicMock
import pytest

# ── webrtcvad ────────────────────────────────────────────────────────────────
webrtcvad_mock = MagicMock()
webrtcvad_vad_instance = MagicMock()
webrtcvad_mock.Vad.return_value = webrtcvad_vad_instance
sys.modules["webrtcvad"] = webrtcvad_mock

# ── faster_whisper ───────────────────────────────────────────────────────────
faster_whisper_mock = MagicMock()
faster_whisper_model_instance = MagicMock()
faster_whisper_mock.WhisperModel.return_value = faster_whisper_model_instance
sys.modules["faster_whisper"] = faster_whisper_mock

# ── piper ────────────────────────────────────────────────────────────────────
piper_mock = MagicMock()
piper_voice_mock = MagicMock()
piper_voice_instance = MagicMock()
piper_voice_instance.config.sample_rate = 22050
piper_voice_mock.PiperVoice.load.return_value = piper_voice_instance
sys.modules["piper"] = piper_mock
sys.modules["piper.voice"] = piper_voice_mock

# ── pymumble ─────────────────────────────────────────────────────────────────
pymumble_mock = MagicMock()
pymumble_constants_mock = MagicMock()
pymumble_constants_mock.PYMUMBLE_CLBK_SOUNDRECEIVED = "sound"
pymumble_constants_mock.PYMUMBLE_CLBK_TEXTMESSAGERECEIVED = "text"
sys.modules["pymumble_py3"] = pymumble_mock
sys.modules["pymumble_py3.constants"] = pymumble_constants_mock

# ── redis ────────────────────────────────────────────────────────────────────
redis_mock = MagicMock()
sys.modules["redis"] = redis_mock


# ── Fixtures exposing mock objects ───────────────────────────────────────────

@pytest.fixture
def vad_instance_mock():
    webrtcvad_vad_instance.is_speech.side_effect = None
    webrtcvad_vad_instance.is_speech.return_value = False
    return webrtcvad_vad_instance


@pytest.fixture
def whisper_model_instance():
    faster_whisper_model_instance.transcribe.side_effect = None
    faster_whisper_model_instance.transcribe.return_value = ([], {})
    return faster_whisper_model_instance


@pytest.fixture
def piper_voice_inst():
    piper_voice_instance.config.sample_rate = 22050
    piper_voice_instance.synthesize.side_effect = None
    return piper_voice_instance
