FROM shenxn/protonmail-bridge:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    libfido2-1 \
    && rm -rf /var/lib/apt/lists/*
