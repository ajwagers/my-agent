#!/usr/bin/env python3
"""
Agent Health TUI Dashboard — Overview.

6 stat cards + 6 time-series charts (Chat, Response Time, Skills,
Policy, Ollama VRAM, Ollama Duration).  Mirrors the Grafana panel.

Usage:
    python3 tui_dashboard.py              # live, refresh every 15 s
    python3 tui_dashboard.py --once       # single snapshot then exit
    python3 tui_dashboard.py --interval 5
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

import plotext as plt
import requests
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

# ── Config ────────────────────────────────────────────────────────────────────

PROMETHEUS    = "http://localhost:9090"
RANGE_MINUTES = 60
RANGE_STEP    = "60s"
REFRESH       = 15

console = Console()
_resize_event = threading.Event()


# ── Terminal size helpers ─────────────────────────────────────────────────────

def _detect_terminal_size() -> tuple[int, int]:
    """
    Return the visible terminal dimensions using os.get_terminal_size().

    The old cursor-probe approach (\033[999;999H\033[6n) was removed because
    several terminal emulators (VTE/MATE, some xterm variants) clamp the
    cursor to the scrollback buffer height rather than the visible viewport,
    reporting an inflated row count.  That caused the layout to be taller
    than the real window, pushing the top panels off-screen.

    os.get_terminal_size() queries the PTY cell grid directly (TIOCGWINSZ),
    which always reflects the visible window — no scrollback contamination.
    Rich's screen=True Live mode keeps this in sync: the terminal updates
    the PTY before sending SIGWINCH, so any read after the signal is fresh.
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
        # Clear the pinned dimensions so console.size falls back to
        # os.get_terminal_size(), which the terminal has already updated
        # before delivering SIGWINCH.  Re-detect is unnecessary here and
        # risks re-reading a partially-updated PTY record.
        console._width  = None
        console._height = None
        _resize_event.set()
    try:
        signal.signal(signal.SIGWINCH, _handler)
    except (AttributeError, OSError):
        pass


# ── State & keyboard thread ───────────────────────────────────────────────────

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


# ── Prometheus helpers ────────────────────────────────────────────────────────

def _prom_query(query: str) -> Optional[float]:
    data = _get(f"{PROMETHEUS}/api/v1/query", {"query": query})
    if not data:
        return None
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    return float(results[0]["value"][1])


def _prom_range(query: str) -> dict:
    end   = int(time.time())
    start = end - RANGE_MINUTES * 60
    data  = _get(f"{PROMETHEUS}/api/v1/query_range",
                 {"query": query, "start": start, "end": end, "step": RANGE_STEP},
                 timeout=10)
    if not data:
        return {}
    out = {}
    for item in data.get("data", {}).get("result", []):
        metric = item["metric"]
        label  = next(
            (v for k, v in metric.items() if k not in ("__name__", "instance", "job", "le")),
            query,
        )
        out[label] = (
            [float(p[0]) for p in item["values"]],
            [float(p[1]) for p in item["values"]],
        )
    return out


# ── Chart helpers ─────────────────────────────────────────────────────────────

_COLORS = ["cyan", "yellow", "green", "red", "magenta", "blue", "orange", "white"]


def _make_chart(series: dict, y_label: str, width: int, height: int) -> Text:
    plt.clf()
    plt.plotsize(width, height)
    plt.theme("dark")
    if y_label:
        plt.ylabel(y_label)
    has_data = any(ts for ts, _ in series.values()) if series else False
    if not has_data:
        plt.plot([0], label="no data", color="white")
    else:
        for i, (label, (ts, vs)) in enumerate(series.items()):
            if ts:
                plt.plot(vs, label=label[:22], color=_COLORS[i % len(_COLORS)])
        for ts, _ in series.values():
            if ts:
                s = datetime.fromtimestamp(ts[0],  tz=timezone.utc).strftime("%H:%M")
                e = datetime.fromtimestamp(ts[-1], tz=timezone.utc).strftime("%H:%M")
                plt.xlabel(f"{s} → {e}")
                break
    return Text.from_ansi(plt.build())


def _chart_panel(series: dict, title: str, y_label: str,
                 width: int, height: int) -> Panel:
    return Panel(
        _make_chart(series, y_label, width, height),
        title=f"[bold]{title}[/bold]",
        border_style="dim",
        padding=(0, 0),
    )


# ── Stat card ─────────────────────────────────────────────────────────────────

def _stat_card(title: str, value: Optional[float], unit: str = "", fmt: str = ".2f",
               warn: Optional[float] = None, crit: Optional[float] = None) -> Panel:
    if value is None:
        text = Text("N/A", style="dim", justify="center")
    else:
        style = (
            "bold red"    if crit is not None and value >= crit else
            "bold yellow" if warn is not None and value >= warn else
            "bold green"
        )
        text = Text(f"{value:{fmt}} {unit}".strip(), style=style, justify="center")
    return Panel(text, title=f"[dim]{title}[/dim]",
                 border_style="dim", box=box.ROUNDED, padding=(0, 0), expand=True)


# ── Overview layout ───────────────────────────────────────────────────────────

def _render_overview(term_width: int, term_height: int) -> Layout:
    # Fetch stats
    req_rate  = _prom_query("sum(rate(agent_chat_requests_total[1m]))")
    queue     = _prom_query("agent_queue_depth")
    approvals = _prom_query("agent_pending_approvals")
    skills_h  = _prom_query("sum(increase(agent_skill_calls_total[1h]))") or 0.0
    denials_h = _prom_query('sum(increase(agent_policy_decisions_total{decision="deny"}[1h]))') or 0.0
    ollama_q  = _prom_query("ollama_pending_requests")

    stat_row = Layout()
    stat_row.split_row(*[Layout(name=f"s{i}") for i in range(6)])
    for i, spec in enumerate([
        ("Req Rate",   req_rate,  "rps", ".3f", 1.0,  5.0),
        ("Queue",      queue,     "",    ".0f", 3.0,  8.0),
        ("Approvals",  approvals, "",    ".0f", 1.0,  3.0),
        ("Skills/1h",  skills_h,  "",    ".0f", None, None),
        ("Denials/1h", denials_h, "",    ".0f", 1.0,  1.0),
        ("Ollama Q",   ollama_q,  "",    ".0f", 2.0,  5.0),
    ]):
        stat_row[f"s{i}"].update(_stat_card(*spec))

    # ── Chart dimensions ──────────────────────────────────────────────────────
    # term_height = raw terminal rows minus root header(1) and footer(1).
    # Content layout: stats(3 fixed) + 3 × ratio-1 chart rows.
    #
    # Row height budget (floor so all 3 rows fit within what Rich allocates):
    #   row_h = (term_height - 3) // 3
    #
    # chart_h (lines passed to plotext):
    #   subtract 2 for Panel top/bottom borders
    #   subtract 1 for plotext's own trailing blank line in build() output
    #   subtract 1 safety margin — plotext may add a legend row or extra label
    #   → chart_h = row_h - 4
    #
    # chart_w (columns passed to plotext):
    #   each half-panel gets term_width // 2 columns from the split_row Layout
    #   subtract 2 for Panel left/right borders
    #   subtract 2 for the y-axis label column plotext reserves inside its canvas
    #   subtract 1 safety margin for off-by-one differences across plotext versions
    #   → chart_w = term_width // 2 - 5
    remaining = term_height - 3
    row_h   = max(8, remaining // 3)
    chart_h = max(4, row_h - 4)
    chart_w = max(40, term_width // 2 - 5)

    resp_series: dict = {}
    for pfx, q in (
        ("p50", "histogram_quantile(0.50, sum by (le,model) (rate(agent_chat_response_ms_bucket[5m])))"),
        ("p95", "histogram_quantile(0.95, sum by (le,model) (rate(agent_chat_response_ms_bucket[5m])))"),
        ("p99", "histogram_quantile(0.99, sum by (le,model) (rate(agent_chat_response_ms_bucket[5m])))"),
    ):
        for lbl, data in _prom_range(q).items():
            resp_series[f"{pfx} {lbl}"] = data

    layout = Layout()
    layout.split_column(
        Layout(name="stats", size=3),
        Layout(name="row2",  ratio=1),
        Layout(name="row3",  ratio=1),
        Layout(name="row4",  ratio=1),
    )
    layout["stats"].update(stat_row)
    layout["row2"].split_row(
        Layout(_chart_panel(
            _prom_range("sum by (channel) (rate(agent_chat_requests_total[2m]))"),
            "Chat Requests / Channel", "req/s", chart_w, chart_h)),
        Layout(_chart_panel(
            resp_series, "Response Time p50/p95/p99", "ms", chart_w, chart_h)),
    )
    layout["row3"].split_row(
        Layout(_chart_panel(
            _prom_range("sum by (skill_name) (rate(agent_skill_calls_total[5m]))"),
            "Skill Calls", "calls/s", chart_w, chart_h)),
        Layout(_chart_panel(
            _prom_range("sum by (decision) (rate(agent_policy_decisions_total[5m]))"),
            "Policy Decisions", "dec/s", chart_w, chart_h)),
    )
    layout["row4"].split_row(
        Layout(_chart_panel(
            _prom_range("ollama_memory_allocated_bytes"),
            "Ollama VRAM", "bytes", chart_w, chart_h)),
        Layout(_chart_panel(
            _prom_range("histogram_quantile(0.95, sum by (le) (rate(ollama_request_duration_seconds_bucket[5m])))"),
            "Ollama Req Duration p95", "s", chart_w, chart_h)),
    )
    return layout


def render(state: _State) -> Layout:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Always read fresh from the PTY so resize is picked up immediately.
    # console.size calls os.get_terminal_size() when _width/_height are None.
    w, h = _detect_terminal_size()

    root = Layout()
    root.split_column(
        Layout(name="header",  size=1),
        Layout(name="content", ratio=1),
        Layout(name="footer",  size=1),
    )
    root["header"].update(
        Text.from_markup(f"[bold cyan] Agent Health[/bold cyan]  [dim]{now_str}   q quit[/dim]")
    )
    root["content"].update(_render_overview(w, h - 2))
    root["footer"].update(
        Text(f" Prometheus: {PROMETHEUS}   Range: last {RANGE_MINUTES}m   Step: {RANGE_STEP}",
             style="dim")
    )
    return root


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent Health Overview Dashboard")
    parser.add_argument("--once",     action="store_true", help="Render once and exit")
    parser.add_argument("--interval", type=int, default=REFRESH, metavar="SEC")
    args = parser.parse_args()

    if args.once:
        console.print(render(_State()))
        return

    # Pin only the console width at startup.  Pinning height was the root cause
    # of the layout-taller-than-terminal bug: _detect_terminal_size() could
    # return an inflated row count (scrollback contamination on VTE/MATE).
    # Width pinning prevents Rich from wrapping lines during the first render
    # frame before the Live loop has settled.  Height is left un-pinned so
    # Rich reads it fresh from the PTY on every render() call.
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
