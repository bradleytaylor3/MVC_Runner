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


def generate(prompt: str, model: str, host: str = DEFAULT_HOST, think: bool = False) -> GenerateResult:
    url = f"http://{host}/api/generate"
    try:
        resp = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False, "think": think},
            timeout=600,
        )
    except requests.exceptions.ConnectionError as e:
        raise OllamaError(
            f"Could not reach Ollama at {url}. Is it running? (`ollama serve`)"
        ) from e

    if resp.status_code != 200:
        raise OllamaError(f"Ollama returned HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    return GenerateResult(
        text=data.get("response", ""),
        prompt_eval_count=data.get("prompt_eval_count"),
        eval_count=data.get("eval_count"),
    )
