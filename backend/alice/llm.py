"""Model provider settings. Any OpenAI-compatible endpoint works."""

import os
from urllib.parse import urlparse

from openai import AsyncOpenAI

BASE_URL = os.environ.get("ALICE_BASE_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("ALICE_API_KEY", "ollama")
MODEL = os.environ.get("ALICE_MODEL", "alice")

# Thinking multiplied GPU time about 25x in testing. Set empty to omit the parameter.
_REASONING_EFFORT = os.environ.get("ALICE_REASONING_EFFORT", "none")
REASONING_ARGS = {"reasoning_effort": _REASONING_EFFORT} if _REASONING_EFFORT else {}

# The model's context window, as set in backend/Modelfile. Only used to warn when a request nears it.
CONTEXT_TOKENS = int(os.environ.get("ALICE_CONTEXT_TOKENS", "16384"))

IS_LOCAL = urlparse(BASE_URL).hostname in ("localhost", "127.0.0.1", "::1")

client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
