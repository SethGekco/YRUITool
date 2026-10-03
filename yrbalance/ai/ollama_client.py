"""
Minimal Ollama client over the local HTTP API (stdlib only).

Ollama exposes a plain JSON REST API on http://localhost:11434:
  GET  /api/tags          -> installed models
  POST /api/chat          -> chat completion

The HTTP call is injectable (`transport`) so the client is testable without a
running server.
"""

import json
import urllib.request
import urllib.error


class OllamaError(Exception):
    """Raised when Ollama is unreachable or returns an error."""


def _http(url, payload=None, method="GET", timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", transport=None,
                 timeout=180):
        self.base_url = base_url.rstrip("/")
        self._transport = transport or _http
        self.timeout = timeout

    def list_models(self):
        """Return installed model names, or raise OllamaError if unreachable."""
        try:
            data = self._transport(f"{self.base_url}/api/tags",
                                   method="GET", timeout=10)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise OllamaError(
                f"Could not reach Ollama at {self.base_url}. Is it running? ({e})")
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat(self, messages, model, temperature=0.2):
        """Send a chat request; return the assistant's text content."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            data = self._transport(f"{self.base_url}/api/chat", payload=payload,
                                   method="POST", timeout=self.timeout)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise OllamaError(f"Ollama request failed: {e}")
        return data.get("message", {}).get("content", "")
