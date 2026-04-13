FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN groupadd -r ghapp && useradd -r -g ghapp -u 1001 -d /app -s /sbin/nologin ghapp

USER ghapp

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9002"]
