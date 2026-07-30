"""Zugriff auf Claude: bevorzugt Anthropic API, sonst Claude-Code-CLI.

Beide Wege liefern JSON zurück. Der CLI-Fallback (`claude -p`) nutzt das
bestehende Claude-Abo des Nutzers und braucht keinen API-Key.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

from . import config


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Erstes JSON-Objekt aus einem Text ziehen (robust gegen Prosa drumherum)."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise LLMError("Keine JSON-Antwort erhalten: %r" % (text or "")[:500])
    return json.loads(match.group(0))


def _media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "image/jpeg"


_client = None
_client_lock = threading.Lock()


def _get_client():
    """Einen Client für alle Aufrufe wiederverwenden (spart TLS-Handshakes)."""
    global _client
    with _client_lock:
        if _client is None:
            import anthropic

            _client = anthropic.Anthropic(timeout=180.0, max_retries=3)
        return _client


def _api_call(prompt: str, images: Optional[List[Path]], model: str) -> dict:
    client = _get_client()
    content = []
    for img in images or []:
        data = base64.standard_b64encode(img.read_bytes()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": _media_type(img), "data": data},
        })
    content.append({"type": "text", "text": prompt})

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("Anfrage wurde vom Modell abgelehnt (refusal)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def _cli_call(prompt: str, images: Optional[List[Path]]) -> dict:
    """Fallback über die Claude-Code-CLI (nutzt das Abo, keinen API-Key)."""
    full_prompt = prompt
    if images:
        paths = "\n".join(str(p) for p in images)
        full_prompt = (
            "Lies zuerst die folgenden Bilddateien mit dem Read-Tool:\n"
            + paths + "\n\n" + prompt
        )
    cmd = [
        config.CLAUDE_CLI, "-p", full_prompt,
        "--output-format", "text",
        "--allowedTools", "Read",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd=str(config.PROJECT_DIR),
        )
    except FileNotFoundError:
        raise LLMError(
            "Weder ANTHROPIC_API_KEY gesetzt noch die claude-CLI gefunden. "
            "Bitte .env ausfüllen (siehe .env.example)."
        )
    if result.returncode != 0:
        raise LLMError("claude-CLI Fehler: %s" % result.stderr[:500])
    return _extract_json(result.stdout)


def ask_json(prompt: str, images: Optional[List[Path]] = None, model: str = "") -> dict:
    """Prompt (optional mit Bildern) an Claude schicken, JSON-Objekt zurück."""
    if config.ANTHROPIC_API_KEY:
        return _api_call(prompt, images, model or config.TEXT_MODEL)
    return _cli_call(prompt, images)
