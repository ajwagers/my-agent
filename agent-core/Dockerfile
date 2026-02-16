FROM python:3.12

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Install agent CLI on PATH
RUN chmod +x /app/agent && \
    cp /app/agent /usr/local/bin/agent

# CLI entrypoint
ENTRYPOINT ["python", "cli.py"]

