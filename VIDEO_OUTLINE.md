# YouTube Video Outline: Build Your Own Self-Hosted AI Agent Stack

## Video Title Options

- "I Built My Own AI Agent That Runs 100% Locally - Here's How"
- "Build a Self-Hosted AI Agent with Ollama, Telegram, and Docker (Full Tutorial)"
- "Ditch ChatGPT: Build Your Own Private AI Agent Stack from Scratch"

## Target Length: 25-35 minutes

---

## INTRO (2-3 min)

### Hook (0:00 - 0:30)
- Open with a live demo: send a message on Telegram from your phone, show the response coming back
- "What if you had your own AI assistant that runs entirely on your machine, responds on Telegram, has a web UI, and costs you nothing per query?"

### What We're Building (0:30 - 1:30)
- Show the architecture diagram (use a simple slide or whiteboard graphic)
- Walk through the components at a high level:
  - Ollama running a local LLM (phi3)
  - A FastAPI core service that wraps the LLM
  - A Telegram bot you can message from your phone
  - A Streamlit web UI with RAG document upload
  - ChromaDB for vector search
  - All containerized with Docker Compose on a private network
- "Three interfaces, one brain, zero cloud dependency"

### Why This Matters (1:30 - 2:30)
- Privacy: your data never leaves your machine
- Cost: no API fees, runs on consumer hardware
- Control: swap models, add tools, customize everything
- Inspired by projects like Openclaw - the idea of a local-first AI agent that actually does things
- Brief mention: CPU-only is fine, you don't need a GPU

### What You'll Need (2:30 - 3:00)
- Docker and Docker Compose installed
- 8 GB RAM recommended (4 GB minimum)
- A Telegram account (optional, for the bot)
- Basic comfort with the terminal
- "All the code is linked in the description"

---

## PART 1: OLLAMA - THE LLM ENGINE (3-4 min)

### Why Ollama (3:00 - 3:30)
- One-line model downloads, runs inference locally
- Huge model library, easy to swap between models
- CPU-friendly with quantized models
- "Think of Ollama as your local OpenAI API"

### Model Choice (3:30 - 4:30)
- Phi-3 Mini: 3.8B params, runs on 4-8 GB RAM, great for CPU
- Show the Ollama model library briefly
- Mention alternatives: Mistral 7B (if you have more RAM), Llama 3 (if you have a GPU)
- "Start small, you can always pull a bigger model later"

### First Container (4:30 - 6:30)
- Show the `docker-compose.yml` with just ollama-runner
- Explain:
  - Volume for model persistence (`ollama_data`)
  - Healthcheck so other services know when it's ready
  - Private network (`agent_net`)
- Live demo:
  ```
  docker compose up -d ollama-runner
  docker exec -it ollama-runner ollama pull phi3
  docker exec -it ollama-runner ollama run phi3 "What is Docker?"
  ```
- Show the response, celebrate the first milestone
- "We now have a working LLM. Everything else is just plumbing."

---

## PART 2: AGENT CORE - THE BRAIN (5-6 min)

### The Design Philosophy (6:30 - 7:30)
- One central API service, multiple frontends
- All LLM logic lives here, not in the Telegram bot or web UI
- "If you want to add a Discord bot tomorrow, you just call the same /chat endpoint"
- Show the simple request flow: Client -> POST /chat -> Ollama -> Response

### Walk Through the Code (7:30 - 10:00)
- **app.py**: Show on screen, walk through line by line
  - FastAPI app setup
  - Ollama client connecting to `ollama-runner:11434` (Docker DNS)
  - `ChatRequest` model with message, model, user_id, channel
  - `/chat` endpoint: logs the request, calls Ollama, returns the response
  - `/health` endpoint for Docker healthcheck
  - RAG routing: if message contains "search docs", query ChromaDB instead
- **cli.py**: Quick walkthrough
  - Click CLI with `chat` and `serve` commands
  - Default behavior: start the FastAPI server
  - `agent chat "message"` sends to the API
- **Dockerfile**: Show briefly
  - Python 3.12 base, install deps, copy code, install CLI on PATH

### Build and Test (10:00 - 12:00)
- Show the docker-compose.yml with agent-core added
  - `depends_on: ollama-runner: condition: service_healthy`
  - Port 8000 exposed
  - Its own healthcheck
- Live demo:
  ```
  docker compose up --build -d agent-core
  docker ps   # Show both containers healthy
  ```
- Test with curl:
  ```
  curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Explain Docker in one sentence"}'
  ```
- Test with the CLI:
  ```
  docker exec -it agent-core agent chat "Hello from the CLI"
  ```
- Show both responses
- "Two services running, talking to each other over a private network. Now let's make it mobile."

---

## PART 3: TELEGRAM BOT (5-6 min)

### Getting Your Bot Token (12:00 - 13:00)
- Screen recording of Telegram:
  - Open @BotFather
  - Send `/newbot`
  - Name it, get the token
  - Send a message to the bot, get your chat ID from the getUpdates API
- "Put these in a `.env` file in your project root, and never commit this file"
- Show the `.env` structure (with placeholder values, not real secrets)

### Walk Through the Code (13:00 - 15:30)
- **bot.py**: Show on screen, highlight key parts
  - Config from environment variables (TOKEN, AGENT_URL, CHAT_ID)
  - `post_init` greeting: sends a status message when the bot boots
    - Time-aware greeting (morning/afternoon/evening)
    - "A nice touch - you know your stack is alive when your phone buzzes"
  - `handle_message`: the main handler
    - Chat ID filtering: only responds to you
    - Typing indicator loop: keeps "typing..." visible while Ollama thinks
    - Forwards message to agent-core's /chat endpoint
    - Splits long responses into chunks (Telegram has a 4096 char limit)
  - "Notice: zero LLM logic here. It's just a thin adapter."
- **Dockerfile**: Brief flash on screen (python:3.12-slim, pip install, CMD bot.py)

### Build and Test (15:30 - 17:30)
- Show docker-compose.yml with telegram-gateway added
  - `depends_on: agent-core: condition: service_healthy`
  - `env_file: .env`
  - No ports exposed (outbound only)
- Live demo:
  ```
  docker compose up --build -d telegram-gateway
  docker compose logs -f telegram-gateway
  ```
- Show the boot greeting arriving on your phone (screen recording of Telegram)
- Send a message from phone, show the typing indicator, wait for response
- "You now have a private AI assistant in your pocket, running on your own machine"

---

## PART 4: CHROMADB - RAG STORAGE (2-3 min)

### What RAG Does (17:30 - 18:30)
- "RAG lets you feed your own documents to the AI"
- Upload a file -> it gets chunked and embedded -> stored in ChromaDB
- When you ask a question, relevant chunks are retrieved and included as context
- Simple diagram: Question -> Vector Search -> Context + Question -> LLM -> Answer

### Setup (18:30 - 19:30)
- Official ChromaDB Docker image, no custom code
- Show the compose config:
  - Port 8100 on host (8000 internal)
  - Persistent volume for data
- Live demo:
  ```
  docker compose up -d chroma-rag
  ```
- "That's it. ChromaDB is ready. The web UI will handle the upload interface."

---

## PART 5: WEB UI (4-5 min)

### What Streamlit Gives Us (19:30 - 20:00)
- Full chat interface with no frontend code
- Sidebar for configuration
- File upload widgets for RAG
- "Streamlit turns a Python script into a web app"

### Walk Through Key Features (20:00 - 22:00)
- Show `app.py` on screen, highlight sections (don't read every line):
  - **Sidebar config**: Ollama host URL, model selection dropdown, temperature/top_p sliders
  - **Chat window**: Message history, streaming responses with typing effect, regenerate button
  - **RAG panel**: File upload for txt/md/py/json/yaml, manual text input, vector store rebuild
  - **Storage options**: Local ChromaDB, Remote ChromaDB, or no embeddings
  - **Chat persistence**: Save/load named conversations through ChromaDB
- Show the Dockerfile: slim Python, system deps for Chroma, Streamlit entrypoint

### Build and Test (22:00 - 24:00)
- Show docker-compose.yml with web-ui added
  - Depends on agent-core and chroma-rag
  - Port 8501 exposed
  - Environment vars for internal service URLs
- Live demo:
  ```
  docker compose up --build -d web-ui
  ```
- Open browser to `http://localhost:8501`
- Screen recording walkthrough:
  - Connect to Ollama, select phi3 model
  - Send a chat message, show streaming response
  - Upload a text file to RAG
  - Ask a question about the uploaded document
  - Adjust temperature, show how responses change

---

## PART 6: FULL STACK LAUNCH (2-3 min)

### Bring It All Together (24:00 - 25:30)
- "Let's tear it all down and launch the complete stack"
  ```
  docker compose down
  docker compose up --build -d
  docker ps
  ```
- Show all 6 containers running, healthchecks passing
- Quick-fire demo hitting all three interfaces:
  1. CLI: `docker exec -it agent-core agent chat "What is RAG?"`
  2. Telegram: send a message from phone
  3. Web UI: send a message in browser
- "Three interfaces, one brain, your hardware, your data"

### Port Map Recap (25:30 - 26:00)
- Quick table on screen:
  - `localhost:8000` - Agent API
  - `localhost:8100` - ChromaDB
  - `localhost:8501` - Web UI
  - Ollama, Telegram, Redis: internal only

---

## PART 7: WHAT'S NEXT (2-3 min)

### Security Tips (26:00 - 27:00)
- Keep `.env` out of git
- Chat ID filtering locks Telegram to you
- Private Docker network keeps services isolated
- For production: non-root containers, read-only mounts, consider removing the agent-core port
- "Security comes before capability in our roadmap — we build the guardrails before giving the agent tools"

### Future Upgrades (27:00 - 28:00)
- Conversation memory and a soul/persona file (rolling history + agent identity)
- Security framework, policy engine, and a health dashboard — built BEFORE tool calling
- Tool calling with per-skill security (web search, file operations, code execution)
- Multi-model routing (phi3 for fast tasks, larger model for deep reasoning)
- Scheduled tasks, heartbeat loop, and proactive notifications
- "This is layer 1 of a full Openclaw-style agent - we'll build up from here"

### Call to Action (28:00 - 28:30)
- "All the code and a full setup guide are linked in the description"
- "If you want to see me add tool calling or conversation memory in the next video, drop a comment"
- Like/subscribe/etc.

---

## PRODUCTION NOTES

### B-Roll / Visuals Needed
- Architecture diagram (clean version for slides)
- Terminal recordings for all demo sections (consider using asciinema or OBS)
- Phone screen recording for Telegram demos
- Browser screen recording for web UI demo
- Simple slides for: model comparison table, port map, request flow diagrams

### Editing Notes
- Cut long `docker compose build` waits, use jump cuts
- Add chapter markers matching the sections above
- Lower-third labels when showing code files ("agent-core/app.py", etc.)
- Speed up model pull if it takes too long, note "this takes a few minutes" with text overlay

### Description Template
```
Build your own self-hosted AI agent that runs 100% locally using
Ollama, FastAPI, Telegram, Streamlit, and Docker.

Code & Setup Guide: [GITHUB_LINK]

TIMESTAMPS:
0:00 - Intro & Demo
3:00 - Part 1: Ollama (LLM Engine)
6:30 - Part 2: Agent Core (FastAPI API)
12:00 - Part 3: Telegram Bot
17:30 - Part 4: ChromaDB (RAG)
19:30 - Part 5: Web UI (Streamlit)
24:00 - Part 6: Full Stack Launch
26:00 - Part 7: What's Next

TECH STACK:
- Ollama + Phi-3 Mini (local LLM)
- FastAPI (agent core API)
- python-telegram-bot (Telegram gateway)
- Streamlit (web UI)
- ChromaDB (vector DB for RAG)
- LangChain (LLM orchestration)
- Docker Compose (container orchestration)

#AI #SelfHosted #Ollama #Docker #AIAgent #LocalLLM #Tutorial
```

### Thumbnail Ideas
- Split screen: phone with Telegram on left, terminal on right, "100% LOCAL" text overlay
- Docker whale icon + Ollama icon + Telegram icon with arrows between them
- "Build Your Own ChatGPT" with a crossed-out cloud icon
