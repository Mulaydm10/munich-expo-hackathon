from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.service import api as service
from src.voice import api


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _clear_keys(monkeypatch, tmp_path):
    for name in (
        "FEATHERLESS_API_KEY",
        "FEATHERLESS_MODEL",
        "FEATHERLESS_BASE_URL",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "_ENV_PATH", tmp_path / ".env")


def test_status_is_disabled_without_keys(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    result = api.status()
    assert result["llm"] is False
    assert result["stt"] is False
    assert result["tts"] is False
    assert result["model"] == api.DEFAULT_MODEL


def test_answer_returns_none_without_key(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    assert api.answer("What is the peak?", {"totals": {}}) is None


def test_env_reads_dotenv_and_ignores_comments(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text(
        "# FEATHERLESS_API_KEY=wrong\nFEATHERLESS_MODEL='test/model'\nOTHER=value\n",
        encoding="utf-8",
    )
    assert api._env("FEATHERLESS_MODEL") == "test/model"
    assert api._env("FEATHERLESS_API_KEY") is None


def test_answer_posts_prompt_and_context(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "The peak is available."}}]},
        )

    result = api.answer(
        "What is the peak?",
        {"totals": {"peak_kw_baseline": 12}},
        client=_client(handler),
    )
    assert result == {
        "text": "The peak is available.",
        "source": f"featherless:{api.DEFAULT_MODEL}",
    }
    assert seen["auth"] == "Bearer test-key"
    system = seen["body"]["messages"][0]["content"]
    assert api.PROMPT_PATH.read_text(encoding="utf-8") in system
    assert json.dumps({"totals": {"peak_kw_baseline": 12}}, separators=(",", ":")) in system


def test_prompt_is_loaded_relative_to_package(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")

    def handler(request):
        body = json.loads(request.content)
        assert api.PROMPT_PATH.read_text(encoding="utf-8") in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert api.answer("hello", {}, client=_client(handler))["text"] == "ok"


@pytest.mark.parametrize("operation", ["transcribe", "speak"])
def test_speech_returns_none_without_key(monkeypatch, tmp_path, operation):
    _clear_keys(monkeypatch, tmp_path)
    result = api.transcribe(b"audio", "audio/webm") if operation == "transcribe" else api.speak("hello")
    assert result is None


def test_transcribe_posts_multipart(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def handler(request):
        assert request.headers["xi-api-key"] == "test-key"
        assert b'name="model_id"' in request.content
        assert b'scribe_v1' in request.content
        assert b'audio-bytes' in request.content
        return httpx.Response(200, json={"text": "what is the peak"})

    assert api.transcribe(b"audio-bytes", "audio/webm", client=_client(handler)) == "what is the peak"


def test_speak_returns_audio(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def handler(request):
        assert request.url.path.endswith(api.DEFAULT_VOICE_ID)
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert json.loads(request.content)["model_id"] == "eleven_flash_v2_5"
        return httpx.Response(200, content=b"mp3")

    assert api.speak("hello", client=_client(handler)) == b"mp3"


@pytest.mark.parametrize("operation", ["answer", "transcribe", "speak"])
def test_upstream_errors_are_wrapped(monkeypatch, tmp_path, operation):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def handler(_request):
        return httpx.Response(503, text="unavailable")

    with pytest.raises(api.VoiceUpstreamError):
        if operation == "answer":
            api.answer("hello", {}, client=_client(handler))
        elif operation == "transcribe":
            api.transcribe(b"audio", "audio/webm", client=_client(handler))
        else:
            api.speak("hello", client=_client(handler))


def test_answer_passes_through_provider_message(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")

    def handler(_request):
        return httpx.Response(403, json={"error": {"message": "model is gated"}})

    with pytest.raises(api.VoiceUpstreamError, match=r"Featherless returned HTTP 403: model is gated"):
        api.answer("hello", {}, client=_client(handler))


def test_transcribe_passes_through_provider_message(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def handler(_request):
        return httpx.Response(401, json={"detail": {"message": "speech_to_text permission required"}})

    with pytest.raises(
        api.VoiceUpstreamError,
        match=r"ElevenLabs returned HTTP 401: speech_to_text permission required",
    ):
        api.transcribe(b"audio", "audio/webm", client=_client(handler))


def test_speak_passes_through_provider_message(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    def handler(_request):
        return httpx.Response(401, json={"detail": {"message": "text_to_speech permission required"}})

    with pytest.raises(
        api.VoiceUpstreamError,
        match=r"ElevenLabs returned HTTP 401: text_to_speech permission required",
    ):
        api.speak("hello", client=_client(handler))


def test_brief_text_mentions_missing_revenue_without_symbol():
    text = api.brief_text(
        {
            "spec": {"date": "2026-09-10"},
            "totals": {
                "peak_kw_baseline": 100,
                "peak_kw_optimised": 90,
                "capacity_revenue_eur": None,
            },
        }
    )
    assert "I don't have the revenue for this scenario." in text
    assert "€" not in text
    assert text.count(".") == 2


def test_voice_routes_report_missing_configuration(monkeypatch, tmp_path):
    _clear_keys(monkeypatch, tmp_path)
    client = TestClient(service.app)
    assert client.get("/api/voice/status").status_code == 200
    response = client.post(
        "/api/voice/ask",
        json={"question": "What is the peak?", "scenario_id": "missing"},
    )
    assert response.status_code == 503
    assert response.json()["how_to_fix"] == "set FEATHERLESS_API_KEY"
