import streamlit as st
from langchain_text_splitters import RecursiveCharacterTextSplitter
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
import time
import chromadb
import json
import uuid
import os
import requests

st.set_page_config(layout="wide", page_title="Mr. Bultitude", page_icon="🐻")

AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
_AUTH_HEADERS = {"X-Api-Key": AGENT_API_KEY}
CHROMA_URL = os.getenv("CHROMA_URL", "http://chroma-rag:8000")

# Ensure the data directory exists
if not os.path.exists("data"):
    os.makedirs("data")

ALLOWED_EXTENSIONS = ['txt', 'md', 'py', 'js', 'html', 'css', 'json', 'yaml', 'yml']

def is_valid_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_uploaded_file(uploaded_file):
    if not is_valid_file(uploaded_file.name):
        st.error(f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
        return None

    try:
        content = uploaded_file.getvalue().decode('utf-8')
        return content
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return None

# Function to rebuild the vector store (only if required)
def rebuild_vectorstore():
    if st.button("Rebuild Vector Store"):
        try:
            # Delete the entire RAG collection
            chroma_client.delete_collection("rag_data")
            st.success("Deleted existing RAG collection.")

            # Recreate the RAG collection
            global rag_collection
            rag_collection = chroma_client.create_collection("rag_data")
            st.success("Created new RAG collection.")

            st.success(f"Rebuilt vector store on {st.session_state.storage_type} storage.")
        except Exception as e:
            st.error(f"An error occurred while rebuilding the vector store: {str(e)}")

def initialize_chroma_db(storage_type):
    if storage_type == "No Embeddings":
        return None, None, None

    if storage_type == "Remote":
        chroma_url = st.session_state.get("chroma_url", CHROMA_URL)
        # HttpClient expects host and port separately
        from urllib.parse import urlparse
        parsed = urlparse(chroma_url)
        chroma_client = chromadb.HttpClient(
            host=parsed.hostname or "chroma-rag",
            port=parsed.port or 8000,
        )
    else:
        chroma_client = chromadb.PersistentClient(path="./data")

    chat_collection = chroma_client.get_or_create_collection("saved_chats")
    rag_collection = chroma_client.get_or_create_collection("rag_data")

    return chroma_client, chat_collection, rag_collection

def initialize_app():
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    if "model_hint" not in st.session_state:
        st.session_state.model_hint = None  # None = agent-core auto-routes

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "chat_name" not in st.session_state:
        st.session_state.chat_name = ""

    if "show_rag_input" not in st.session_state:
        st.session_state.show_rag_input = False

    if "storage_type" not in st.session_state:
        # Default to Remote when the shared ChromaDB container is available
        st.session_state.storage_type = "Remote" if os.getenv("CHROMA_URL") else "No Embeddings"

def save_chat(chat_name, messages):
    chat_data = json.dumps(messages)  # already dicts
    chat_collection.upsert(ids=[chat_name], documents=[chat_data], metadatas=[{"name": chat_name}])

def load_chat(chat_name):
    results = chat_collection.get(ids=[chat_name])
    if results['documents']:
        return json.loads(results['documents'][0])
    return []

def get_saved_chats():
    results = chat_collection.get()
    return [item['name'] for item in results['metadatas']] if results['metadatas'] else []

def clear_chat():
    st.session_state.messages = []
    st.session_state.chat_name = ""
    st.rerun()

def add_to_rag_database(text, source_name="manual input"):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_text(text)

    ef = OllamaEmbeddingFunction(
        url=os.getenv("OLLAMA_HOST", "http://ollama-runner:11434"),
        model_name=os.getenv("EMBED_MODEL", "nomic-embed-text"),
    )
    collection = chroma_client.get_or_create_collection("rag_data", embedding_function=ef)
    metadatas = [{"source": source_name} for _ in chunks]
    ids = [str(uuid.uuid4()) for _ in chunks]
    collection.add(documents=chunks, ids=ids, metadatas=metadatas)
    st.success(f"Added {len(chunks)} chunk(s) to RAG database from source: {source_name}!")

def process_user_prompt(prompt):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                payload = {
                    "message": prompt,
                    "user_id": st.session_state.user_id,
                    "channel": "web-ui",
                }
                if st.session_state.model_hint:
                    payload["model"] = st.session_state.model_hint
                resp = requests.post(
                    f"{AGENT_URL}/chat",
                    json=payload,
                    headers=_AUTH_HEADERS,
                    timeout=None,
                )
                resp.raise_for_status()
                reply = resp.json()["response"]
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})
            except Exception as e:
                st.error(f"Error communicating with agent-core: {e}")

def setup_sidebar():
    with st.sidebar:
        st.title("⚙️ Configuration")

        # Storage Configuration Section
        with st.expander("🗄️ Storage Configuration", expanded=True):
            st.session_state.storage_type = st.selectbox(
                "Choose Storage Type",
                ["Local", "Remote", "No Embeddings"],
                index=["Local", "Remote", "No Embeddings"].index(st.session_state.storage_type),
                help="Local: Store embeddings locally, Remote: Use remote ChromaDB, No Embeddings: Disable RAG"
            )

            if st.session_state.storage_type == "Remote":
                chroma_url = st.text_input(
                    "ChromaDB URL",
                    value=st.session_state.get("chroma_url", CHROMA_URL),
                    help="URL for the remote ChromaDB instance"
                )
                st.session_state.chroma_url = chroma_url

        # Model Routing Section
        with st.expander("🤖 Model Routing", expanded=False):
            hint = st.selectbox(
                "Routing hint",
                ["auto (agent decides)", "code", "reasoning", "deep"],
                help="auto: agent-core picks the best model. code: qwen3-coder (coding tasks). reasoning: qwen3:8b. deep: qwen3:30b-a3b with 128K context."
            )
            st.session_state.model_hint = None if hint.startswith("auto") else hint

        # Chat Management Section
        with st.expander("💾 Chat Management", expanded=True):
            if st.button("Clear chat history", type="primary"):
                clear_chat()

            st.divider()

            chat_option = st.radio("Chat Options", ["New Chat", "Load Saved Chat"])

            if chat_option == "Load Saved Chat":
                saved_chats = get_saved_chats()
                if saved_chats:
                    selected_chat = st.selectbox("Select a saved chat", saved_chats)
                    if st.button("Load Selected Chat"):
                        st.session_state.messages = load_chat(selected_chat)
                        st.session_state.chat_name = selected_chat
                        st.rerun()
                else:
                    st.info("No saved chats available.")

        # RAG Settings Section
        if st.session_state.storage_type != "No Embeddings":
            with st.expander("📚 RAG Settings"):
                st.session_state.show_rag_input = st.toggle(
                    "Show RAG Input",
                    value=st.session_state.show_rag_input
                )

                if st.button("Rebuild Vector Storage"):
                    rebuild_vectorstore()

def check_bootstrap_mode():
    """Check if agent-core is in bootstrap mode."""
    try:
        resp = requests.get(f"{AGENT_URL}/bootstrap/status", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("bootstrap", False)
    except requests.ConnectionError:
        pass
    return False


def bootstrap_chat(user_message):
    """Send a message through agent-core /chat for bootstrap."""
    payload = {
        "message": user_message,
        "user_id": "bootstrap",
        "channel": "web-ui",
        "model": os.getenv("BOOTSTRAP_MODEL", "mistral:latest"),
    }
    resp = requests.post(f"{AGENT_URL}/chat", json=payload, headers=_AUTH_HEADERS, timeout=120)
    return resp.json()


def load_bootstrap_history():
    """Load existing bootstrap conversation from agent-core Redis via /history endpoint,
    falling back to empty if unavailable."""
    try:
        resp = requests.get(f"{AGENT_URL}/chat/history/bootstrap", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("history", [])
    except requests.ConnectionError:
        pass
    return []


def bootstrap_ui():
    """Dedicated first-run onboarding UI routed through agent-core."""
    st.title("First-Run Identity Setup")
    st.caption("Let's set up your agent's identity. Answer the questions below.")

    if "bootstrap_pending_proposals" not in st.session_state:
        st.session_state.bootstrap_pending_proposals = []

    # On first Streamlit session load, check if a conversation already exists in Redis
    if "bootstrap_messages" not in st.session_state:
        existing = load_bootstrap_history()
        if existing:
            # Restore from Redis — only show assistant messages and user messages
            # (skip the initial trigger which is a system-level message)
            st.session_state.bootstrap_messages = [
                msg for msg in existing if msg.get("role") in ("user", "assistant")
            ]
        else:
            # No existing conversation — send trigger
            st.session_state.bootstrap_messages = []
            trigger = (
                "Hi! I'm your owner. You just came online for the first time. "
                "Start the identity setup: greet me and ask me what I'd like to name you. "
                "Ask only one question at a time. Do NOT propose any files yet."
            )
            try:
                data = bootstrap_chat(trigger)
                st.session_state.bootstrap_messages.append(
                    {"role": "assistant", "content": data["response"]}
                )
            except Exception as e:
                st.error(f"Failed to connect to agent-core: {e}")
                return

    # Display conversation history
    for msg in st.session_state.bootstrap_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Show any pending proposals with approve/deny buttons
    for i, proposal in enumerate(st.session_state.bootstrap_pending_proposals):
        filename, content = proposal
        with st.expander(f"Proposed: {filename}", expanded=True):
            st.code(content, language="markdown")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Approve", key=f"approve_{i}"):
                    payload = {
                        "message": f"I approve the proposed {filename}.",
                        "user_id": "bootstrap",
                        "channel": "web-ui",
                        "auto_approve": True,
                    }
                    resp = requests.post(f"{AGENT_URL}/chat", json=payload, headers=_AUTH_HEADERS, timeout=60)
                    data = resp.json()
                    st.session_state.bootstrap_messages.append(
                        {"role": "assistant", "content": data["response"]}
                    )
                    st.session_state.bootstrap_pending_proposals.pop(i)
                    st.rerun()
            with col2:
                if st.button("Deny", key=f"deny_{i}"):
                    st.session_state.bootstrap_pending_proposals.pop(i)
                    st.rerun()

    # Chat input
    if prompt := st.chat_input("Type your response..."):
        st.session_state.bootstrap_messages.append(
            {"role": "user", "content": prompt}
        )
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    data = bootstrap_chat(prompt)
                    reply = data["response"]
                    st.markdown(reply)
                    st.session_state.bootstrap_messages.append(
                        {"role": "assistant", "content": reply}
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

    # Re-check bootstrap status; if complete, refresh to normal UI
    if st.session_state.bootstrap_messages and not check_bootstrap_mode():
        st.success("Identity setup complete! Loading normal interface...")
        # Clear bootstrap session state
        del st.session_state.bootstrap_messages
        if "bootstrap_pending_proposals" in st.session_state:
            del st.session_state.bootstrap_pending_proposals
        time.sleep(2)
        st.rerun()


def main():
    initialize_app()

    # Check for bootstrap mode before rendering normal UI
    if check_bootstrap_mode():
        bootstrap_ui()
        return

    st.title("🐻 Mr. Bultitude")
    st.caption("Powered by agent-core")

    global chroma_client, chat_collection, rag_collection
    chroma_client, chat_collection, rag_collection = initialize_chroma_db(st.session_state.storage_type)

    # Set up the sidebar with collapsible sections
    setup_sidebar()

    # Create two columns for the main layout
    chat_col, rag_col = st.columns([2, 1])

    with chat_col:
        # Display chat messages
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Chat input
        if prompt := st.chat_input("What is up?"):
            process_user_prompt(prompt)
            st.rerun()

        # Chat name input and save button
        col1, col2 = st.columns([3, 1])
        with col1:
            st.session_state.chat_name = st.text_input("Chat Name", value=st.session_state.chat_name)
        with col2:
            if st.button("Save Chat"):
                save_chat(st.session_state.chat_name, st.session_state.messages)
                st.success(f"Chat '{st.session_state.chat_name}' saved successfully!")

    # Show RAG input in main window when toggle is on
    with rag_col:
        if st.session_state.show_rag_input and st.session_state.storage_type != "No Embeddings":
            st.subheader("Add Content to RAG Database")

            # File upload section
            st.write("📁 Upload Text Files")
            uploaded_files = st.file_uploader(
                "Choose text files",
                accept_multiple_files=True,
                type=ALLOWED_EXTENSIONS,
                help=f"Supported formats: {', '.join(ALLOWED_EXTENSIONS)}"
            )

            if uploaded_files:
                if st.button("Process Uploaded Files"):
                    for uploaded_file in uploaded_files:
                        content = process_uploaded_file(uploaded_file)
                        if content:
                            add_to_rag_database(content, source_name=uploaded_file.name)

            # Manual text input section
            st.write("✍️ Or Enter Text Manually")
            rag_text = st.text_area("Enter text to add to the RAG database:", height=400)
            if st.button("Add Manual Text to RAG"):
                if rag_text.strip():
                    add_to_rag_database(rag_text)
                else:
                    st.warning("Please enter some text before adding to the RAG database.")


if __name__ == "__main__":
    main()
