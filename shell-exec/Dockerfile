FROM python:3.12-slim

WORKDIR /app

# Install bash + basic coreutils so commands actually work
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Non-root user — same UID as agent_sandbox volume default
RUN groupadd -r runner && useradd -r -g runner -u 1000 -d /sandbox runner

# Service runs as root so it can bind the port; subprocess runs as runner
# (subprocess drops to runner via user= param — see main.py)
CMD ["python", "main.py"]
