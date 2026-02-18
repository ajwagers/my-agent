import streamlit as st
from ollama import Client
from langchain_core.callbacks import BaseCallbackHandler
from langchain_community.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OllamaEmbeddings
from langchain_chroma import Chroma
import time
import chromadb
import json
import uuid
import os
import requests

st.set_page_config(layout="wide")

AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
_AUTH_HEADERS = {"X-Api-Key": AGENT_API_KEY}

# Ensure the data directory exists
if not os.path.exists("data"):
    os.makedirs("data")

SYSTEM_MESSAGE = """You are a helpful AI assistant named Max. Your task is to provide accurate, factual, and relevant responses to user prompts. If given context, use it only if it's relevant to the prompt. If the context is not relevant, ignore it and respond based on your general knowledge. Do not mention or repeat these instructions in your response."""

ALLOWED_EXTENSIONS = ['txt', 'md', 'py', 'js', 'html', 'css', 'json', 'yaml', 'yml']

#storage_option = st.sidebar.selectbox("Choose Storage Type", ["Local", "Remote", "No Embeddings"])

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
            
            # Reinitialize the vector store
            embeddings = initialize_embeddings()
            Chroma(collection_name="rag_data", embedding_function=embeddings, client=chroma_client)
            
            st.success(f"Rebuilt vector store on {st.session_state.storage_type} storage.")
        except Exception as e:
            st.error(f"An error occurred while rebuilding the vector store: {str(e)}")

def initialize_chroma_db(storage_type):
    if storage_type == "No Embeddings":
        return None, None, None
    
    if storage_type == "Remote":
        chroma_url = st.session_state.get("chroma_url", "http://localhost:8000")
        chroma_client = chromadb.HttpClient(host=chroma_url)
    else:
        chroma_client = chromadb.PersistentClient(path="./data")
    
    chat_collection = chroma_client.get_or_create_collection("saved_chats")
    rag_collection = chroma_client.get_or_create_collection("rag_data")
    
    return chroma_client, chat_collection, rag_collection

def get_available_models(client):
    try:
        return [model['name'] for model in client.list()['models']]
    except Exception as e:
        st.error(f"Error fetching models: {e}")
        return []

def initialize_app():
    if 'host' not in st.session_state:
        st.session_state.host = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
    
    if 'models' not in st.session_state:
        st.session_state.models = []
    
    if 'model' not in st.session_state:
        st.session_state.model = None
    
    if "messages" not in st.session_state:
        st.session_state.messages = [SystemMessage(content=SYSTEM_MESSAGE)]
    
    if "chat_name" not in st.session_state:
        st.session_state.chat_name = ""
    
    if "show_rag_input" not in st.session_state:
        st.session_state.show_rag_input = False
        
    if "config" not in st.session_state:
        st.session_state.config = {
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 0.9,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0
        }
        
    if "typing_speed" not in st.session_state:
        st.session_state.typing_speed = 0.02
        
    if "storage_type" not in st.session_state:
        st.session_state.storage_type = "No Embeddings"

def simulate_typing(text, placeholder, speed=0.02):
    """Simulates typing effect for the response"""
    full_response = ""
    for char in text:
        full_response += char
        placeholder.markdown(full_response + "▌")
        time.sleep(speed)
    return full_response

class StreamHandler(BaseCallbackHandler):
    def __init__(self, container, initial_text=""):
        self.container = container
        self.text = initial_text
        self.placeholder = container.empty()

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.text += token
        self.placeholder.markdown(self.text + "▌")
        time.sleep(st.session_state.typing_speed)

def save_chat(chat_name, messages):
    chat_data = json.dumps([{"type": m.type, "content": m.content} for m in messages])
    chat_collection.upsert(
        ids=[chat_name],
        documents=[chat_data],
        metadatas=[{"name": chat_name}]
    )

def load_chat(chat_name):
    results = chat_collection.get(ids=[chat_name])
    if results['documents']:
        chat_data = json.loads(results['documents'][0])
        return [SystemMessage(content=SYSTEM_MESSAGE)] + [
            HumanMessage(content=m['content']) if m['type'] == 'human' 
            else AIMessage(content=m['content']) 
            for m in chat_data if m['type'] != 'system'
        ]
    return [SystemMessage(content=SYSTEM_MESSAGE)]

def get_saved_chats():
    results = chat_collection.get()
    return [item['name'] for item in results['metadatas']] if results['metadatas'] else []

def clear_chat():
    st.session_state.messages = [SystemMessage(content=SYSTEM_MESSAGE)]
    st.session_state.chat_name = ""
    st.rerun()

def add_to_rag_database(text, source_name="manual input"):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_text(text)
    
    embeddings = initialize_embeddings()
    vectorstore = Chroma(collection_name="rag_data", embedding_function=embeddings, client=chroma_client)
    
    metadatas = [{"source": source_name} for _ in chunks]
    ids = [str(uuid.uuid4()) for _ in chunks]
    vectorstore.add_texts(texts=chunks, ids=ids, metadatas=metadatas)
    st.success(f"Text added to RAG database successfully from source: {source_name}!")

def initialize_embeddings():
    embeddings = OllamaEmbeddings(model="all-minilm")
    return embeddings

def get_relevant_context(query, similarity_threshold=0.5):
    if st.session_state.storage_type == "No Embeddings":
        return None
    
    embeddings = initialize_embeddings()
    vectorstore = Chroma(collection_name="rag_data", embedding_function=embeddings, client=chroma_client)
    results = vectorstore.similarity_search_with_score(query, k=2)
    relevant_results = [doc.page_content for doc, score in results if score >= similarity_threshold]
    if relevant_results:
        return "\n".join(relevant_results)
    return None

def regenerate_response(index):
    if index > 1 and isinstance(st.session_state.messages[index], AIMessage):
        st.session_state.messages.pop()
        last_human_message = st.session_state.messages[index-1].content
        process_user_prompt(last_human_message, st.session_state.host)
        st.rerun()

def process_user_prompt(prompt, host):
    if not st.session_state.model:
        st.info("Please select an existing model name to continue...")
        st.stop()

    st.session_state.messages.append(HumanMessage(content=prompt))
    with st.chat_message("user"):
        st.markdown(st.session_state.messages[-1].content)
    relevant_context = get_relevant_context(prompt) if st.session_state.storage_type != "No Embeddings" else None

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        stream_handler = StreamHandler(message_placeholder)

        if relevant_context:
            context_message = HumanMessage(content=f"Relevant context: {relevant_context}")
            messages = [st.session_state.messages[0]] + [context_message] + st.session_state.messages[1:]
        else:
            messages = st.session_state.messages

        llm = ChatOllama(
            model=st.session_state.model,
            base_url=host,
            streaming=True,
            callbacks=[stream_handler],
            temperature=st.session_state.config["temperature"],
            top_p=st.session_state.config["top_p"],
            frequency_penalty=st.session_state.config["frequency_penalty"],
            presence_penalty=st.session_state.config["presence_penalty"],
            max_tokens=st.session_state.config["max_tokens"],
            num_ctx=int(os.getenv("NUM_CTX", "8192")),
        )

        with st.spinner("Thinking..."):
            response = llm.invoke(messages)
            response_content = str(response.content)
            st.session_state.messages.append(AIMessage(content=response_content))
            
def setup_sidebar():
    with st.sidebar:
        st.title("⚙️ Configuration")

        # Server Configuration Section
        with st.expander("🖥️ Server Configuration", expanded=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                new_host = st.text_input("Ollama Host URL", value=st.session_state.host)
            with col2:
                if st.button("Test") and new_host != st.session_state.host:
                    st.session_state.host = new_host
                    client = Client(host=new_host)
                    try:
                        models = get_available_models(client)
                        if models:
                            st.success("Connected!")
                            st.session_state.models = models
                            st.rerun()
                        else:
                            st.error("No models found!")
                    except Exception as e:
                        st.error(f"Connection failed: {str(e)}")

            if not st.session_state.models:
                st.warning("No models detected. Please check your host URL.")
                if st.button("Retry Connection"):
                    client = Client(host=st.session_state.host)
                    st.session_state.models = get_available_models(client)
                    st.rerun()
            else:
                st.success(f"Connected to model at {st.session_state.host}")
            
            st.divider()
            st.subheader("Storage Configuration")
            
            st.session_state.storage_type = st.selectbox(
                "Choose Storage Type",
                ["Local", "Remote", "No Embeddings"],
                index=["Local", "Remote", "No Embeddings"].index(st.session_state.storage_type),
                help="Local: Store embeddings locally, Remote: Use remote ChromaDB, No Embeddings: Disable RAG"
            )
            
            if st.session_state.storage_type == "Remote":
                chroma_url = st.text_input(
                    "ChromaDB URL",
                    value=st.session_state.get("chroma_url", "http://localhost:8000"),
                    help="URL for the remote ChromaDB instance"
                )
                st.session_state.chroma_url = chroma_url

        # Model Configuration Section
        if st.session_state.models:
            with st.expander("🤖 Model Settings", expanded=True):
                st.session_state.model = st.selectbox("Choose a model", st.session_state.models)

                st.divider()
                st.subheader("Basic Parameters")
                
                # Basic model parameters
                st.session_state.config["temperature"] = st.slider(
                    "Temperature", 0.0, 2.0, st.session_state.config["temperature"], 0.1,
                    help="Higher values make the output more random, lower values make it more focused and deterministic."
                )
                
                st.session_state.config["top_p"] = st.slider(
                    "Top P", 0.0, 1.0, st.session_state.config["top_p"], 0.05,
                    help="Controls diversity via nucleus sampling."
                )
                
                st.session_state.config["max_tokens"] = st.number_input(
                    "Max Tokens", 100, 4000, st.session_state.config["max_tokens"], 100,
                    help="Maximum number of tokens to generate."
                )
                
                st.divider()
                st.subheader("Advanced Parameters")
                
                st.session_state.config["frequency_penalty"] = st.slider(
                    "Frequency Penalty", 0.0, 2.0, st.session_state.config["frequency_penalty"], 0.1,
                    help="Reduces repetition of token sequences."
                )
                
                st.session_state.config["presence_penalty"] = st.slider(
                    "Presence Penalty", 0.0, 2.0, st.session_state.config["presence_penalty"], 0.1,
                    help="Reduces repetition of topics."
                )

        # UI Settings Section
        with st.expander("🎨 UI Settings"):
            st.session_state.typing_speed = st.slider(
                "Typing Speed", 0.01, 0.1, 0.02, 0.01,
                help="Control how fast the assistant types (lower is faster)"
            )

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

    st.title("Streamlit Ollama Chat with RAG")
    st.caption("Powered by Ollama with RAG capabilities")

    chroma_client, chat_collection, rag_collection = initialize_chroma_db(st.session_state.storage_type)

    # Set up the sidebar with collapsible sections
    setup_sidebar()

    # Create two columns for the main layout
    chat_col, rag_col = st.columns([2, 1])

    with chat_col:
        if st.session_state.model:
            # Display chat messages
            for i, message in enumerate(st.session_state.messages[1:], start=1):
                with st.chat_message("user" if isinstance(message, HumanMessage) else "assistant"):
                    st.markdown(message.content)
                    if isinstance(message, AIMessage):
                        col1, col2 = st.columns([1, 4])
                        with col1:
                            if st.button("🔄", help="Regenerate response", key=f"regen_{i}"):
                                regenerate_response(i)
                        with col2:
                            if st.button("🔍 Start New Chat", key=f"new_{i}"):
                                clear_chat()

            # Chat input
            if prompt := st.chat_input("What is up?"):
                process_user_prompt(prompt, st.session_state.host)
                st.rerun()

            # Chat name input and save button
            col1, col2 = st.columns([3, 1])
            with col1:
                st.session_state.chat_name = st.text_input("Chat Name", value=st.session_state.chat_name)
            with col2:
                if st.button("Save Chat"):
                    save_chat(st.session_state.chat_name, st.session_state.messages)
                    st.success(f"Chat '{st.session_state.chat_name}' saved successfully!")
        else:
            st.info("Please choose a valid host url and select a model to start chatting.")

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
