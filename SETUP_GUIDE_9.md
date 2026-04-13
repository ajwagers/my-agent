# Setup Guide 9 — Grafana + Prometheus Monitoring Dashboard

> **Phase 8A** — Covers: replacing the Streamlit health dashboard with Prometheus metrics instrumentation and a Grafana operational dashboard. Adds time-series monitoring, GPU/VRAM visibility, response time percentiles, and a pre-built 12-panel dashboard that auto-loads at startup.
>
> **Prerequisites:** SETUP_GUIDE_8.md complete. Stack running with `docker compose up -d`. Docker + NVIDIA Container Toolkit already configured.

---

## What This Phase Adds

| Capability | Description |
|---|---|
| **Prometheus metrics in agent-core** | Counters, histograms, and gauges exposed at `GET /metrics`. Incremented automatically by the existing tracing layer — no changes to calling code required. |
| **Grafana operational dashboard** | 12-panel "Agent Health" dashboard, auto-provisioned at startup. Opens at `http://localhost:3000`. |
| **Response time percentiles** | p50 / p95 / p99 per model as time-series charts — shows latency distribution, not just averages. |
| **GPU/VRAM monitoring** | Ollama exposes native Prometheus metrics including `ollama_memory_allocated_bytes`. Grafana displays VRAM usage over time. |
| **Queue depth gauge** | Live depth of the Redis chat queue — turns orange at 3, red at 8. |
| **Skill call breakdown** | Per-skill invocation rate as a time-series chart. See which skills are being used and how often. |
| **Alert-ready** | All panels are built with threshold-based colouring. Grafana alerting can be configured to notify via webhook, Telegram, email, etc. |

The old Streamlit dashboard (`http://localhost:8502`) is retained for Redis log browsing and the raw event feed. The two tools complement each other: Grafana for time-series operational monitoring, Streamlit for event-level audit and log browsing.

---

## Step 1 — Add the Grafana Password to `.env`

Open `.env` and add:

```bash
GRAFANA_ADMIN_PASSWORD=choose-a-strong-password
```

Or generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(20))"
```

---

## Step 2 — Rebuild agent-core

`prometheus_client` was added to `agent-core/requirements.txt`. Rebuild the image:

```bash
docker compose build agent-core
```

---

## Step 3 — Bring Up the New Services

```bash
docker compose up -d agent-core prometheus grafana
```

This starts:
1. Rebuilt `agent-core` — now exposes `GET /metrics` and runs the gauge-updater background task
2. `prometheus` — scrapes `agent-core:8000/metrics` and `ollama-runner:11434/metrics` every 15 seconds
3. `grafana` — loads the Prometheus datasource and "Agent Health" dashboard from `grafana/provisioning/`

Check all services are up:

```bash
docker compose ps
```

All three should show `running`. Grafana may take 15–20 seconds to fully start on first boot while it initialises its internal SQLite database.

---

## Step 4 — Verify the Metrics Endpoint

From the host:

```bash
curl -s http://localhost:8000/metrics | head -30
```

You should see Prometheus-format output like:

```
# HELP agent_chat_requests_total Total incoming chat requests
# TYPE agent_chat_requests_total counter
agent_chat_requests_total{channel="telegram",model="phi4-mini:latest"} 12.0
# HELP agent_queue_depth Current depth of the Redis chat queue (queue:chat)
# TYPE agent_queue_depth gauge
agent_queue_depth 0.0
...
```

If the endpoint returns 404, the agent-core rebuild didn't complete cleanly — check `docker logs agent-core --tail 20`.

---

## Step 5 — Verify Prometheus Is Scraping

Check Prometheus targets from the host (Prometheus is internal-only — no host port):

```bash
docker exec prometheus wget -qO- http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E '"health"|"job"'
```

Both `agent-core` and `ollama` jobs should show `"health": "up"`.

If `agent-core` shows `"health": "down"`, check that `agent-core` is reachable from the `prometheus` container:

```bash
docker exec prometheus wget -qO- http://agent-core:8000/metrics | head -5
```

---

## Step 6 — Open Grafana

Navigate to **http://localhost:3000**

- **Username:** `admin`
- **Password:** the value you set in Step 1

### Find the dashboard

The "Agent Health" dashboard is pre-loaded. Two ways to reach it:

1. Click the **Dashboards** icon in the left sidebar → **Browse** → click **Agent Health**
2. Go directly: `http://localhost:3000/d/agent-health`

### Set it as the home page (optional but recommended)

Profile icon (bottom left) → **Preferences** → **Home Dashboard** → select **Agent Health** → **Save**.

---

## Step 7 — Verify Key Panels

Send a few messages through Telegram or Mumble to generate traffic, then check:

| Panel | What to verify |
|---|---|
| **Request Rate (1m)** | Should show a non-zero value after a message is sent |
| **Queue Depth** | Should drop to 0 within a few seconds of a message being processed |
| **Ollama Pending Requests** | Peaks during a response, returns to 0 when done |
| **Chat Requests by Channel** | Should show a line for `telegram` or `mumble` |
| **Response Time p50/p95/p99** | Populates after the first completed response |
| **Ollama GPU Memory (VRAM used)** | Shows current VRAM allocation — confirm it's under 8 GB |

> **Note:** "No data" on first load is normal — time-series panels need at least one scrape interval (15 seconds) of data before they render.

---

## Step 8 — Optional: Configure an Alert

Example: alert when queue depth stays above 5 for 2 minutes (agent is stuck).

1. Open the **Queue Depth** panel → **Edit** (pencil icon)
2. Click **Alert** tab → **New alert rule**
3. Set condition: `agent_queue_depth > 5`
4. Set "For" duration: `2m`
5. Add a notification channel (Alerting → Contact points → add Telegram webhook URL, email, etc.)
6. Save

---

## Troubleshooting

### Grafana shows "Datasource not found"

The provisioning file wasn't loaded. Check:

```bash
docker logs grafana 2>&1 | grep -i "provision\|error"
```

The provisioning volume mount must be:
```
./grafana/provisioning:/etc/grafana/provisioning:ro
```

Verify it in `docker-compose.yml` and restart:
```bash
docker compose restart grafana
```

---

### Metrics endpoint returns 404

The agent-core rebuild didn't include the new `metrics.py` or `app.py` changes. Force a clean rebuild:

```bash
docker compose build --no-cache agent-core
docker compose up -d agent-core
```

---

### Prometheus shows agent-core target as "down"

Check that agent-core is healthy first:

```bash
docker compose ps agent-core
curl -s http://localhost:8000/health
```

If agent-core is healthy but Prometheus can't reach it, check they are on the same Docker network:

```bash
docker inspect prometheus | grep -A5 Networks
docker inspect agent-core | grep -A5 Networks
```

Both should show `my-agent_agent_net` (or whatever your compose project prefix is).

---

### Ollama VRAM panel shows "No data"

Ollama's Prometheus metrics endpoint was added in v0.1.47. Verify:

```bash
docker exec ollama-runner curl -s http://localhost:11434/metrics | head -5
```

If this returns an error, pull a newer Ollama image:

```bash
docker compose pull ollama-runner
docker compose up -d ollama-runner
```

---

### Response time panels empty after several messages

The `agent_chat_response_ms` histogram is only populated by `log_chat_response()` calls. Check that agent-core is logging responses:

```bash
docker logs agent-core 2>&1 | grep "chat" | tail -5
```

If there are no chat log lines, the `tracing.log_chat_response()` call path may not be reached — check for earlier errors in `docker logs agent-core`.

---

## Architecture — How It Fits Together

```
agent-core /metrics  ←── scrapes every 15s ───  prometheus :9090
ollama-runner /metrics ──────────────────────────────────────┘
                                                              │
                                                     queries  │
                                                              ▼
                                                    grafana :3000
                                                  (Agent Health dashboard)

agent-core tracing.py  ──► Redis logs:*  ──►  dashboard :8502
                                               (event feed, audit log)
```

Prometheus handles time-series (what happened over time). The Streamlit dashboard handles event-level detail (what exactly happened in this request). Use both.

---

## What's Next (Phase 8B+)

- **Grafana alerting** — configure Telegram webhook contact point to receive alerts when queue depth spikes, VRAM exceeds threshold, or services go down
- **Phase 8B: Notion Integration** — skill to read/write Notion pages and databases
- **Hardware upgrade path** — RTX 3090/4090 (24 GB VRAM) unlocks `qwen3-coder:30b`

**Full roadmap:** See `PRD.md` Phase 8 section.
