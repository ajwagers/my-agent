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
