from piper.voice import PiperVoice
import wave
import io
import numpy as np
from scipy.signal import resample_poly
from math import gcd

VOICE_MODEL = "/app/voices/en_US-lessac-medium.onnx"
OUTPUT_RATE = 48000  # Mumble's required sample rate


class PiperTTS:
    def __init__(self):
        self.voice = PiperVoice.load(VOICE_MODEL)
        self.native_rate = self.voice.config.sample_rate  # typically 22050

    def synthesize(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.native_rate)
            self.voice.synthesize(text, wav)
        buf.seek(44)  # skip WAV header
        pcm = buf.read()
        if self.native_rate == OUTPUT_RATE:
            return pcm
        # Resample to 48000 Hz
        audio = np.frombuffer(pcm, dtype=np.int16)
        g = gcd(OUTPUT_RATE, self.native_rate)
        resampled = resample_poly(audio, OUTPUT_RATE // g, self.native_rate // g)
        return resampled.astype(np.int16).tobytes()
