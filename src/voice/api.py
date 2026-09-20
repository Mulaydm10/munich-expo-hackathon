"""Featherless and ElevenLabs adapters for the depot-operator copilot."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile

LANE = "src/voice"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env"
FEATHERLESS_BASE = "https://api.featherless.ai/v1"
DEFAULT_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
PROMPT_PATH = Path(__file__).with_name("prompt.txt")


class VoiceUpstreamError(RuntimeError):
    """An upstream speech or language provider failed."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value or None
    try:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() != name:
            continue
        candidate = candidate.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "'\"":
            candidate = candidate[1:-1]
        return candidate or None
    return None


def _model() -> str:
    return _env("FEATHERLESS_MODEL") or DEFAULT_MODEL


def _voice_id() -> str:
    return _env("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def status() -> dict:
    return {
        "llm": bool(_env("FEATHERLESS_API_KEY")),
        "stt": bool(_env("ELEVENLABS_API_KEY")),
        "tts": bool(_env("ELEVENLABS_API_KEY")),
        "model": _model(),
        "voice_id": _voice_id(),
    }


def _result(doc: dict) -> dict:
    return doc.get("result", doc)


def build_context(
    doc: dict,
    *,
    assumptions: list | None = None,
    site: dict | None = None,
) -> dict:
    result = _result(doc)
    context = {
        key: result.get(key)
        for key in ("spec", "totals", "scorecard", "forecast_accuracy", "warnings")
    }
    if assumptions is not None:
        context["assumptions"] = assumptions
    if site is not None:
        context["site"] = site
    return context


def _client_or_new(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (client, False) if client is not None else (httpx.Client(), True)


def _close(client: httpx.Client, owned: bool) -> None:
    if owned:
        client.close()


def answer(question: str, context: dict, *, client: httpx.Client | None = None) -> dict | None:
    key = _env("FEATHERLESS_API_KEY")
    if not key:
        return None
    model = _model()
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": f"{prompt}\n\nContext JSON:\n{json.dumps(context, separators=(',', ':'))}",
            },
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
        "max_tokens": 160,
    }
    http, owned = _client_or_new(client)
    try:
        response = http.post(
            f"{(_env('FEATHERLESS_BASE_URL') or FEATHERLESS_BASE).rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
        if response.is_error:
            raise VoiceUpstreamError(f"Featherless returned HTTP {response.status_code}")
        body = response.json()
        text = body["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise VoiceUpstreamError("Featherless returned no answer text")
        return {"text": text.strip(), "source": f"featherless:{model}"}
    except VoiceUpstreamError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise VoiceUpstreamError(f"Featherless request failed: {type(exc).__name__}") from exc
    finally:
        _close(http, owned)


def transcribe(
    audio: bytes,
    mime: str,
    *,
    client: httpx.Client | None = None,
) -> str | None:
    key = _env("ELEVENLABS_API_KEY")
    if not key:
        return None
    http, owned = _client_or_new(client)
    try:
        response = http.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": key},
            data={"model_id": "scribe_v1"},
            files={"file": ("audio.webm", audio, mime or "audio/webm")},
            timeout=12,
        )
        if response.is_error:
            raise VoiceUpstreamError(f"ElevenLabs returned HTTP {response.status_code}")
        text = response.json().get("text")
        if not isinstance(text, str):
            raise VoiceUpstreamError("ElevenLabs returned no transcript text")
        return text
    except VoiceUpstreamError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise VoiceUpstreamError(f"ElevenLabs transcription failed: {type(exc).__name__}") from exc
    finally:
        _close(http, owned)


def speak(text: str, *, client: httpx.Client | None = None) -> bytes | None:
    key = _env("ELEVENLABS_API_KEY")
    if not key:
        return None
    http, owned = _client_or_new(client)
    try:
        response = http.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{_voice_id()}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_flash_v2_5"},
            timeout=12,
        )
        if response.is_error:
            raise VoiceUpstreamError(f"ElevenLabs returned HTTP {response.status_code}")
        return response.content
    except VoiceUpstreamError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise VoiceUpstreamError(f"ElevenLabs speech failed: {type(exc).__name__}") from exc
    finally:
        _close(http, owned)


def _spoken(value: Any, digits: int = 1) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "unknown"


def brief_text(result: dict) -> str:
    payload = _result(result)
    totals = payload.get("totals") or {}
    spec = payload.get("spec") or {}
    baseline = totals.get("peak_kw_baseline")
    optimised = totals.get("peak_kw_optimised")
    if baseline is None or optimised is None:
        first = "I don't have the peak for this scenario."
    else:
        date = spec.get("date")
        suffix = f" on {date}" if date else ""
        first = (
            f"The optimised peak is {_spoken(optimised)} kilowatts versus "
            f"{_spoken(baseline)} kilowatts baseline{suffix}."
        )
    revenue = totals.get("capacity_revenue_eur")
    if revenue is None:
        second = "I don't have the revenue for this scenario."
    else:
        second = f"Capacity revenue is {_spoken(revenue, 2)} euros."
    return f"{first} {second}"


router = APIRouter(prefix="/api/voice")


def _error(status_code: int, error: str, detail: str, how_to_fix: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "how_to_fix": how_to_fix},
    )


def _assumptions() -> list[dict]:
    from src.market import api as market

    return [{"key": key, **asdict(value)} for key, value in market.ASSUMPTIONS.items()]


@router.get("/status")
def voice_status() -> dict:
    return status()


@router.post("/ask")
async def voice_ask(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "bad_request", "request body must be JSON", "send question and scenario_id")
    question = body.get("question") if isinstance(body, dict) else None
    scenario_id = body.get("scenario_id") if isinstance(body, dict) else None
    if not isinstance(question, str) or not question.strip():
        return _error(400, "bad_request", "question is required", "send a non-empty question")
    if len(question) > 500:
        return _error(400, "bad_request", "question must be 500 characters or fewer", "shorten the question")
    if not isinstance(scenario_id, str) or not scenario_id:
        return _error(400, "bad_request", "scenario_id is required", "send the loaded scenario id")
    if not _env("FEATHERLESS_API_KEY"):
        return _error(
            503,
            "voice_unavailable",
            "Featherless is not configured",
            "set FEATHERLESS_API_KEY",
        )
    from src.service import api as service

    doc = service._doc_or_404(scenario_id)
    site = None
    site_id = body.get("site_id")
    if site_id:
        site = next(
            (row for row in doc.get("map", {}).get("sites", []) if row.get("site_id") == site_id),
            None,
        )
    try:
        result = answer(question, build_context(doc, assumptions=_assumptions(), site=site))
    except VoiceUpstreamError as exc:
        return _error(502, "voice_upstream_error", exc.detail, "retry the request")
    return JSONResponse(result or {})


@router.post("/transcribe")
async def voice_transcribe(request: Request) -> JSONResponse:
    if not _env("ELEVENLABS_API_KEY"):
        return _error(
            503,
            "voice_unavailable",
            "ElevenLabs is not configured",
            "set ELEVENLABS_API_KEY",
        )
    try:
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ValueError("multipart field `file` is required")
        text = transcribe(await upload.read(), upload.content_type or "audio/webm")
    except VoiceUpstreamError as exc:
        return _error(502, "voice_upstream_error", exc.detail, "retry the request")
    except (ValueError, TypeError) as exc:
        return _error(400, "bad_request", str(exc), "send an audio file in multipart field `file`")
    return JSONResponse({"text": text or ""})


@router.post("/speak")
async def voice_speak(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "bad_request", "request body must be JSON", "send text")
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return _error(400, "bad_request", "text is required", "send non-empty text")
    if len(text) > 600:
        return _error(400, "bad_request", "text must be 600 characters or fewer", "shorten the text")
    if not _env("ELEVENLABS_API_KEY"):
        return _error(
            503,
            "voice_unavailable",
            "ElevenLabs is not configured",
            "set ELEVENLABS_API_KEY",
        )
    try:
        audio = speak(text)
    except VoiceUpstreamError as exc:
        return _error(502, "voice_upstream_error", exc.detail, "retry the request")
    return Response(content=audio or b"", media_type="audio/mpeg")
