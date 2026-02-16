# My-Agent: Self-Hosted AI Agent Stack Setup Guide

A complete walkthrough for building a self-hosted, multi-interface AI agent powered by Ollama, running entirely in Docker containers on your own hardware.

## What You're Building

A personal AI assistant with three ways to interact:

- **CLI** - Chat from your terminal via `agent chat "your message"`
- **Telegram Bot** - Chat from your phone with typing indicators and a boot greeting
- **Web UI** - A Streamlit-based chat interface with RAG document upload, saved chats, and model configuration

All interfaces funnel through a single **agent-core** FastAPI service that talks to **Ollama** for local LLM inference, with optional **ChromaDB** for retrieval-augmented generation (RAG).

### Architecture

```
                         +------------------+
                         |  ollama-runner   |
                         |  (LLM engine)   |
                         +--------+---------+
                                  |
                                  | :11434
                                  |
+-------------+          +--------+---------+          +-----------+
|  telegram-  +--------->+   agent-core     +<---------+  web-ui   |
|  gateway    |  :8000   |   (FastAPI)      |  :8000   | (Streamlit|
+-------------+          +--------+---------+          +-----+-----+
                                  |                          |
                                  | :8000                    | :8000
                                  |                          |
                         +--------+---------+                |
                         |   chroma-rag     +<---------------+
                         |   (ChromaDB)     |
                         +------------------+
```

All services communicate over a private Docker bridge network (`agent_net`). Only the agent-core API (port 8000), ChromaDB UI (port 8100), and web UI (port 8501) are exposed to the host.

### Model Selection

This stack uses **Phi-3 Mini** (`phi3:latest`) as the default model. It's a ~3.8B parameter model that runs well on CPU-only machines with 4-8 GB of RAM. Good alternatives for CPU-only setups:

| Model | Size | Best For |
|---|---|---|
| Phi-3 Mini | ~3.8B | Default brain, planning, chat |
| Llama 3.x (3B) | ~3B | Fast, coherent CPU alternative |
| Qwen 0.5-1.8B | <2B | Background jobs, routing |
| Gemma 2B | ~2B | Lightweight general use |

If you have a GPU, you can step up to Llama 3 8B or Mistral 7B for stronger reasoning.

---

## Prerequisites

- **Docker** and **Docker Compose** installed
- **8+ GB RAM** recommended (4 GB minimum for phi3)
- A **Telegram account** (for the Telegram bot, optional)
- A Linux, Mac, or Windows (WSL2) machine

---

## Step 1: Create the Project Structure

```bash
mkdir -p my-agent/{agent-core,telegram-gateway,web-ui,ollama}
cd my-agent
```

Your directory will look like this when finished:

```
my-agent/
├── docker-compose.yml
├── .env                    # Secrets (never commit this)
├── agent-core/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py              # FastAPI service
│   ├── cli.py              # CLI logic
│   ├── tools.py            # Tool definitions
│   └── agent               # Shell wrapper for CLI
├── telegram-gateway/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── bot.py              # Telegram bot
├── web-ui/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py              # Streamlit chat UI
└── ollama/                  # Optional, for custom Ollama config
```

---

## Step 2: Set Up Ollama (LLM Engine)

No custom code needed here -- we use the official Ollama Docker image.

### Start Ollama

```bash
docker compose up -d ollama-runner
```

### Pull Your Model

```bash
docker exec -it ollama-runner ollama pull phi3
```

### Verify It Works

```bash
docker exec -it ollama-runner ollama run phi3 "Say hello"
```

You should see a response from the model.

---

## Step 3: Build the Agent Core

This is the central hub -- a FastAPI service that wraps Ollama and provides a `/chat` API endpoint.

### agent-core/requirements.txt

```
fastapi==0.115.0
uvicorn==0.32.0
ollama==0.3.3
click==8.1.7
requests==2.32.3
chromadb
```

### agent-core/Dockerfile

```dockerfile
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
```

### agent-core/agent (shell wrapper)

```bash
#!/bin/bash
# Wrapper to make "agent chat ..." work
exec python /app/cli.py "$@"
```

Make it executable:

```bash
chmod +x agent-core/agent
```

### agent-core/app.py (FastAPI service)

```python
from fastapi import FastAPI
from pydantic import BaseModel
from ollama import Client
import chromadb
import os

app = FastAPI()
ollama_client = Client(host='http://ollama-runner:11434')

class ChatRequest(BaseModel):
    message: str
    model: str = "phi3:latest"
    user_id: str = None
    channel: str = None

def rag_tool(query):
    chroma_client = chromadb.HttpClient(host='chroma-rag', port=8000)
    collection = chroma_client.get_collection("rag_data")
    results = collection.query(query_texts=[query], n_results=3)
    return results['documents'][0]

@app.post("/chat")
async def chat(request: ChatRequest):
    print(f"[{request.channel}:{request.user_id}] {request.message}")

    if "search docs" in request.message.lower():
        docs = rag_tool(request.message)
        return {"response": "\n".join(docs)}

    response = ollama_client.chat(
        model=request.model,
        messages=[{"role": "user", "content": request.message}]
    )
    return {"response": response['message']['content']}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### agent-core/cli.py (CLI + service launcher)

```python
import click
import requests
import sys
import subprocess

@click.group()
def cli():
    pass

@cli.command()
@click.argument('message')
@click.option('--model', default='phi3:latest', help='Ollama model')
def chat(message, model):
    """Simple chat via API."""
    resp = requests.post("http://localhost:8000/chat", json={"message": message, "model": model})
    print(resp.json()["response"])

@cli.command()
def serve():
    """Start the FastAPI service."""
    subprocess.run(["python", "app.py"])

if __name__ == "__main__":
    if len(sys.argv) == 1:
        cli(['serve'])  # Default: start service
    else:
        cli()
```

### agent-core/tools.py (tool definitions)

```python
TOOLS = {
    "rag": {"url": "http://chroma-rag:8000", "desc": "Document search"},
    "web_search": {"cmd": "tavily_api_call"},
    "code_exec": {"sandbox": "/workspace"},
    "file_tools": {"dir": "/workspace"}
}
```

### Build, Run, and Test

```bash
docker compose up --build -d agent-core
```

Wait for Ollama to be healthy first (agent-core depends on it), then test:

```bash
# From host (port 8000 is exposed)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, who are you?"}'

# Or via the in-container CLI
docker exec -it agent-core agent chat "Hello from CLI"
```

---

## Step 4: Set Up the Telegram Bot

The Telegram gateway is a thin adapter: it receives messages from Telegram, forwards them to agent-core, and sends the response back.

### Get a Telegram Bot Token

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts to name your bot
3. Copy the token BotFather gives you
4. Send a message to your new bot, then get your chat ID by visiting:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`

### Create the .env file

In the project root, create a `.env` file:

```
TELEGRAM_TOKEN=your_bot_token_here
CHAT_ID=your_chat_id_here
AGENT_URL=http://agent-core:8000
```

> **IMPORTANT:** Never commit this file to version control. Add `.env` to your `.gitignore`.

### telegram-gateway/requirements.txt

```
python-telegram-bot==21.5
requests==2.32.3
```

### telegram-gateway/Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
```

### telegram-gateway/bot.py

```python
import logging
import os
import requests
import datetime
import asyncio
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from zoneinfo import ZoneInfo

# Config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
YOUR_CHAT_ID = int(os.getenv("CHAT_ID", "0"))

MAX_TG_LEN = 4096  # Hard Telegram limit

async def post_init(application):
    """Send a greeting when the bot starts up."""
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour

    if 5 <= hour < 12:
        greeting = "Good Morning"
    elif 12 <= hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    uptime_msg = f"""
**{greeting}!**

**Agent Stack Online:**
- Ollama: phi3:latest loaded
- CLI: `agent chat` ready
- Telegram: Private responses
- RAG: ChromaDB healthy (if enabled)

**Boot:** {now.strftime('%Y-%m-%d %H:%M:%S EST')}
"""

    await application.bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=uptime_msg,
        parse_mode="Markdown"
    )
    logger.info(f"Sent {greeting} message")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only respond to your chat ID
    if YOUR_CHAT_ID and update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Show typing indicator while waiting
    typing_task = asyncio.create_task(_typing_loop(chat_id, context))

    try:
        resp = requests.post(
            f"{AGENT_URL}/chat",
            json={"message": user_message, "model": "phi3:latest"},
            timeout=None,
        )
        resp.raise_for_status()
        reply_text = resp.json()["response"]
    except requests.exceptions.Timeout:
        reply_text = "Agent timed out (took too long)."
    except Exception as e:
        logger.exception("Agent error")
        reply_text = f"Error: {e}"
    finally:
        typing_task.cancel()

    # Send in chunks instead of truncating
    for chunk in _split_message(reply_text, MAX_TG_LEN):
        await update.message.reply_text(chunk)


async def _typing_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep typing status alive until cancelled."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return

def _split_message(text: str, max_len: int):
    """Yield chunks <= max_len, splitting on line breaks or spaces."""
    if len(text) <= max_len:
        yield text
        return

    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
        split_pos = text.rfind("\n", start, end)
        if split_pos == -1:
            split_pos = text.rfind(" ", start, end)
        if split_pos == -1 or split_pos <= start:
            split_pos = end
        yield text[start:split_pos]
        start = split_pos


def main():
    """Non-async main - run_polling handles event loop"""
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN env var required")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))

    logger.info("Telegram bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
```

### Build, Run, and Test

```bash
docker compose up --build -d telegram-gateway
```

Check logs:

```bash
docker compose logs -f telegram-gateway
```

Message your bot on Telegram -- it should reply via agent-core and Ollama.

---

## Step 5: Set Up ChromaDB (RAG)

ChromaDB provides vector storage for retrieval-augmented generation. This lets you upload documents and have the agent search them for context when answering.

No custom code needed -- we use the official ChromaDB Docker image. It's configured in `docker-compose.yml` and is accessible at `http://chroma-rag:8000` on the internal network, and `http://localhost:8100` from the host.

```bash
docker compose up -d chroma-rag
```

---

## Step 6: Set Up the Web UI

A Streamlit-based chat interface with model configuration, RAG document upload, chat history, and streaming responses.

### web-ui/requirements.txt

```
streamlit
ollama
langchain
langchain-community
langchain-chroma
langchain-text-splitters
chromadb
chromadb[all]
requests
```

### web-ui/Dockerfile

```dockerfile
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
```

### web-ui/app.py

This is a large Streamlit application. Key features include:

- **Sidebar** with server config, model selection (temperature, top_p, etc.), chat management, and RAG settings
- **Chat window** with streaming responses and a typing effect
- **RAG panel** for uploading text files or pasting content into the ChromaDB vector store
- **Storage options**: Local ChromaDB, Remote ChromaDB, or No Embeddings
- **Chat persistence**: Save and load named chat sessions via ChromaDB

The web UI connects directly to Ollama via the LangChain `ChatOllama` client for chat, and to ChromaDB for embeddings and document retrieval.

### Build, Run, and Test

```bash
docker compose up --build -d web-ui
```

Open your browser to `http://localhost:8501`.

---

## Step 7: Launch the Full Stack

Start everything at once:

```bash
docker compose up --build -d
```

### Boot Order

Docker Compose handles service dependencies automatically:

1. **ollama-runner** starts first and pulls the model
2. **agent-core** waits for Ollama's healthcheck to pass, then starts
3. **telegram-gateway** waits for agent-core's healthcheck to pass, then starts
4. **chroma-rag** and **web-ui** start alongside or after agent-core

### Verify Everything Is Running

```bash
docker ps
```

You should see all containers with `(healthy)` status for ollama-runner and agent-core.

### Test Each Interface

```bash
# CLI
docker exec -it agent-core agent chat "Hello from CLI"

# API
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello from curl"}'

# Web UI
# Open http://localhost:8501

# Telegram
# Send a message to your bot on Telegram
```

---

## docker-compose.yml (Complete)

```yaml
version: '3.8'

services:
  ollama-runner:
    image: ollama/ollama:latest
    container_name: ollama-runner
    networks:
      - agent_net
    volumes:
      - ollama_data:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  agent-core:
    build: ./agent-core
    container_name: agent-core
    networks:
      - agent_net
    depends_on:
      ollama-runner:
        condition: service_healthy
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped

  telegram-gateway:
    build: ./telegram-gateway
    container_name: telegram-gateway
    networks:
      - agent_net
    depends_on:
      agent-core:
        condition: service_healthy
    env_file:
      - .env
    restart: unless-stopped

  chroma-rag:
    image: chromadb/chroma:latest
    container_name: chroma-rag
    networks:
      - agent_net
    ports:
      - "8100:8000"
    volumes:
      - chroma_data:/chroma/chroma
    command: chroma run --host 0.0.0.0 --port 8000
    restart: unless-stopped

  web-ui:
    build: ./web-ui
    container_name: web-ui
    networks:
      - agent_net
    ports:
      - "8501:8501"
    depends_on:
      - agent-core
      - chroma-rag
    environment:
      - AGENT_URL=http://agent-core:8000
      - CHROMA_URL=http://chroma-rag:8000
    restart: unless-stopped

  redis:
    image: redis:alpine
    restart: unless-stopped

networks:
  agent_net:
    driver: bridge

volumes:
  ollama_data:
  chroma_data:
```

---

## Exposed Ports Summary

| Service | Host Port | Purpose |
|---|---|---|
| agent-core | 8000 | Chat API + CLI access |
| chroma-rag | 8100 | ChromaDB UI / API |
| web-ui | 8501 | Streamlit chat interface |
| ollama-runner | (none) | Internal only via Docker network |
| telegram-gateway | (none) | Outbound to Telegram API only |
| redis | (none) | Internal only (not yet wired) |

---

## Useful Commands

```bash
# View logs for all services
docker compose logs -f

# View logs for a specific service
docker compose logs -f agent-core

# Restart a single service
docker compose restart telegram-gateway

# Rebuild after code changes
docker compose up --build -d

# Stop everything
docker compose down

# Stop and remove volumes (deletes model cache and ChromaDB data)
docker compose down -v

# Check container health
docker inspect agent-core --format '{{json .State.Health}}'

# Pull a different model into Ollama
docker exec -it ollama-runner ollama pull mistral
```

---

## Security Notes

- **Secrets**: Keep your `.env` file out of version control. Add it to `.gitignore`. For production, consider Docker secrets instead.
- **Chat ID filtering**: The Telegram bot only responds to your configured chat ID, ignoring messages from anyone else.
- **Network isolation**: All services communicate over a private Docker bridge network. Only ports you explicitly map are accessible from the host.
- **No host mounts**: The agent containers don't mount host directories, limiting blast radius.
- **Consider hardening**: For production use, run containers as a non-root user, add read-only volume mounts, and consider removing the agent-core port mapping once you rely on the Telegram bot and web UI exclusively.

---

## Roadmap / Future Ideas

Based on the Openclaw-inspired capability tiers this project draws from, potential next steps include:

1. **Soul / Persona file** - Give the agent a persistent identity and personality via a system prompt
2. **Conversation memory** - Wire in Redis for rolling message history per user/session instead of single-shot Q&A
3. **Multi-model routing** - Use phi3 for quick tasks and a larger model (Llama 3 8B) for deep reasoning
4. **Security & observability** - Policy engine, structured tracing, health dashboard, and container hardening — all BEFORE adding skills
5. **Tool calling** - Let the LLM invoke tools (web search, file operations, code execution) with per-skill security, allow-lists, and hard deny-lists
6. **Heartbeat & jobs** - Scheduled tasks, proactive notifications, event-driven automations
7. **Long-term memory** - Store user preferences and facts in ChromaDB for personalized responses

---

## Uninstall / Clean Removal

If you want to tear down the stack and remove everything, follow these steps. Each step is independent — you can stop at any point to keep some artifacts.

### 1. Stop all running containers

```bash
cd my-agent
docker compose down
```

This stops and removes all containers but preserves images, volumes, and your code.

### 2. Remove persistent volumes (model cache, ChromaDB data)

```bash
docker compose down -v
```

The `-v` flag removes the named volumes defined in `docker-compose.yml`:
- `ollama_data` — downloaded Ollama models (phi3, etc.)
- `chroma_data` — ChromaDB vector store and uploaded documents

**This deletes all downloaded models and any documents you uploaded to RAG.** You will need to re-pull models and re-upload documents if you rebuild later.

### 3. Remove built Docker images

Docker Compose builds custom images for agent-core, telegram-gateway, and web-ui. To remove them:

```bash
docker compose down -v --rmi all
```

The `--rmi all` flag removes all images used by the services — both the ones Compose built and the ones it pulled (ollama, chromadb, redis). If you only want to remove the custom-built images and keep the base images:

```bash
docker compose down -v --rmi local
```

### 4. Remove the orphaned Docker network

The private bridge network (`agent_net`) is usually removed by `docker compose down`, but if it lingers:

```bash
docker network rm my-agent_agent_net
```

### 5. Verify nothing is left

```bash
# Check for any remaining containers
docker ps -a | grep -E "agent-core|telegram-gateway|web-ui|ollama-runner|chroma-rag|redis"

# Check for any remaining volumes
docker volume ls | grep -E "ollama_data|chroma_data"

# Check for any remaining images
docker images | grep -E "my-agent|ollama|chromadb|redis"
```

If anything shows up, remove it manually:

```bash
docker rm <container_id>
docker volume rm <volume_name>
docker rmi <image_id>
```

### 6. Delete the project files

```bash
cd ..
rm -rf my-agent
```

**This permanently deletes all source code, the `.env` secrets file, and all documentation.** Make sure you've backed up anything you want to keep before running this.

### Quick one-liner (nuclear option)

To remove everything in a single command — containers, volumes, images, network, and the project directory:

```bash
cd my-agent && docker compose down -v --rmi all && cd .. && rm -rf my-agent
```
