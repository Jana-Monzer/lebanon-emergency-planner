"""Environment configuration and shared LLM clients.

By default everything goes through the OpenAI SDK. When OPENAI_BASE_URL is Google's
OpenAI-compatible endpoint (https://generativelanguage.googleapis.com/v1beta/openai/),
embeddings still use it, but chat uses LangChain's native Gemini client (see get_chat_model).
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")
# google-genai logs an advisory about automatic function calling on every client; we don't use AFC.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or None
LLM_PROVIDER = "gemini" if BASE_URL and "generativelanguage.googleapis.com" in BASE_URL else "openai"
CHAT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
# "json_schema" (native structured output) or "function_calling" (for endpoints without json_schema support)
STRUCTURED_OUTPUT_METHOD = os.getenv("STRUCTURED_OUTPUT_METHOD", "json_schema")
# Free-tier keys hit per-minute rate limits; the SDK backs off and retries on HTTP 429.
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "8"))
# Client-side pacing of chat calls (0 = unlimited). Gemini's free tier allows 5/min for flash models.
REQUESTS_PER_MINUTE = float(os.getenv("LLM_REQUESTS_PER_MINUTE", "0"))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable {name}. Copy .env.example to .env and fill it in.")
    return value


def schools_layer_url() -> str:
    return require_env("SCHOOLS_LAYER_URL").rstrip("/")


def hospitals_layer_url() -> str:
    return require_env("HOSPITALS_LAYER_URL").rstrip("/")


@lru_cache
def get_chat_model():
    from langchain_core.rate_limiters import InMemoryRateLimiter

    limiter = None
    if REQUESTS_PER_MINUTE > 0:
        limiter = InMemoryRateLimiter(requests_per_second=REQUESTS_PER_MINUTE / 60, check_every_n_seconds=0.2)
    if LLM_PROVIDER == "gemini":
        # Gemini 3 models reject multi-step tool loops unless each tool call's thought
        # signature is sent back; LangChain's native Gemini client does that, ChatOpenAI doesn't.
        from langchain_google_genai import ChatGoogleGenerativeAI

        # No temperature: Gemini 3 models use fixed sampling and warn when it is set.
        return ChatGoogleGenerativeAI(model=CHAT_MODEL, google_api_key=require_env("OPENAI_API_KEY"),
                                      max_retries=MAX_RETRIES, rate_limiter=limiter)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=CHAT_MODEL, temperature=0, api_key=require_env("OPENAI_API_KEY"),
                      base_url=BASE_URL, max_retries=MAX_RETRIES, rate_limiter=limiter)


def structured(schema):
    """The chat model in Structured Output Mode for a Pydantic schema."""
    return get_chat_model().with_structured_output(schema, method=STRUCTURED_OUTPUT_METHOD)


@lru_cache
def get_openai_client():
    from openai import OpenAI

    return OpenAI(api_key=require_env("OPENAI_API_KEY"), base_url=BASE_URL, max_retries=MAX_RETRIES)
