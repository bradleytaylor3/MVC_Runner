"""Minimal HTTP client for a local Ollama server's /api/generate endpoint."""

from dataclasses import dataclass

import requests

DEFAULT_HOST = "localhost:11434"


class OllamaError(RuntimeError):
    pass


@dataclass
class GenerateResult:
    text: str
    prompt_eval_count: int | None
    eval_count: int | None


def generate(
    prompt: str,
    model: str,
    host: str = DEFAULT_HOST,
    think: bool = False,
    format: dict | str | None = None,
    options: dict | None = None,
) -> GenerateResult:
    """`format` enables Ollama's grammar-constrained structured output: pass a
    JSON Schema dict to force the response to be JSON matching that shape (or
    the literal string "json" for schema-less valid-JSON-only). This makes
    output format reliable even on small models that don't reliably follow
    prose formatting instructions on their own.

    `options` is passed through verbatim as Ollama's per-request sampling
    options (e.g. {"temperature": 0.2, "seed": 0}) — unset by default, which
    leaves sampling at the model/server's own defaults."""
    url = f"http://{host}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "think": think}
    if format is not None:
        payload["format"] = format
    if options is not None:
        payload["options"] = options

    try:
        resp = requests.post(url, json=payload, timeout=600)
    except requests.exceptions.ConnectionError as e:
        raise OllamaError(
            f"Could not reach Ollama at {url}. Is it running? (`ollama serve`)"
        ) from e
    except requests.exceptions.Timeout as e:
        raise OllamaError(f"Ollama at {url} did not respond within 600s.") from e

    if resp.status_code != 200:
        raise OllamaError(f"Ollama returned HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    return GenerateResult(
        text=data.get("response", ""),
        prompt_eval_count=data.get("prompt_eval_count"),
        eval_count=data.get("eval_count"),
    )
