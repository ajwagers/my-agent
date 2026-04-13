# My-Agent / Summit Pine — User's Manual

> Personal AI assistant for Andy & Summit Pine Small-Batch Soap & Skincare.
> Last updated: March 2026

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Interfaces Overview](#2-interfaces-overview)
3. [Telegram Bot](#3-telegram-bot)
4. [Mumble Voice Chat](#4-mumble-voice-chat)
5. [CLI](#5-cli)
6. [Streamlit Dashboard](#6-streamlit-dashboard)
7. [Skills Reference](#7-skills-reference)
8. [Receipt & Document Ingestion](#8-receipt--document-ingestion)
9. [Hours Tracking](#9-hours-tracking)
10. [Inventory & Quick Ingest](#10-inventory--quick-ingest)
11. [Memory System](#11-memory-system)
12. [Persona System](#12-persona-system)
13. [Approval System](#13-approval-system)
14. [Scheduled Jobs](#14-scheduled-jobs)
15. [Model Routing](#15-model-routing)
16. [Privacy & Channel Trust Model](#16-privacy--channel-trust-model)
17. [Tips & Gotchas](#17-tips--gotchas)

---

## 1. Quick Start

| What you want to do | How |
|---|---|
| Ask a question | Send any message in Telegram |
| Look up current info or prices | Just ask — web search is automatic |
| Log a receipt | Photo → Telegram, or Dashboard → Costs → Scan Receipt |
| Update inventory | "Update coconut oil to 3.2 kg" in Telegram, or Dashboard → Inventory |
| Log hours worked | "I worked 4 hours today on production" in Telegram |
| Check P&L | Dashboard → Costs, or "What's the profit summary for March?" in Telegram |
| Save a fact | `/remember Andy prefers grams over ounces` in Telegram |
| Switch to business persona | `/switch summit_pine` in Telegram |
| Run a calculation | "What is 450 × 0.38 + 12?" |
| Look up the FAQ | Dashboard → FAQ tab, or "Do we have an FAQ entry about shipping?" |

---

## 2. Interfaces Overview

The agent is accessible through three primary interfaces. All share the same brain, memory, and skill set.

| Interface | Address | Trust Level | Notes |
|---|---|---|---|
| Telegram Bot | Your personal chat | Private | Primary interface — full access |
| Mumble Voice | Mumble server (owner cert) | Private | Voice in, voice out |
| Streamlit Dashboard | http://localhost:8504 | Private | Web UI for Summit Pine data |
| CLI (terminal) | `agent chat "..."` | Private | Quick scripted queries |

---

## 3. Telegram Bot

Telegram is the main way to talk to the agent. Send messages naturally — no special syntax required for most things.

### Basic Chat

Just write as you would to a person:

```
You: What's the price of sodium hydroxide on Brambleberry right now?
You: How many grams of lye do I need for a 500g batch with 5% superfat using olive and coconut oil?
You: Summarize the last 3 orders
You: What was the weather like in Denver last Tuesday?
```

The agent picks the right model and skills automatically.

### Slash Commands

| Command | What it does |
|---|---|
| `/remember <text>` | Save a fact to long-term memory |
| `/switch <persona>` | Switch the agent persona |
| `/switch` | List available personas |

Examples:

```
/remember My Brambleberry account number is 445-ABC
/remember Cure time for bastille bars is 6 weeks
/switch summit_pine
/switch default
/switch
```

### Sending Photos

Take a photo of a receipt and send it directly — the agent will OCR it and log the expenses. You can add a caption to give context:

```
[Photo of receipt]
Caption: This is the Brambleberry order for the shampoo bar batch
```

Without a caption, the default action is: "Please extract and log the expenses from this receipt."

### Sending PDFs and Image Files

Send any PDF or image (JPEG, PNG, WebP) as a **document** (not compressed photo):

- PDF: text is extracted automatically, then processed
- Image sent as document: treated the same as a photo for OCR

Tip: In Telegram, use "Send as File" (the paperclip → File path) to avoid compression.

### What the Bot Does With Incoming Files

| File type | What happens |
|---|---|
| Compressed photo | OCR + expense extraction (or custom caption instruction) |
| Image sent as document | Same as photo |
| PDF sent as document | Text extracted → processed per caption or default |
| Plain text message | Processed directly |

---

## 4. Mumble Voice Chat

Voice input uses Whisper (speech-to-text) and Piper (text-to-speech). Responses are spoken in plain prose — no markdown, no bullet points, no code.

### Push-to-Talk

Press and hold your PTT key while speaking. The agent detects when you stop (VAD — Voice Activity Detection) and begins processing.

### Channels

| Channel | Who | Access |
|---|---|---|
| `mumble_owner` | Andy (cert-authenticated) | Full private access — all skills |
| General / guest | Anyone | General questions only |

Owner access is tied to your Mumble client certificate, not a password.

### Saving Something to Memory by Voice

After the agent finishes a response, say any of:

```
Save that
Remember that
Add that to memory
```

The agent will store the response content in long-term memory.

### Voice Tips

- Speak at a normal pace — the VAD waits for a natural pause, not a hard button release
- For long agent responses, the progress ticks come at 30s, 90s, and 180s — the agent is thinking
- The agent keeps voice answers to 1–4 sentences when responding in Mumble; if you need more detail, switch to Telegram

---

## 5. CLI

Use the `agent` command for quick terminal queries or scripting.

```bash
agent chat "What is 15% of 847?"
agent chat --reason "Explain the saponification process for olive oil"
agent chat --model deep "Summarize this long document: ..."
```

| Flag | Model used | When to use |
|---|---|---|
| (none) | phi4-mini (fast) | Quick questions |
| `--reason` | qwen3:8b | Reasoning, tool calls, code |
| `--model deep` | qwen2.5:14b | Long context, complex analysis |

CLI runs with full private trust — same as Telegram.

---

## 6. Streamlit Dashboard

Open http://localhost:8504 in your browser.

The dashboard is organized into tabs. Each tab talks to the same backend as Telegram.

### Dashboard Tab

At-a-glance business health:

- Revenue this month
- Orders this month (and how many are pending)
- Low stock alerts (highlighted in red when critical)
- Recent orders table
- Batches currently curing or ready to ship

### Inventory Tab

Full inventory list with a category filter dropdown.

**Update a single item:**
Fill in item name + new quantity → click Update.

**Quick Ingest (powerful shortcut):**
Paste free-form notes in natural language. The agent parses them and routes to the right skill.

```
Coconut oil: 5 kg
Shea butter: 2 kg, $28.50
Castor oil 500 mL
Brambleberry order — lye 2 lb, cocoa butter 1 lb, fragrance oils 4 oz each
```

Use the radio button to tell the agent what to do:
- **Auto-detect** — agent decides (inventory update, expense log, or both)
- **Inventory update** — force an inventory quantity update
- **Expense log** — force expense logging

### Batches Tab

View all production batches. Filter by status (curing, ready, shipped, etc.).

**Record a new batch:**

| Field | Example |
|---|---|
| Batch number | SP-2026-031 |
| Product type | Bastille Bar |
| Date | 2026-03-19 |
| Quantity | 48 bars |
| QC notes | pH 8.5 at pour |

Cure date is auto-calculated based on product type.

**Update batch status:**
Enter batch number, new status, optional pH test result and QC notes.

### Orders Tab

Order list with status and channel filters. Click any row to see the full order JSON.

**Update an order:**
Enter order ID, new status, and optional tracking number.

### Costs Tab

Monthly financial overview.

**P&L Summary:**
- Revenue, Expenses, Gross Profit, Margin % for the selected month

**Expenses Table:**
- All expenses for the month with category breakdown
- Pie chart by category

**Manual Expense Log:**
Fill in the form: date, amount, category, vendor, description.

**Scan Receipt:**
Upload a receipt image (JPEG, PNG, WebP, BMP, TIFF) or PDF. Add an optional note for context. Click "Scan & Log Receipt" — the agent OCRs or extracts the text and logs the line items automatically.

**Batch COGS Calculator:**
Enter a batch number to see a full ingredient cost breakdown (unit cost × grams used for each ingredient).

### Hours Tab

Track labour time for yourself (or anyone who works on Summit Pine).

**Monthly Summary:**
Hours by person + labour cost for the selected month.

**Log Hours Form:**

| Field | Example |
|---|---|
| Date | 2026-03-19 |
| Person | Andy |
| Hours | 3.5 |
| Hourly rate | 25.00 |
| Task description | Production — bastille batch SP-2026-031 |

You can also log hours via Telegram (see Section 9).

### Sales Tab

Date-range KPI metrics:
- Total Revenue
- Total Orders
- Average Order Value
- Refunds

Charts:
- Weekly revenue bar chart broken down by channel (Etsy, direct, wholesale, etc.)
- Revenue by channel pie chart
- Top products table (from itemised order data)

### Recipes Tab

Browse all production recipes. Filter by tag (e.g., "shampoo", "cold process", "lotion").

Expand any recipe card to see:
- Full ingredient list with amounts
- Step-by-step instructions
- Yield, prep time, tags

**Add a recipe:**
Fill in the form (name, yield, prep time, tags, ingredients as CSV lines, instructions), or just tell the agent via Telegram:

```
Add a recipe for lavender shampoo bar: 300g coconut oil, 200g castor oil,
150g lye, 350g distilled water, 20mL lavender EO. Hot process, 45 min,
yields 20 bars.
```

### Promos Tab

Manage discount codes and promotions.

**Active promotions table:**
Toggle to show all (including expired/deactivated).

**Create a promotion:**

| Field | Example |
|---|---|
| Name | Spring 2026 Sale |
| Code | SPRING20 |
| Discount type | Percentage |
| Value | 20 |
| Start date | 2026-03-20 |
| End date | 2026-04-05 |
| Applies to | All products |

**Deactivate:** Enter the promotion ID and click Deactivate.

### FAQ Tab

Browse and search the customer FAQ.

- Filter by category and keyword
- Expand any card to read the full Q&A, guardrail label, and usage count
- Add new FAQ entries via the form

---

## 7. Skills Reference

The agent has 24 skills. Most are invoked automatically based on what you ask — you don't need to name them explicitly.

### General Skills

| Skill | What it does | Invoked by |
|---|---|---|
| **web_search** | Real-time web search (Brave + Tavily fallback) | Any question about current events, prices, news |
| **rag_search** | Semantic search over uploaded documents | "Search my documents for..." |
| **rag_ingest** | Add text to the document knowledge base | "Add this to my knowledge base: ..." |
| **url_fetch** | Fetch and extract text from a URL | "Summarize this page: https://..." |
| **file_read** | Read files from /sandbox | "Read the file report.csv from sandbox" |
| **file_write** | Write files to /sandbox | "Save this as notes.txt in sandbox" |
| **pdf_parse** | Extract text from PDFs in /sandbox | Automatic when you send a PDF |
| **remember** | Save a fact to ChromaDB memory | `/remember ...` or "Remember that ..." |
| **recall** | Retrieve facts from ChromaDB memory | "Do you remember what I said about ...?" |
| **memory_capture** | Save to pgvector Open Brain memory | Automatic after important responses |
| **memory_search** | Search pgvector Open Brain memory | Automatic context injection |
| **calculate** | Safe math evaluator | Any arithmetic or formula |
| **convert_units** | Unit conversion (pint-backed) | "Convert 2.5 lb to grams" |
| **python_exec** | Run sandboxed Python (always needs approval) | "Run this Python script: ..." |
| **calendar_read** | Read Outlook or Proton calendar events | "What's on my calendar this week?" |
| **calendar_write** | Create/update/delete calendar events | "Add a dentist appointment for Friday 2pm" |
| **create_task** | Schedule recurring or one-off jobs | "Remind me every Monday to check inventory" |
| **list_tasks** | List scheduled jobs | "List my scheduled tasks" |
| **cancel_task** | Cancel a scheduled job | "Cancel task abc-123" |

### Summit Pine Business Skills

| Skill | What it does | Invoked by |
|---|---|---|
| **sp_inventory** | Manage inventory, update quantities, track batches | "Update coconut oil to 4 kg", "What's the inventory?" |
| **sp_orders** | Create, look up, update orders | "Look up order #1042", "Mark order 1055 as shipped" |
| **sp_faq** | Search/manage customer support FAQ | "Do we have an FAQ about international shipping?" |
| **sp_costs** | Log expenses, view P&L, compute batch COGS | "Log a $42 expense for ULINE packaging" |
| **sp_time_log** | Track labour hours | "I worked 3 hours today on production" |
| **sp_recipes** | Manage production recipes | "Show me the bastille bar recipe" |
| **sp_promotions** | Manage discount codes and promotions | "Create a 15% off code WELCOME15" |

### Example Skill Invocations via Telegram

```
"What's the current price of shea butter on Majestic Mountain Sage?"
→ web_search

"How many grams is 1.5 lb?"
→ convert_units

"What is (450 × 0.38) + (200 × 0.52)?"
→ calculate

"What's on my calendar for the next 7 days?"
→ calendar_read  (private channel only)

"We got an order from Jane Smith, 3 bars of pine tar soap, ship to Portland"
→ sp_orders

"Show me the COGS for batch SP-2026-028"
→ sp_costs → batch_cogs

"What's the profit margin for March?"
→ sp_costs → profit_summary
```

---

## 8. Receipt & Document Ingestion

### Via Telegram

**Photo of a receipt:**
```
[Send photo]
Caption: Brambleberry order — supplies for shampoo batch
```
No caption? Default action is to extract and log all expenses from the receipt.

**PDF as document:**
```
[Attach PDF as File]
Caption: March ULINE invoice
```

**Image as document (lossless):**
```
[Attach .jpg as File]
```
Treated the same as a photo — OCR is applied.

**Plain text:**
```
Bought lye from Brambleberry $18.50
Ordered packaging from ULINE $42.00
Coconut oil from Costco $24.99
```
The agent extracts line items and logs them.

### Via Dashboard (Costs Tab → Scan Receipt)

1. Click the **Costs** tab
2. Scroll to the **Scan Receipt** section
3. Upload image (JPEG, PNG, WebP, BMP, TIFF) or PDF
4. Add an optional note (e.g., "This is for batch SP-2026-030 supplies")
5. Click **Scan & Log Receipt**
6. Review the extracted line items and confirm

### Supported File Formats

| Format | Via Telegram | Via Dashboard |
|---|---|---|
| JPEG / JPG | Yes (photo or file) | Yes |
| PNG | Yes (as file) | Yes |
| WebP | Yes (as file) | Yes |
| BMP | No | Yes |
| TIFF | No | Yes |
| PDF | Yes (as file) | Yes |

---

## 9. Hours Tracking

### Via Telegram (Natural Language)

The agent understands a wide range of phrasings:

```
I worked 3 hours today on production
Worked 4.5 hours this morning on packaging
Log 2.5 hours yesterday, admin work
I started at 9am and finished at 2pm — labelling
Started work at 8:30, stopped at noon, bastille batch SP-2026-031
Show me the time summary for March
How many hours have I logged this week?
```

The agent:
- Parses hours or start/end time (and does the subtraction for you)
- Defaults date to today if not specified
- Defaults person to Andy
- Logs against the task description you provide

### Via Dashboard (Hours Tab)

Fill in the **Log Hours** form directly:

| Field | Required | Example |
|---|---|---|
| Date | Yes | 2026-03-19 |
| Person | Yes | Andy |
| Hours | Yes | 3.5 |
| Hourly rate | Yes | 25.00 |
| Task description | Yes | Production — bastille bar batch |

### Viewing Hours

**Telegram:**
```
Show me my hours for March
What's my total labour cost this month?
Hours summary for the last 4 weeks
```

**Dashboard:** Hours tab shows monthly summary by person with total labour cost and a time log table.

---

## 10. Inventory & Quick Ingest

### Via Telegram

```
Update coconut oil quantity to 3.2 kg
Add 2 kg shea butter to inventory
What's the current stock of lye?
Show me all low stock items
List inventory by category
We used 450g coconut oil and 200g castor oil for batch SP-2026-031
```

### Via Dashboard (Inventory Tab → Quick Ingest)

Paste free-form notes in any format:

```
Coconut oil: 5 kg
Shea butter: 2 kg, $28.50 from Majestic Mountain Sage
Castor oil 1 L
Lye (NaOH) 2 lb — from Brambleberry, $9.99
```

Select the ingestion mode:
- **Auto-detect**: Agent decides whether to update inventory quantities, log expenses, or both. Best for mixed notes.
- **Inventory update**: Only update quantities.
- **Expense log**: Only log the financial entries.

Click **Ingest with Agent** and review the result.

---

## 11. Memory System

The agent has three memory layers that work together transparently.

### Three Layers

| Layer | What it stores | How long |
|---|---|---|
| Redis rolling window | Recent conversation (~6000 tokens) | Current session |
| ChromaDB `agent_memory` | Explicitly saved facts | Permanent |
| Open Brain MCP (pgvector) | Semantic memory; auto-injected into context | Permanent |

For most purposes you only need to think about the ChromaDB layer — the others are automatic.

### Saving a Memory

**Telegram command:**
```
/remember Andy's preferred lye supplier is Brambleberry
/remember Cure time for castile soap is 6 weeks minimum
/remember The etsy shop URL is etsy.com/shop/summitpine
```

**Natural language in chat:**
```
Remember that I prefer metric units for all recipes
Please remember that Jane is our wholesale buyer at Sage & Cedar Spa
```

**Mumble voice:**
Say "Save that" or "Remember that" after any bot response — the response content is stored.

### Searching Memory

```
Do you remember my tea preference?
What do you know about my workshop setup?
What did I say about cure times?
Have I logged anything about wholesale customers?
```

### Tips

- Be specific when saving — "Remember that coconut oil usage rate is 30% for shampoo bars" is more useful than "remember coconut oil"
- Memory is searched semantically — similar phrasing will match even if the exact words differ
- The agent automatically injects relevant memories before answering each question, so you usually don't need to search manually

---

## 12. Persona System

The agent supports multiple personas. Each persona has a different focus, tone, and set of available skills.

### Available Personas

| Persona | Focus | Skills |
|---|---|---|
| `default` | General AI assistant | All 24 skills |
| `summit_pine` | Summit Pine business assistant | Business skills + core utilities |

### Switching Personas

**Telegram:**
```
/switch summit_pine
/switch default
/switch
```

`/switch` with no argument lists all available personas.

### When to Use Each

**default**: General questions, web searches, calculations, calendar, personal tasks, coding help.

**summit_pine**: Business-only mode. Keeps responses focused on Summit Pine inventory, orders, costs, recipes, and FAQ. Filters out personal / off-topic queries.

The persona persists for your session. Switching takes effect immediately.

---

## 13. Approval System

Some actions are potentially irreversible or sensitive. The agent will pause and ask for approval before proceeding.

### Skills That Require Approval

| Skill | Why |
|---|---|
| `python_exec` | Runs arbitrary code in a sandbox — always asks |
| `calendar_write` | Creates, updates, or deletes calendar events |
| `file_write` (Zone 2) | Writes to identity/config files |

### How Approval Works

1. The agent describes what it is about to do
2. Telegram shows **Approve** and **Deny** buttons
3. You have up to **5 minutes** to respond
4. If you tap Approve, the action runs
5. If you tap Deny or the 5 minutes pass, the action is cancelled

### Example

```
You: Create a calendar event for Thursday 3pm — dye testing session
Agent: I'll create a new calendar event:
       Title: Dye Testing Session
       Date: Thursday, March 21, 2026
       Time: 3:00 PM
       [Approve] [Deny]
```

---

## 14. Scheduled Jobs

You can schedule recurring or one-off jobs using natural language. The agent will run these in the background and send you results via Telegram.

### Creating a Job

```
Remind me every Monday at 9am to check inventory
Every day at 8am send me the low stock report
Send me a weekly P&L summary every Friday at 5pm
Remind me in 2 hours to check the bastille batch
```

### Listing Jobs

```
List my scheduled tasks
What recurring jobs do I have?
Show me all my reminders
```

### Cancelling a Job

```
Cancel task abc-123
Stop the weekly P&L reminder
Cancel all my scheduled tasks
```

(When you list tasks, each one shows its job ID. Use that ID to cancel if the name is ambiguous.)

---

## 15. Model Routing

The agent automatically picks the right AI model based on your query. You don't usually need to think about this.

### Auto-Routing

| Query type | Model used | Typical response time |
|---|---|---|
| Quick questions, chat | phi4-mini (2.5 GB) | Fast (a few seconds) |
| Tool calls, reasoning, code | qwen3:8b (5.2 GB) | Moderate (10–30s) |
| Long documents, deep analysis | qwen2.5:14b (9 GB) | Slower (30–90s) |

### Forcing a Model

**Telegram:**
```
Use the deep model: summarize this entire recipe document
Deep: compare these two batch reports
```

**CLI:**
```bash
agent chat --reason "Design a new recipe for a pine tar and charcoal bar"
agent chat --model deep "Analyze the last 6 months of sales data: ..."
```

### Model Notes

- The first cold load of qwen3:8b from disk takes a few minutes. Subsequent queries on that model are fast.
- Models over 15 GB cannot run on this hardware — the agent will return a clear error if attempted.
- If a query is about current events, prices, or anything time-sensitive, the agent uses web_search regardless of which model handles it.

---

## 16. Privacy & Channel Trust Model

The agent enforces strict access control based on where a message comes from.

### Trust Levels

| Interface | Channel ID | Trust | What you can do |
|---|---|---|---|
| Telegram (your chat ID) | `telegram` | Private | Everything |
| CLI terminal | `cli` | Private | Everything |
| Dashboard (port 8504) | `cli` | Private | Everything |
| Mumble — owner cert | `mumble_owner` | Private | Everything |
| Mumble — guest | `mumble` | Public | General questions only |
| Web UI (port 8501) | `web-ui` | Public | General questions only |

### What "Private" Enables

- Access to your personal calendar
- Access to customer order data (sp_orders)
- Access to personal memory and saved facts
- Access to all skills including python_exec and calendar_write
- Full approval workflow

### What "Public" Restricts

- No calendar access
- No customer order data
- No personal memory retrieval
- No file write, no code execution
- Responses are limited to general product/business knowledge and FAQ

### How the Restriction Is Enforced

Privacy is enforced at **three independent layers**:
1. The skill itself checks the channel trust level before executing
2. The memory system filters what context is injected
3. The system prompt explicitly prohibits sharing personal data in public channels

This means no single bug in one layer can expose private data.

---

## 17. Tips & Gotchas

### General

- **Web search is automatic.** You don't need to say "search the web for...". Just ask the question — the agent detects when it needs current information and searches automatically. This covers prices, news, recent events, current leadership, etc.

- **Metric vs imperial.** If you haven't told the agent your preference, it may use either. Save it once: `/remember I prefer metric units for all measurements.`

- **Long responses in Mumble.** Voice responses are intentionally short (1–4 sentences). If you need a detailed answer, ask via Telegram and the agent will give the full version.

- **Python execution always needs approval.** Even simple scripts. This is by design. Tap Approve on the Telegram buttons within 5 minutes.

- **The approval window is 5 minutes.** If you don't respond, the action is cancelled. Just ask again.

- **Receipts with poor lighting OCR badly.** For best results: good lighting, flat surface, all text in frame. The agent will extract what it can and flag unclear items.

### Summit Pine Specific

- **COGS calculation requires batch number.** The batch COGS tool pulls ingredient usage from the batch record. If a batch wasn't recorded with ingredient quantities, COGS won't be accurate.

- **Inventory updates are not reversible via chat.** Double-check quantities before confirming. You can always look up current stock and update again if needed.

- **Orders contain customer PII.** The `sp_orders` skill is only available in private channels. Don't try to access it from the web UI or Mumble guest channel — it won't work.

- **Cure date is auto-calculated.** When you record a new batch, you only need the pour date. The cure date is set based on product type. You can override it if needed by specifying it explicitly.

- **Quick Ingest is forgiving.** It handles mixed formats, abbreviations, and partial information. If it misparses something, you'll see the result before it's committed and can correct it.

### Memory

- **Memory persists across sessions.** Facts saved with `/remember` or "remember that" are permanent until you explicitly delete them.

- **You don't need to search memory manually.** The agent injects relevant memories automatically before answering. You only need to search if you want to browse what's been saved.

- **Be specific with /remember.** "Remember coconut oil" is ambiguous. "Remember that coconut oil usage cap for shampoo bars is 30%" is useful.

### Scheduling

- **Job IDs are shown when you list tasks.** Note them down if you're creating many jobs, so you can cancel specific ones.

- **Scheduled jobs send results to Telegram.** Make sure Telegram notifications are on if you create early-morning reminders.

### Models

- **First qwen3:8b load is slow.** If you haven't used a tool-calling query in a while, the first one might take a minute or two. Subsequent ones are fast.

- **Say "deep:" for long documents.** Prepend "deep:" to your message to route to qwen2.5:14b, which handles long context better: `deep: summarize all these batch notes: ...`

- **Don't request models larger than the hardware supports.** The agent will tell you clearly if a model can't load. Stick to the three available models.

---

*This manual covers all end-user features. For system administration, deployment, or development questions, refer to the project README and architecture documentation.*
