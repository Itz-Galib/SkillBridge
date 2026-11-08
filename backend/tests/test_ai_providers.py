import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_providers


class FakeResponse:
    def __init__(self, content=None, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if isinstance(self.content, (dict, list)):
            return self.content
        return {"choices": [{"message": {"content": self.content}}]}


def _profile(**updates):
    profile = ai_providers.empty_profile()
    profile["evidence"] = []
    profile.update(updates)
    return profile


def _gemini_response(profile, status_code=200):
    return FakeResponse(
        {
            "candidates": [{
                "content": {
                    "parts": [{"text": json.dumps(profile)}],
                },
            }],
        },
        status_code=status_code,
    )


def test_provider_calls_share_schema_and_normalization(monkeypatch):
    provider_output = _profile(
        headline="Software Engineer",
        skills=[{"name": "Python", "category": "Programming"}],
        evidence=[
            {"field": "headline", "source_snippet": "Software Engineer"},
            {"field": "skills.0.name", "source_snippet": "Python"},
            {"field": "summary", "source_snippet": "not in this CV"},
        ],
    )
    posts = []
    gets = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        if ":generateContent" in url:
            return _gemini_response(provider_output)
        return FakeResponse(json.dumps(provider_output))

    def fake_get(url, **kwargs):
        gets.append((url, kwargs))
        return FakeResponse({"data": [{"id": "openai/gpt-oss-120b"}]})

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    monkeypatch.setattr(ai_providers.httpx, "get", fake_get)
    cv_text = "Software Engineer with Python experience and a long CV summary."
    outputs = []

    for provider, config in ai_providers.PROVIDERS.items():
        result = ai_providers._call_provider(provider, "test-key", cv_text)
        outputs.append(result["profile"])
        url, request = posts[-1]
        if provider == "gemini":
            assert ":generateContent" in url
            assert request["headers"]["x-goog-api-key"] == "test-key"
            assert request["json"]["generationConfig"]["responseMimeType"] == "application/json"
            assert request["json"]["generationConfig"]["temperature"] == 0
        else:
            assert url == config["base_url"] + "/chat/completions"
            assert request["headers"]["Authorization"].startswith("Bearer ")
            assert request["json"]["response_format"] == {"type": "json_object"}
            assert request["json"]["temperature"] == 0
            assert request["json"]["messages"][0]["content"] == ai_providers.SYSTEM_PROMPT
        assert '"skills"' in ai_providers.SYSTEM_PROMPT

    assert all(profile == outputs[0] for profile in outputs)
    assert outputs[0]["skills"] == [
        {"name": "Python", "category": "Programming", "status": "claimed"}
    ]
    assert len(posts) == len(ai_providers.PROVIDERS)
    assert len(gets) == 1
    assert gets[0][0] == "https://api.groq.com/openai/v1/models"
    assert gets[0][1]["headers"]["Authorization"].startswith("Bearer ")
    assert gets[0][1]["timeout"] == 90.0
    assert ai_providers.PROVIDERS["grok"]["base_url"] == "https://api.x.ai/v1"
    assert ai_providers.PROVIDERS["groq"]["base_url"] == "https://api.groq.com/openai/v1"


def test_validation_normalizes_nested_aliases_and_filters_unsupported_values():
    result = ai_providers._validated_result(
        _profile(
            name="CV Candidate",
            education=[{
                "degree": "BSc",
                "field_of_study": "Computer Science",
                "institution": "North University",
                "start_date": "2020",
                "end_date": "2024",
                "description": "Studied software engineering",
            }],
            experience=[{
                "job_title": "Software Intern",
                "company": "ABC Ltd",
                "is_current": True,
                "start_date": "2023",
                "end_date": "Present",
                "achievements": ["Built an API"],
                "technologies": ["Python"],
            }],
            projects=[{
                "name": "SkillBridge",
                "project_url": "https://example.test",
                "technologies": ["React"],
            }],
            skills=[{"name": "Fabricated", "category": "Technical"}],
            evidence=[
                {"field": "name", "source_snippet": "CV Candidate"},
                {"field": "education.0.institution", "source_snippet": "North University"},
                {"field": "education.0.field_of_study", "source_snippet": "Computer Science"},
                {"field": "education.0.start_date", "source_snippet": "2020"},
                {"field": "education.0.end_date", "source_snippet": "2024"},
                {"field": "education.0.description", "source_snippet": "Studied software engineering"},
                {"field": "experience.0.job_title", "source_snippet": "Software Intern"},
                {"field": "experience.0.company", "source_snippet": "ABC Ltd"},
                {"field": "experience.0.start_date", "source_snippet": "2023"},
                {"field": "experience.0.end_date", "source_snippet": "Present"},
                {"field": "experience.0.achievements.0", "source_snippet": "Built an API"},
                {"field": "experience.0.technologies.0", "source_snippet": "Python"},
                {"field": "projects.0.name", "source_snippet": "SkillBridge"},
                {"field": "projects.0.project_url", "source_snippet": "https://example.test"},
                {"field": "projects.0.technologies.0", "source_snippet": "React"},
                {"field": "skills.0.name", "source_snippet": "not supported"},
            ],
        ),
        (
            "CV Candidate BSc Computer Science North University 2020 2024 "
            "Studied software engineering Software Intern ABC Ltd 2023 "
            "Present Built an API Python SkillBridge https://example.test React"
        ),
        {"name": "Account Name", "email": "account@example.test"},
    )

    profile = result["profile"]
    assert profile["name"] == "Account Name"
    assert profile["education"] == [{
        "degree": "BSc",
        "field": "Computer Science",
        "institution": "North University",
        "location": "",
        "start_year": "2020",
        "end_year": "2024",
        "grade": "",
        "details": "Studied software engineering",
    }]
    assert profile["experience"][0]["title"] == "Software Intern"
    assert profile["experience"][0]["current"] is True
    assert profile["experience"][0]["achievements"] == ["Built an API"]
    assert profile["projects"][0]["url"] == "https://example.test"
    assert profile["projects"][0]["technologies"] == ["React"]
    assert profile["skills"] == []


def test_unmatched_evidence_is_removed_and_cv_name_is_suggested():
    result = ai_providers._validated_result(
        _profile(
            name="CV Name",
            headline="Invented Engineer",
            location="Dhaka",
            evidence=[
                {"field": "name", "source_snippet": "CV Name"},
                {"field": "headline", "source_snippet": "not in this CV"},
                {"field": "location", "source_snippet": "Dhaka"},
            ],
        ),
        "CV Name lives in Dhaka.",
        {"name": "Account Name", "email": "account@example.test"},
    )
    assert result["profile"]["name"] == "Account Name"
    assert result["profile"]["headline"] == ""
    assert result["profile"]["location"] == "Dhaka"
    assert result["suggested_name"] == "CV Name"
    assert any(item["field"] == "name_suggestion" for item in result["evidence"])
    assert all("not in this CV" not in item["source_snippet"] for item in result["evidence"])


def test_invalid_profile_schema_is_rejected():
    with pytest.raises(ValueError, match="missing keys"):
        ai_providers._validated_result(
            {"headline": "Engineer"},
            "A sufficiently long CV with an explicit summary.",
            {},
        )


def test_json_embedded_in_non_json_response_is_rejected():
    with pytest.raises(ValueError, match="valid JSON"):
        ai_providers.parse_json_response('Here is the result: {"headline": "Engineer"}')


def test_groq_model_must_be_available_before_chat_request(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "missing-model")
    post_called = False

    def fake_get(*args, **kwargs):
        return FakeResponse({"data": [{"id": "active-model"}]})

    def fake_post(*args, **kwargs):
        nonlocal post_called
        post_called = True
        return FakeResponse("{}")

    monkeypatch.setattr(ai_providers.httpx, "get", fake_get)
    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    with pytest.raises(ai_providers.ProviderAPIError) as error:
        ai_providers._call_provider("groq", "test-key", "A CV long enough to send.")
    assert error.value.code == "model_not_available:missing-model"
    assert not post_called


def test_groq_failure_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "gemini")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_MODEL", "missing-model")
    monkeypatch.setattr(
        ai_providers.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"data": [{"id": "active-model"}]}),
    )
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _gemini_response(_profile(
            headline="Data Analyst",
            evidence=[{"field": "headline", "source_snippet": "Data Analyst"}],
        ))

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    result = ai_providers.extract_cv_profile(
        "Data Analyst with reporting experience and a sufficiently long CV."
    )
    assert result["provider"] == "gemini"
    assert calls == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ]


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
    response = FakeResponse(payload, status_code=429, headers={"x-request-id": "req_test123"})
    monkeypatch.setattr(ai_providers.httpx, "post", lambda *args, **kwargs: response)

    with pytest.raises(ai_providers.ProviderAPIError) as error:
        ai_providers._call_provider(
            "openai", "never-log-this-key", "A CV long enough to send."
        )
    assert error.value.code in {"invalid_api_key", "credit_balance_exhausted"}
    assert error.value.request_id == "req_test123"
    assert "secret text" not in str(error.value)
    assert "never-log-this-key" not in str(error.value)


def test_openai_failure_continues_to_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "api.openai.com" in url:
            return FakeResponse(
                [{"code": "credit_balance_exhausted", "message": "secret text"}],
                status_code=429,
                headers={"x-request-id": "req_openai"},
            )
        return _gemini_response(_profile(
            headline="Data Analyst",
            evidence=[{"field": "headline", "source_snippet": "Data Analyst"}],
        ))

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    result = ai_providers.extract_cv_profile(
        "Data Analyst with reporting experience and a sufficiently long CV."
    )
    assert result["provider"] == "gemini"
    assert calls == [
        "https://api.openai.com/v1/chat/completions",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
    ]


def test_invalid_json_and_both_provider_failures_are_reported_safely(monkeypatch, caplog):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "")
    monkeypatch.setenv("OPENAI_API_KEY", "never-log-openai-key")
    monkeypatch.setattr(
        ai_providers.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse("not-json"),
    )
    with pytest.raises(ai_providers.AIExtractionError, match="openai: ValueError"):
        ai_providers.extract_cv_profile(
            "A sufficiently long CV body that has enough text to parse."
        )
    assert "never-log-openai-key" not in caplog.text

    monkeypatch.setenv("AI_PROVIDER_FALLBACKS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "never-log-gemini-key")
    calls = []

    def fail_both(url, **kwargs):
        calls.append(url)
        return FakeResponse(
            {"error": {"code": "quota_exhausted", "message": "secret provider body"}},
            status_code=429,
        )

    monkeypatch.setattr(ai_providers.httpx, "post", fail_both)
    with pytest.raises(ai_providers.AIExtractionError) as error:
        ai_providers.extract_cv_profile(
            "A sufficiently long CV body that has enough text to parse."
        )
    assert "openai returned HTTP 429" in str(error.value)
    assert "gemini returned HTTP 429" in str(error.value)
    assert "secret provider body" not in str(error.value)
    assert "never-log-openai-key" not in caplog.text
    assert "never-log-gemini-key" not in caplog.text
    assert len(calls) == 2


def test_provider_503_retries_with_backoff(monkeypatch):
    profile_output = _profile(
        headline="Data Analyst",
        evidence=[{"field": "headline", "source_snippet": "Data Analyst"}],
    )
    responses = [
        FakeResponse(
            {"error": {"status": "UNAVAILABLE"}},
            status_code=503,
        ),
        _gemini_response(profile_output),
    ]
    delays = []
    monkeypatch.setattr(
        ai_providers.httpx,
        "post",
        lambda *args, **kwargs: responses.pop(0),
    )
    monkeypatch.setattr(ai_providers.time, "sleep", delays.append)

    result = ai_providers._call_provider(
        "gemini",
        "test-key",
        "Data Analyst experienced in reporting and data quality.",
    )
    assert result["profile"]["headline"] == "Data Analyst"
    assert delays == [1]


def _error_response():
    return FakeResponse(
        {"error": {"code": "invalid_api_key", "message": "secret text"}},
        status_code=401,
        headers={"x-request-id": "req_test123"},
    )


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
