import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_providers


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.is_error = False
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


def test_provider_calls_share_schema_and_normalization(monkeypatch):
    seen = []
    provider_output = {
        "headline": "Software Engineer",
        "skills": [{"name": "Python", "category": "Programming"}],
        "evidence": [
            {"field": "headline", "source_snippet": "Software Engineer"},
            {"field": "skills.0.name", "source_snippet": "Python"},
            {"field": "skills.0.category", "source_snippet": "Python"},
            {"field": "summary", "source_snippet": "not in this CV"},
        ],
    }

    def fake_post(url, **kwargs):
        seen.append((url, kwargs))
        return FakeResponse(__import__("json").dumps(provider_output))

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    cv_text = "Software Engineer with Python experience and a long CV summary."
    outputs = []
    for provider, config in ai_providers.PROVIDERS.items():
        result = ai_providers._call_provider(provider, "test-key", cv_text)
        outputs.append(result["profile"])
        url, request = seen[-1]
        assert url == config["base_url"] + "/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer test-key"
        assert request["json"]["response_format"] == {"type": "json_object"}
        assert request["json"]["messages"][0]["content"] == ai_providers.SYSTEM_PROMPT
        assert '"skills"' in request["json"]["messages"][0]["content"]
    assert all(profile == outputs[0] for profile in outputs)
    assert outputs[0]["skills"] == [{"name": "Python", "category": "Programming"}]
    assert len(seen) == 4


def test_validation_keeps_only_values_with_literal_evidence():
    result = ai_providers._validated_result(
        {
            "headline": "Invented Engineer",
            "location": "Dhaka",
            "skills": [
                {"name": "Python", "category": "Technical"},
                {"name": "Fabricated", "category": "Technical"},
            ],
            "evidence": [
                {"field": "headline", "source_snippet": "Invented Engineer"},
                {"field": "location", "source_snippet": "Dhaka"},
                {"field": "skills.0.name", "source_snippet": "Python"},
                {"field": "skills.0.category", "source_snippet": "Python"},
                {"field": "skills.1.name", "source_snippet": "not present"},
            ],
        },
        "Candidate is skilled in Python, and lives in Dhaka.",
        {"name": "Candidate Name", "email": "candidate@example.com"},
    )
    assert result["profile"]["name"] == "Candidate Name"
    assert result["profile"]["email"] == "candidate@example.com"
    assert result["profile"]["headline"] == ""
    assert result["profile"]["location"] == "Dhaka"
    assert result["profile"]["skills"] == [{"name": "Python", "category": "Technical"}]
    assert {item["field"] for item in result["evidence"]} == {
        "location",
        "skills.0.name",
        "skills.0.category",
    }


def test_response_rejects_fields_outside_the_shared_schema():
    with pytest.raises(ValueError, match="profile schema"):
        ai_providers._validated_result(
            {"unexpected": "value"},
            "A sufficiently long CV with an explicit summary.",
            {},
        )


def test_provider_http_failure_exposes_safe_status_diagnostics(monkeypatch):
    monkeypatch.setattr(
        ai_providers.httpx,
        "post",
        lambda *args, **kwargs: _error_response(),
    )
    with pytest.raises(ai_providers.ProviderAPIError) as error:
        ai_providers._call_provider("openai", "never-log-this-key", "A CV long enough to send.")
    assert error.value.status_code == 401
    assert error.value.code == "invalid_api_key"
    assert error.value.request_id == "req_test123"
    assert "secret text" not in str(error.value)
    assert "never-log-this-key" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": "invalid_api_key", "message": "secret text"}},
        [{"code": "credit_balance_exhausted", "message": "secret text"}],
    ],
)
def test_http_errors_accept_object_and_list_bodies(monkeypatch, payload):
    response = FakeResponse("")
    response.is_error = True
    response.status_code = 429
    response.headers = {"x-request-id": "req_test123"}
    response.json = lambda: payload
    monkeypatch.setattr(ai_providers.httpx, "post", lambda *args, **kwargs: response)

    with pytest.raises(ai_providers.ProviderAPIError) as error:
        ai_providers._call_provider(
            "openai", "never-log-this-key", "A CV long enough to send."
        )
    assert error.value.code in {"invalid_api_key", "credit_balance_exhausted"}
    assert error.value.request_id == "req_test123"
    assert "secret text" not in str(error.value)
    assert "never-log-this-key" not in str(error.value)


def test_list_shaped_openai_error_continues_to_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "api.openai.com" in url:
            response = FakeResponse("")
            response.is_error = True
            response.status_code = 429
            response.headers = {"x-request-id": "req_openai"}
            response.json = lambda: [
                {"code": "credit_balance_exhausted", "message": "secret text"}
            ]
            return response
        return FakeResponse("{}")

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    result = ai_providers.extract_cv_profile(
        "A sufficiently long CV body that can be used to test provider fallback."
    )
    assert result["provider"] == "gemini"
    assert calls == [
        "https://api.openai.com/v1/chat/completions",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ]


def test_provider_503_retries_with_backoff(monkeypatch):
    profile_output = {
        "headline": "Data Analyst",
        "evidence": [
            {"field": "headline", "source_snippet": "Data Analyst"}
        ],
    }
    responses = []
    delays = []

    unavailable = FakeResponse("")
    unavailable.is_error = True
    unavailable.status_code = 503
    unavailable.headers = {}
    unavailable.json = lambda: {"error": {"status": "UNAVAILABLE"}}
    responses.extend([unavailable, FakeResponse(__import__("json").dumps(profile_output))])

    monkeypatch.setattr(ai_providers.httpx, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(ai_providers.time, "sleep", delays.append)

    result = ai_providers._call_provider(
        "gemini",
        "test-key",
        "Data Analyst experienced in reporting and data quality.",
    )
    assert result["profile"]["headline"] == "Data Analyst"
    assert delays == [1]


def _error_response():
    response = FakeResponse("")
    response.is_error = True
    response.status_code = 401
    response.headers = {"x-request-id": "req_test123"}
    response.json = lambda: {
        "error": {"code": "invalid_api_key", "message": "secret text"}
    }
    return response


def test_extract_requires_a_configured_provider_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "")
    for config in ai_providers.PROVIDERS.values():
        monkeypatch.delenv(config["single_key_env"], raising=False)
        monkeypatch.delenv(config["keys_env"], raising=False)

    with pytest.raises(ai_providers.AIExtractionError, match="No AI API key"):
        ai_providers.extract_cv_profile(
            "A sufficiently long CV body with enough text for validation."
        )


def test_unsupported_fallback_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "not-a-provider")
    with pytest.raises(ai_providers.AIExtractionError, match="Unsupported AI provider"):
        ai_providers._provider_order()
