import click
import requests
import sys
import subprocess
import os
import time
import threading
import json
import tempfile

IDENTITY_DIR = os.environ.get("IDENTITY_DIR", "/agent")
BOOTSTRAP_FILE = os.path.join(IDENTITY_DIR, "BOOTSTRAP.md")
BOOTSTRAP_MODEL = os.environ.get("BOOTSTRAP_MODEL", "mistral:latest")
API_BASE = "http://localhost:8000"


def _wait_for_health(timeout=30):
    """Block until /health responds or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{API_BASE}/health", timeout=2)
            if r.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


def _start_server_thread():
    """Run uvicorn in a daemon thread."""
    import uvicorn
    from app import app

    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": 8000, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    return thread


def _write_identity_file(filename, content):
    """Write a file to the identity directory."""
    path = os.path.join(IDENTITY_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _clear_redis_session(user_id):
    """Clear conversation history for a session via a dummy mechanism."""
    try:
        import redis as redis_lib
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
        r = redis_lib.from_url(redis_url, decode_responses=True)
        r.delete(f"chat:{user_id}")
    except Exception:
        pass  # Non-critical — worst case is stale history


def _chat(message, user_id="bootstrap-soul", model="deep"):
    """Send a message through /chat and return the response text."""
    payload = {
        "message": message,
        "user_id": user_id,
        "channel": "cli",
        "model": model,
    }
    resp = requests.post(f"{API_BASE}/chat", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["response"]


def _review_soul(content):
    """Let the owner approve, edit, or regenerate SOUL.md content.
    Returns (action, content) where action is 'approve' or 'regenerate'.
    """
    while True:
        print("\n" + "=" * 50)
        print("Proposed SOUL.md:")
        print("-" * 50)
        print(content)
        print("-" * 50)

        choice = click.prompt(
            "[a]pprove  [e]dit  [r]egenerate",
            type=click.Choice(["a", "e", "r"], case_sensitive=False),
        )

        if choice == "a":
            return "approve", content
        elif choice == "r":
            return "regenerate", content
        elif choice == "e":
            editor = os.environ.get("EDITOR", "")
            if editor:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    subprocess.run([editor, tmp_path], check=True)
                    with open(tmp_path, "r") as f:
                        content = f.read()
                finally:
                    os.unlink(tmp_path)
            else:
                print("\nNo $EDITOR set. Enter new content (end with a blank line):")
                lines = []
                while True:
                    try:
                        line = input()
                    except EOFError:
                        break
                    if line == "":
                        break
                    lines.append(line)
                if lines:
                    content = "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 1: Form-based identity and user input
# ---------------------------------------------------------------------------

def _phase1_form():
    """Collect identity and user info via CLI prompts. Returns (identity, user) dicts."""
    print("\n=== Phase 1: Identity Setup ===\n")
    print("Let's figure out who your agent is.\n")

    identity = {
        "name": click.prompt("Agent name"),
        "nature": click.prompt("What kind of creature?",
                               default="AI assistant"),
        "vibe": click.prompt("Vibe / personality style",
                             default="helpful and curious"),
        "emoji": click.prompt("Signature emoji", default="\U0001f916"),
    }

    print(f"\nMeet {identity['emoji']} {identity['name']}, "
          f"a {identity['vibe']} {identity['nature']}.\n")

    print("=== About You ===\n")

    user = {
        "name": click.prompt("Your name"),
        "call_me": click.prompt("How should the agent address you?",
                                default=""),
        "timezone": click.prompt("Your timezone", default=""),
        "notes": click.prompt("Anything else the agent should know about you?",
                              default=""),
    }
    # Default call_me to name if left blank
    if not user["call_me"]:
        user["call_me"] = user["name"]

    return identity, user


def _write_phase1_files(identity, user):
    """Write IDENTITY.md and USER.md from form data."""
    identity_content = (
        f"# Agent Identity\n"
        f"name: {identity['name']}\n"
        f"nature: {identity['nature']}\n"
        f"vibe: {identity['vibe']}\n"
        f"emoji: {identity['emoji']}\n"
    )
    _write_identity_file("IDENTITY.md", identity_content)
    print(f"  Wrote IDENTITY.md")

    user_content = (
        f"# Owner Profile\n"
        f"name: {user['name']}\n"
        f"call_me: {user['call_me']}\n"
        f"timezone: {user['timezone']}\n"
        f"notes: {user['notes']}\n"
    )
    _write_identity_file("USER.md", user_content)
    print(f"  Wrote USER.md")


# ---------------------------------------------------------------------------
# Phase 2: Soul conversation with DEEP_MODEL
# ---------------------------------------------------------------------------

SOUL_PROMPT_TEMPLATE = """\
You are helping craft a personality file (SOUL.md) for an AI agent.

The agent:
- Name: {name}
- Nature: {nature}
- Vibe: {vibe}
- Emoji: {emoji}

The owner:
- Name: {owner_name}
- Goes by: {call_me}
- Timezone: {timezone}
- Notes: {notes}

Have a brief conversation (2-3 exchanges) about how {name} should behave: \
personality, tone, boundaries, quirks. Build on the vibe above but make it specific.

Keep your responses to 2-3 sentences. Ask one question at a time.

When the owner says they're ready (or after 2-3 exchanges), write the final \
personality prompt for {name}. Write it as instructions to the agent in second \
person ("You are..."). Keep it under 1000 characters. Write ONLY the prompt, \
no preamble or explanation.
"""

FINALIZE_MESSAGE = (
    "I'm happy with what we've discussed. Now write the final SOUL.md "
    "personality prompt. Write ONLY the prompt content — no preamble, "
    "no explanation, no markdown headers. Just the personality instructions."
)


def _phase2_soul(identity, user):
    """Run the soul conversation and write SOUL.md."""
    print("\n=== Phase 2: Soul Conversation ===\n")
    print(f"Now let's figure out {identity['name']}'s personality.\n")
    print("Chat with the agent about how it should behave. When you're ready")
    print("to finalize, type 'done' (or just keep chatting — it'll wrap up).\n")

    # Rewrite BOOTSTRAP.md with focused soul prompt
    soul_prompt = SOUL_PROMPT_TEMPLATE.format(
        name=identity["name"],
        nature=identity["nature"],
        vibe=identity["vibe"],
        emoji=identity["emoji"],
        owner_name=user["name"],
        call_me=user["call_me"],
        timezone=user["timezone"],
        notes=user["notes"],
    )
    _write_identity_file("BOOTSTRAP.md", soul_prompt)

    # Clear any prior bootstrap history
    _clear_redis_session("bootstrap-soul")

    # Send trigger to start the conversation
    trigger = (
        f"Hi {identity['name']}! Let's figure out your personality together. "
        f"You're a {identity['vibe']} {identity['nature']}. "
        f"What questions do you have about how you should behave?"
    )
    reply = _chat(trigger)
    print(f"{identity['name']}: {reply}\n")

    # Conversation loop
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nBootstrap interrupted.")
            return False

        if not user_input.strip():
            continue

        if user_input.strip().lower() == "done":
            break

        reply = _chat(user_input)
        print(f"\n{identity['name']}: {reply}\n")

    # Finalize: ask the model to produce the SOUL.md content
    while True:
        soul_content = _chat(FINALIZE_MESSAGE)
        action, soul_content = _review_soul(soul_content)

        if action == "approve":
            _write_identity_file("SOUL.md", soul_content)
            print(f"\n  Wrote SOUL.md")
            break
        else:
            # Regenerate — tell the model to try again
            print("\nRegenerating...\n")
            _clear_redis_session("bootstrap-soul")
            # Re-send the finalize request with a nudge
            regen_msg = (
                "The owner wants a different take. Rewrite the SOUL.md "
                "personality prompt with a fresh approach. Write ONLY the "
                "prompt content, no preamble."
            )
            soul_content = _chat(regen_msg)

    return True


def _complete_bootstrap(identity):
    """Delete BOOTSTRAP.md and print completion message."""
    if os.path.isfile(BOOTSTRAP_FILE):
        os.remove(BOOTSTRAP_FILE)

    print("\n" + "=" * 50)
    print(f"{identity['emoji']} Bootstrap complete!")
    print(f"  {identity['name']} is ready.")
    print("=" * 50)
    print("\nFiles written:")
    print(f"  {IDENTITY_DIR}/IDENTITY.md")
    print(f"  {IDENTITY_DIR}/USER.md")
    print(f"  {IDENTITY_DIR}/SOUL.md")
    print(f"\nBOOTSTRAP.md has been removed.")
    print(f"The agent will now use SOUL.md as its system prompt.\n")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
def cli():
    pass


@cli.command()
@click.argument('message')
@click.option('--model', default=None, help='Ollama model (omit for auto-routing)')
@click.option('--reason', '-r', is_flag=True, help='Force the reasoning model')
@click.option('--deep', '-d', is_flag=True, help='Force the deep model')
@click.option('--session', default='cli-default', help='Session/user ID for conversation memory')
def chat(message, model, reason, deep, session):
    """Simple chat via API."""
    if deep:
        model = "deep"
    elif reason:
        model = "reasoning"
    payload = {
        "message": message,
        "user_id": session,
        "channel": "cli",
    }
    if model is not None:
        payload["model"] = model
    resp = requests.post(f"{API_BASE}/chat", json=payload)
    data = resp.json()
    print(data["response"])


@cli.command()
def bootstrap():
    """Interactive first-run identity onboarding (two-phase: form + soul conversation)."""
    if not os.path.isfile(BOOTSTRAP_FILE):
        print("No BOOTSTRAP.md found — nothing to do.")
        return

    # Phase 1: form input (no server needed)
    identity, user = _phase1_form()
    _write_phase1_files(identity, user)

    # Start server for phase 2 (skip if already running, e.g. inside Docker)
    server_thread = None
    if _wait_for_health(timeout=2):
        print("\nServer already running.")
    else:
        server_thread = _start_server_thread()
        print("\nWaiting for server to start...")
        if not _wait_for_health():
            print("ERROR: Server failed to start within 30 seconds.", file=sys.stderr)
            sys.exit(1)

    # Phase 2: soul conversation
    if _phase2_soul(identity, user):
        _complete_bootstrap(identity)

    # If we started the server ourselves, keep it alive
    if server_thread:
        print("Server is running. Press Ctrl+C to stop.")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            print("\nShutting down.")


@cli.command()
def serve():
    """Start the FastAPI service."""
    if os.path.isfile(BOOTSTRAP_FILE):
        print("Bootstrap mode detected. Use the web UI or 'bootstrap' command to complete setup.")
    subprocess.run(["python", "app.py"])


if __name__ == "__main__":
    if len(sys.argv) == 1:
        cli(['serve'])  # Default: start service
    else:
        cli()
