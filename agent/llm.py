import os

from langchain_ollama import ChatOllama

MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
# Hard cap on reply length: a small model can fall into a repeat loop and generate forever,
# which blocks Ollama's single request slot for every other call.
NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "1500"))
TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))  # seconds; without it a stalled call hangs the run forever


def get_llm() -> ChatOllama:
    # temperature 0 keeps tool calls repeatable; num_ctx leaves room for tool output
    return ChatOllama(model=MODEL, base_url=BASE_URL, temperature=0, num_ctx=NUM_CTX, num_predict=NUM_PREDICT,
                      client_kwargs={"timeout": TIMEOUT})
