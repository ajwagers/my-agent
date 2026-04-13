# Setup Guide 10 — Model Upgrade: qwen3:8b → gemma4:e4b

> **Post-Phase 8A patch** — Covers: replacing qwen3:8b with Google's Gemma 4 E4B as the REASONING_MODEL, TOOL_MODEL, and CODING_MODEL. Eliminates the tool-forcing regex maintenance burden and unlocks a 128K context window.
>
> **Prerequisites:** SETUP_GUIDE_9.md complete. Stack running with `docker compose up -d`.

---

## Why This Change

qwen3:8b required increasingly complex workarounds to get reliable tool calling:

- `think=False` parameter to suppress the extended reasoning mode that caused the model to reason itself out of calling tools
- 15+ regex signal patterns in `_tool_forcing_directive()` to inject "You MUST call this tool" directives
- Custom nudge logic in `skill_runner.py` to retry when the model answered from training data
- Model-specific comments scattered across the codebase

Every new skill added more patterns. The tool-forcing file grew longer than most skills. And it still missed edge cases.

gemma4:e4b solves this at the architecture level:

| | qwen3:8b | gemma4:e4b |
|---|---|---|
| Params | 8B | 4.5B effective (26B MoE) |
| VRAM (GTX 1070) | ~5.2 GB (74% GPU / 26% CPU) | ~6 GB (fits in 8 GB with headroom) |
| Context window | 32K | **128K** |
| Native function calling | Unreliable — required `think=False` + regex forcing | Native, architecture-level |
| Tool use benchmark (τ2-bench Retail) | ~6.6% (Gemma 3 class) | **86.4%** (31B) / strong across all sizes |
| Multimodal | No | Yes (for future vision use) |

---

## Step 1 — Update Ollama

gemma4:e4b requires Ollama v0.18 or later. The container was running v0.17.0 which cannot pull it.

```bash
docker compose pull ollama-runner
docker compose up -d ollama-runner
```

Wait for the container to restart, then confirm the version:

```bash
docker exec ollama-runner ollama --version
```

Should show `0.18.x` or later.

---

## Step 2 — Pull gemma4:e4b

```bash
docker exec ollama-runner ollama pull gemma4:e4b
```

This downloads approximately **9.6 GB**. On a home connection this may take 5–15 minutes. The model will be stored in the `ollama_data` named volume.

Track progress — the pull command shows a live progress bar inside the container. If you want to watch from the host:

```bash
docker exec ollama-runner ollama list
```

When complete, `gemma4:e4b` should appear in the list.

---

## Step 3 — Rebuild and Restart

The model name changes are in `docker-compose.yml` environment variables. No code changes are needed — the defaults in `app.py` were also updated, but docker-compose takes precedence.

Rebuild agent-core and open-brain-mcp (the two services with model references):

```bash
docker compose up -d --build agent-core open-brain-mcp
```

Confirm both are running:

```bash
docker compose ps agent-core open-brain-mcp
```

---

## Step 4 — Verify the Model Is Being Used

Send a message through Telegram or the CLI:

```bash
docker exec -it agent-core python cli.py chat "what model are you using right now?"
```

The response JSON includes `"model": "gemma4:e4b"`. Confirm it matches.

You can also check the Grafana dashboard at **http://localhost:3000**. The **Chat Requests by Channel** and **Response Time by Model** panels show the active model name as a label.

---

## Step 5 — Smoke Test Tool Calling

The most important thing to verify is that gemma4:e4b calls tools reliably without the old `think=False` + signal-pattern machinery.

Run a few real-time queries that previously required tool forcing with qwen3:8b:

```bash
# Should trigger web_search automatically
docker exec -it agent-core python cli.py chat "what's the current Ollama version?"

# Should trigger calculate skill
docker exec -it agent-core python cli.py chat "what is 847 * 23?"

# Should trigger calendar_read (if configured)
docker exec -it agent-core python cli.py chat "what's on my calendar this week?"
```

Each should result in `tool_iterations > 0` in the trace output (visible in `docker logs agent-core --tail 10`).

---

## Troubleshooting

### `docker exec ollama-runner ollama pull gemma4:e4b` says "Please download the latest version"

Ollama inside the container is too old. Make sure you ran `docker compose pull ollama-runner` **before** attempting the pull.

```bash
docker compose pull ollama-runner
docker compose up -d ollama-runner
# Wait 10 seconds for restart
docker exec ollama-runner ollama pull gemma4:e4b
```

---

### agent-core logs show `model "gemma4:e4b" not found`

The model was not pulled, or was pulled into a different Ollama instance. Verify:

```bash
docker exec ollama-runner ollama list | grep gemma4
```

If it's missing, re-run the pull from Step 2.

---

### Responses are slow on the first query after container restart

gemma4:e4b is ~9.6 GB. Cold load from disk takes longer than qwen3:8b (5.2 GB). After the first query warms the model into VRAM, subsequent responses are fast. This is normal — Ollama's `keep_alive` setting controls how long the model stays loaded.

---

### VRAM exceeds 8 GB (out of memory)

gemma4:e4b's minimum VRAM is 6 GB, leaving ~2 GB headroom on the GTX 1070. If other GPU processes are running (e.g., another Ollama model not yet unloaded), you may hit the ceiling.

Check current VRAM allocation:

```bash
docker exec ollama-runner nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
```

Or check the Grafana **Ollama GPU Memory (VRAM used)** panel.

If VRAM is full, make sure only one model is loaded at a time. Ollama unloads models after `keep_alive` timeout — by default this is 5 minutes of inactivity.

---

## What Changed in the Codebase

| File | Change |
|---|---|
| `docker-compose.yml` | `REASONING_MODEL`, `TOOL_MODEL`, `CODING_MODEL` (agent-core) + `LLM_MODEL` (open-brain-mcp) → `gemma4:e4b` |
| `agent-core/app.py` | Default fallback values for `REASONING_MODEL`, `TOOL_MODEL`, `CODING_MODEL` → `gemma4:e4b` |
| `open-brain-mcp/metadata.py` | `REASONING_MODEL` default → `gemma4:e4b`; module docstring updated |
| `web-ui/app.py` | Routing hint help text updated to reflect new model names |
| `agent-core/tests/test_skills.py` | `PythonExecSkill` test fixture `reasoning_model` → `gemma4:e4b` |

The `think=False` flag in `skill_runner.py` is retained — it is harmless if the model doesn't expose a thinking mode, and may still be beneficial for any future model swaps.

---

## Architecture — Model Routing After This Change

```
User message (any channel)
  → route_model()
      model="deep"      → qwen2.5:14b      (32K ctx, long context)
      model="reasoning" → gemma4:e4b       (128K ctx, native tool calling)
      model="code"      → gemma4:e4b       (128K ctx, native tool calling)
      model=null + skills registered:
        coding keywords → gemma4:e4b       (128K ctx)
        no keywords     → gemma4:e4b       (128K ctx)
      model=null + no skills:
        coding keywords → gemma4:e4b
        reasoning kw    → gemma4:e4b
        else            → phi4-mini:latest  (fast, 3.8B, no tools)
```

---

## What's Next

- **Observe tool calling in production** — watch Grafana for tool call rates per skill. If gemma4:e4b's native function calling is as reliable as benchmarks suggest, `_tool_forcing_directive()` can be significantly trimmed in a future cleanup pass.
- **Context window upgrade** — NUM_CTX is currently set to 32768. gemma4:e4b supports 128K. Raising it would allow much longer conversation histories before truncation, at the cost of additional VRAM. Monitor VRAM headroom before increasing.
- **Phase 8B** — Notion integration or next integration target.

**Full roadmap:** See `PRD.md` Phase 8 section.
