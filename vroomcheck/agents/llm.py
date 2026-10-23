"""Thin LLM client: Llama via Groq (default) or Gemini, always returning JSON.

If no API key is configured the provider resolves to `offline`, and the agents
fall back to deterministic templates so the pipeline and demo still run end to
end (the brief is then labelled as template-generated).
"""
from __future__ import annotations

import json
import logging
import re

import requests

from vroomcheck import config

log = logging.getLogger(__name__)


def active_provider() -> str:
    p = config.LLM_PROVIDER.lower()
    if p == "groq" and config.GROQ_API_KEY:
        return "groq"
    if p == "gemini" and config.GEMINI_API_KEY:
        return "gemini"
    if config.GROQ_API_KEY:
        return "groq"
    if config.GEMINI_API_KEY:
        return "gemini"
    return "offline"


def model_name() -> str:
    return {"groq": config.GROQ_MODEL, "gemini": config.GEMINI_MODEL}.get(active_provider(), "template")


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def chat_json(system: str, user: str, temperature: float = 0.1) -> dict:
    provider = active_provider()
    if provider == "groq":
        from groq import Groq

        client = Groq(api_key=config.GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=config.GROQ_MODEL,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return _parse_json(resp.choices[0].message.content)
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        r = requests.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=120)
        r.raise_for_status()
        return _parse_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    raise RuntimeError("no LLM provider configured")
