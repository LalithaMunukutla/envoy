import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
PEOPLE_DIR = DATA_DIR / "people"
PACKS_DIR = ROOT / "packs"
FRONTEND_DIR = ROOT / "frontend"
DB_PATH = Path(os.getenv("ENVOY_DB", ROOT / "envoy.db"))

API_KEY = os.getenv("ANTHROPIC_API_KEY") or None

# Optional OpenAI-compatible backend (LiteLLM proxy, OpenRouter, local, etc.).
# Set both to route all LLM calls there instead of the Anthropic API.
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/") or None
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None

if OPENAI_BASE_URL and OPENAI_API_KEY:
    PROVIDER = "openai"
    _default_smart = _default_fast = "gpt-4o-mini"
elif API_KEY:
    PROVIDER = "anthropic"
    _default_smart, _default_fast = "claude-sonnet-5-5", "claude-haiku-5-5"
else:
    PROVIDER = "none"
    _default_smart, _default_fast = "claude-sonnet-5-5", "claude-haiku-5-5"

MODE = (os.getenv("ENVOY_MODE") or ("mock" if PROVIDER == "none" else "live")).lower()
MODEL_SMART = os.getenv("ENVOY_MODEL_SMART", _default_smart)
MODEL_FAST = os.getenv("ENVOY_MODEL_FAST", _default_fast)
PACE = float(os.getenv("ENVOY_PACE", "0.7"))
