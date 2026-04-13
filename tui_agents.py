#!/usr/bin/env python3
"""
Agent Insights TUI Dashboard — Agents, Jobs & Personas.

Shows per-model performance, skill usage, persona list, and the live job queue.

Usage:
    python3 tui_agents.py                    # live, refresh every 15 s
    python3 tui_agents.py --once             # single snapshot then exit
    python3 tui_agents.py --interval 5
    python3 tui_agents.py --key <api-key>    # or set AGENT_API_KEY env var
Press q or Ctrl-C to quit.
"""
import argparse
import os
import select
import shutil
import signal
import sys
import termios
import threading
import time
import tty
from datetime import datetime, timezone
from typing import Optional

import requests
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── Config ────────────────────────────────────────────────────────────────────

PROMETHEUS = "http://localhost:9090"
AGENT_API  = "http://localhost:8000"
AGENT_KEY  = ""
REFRESH    = 15

console = Console()
_resize_event = threading.Event()


# ── Terminal size helpers ─────────────────────────────────────────────────────

def _detect_terminal_size() -> tuple[int, int]:
    """
    Return the visible terminal dimensions using os.get_terminal_size().

    The cursor-probe approach (\033[999;999H\033[6n) was removed: VTE/MATE
    Terminal clamps to scrollback buffer height, not the visible viewport,
    producing an inflated row count that makes the layout overflow off-screen.
    os.get_terminal_size() queries TIOCGWINSZ directly — always the visible
    window, no scrollback contamination.
    """
    try:
        sz = os.get_terminal_size(sys.stdout.fileno())
        if sz.columns >= 40 and sz.lines >= 10:
            return sz.columns, sz.lines
    except Exception:
        pass
    try:
        sz = shutil.get_terminal_size()
        return sz.columns, sz.lines
    except Exception:
        return 160, 40


def _setup_resize_handler() -> None:
    def _handler(sig, frame):
        # Clear pins so console.size falls back to os.get_terminal_size(),
        # which the terminal updates before delivering SIGWINCH.
        console._width  = None
        console._height = None
        _resize_event.set()
    try:
        signal.signal(signal.SIGWINCH, _handler)
    except (AttributeError, OSError):
        pass


# ── State & keyboard ──────────────────────────────────────────────────────────

class _State:
    def __init__(self, interval: int = REFRESH):
        self.interval = interval
        self.running  = True


def _keyboard_thread(state: _State) -> None:
    if not sys.stdin.isatty():
        return
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return
    try:
        tty.setraw(fd)
        while state.running:
            r, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not r:
                continue
            ch = sys.stdin.read(1)
            if ch in ("\x03", "q", "Q"):
                state.running = False
    except Exception:
        state.running = False
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, timeout: int = 5) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _agent_get(path: str, params: dict = None) -> Optional[dict]:
    headers = {"X-API-Key": AGENT_KEY} if AGENT_KEY else {}
    try:
        r = requests.get(f"{AGENT_API}{path}", params=params,
                         headers=headers, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _prom_labeled(query: str) -> dict[str, float]:
    data = _get(f"{PROMETHEUS}/api/v1/query", {"query": query})
    if not data:
        return {}
    out = {}
    for item in data.get("data", {}).get("result", []):
        metric = item["metric"]
        label = next(
            (v for k, v in metric.items() if k not in ("__name__", "instance", "job", "le")),
            "unknown",
        )
        out[label] = float(item["value"][1])
    return out


# ── Table builders ────────────────────────────────────────────────────────────

def _model_table() -> Table:
    req_1h  = _prom_labeled("sum by (model) (increase(agent_chat_requests_total[1h]))")
    p50     = _prom_labeled("histogram_quantile(0.50, sum by (le,model) (rate(agent_chat_response_ms_bucket[1h])))")
    p95     = _prom_labeled("histogram_quantile(0.95, sum by (le,model) (rate(agent_chat_response_ms_bucket[1h])))")
    models  = sorted(set(list(req_1h) + list(p50) + list(p95)))

    tbl = Table(title="Model Performance (1h)", box=box.SIMPLE_HEAD, expand=True,
                title_style="bold cyan", header_style="bold")
    tbl.add_column("Model",  style="cyan", no_wrap=True)
    tbl.add_column("Reqs",   justify="right")
    tbl.add_column("p50",    justify="right")
    tbl.add_column("p95",    justify="right")

    if not models:
        tbl.add_row("[dim]no data[/dim]", "—", "—", "—")
    else:
        for m in models:
            tbl.add_row(
                m,
                f"{req_1h[m]:.0f}" if m in req_1h else "—",
                f"{p50[m]/1000:.1f}s" if m in p50 else "—",
                f"{p95[m]/1000:.1f}s" if m in p95 else "—",
            )
    return tbl


def _skill_table() -> Table:
    calls  = _prom_labeled("sum by (skill_name) (increase(agent_skill_calls_total[1h]))")
    errors = _prom_labeled("sum by (skill_name) (increase(agent_skill_errors_total[1h]))")

    tbl = Table(title="Skill Usage (1h)", box=box.SIMPLE_HEAD, expand=True,
                title_style="bold cyan", header_style="bold")
    tbl.add_column("Skill",  style="cyan", no_wrap=True)
    tbl.add_column("Calls",  justify="right")
    tbl.add_column("Errors", justify="right")

    if not calls:
        tbl.add_row("[dim]no data[/dim]", "—", "—")
    else:
        for s in sorted(calls):
            e = errors.get(s, 0)
            tbl.add_row(s, f"{calls[s]:.0f}",
                        Text(f"{e:.0f}", style="red" if e > 0 else "green"))
    return tbl


def _persona_table() -> Table:
    data     = _agent_get("/personas")
    personas = data.get("personas", []) if data else []

    tbl = Table(title="Personas", box=box.SIMPLE_HEAD, expand=True,
                title_style="bold cyan", header_style="bold")
    tbl.add_column("Name",         style="cyan", no_wrap=True)
    tbl.add_column("Display Name", no_wrap=True)
    tbl.add_column("Type",         justify="center")
    tbl.add_column("Skills",       justify="center")

    if not personas:
        tbl.add_row("[dim]unavailable[/dim]", "—", "—", "—")
    else:
        for p in personas:
            kind = "[dim]builtin[/dim]" if p.get("is_builtin") else "[yellow]custom[/yellow]"
            sk   = p.get("allowed_skills")
            tbl.add_row(p.get("name", "?"), p.get("display_name", "?"),
                        kind, "all" if sk is None else str(len(sk)))
    return tbl


def _fmt_dur(seconds: float) -> str:
    s = int(abs(seconds))
    sign = "+" if seconds >= 0 else "-"
    if s < 60:   return f"{sign}{s}s"
    if s < 3600: return f"{sign}{s//60}m{s%60:02d}s"
    if s < 86400:return f"{sign}{s//3600}h{(s%3600)//60:02d}m"
    return f"{sign}{s//86400}d{(s%86400)//3600:02d}h"


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


def _job_table(now: float) -> Table:
    data = _agent_get("/jobs")
    jobs = data.get("jobs", []) if data else []
    status_order = {"running": 0, "pending": 1, "completed": 2, "failed": 3, "cancelled": 4}
    jobs.sort(key=lambda j: (status_order.get(j.get("status", ""), 9), j.get("run_at", 0)))

    tbl = Table(title=f"Job Queue  ({len(jobs)} jobs)", box=box.SIMPLE_HEAD, expand=True,
                title_style="bold cyan", header_style="bold")
    tbl.add_column("Type",     no_wrap=True, width=9)
    tbl.add_column("Status",   no_wrap=True, width=10)
    tbl.add_column("User",     no_wrap=True, width=12)
    tbl.add_column("Persona",  no_wrap=True, width=11)
    tbl.add_column("Next Run", no_wrap=True, width=9)
    tbl.add_column("Age",      no_wrap=True, width=8)
    tbl.add_column("Every",    no_wrap=True, width=7)
    tbl.add_column("Last Run", no_wrap=True, width=13)
    tbl.add_column("Prompt",   ratio=1)

    styles = {"running": "bold yellow", "pending": "green",
              "completed": "dim", "failed": "bold red", "cancelled": "dim red"}

    if not jobs:
        tbl.add_row("[dim]no jobs[/dim]", *["—"] * 8)
    else:
        for j in jobs:
            status   = j.get("status", "?")
            st       = styles.get(status, "white")
            run_at   = j.get("run_at")
            created  = j.get("created_at")
            interval = j.get("interval_seconds")
            tbl.add_row(
                Text(j.get("job_type", "?")[:9], style=st),
                Text(status,                      style=st),
                Text(str(j.get("user_id","?"))[:10], style="dim"),
                Text(j.get("persona","default")[:11], style="dim"),
                Text(_fmt_dur(run_at - now) if run_at else "—",
                     style="cyan" if run_at and run_at > now else "yellow"),
                Text(_fmt_dur(now - created) if created else "—", style="dim"),
                Text(_fmt_dur(interval) if interval else "—", style="dim"),
                Text(_fmt_ts(j.get("last_run")), style="dim"),
                Text(j.get("prompt","")[:80], style="dim",
                     overflow="ellipsis", no_wrap=True),
            )
    return tbl


# ── Main layout ───────────────────────────────────────────────────────────────

def _render_agents(term_height: int) -> Layout:
    now = time.time()
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=1),
        Layout(name="mid", size=min(8, max(6, term_height // 6))),
        Layout(name="bot", ratio=2),
    )
    layout["top"].split_row(
        Layout(Panel(_model_table(), border_style="dim", padding=(0, 1))),
        Layout(Panel(_skill_table(), border_style="dim", padding=(0, 1))),
    )
    layout["mid"].update(Panel(_persona_table(), border_style="dim", padding=(0, 1)))
    layout["bot"].update(Panel(_job_table(now),  border_style="dim", padding=(0, 1)))
    return layout


def render(state: _State) -> Layout:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    w, h = _detect_terminal_size()

    root = Layout()
    root.split_column(
        Layout(name="header",  size=1),
        Layout(name="content", ratio=1),
        Layout(name="footer",  size=1),
    )
    root["header"].update(
        Text.from_markup(f"[bold cyan] Agent Insights[/bold cyan]  [dim]{now_str}   q quit[/dim]")
    )
    root["content"].update(_render_agents(h - 2))
    root["footer"].update(
        Text(f" Agent: {AGENT_API}   Prometheus: {PROMETHEUS}", style="dim")
    )
    return root


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent Insights Dashboard")
    parser.add_argument("--once",     action="store_true", help="Render once and exit")
    parser.add_argument("--interval", type=int, default=REFRESH, metavar="SEC")
    parser.add_argument("--key",      default="",
                        help="Agent API key (overrides AGENT_API_KEY env)")
    args = parser.parse_args()

    global AGENT_KEY
    AGENT_KEY = args.key or os.getenv("AGENT_API_KEY", "")

    if args.once:
        console.print(render(_State()))
        return

    # Pin only console width at startup to prevent line-wrapping artefacts
    # on the first render frame.  Height is left un-pinned so Rich reads it
    # fresh from the PTY (which is always correct in screen=True Live mode).
    w, _ = _detect_terminal_size()
    console._width = w

    _setup_resize_handler()
    state = _State(interval=args.interval)
    kb = threading.Thread(target=_keyboard_thread, args=(state,), daemon=True)
    kb.start()

    with Live(render(state), console=console, refresh_per_second=4, screen=True) as live:
        last_refresh = time.monotonic()
        try:
            while state.running:
                if _resize_event.is_set():
                    _resize_event.clear()
                    live.update(render(state))
                    last_refresh = time.monotonic()
                    continue
                if time.monotonic() - last_refresh >= state.interval:
                    live.update(render(state))
                    last_refresh = time.monotonic()
                else:
                    time.sleep(0.05)
        except KeyboardInterrupt:
            state.running = False

    console.print()


if __name__ == "__main__":
    main()
