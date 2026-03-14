import time
import webrtcvad

SAMPLE_RATE = 48000
FRAME_MS = 20          # webrtcvad supports 10/20/30ms frames
FRAME_SAMPLES = 960    # 48000 * 0.02
FRAME_BYTES = 1920     # 960 samples × 2 bytes (int16)
SILENCE_TIMEOUT = 0.8  # seconds of in-stream silence → emit utterance
STREAM_TIMEOUT = 0.6   # seconds since last chunk → flush (PTT released)
MIN_SPEECH_SEC = 0.3   # ignore clips shorter than 300ms
MAX_SPEECH_SEC = 30.0  # cap long utterances (force emit)


class UserBuffer:
    """Per-user VAD state and audio accumulator."""

    def __init__(self, username: str):
        self.username = username
        self.vad = webrtcvad.Vad(1)   # 0=permissive … 3=strict; 1 suits Mumble
        self.remainder: bytes = b""
        self.audio: bytes = b""
        self.is_speaking: bool = False
        self.speech_start: float = 0.0
        self.last_voiced_time: float = 0.0
        self.last_chunk_time: float = 0.0  # wall-clock time of last received chunk

    def add_chunk(self, pcm: bytes) -> bytes | None:
        """
        Process incoming PCM chunk. Returns utterance bytes when speech ends
        within the stream, or None if still collecting.
        """
        self.last_chunk_time = time.monotonic()
        data = self.remainder + pcm
        offset = 0
        utterance = None

        while offset + FRAME_BYTES <= len(data):
            frame = data[offset:offset + FRAME_BYTES]
            offset += FRAME_BYTES

            try:
                voiced = self.vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                voiced = False

            now = time.monotonic()

            if voiced:
                if not self.is_speaking:
                    self.is_speaking = True
                    self.speech_start = now
                    self.audio = b""
                self.audio += frame
                self.last_voiced_time = now
            else:
                if self.is_speaking:
                    self.audio += frame
                    silence_duration = now - self.last_voiced_time
                    speech_duration = len(self.audio) / FRAME_BYTES * (FRAME_MS / 1000)
                    # Emit if silence long enough, or utterance hit the cap
                    if silence_duration >= SILENCE_TIMEOUT or speech_duration >= MAX_SPEECH_SEC:
                        if speech_duration >= MIN_SPEECH_SEC:
                            utterance = self.audio
                        self._reset()

        self.remainder = data[offset:]
        return utterance

    def flush_if_stale(self, now: float) -> bytes | None:
        """
        Called by the flush thread. If audio stopped arriving (PTT released)
        and we have buffered speech, emit it.
        """
        if not self.is_speaking:
            return None
        stream_idle = now - self.last_chunk_time
        if stream_idle < STREAM_TIMEOUT:
            return None
        speech_duration = len(self.audio) / FRAME_BYTES * (FRAME_MS / 1000)
        if speech_duration >= MIN_SPEECH_SEC:
            utterance = self.audio
        else:
            utterance = None
        self._reset()
        return utterance

    def _reset(self):
        self.is_speaking = False
        self.speech_start = 0.0
        self.last_voiced_time = 0.0
        self.audio = b""


class VADTracker:
    """Tracks per-session VAD buffers for multiple Mumble users."""

    def __init__(self):
        self._buffers: dict[int, UserBuffer] = {}

    def add_audio(self, session: int, username: str, pcm: bytes) -> bytes | None:
        if session not in self._buffers:
            self._buffers[session] = UserBuffer(username)
        return self._buffers[session].add_chunk(pcm)

    def flush_stale(self) -> list[tuple[str, bytes]]:
        """Return (username, pcm) pairs for any session whose stream went quiet."""
        now = time.monotonic()
        results = []
        for buf in self._buffers.values():
            utterance = buf.flush_if_stale(now)
            if utterance is not None:
                results.append((buf.username, utterance))
        return results

    def remove_session(self, session: int):
        self._buffers.pop(session, None)
