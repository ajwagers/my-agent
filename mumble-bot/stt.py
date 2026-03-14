from faster_whisper import WhisperModel
import numpy as np
from scipy.signal import resample_poly


class WhisperSTT:
    def __init__(self):
        self.model = WhisperModel("small", device="cpu", compute_type="int8")

    def transcribe(self, pcm_48k: bytes) -> str:
        audio_int16 = np.frombuffer(pcm_48k, dtype=np.int16)
        audio_16k = resample_poly(audio_int16, 1, 3).astype(np.int16)  # 48k→16k
        audio_f32 = audio_16k.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(audio_f32, language="en", beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()
