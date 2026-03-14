FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libopus0 libopus-dev build-essential wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Whisper small model (~244 MB) at build time so startup is instant
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# Download Piper voice model (~63 MB)
RUN mkdir -p /app/voices && \
    wget -q -O /app/voices/en_US-lessac-medium.onnx \
      "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" && \
    wget -q -O /app/voices/en_US-lessac-medium.onnx.json \
      "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

COPY . .
CMD ["python", "bot.py"]
