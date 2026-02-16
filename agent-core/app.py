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
