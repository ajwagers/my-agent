FROM python:3.12-slim

WORKDIR /app

# Install system deps for Chroma
RUN apt-get update && apt-get install -y \
    curl wget gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]

