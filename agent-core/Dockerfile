FROM python:3.12

WORKDIR /app

# Tesseract OCR engine for receipt scanning
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Install agent CLI on PATH
RUN chmod +x /app/agent && \
    cp /app/agent /usr/local/bin/agent

# CLI entrypoint
ENTRYPOINT ["python", "cli.py"]

