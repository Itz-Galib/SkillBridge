"""Tests for CV document storage + extraction drafts (SQLite + local storage; S3 via moto)."""
import os, sys, tempfile, importlib, json, base64

_TMP = tempfile.mkdtemp(prefix="sb_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["STORAGE_LOCAL_DIR"] = os.path.join(_TMP, "cv_files")
os.environ["SEED_DEMO"] = "false"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("STORAGE_BACKEND", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational
import io

import main
import cv_store
import storage

client = TestClient(main.app)
CV = b"Rafi Ahmed\nB.Sc. in Computer Science and Engineering\nSkills: Python, SQL, React, Git\nBuilt dashboards in Tableau and pandas for two internships."


def _user(email):
    r = client.post("/api/auth/register", json={"email": email, "name": "Rafi Ahmed", "password": "password123"})
    token = r.json().get("token") or client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/consent", json={"lang": "en"}, headers=h).status_code == 200
    uid = main.one("SELECT id FROM users WHERE email=?", (email,))["id"]
    return uid, h


def test_cv_text_extraction_stays_private_until_profile_confirmation(monkeypatch):
    import ai_providers

    uid, headers = _user("profile-draft@example.com")
    cv_text = (
        "CV Name\n"
        "Email: rafi@example.com | Phone: +880 1712-345678\n"
        "Junior Software Engineer\n"
        "Skills: Python\n"
    )
    received_text = {}

    def fake_extract(redacted_text, identity=None):
        received_text["text"] = redacted_text
        return {
            "profile": {
                "name": identity["name"],
                "email": identity["email"],
                "headline": "Junior Software Engineer",
                "phone": "",
                "location": "",
                "summary": "",
                "linkedin": "",
                "github": "",
                "portfolio": "",
                "education": [{
                    "degree": "BSc",
                    "field": "Computer Science",
                    "institution": "North University",
                    "location": "Dhaka",
                    "start_year": "2020",
                    "end_year": "2024",
                    "grade": "3.8",
                    "details": "Computer science studies",
                }],
                "experience": [{
                    "title": "Software Intern",
                    "company": "ABC Ltd",
                    "location": "Dhaka",
                    "employment_type": "Internship",
                    "start_date": "2023",
                    "end_date": "2024",
                    "current": False,
                    "description": "Built an API",
                    "achievements": ["Improved response times"],
                    "technologies": ["Python", "FastAPI"],
                }],
                "projects": [{
                    "name": "SkillBridge",
                    "description": "Candidate profile app",
                    "role": "Developer",
                    "technologies": ["React"],
                    "url": "https://example.test",
                    "start_date": "2024",
                    "end_date": "2025",
                }],
                "skills": [{"name": "Python", "category": "Programming"}],
                "certifications": [
                    {"name": "Python Certificate", "issuer": "Example Institute"}
                ],
                "achievements": [{"title": "Hackathon finalist"}],
                "languages": [{"name": "English", "proficiency": "Fluent"}],
                "preferences": {
                    "job_types": [],
                    "work_mode": "",
                    "locations": [],
                },
            },
            "evidence": [
                {
                    "field": "headline",
                    "value": "Junior Software Engineer",
                    "source_snippet": "Junior Software Engineer",
                    "source": "ai_extraction",
                    "confidence": 0.0,
                },
                {
                    "field": "skills.0.name",
                    "value": "Python",
                    "source_snippet": "Python",
                    "source": "ai_extraction",
                    "confidence": 0.0,
                },
                {
                    "field": "skills.0.name",
                    "value": "Python",
                    "source_snippet": "Python",
                    "source": "ai_extraction",
                    "confidence": 0.0,
                },
            ],
            "provider": "openai",
            "model": "test-model",
        }

    monkeypatch.setattr(ai_providers, "extract_cv_profile", fake_extract)
    response = client.post(
        "/api/cv/profile-extract",
        files={"file": ("profile.txt", cv_text.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert "rafi@example.com" not in received_text["text"]
    assert "+880" not in received_text["text"]
    assert "Rafi Ahmed" not in received_text["text"]
    assert main.one("SELECT user_id FROM candidate_profiles WHERE user_id=?", (uid,)) is None

    draft = client.get("/api/candidate/profile/draft", headers=headers)
    assert draft.status_code == 200
    assert draft.json()["profile"]["email"] == "rafi@example.com"
    assert draft.json()["profile"]["name"] == "CV Name"
    assert draft.json()["profile"]["email"] == "rafi@example.com"
    assert "name_suggestion" not in draft.json()["extraction"]
    assert draft.json()["profile"]["experience"][0]["title"] == "Software Intern"
    assert draft.json()["profile"]["projects"][0]["url"] == ""
    assert "fields" not in draft.json()["extraction"]
    assert "low_confidence_fields" in draft.json()["extraction"]
    assert "completeness" in draft.json()
    extraction_response = response.json()["extraction"]
    assert "fields" not in extraction_response
    assert len(extraction_response["low_confidence_fields"]) == len(
        set(extraction_response["low_confidence_fields"])
    )
    stored_evidence = cv_store.list_extractions(
        uid, response.json()["document"]["id"]
    )
    pairs = [(row["field"], row["value"]) for row in stored_evidence]
    assert len(pairs) == len(set(pairs))

    edited = draft.json()["profile"]
    edited["skills"][0]["category"] = "Technical"
    save = client.put(
        "/api/candidate/profile/draft",
        json=edited,
        headers=headers,
    )
    assert save.status_code == 200, save.text
    confirmed = client.post(
        "/api/candidate/profile/confirm",
        json={},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    repeated = client.post(
        "/api/candidate/profile/confirm",
        json={},
        headers=headers,
    )
    assert repeated.status_code == 200, repeated.text
    assert main.one(
        "SELECT COUNT(*) AS count FROM candidate_profiles WHERE user_id=?",
        (uid,),
    )["count"] == 1

    confirmed_response = client.get("/api/candidate/profile", headers=headers).json()
    profile = confirmed_response["profile"]
    assert confirmed_response["completeness"]["total"] >= 0
    assert "Projects" in confirmed_response["completeness"]["sections"]
    assert profile["name"] == "CV Name"
    assert profile["email"] == "rafi@example.com"
    assert confirmed_response["user"]["name"] == "Rafi Ahmed"
    assert confirmed_response["user"]["email"] == "profile-draft@example.com"
    assert profile["skills"] == [
        {"name": "Python", "category": "Technical", "status": "claimed"}
    ]
    assert profile["certifications"][0]["name"] == "Python Certificate"
    assert profile["achievements"][0]["title"] == "Hackathon finalist"
    assert profile["languages"][0]["name"] == "English"
    assert profile["education"][0]["field"] == "Computer Science"
    assert profile["education"][0]["grade"] == "3.8"
    assert profile["experience"][0]["achievements"] == ["Improved response times"]
    assert profile["experience"][0]["technologies"] == ["Python", "FastAPI"]
    assert profile["projects"][0] == {
        "name": "SkillBridge",
        "url": "",
    }

    updated_profile = {key: value for key, value in profile.items() if key != "updated"}
    updated_profile["name"] = "Reviewed Profile Name"
    update = client.put(
        "/api/candidate/profile",
        json=updated_profile,
        headers=headers,
    )
    assert update.status_code == 200, update.text
    updated = client.get("/api/candidate/profile", headers=headers).json()
    assert updated["profile"]["name"] == "Reviewed Profile Name"
    assert updated["user"]["name"] == "Rafi Ahmed"
    assert updated["profile"]["experience"][0]["technologies"] == ["Python", "FastAPI"]


def test_provider_failures_use_private_deterministic_draft_when_meaningful(monkeypatch, caplog):
    import ai_providers

    uid, headers = _user("offline-draft@example.com")
    cv_text = (
        "Rafi Ahmed\n"
        "Skills\n"
        "Python, SQL, React\n"
        "Built dashboards for internship projects."
    )

    def fail_extract(*args, **kwargs):
        raise ai_providers.AIExtractionError(
            "All configured AI providers failed. groq returned HTTP 401"
        )

    monkeypatch.setattr(ai_providers, "extract_cv_profile", fail_extract)
    response = client.post(
        "/api/cv/profile-extract",
        files={"file": ("profile.txt", cv_text.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["method"] == "cv_offline"
    assert response.json()["profile"]["skills"]
    assert response.json()["extraction"]["ai_error"]
    assert main.one(
        "SELECT user_id FROM candidate_profiles WHERE user_id=?",
        (uid,),
    ) is None
    assert "Rafi Ahmed" not in caplog.text
    assert "rafi@example.com" not in caplog.text


def test_unusable_cv_does_not_create_success_shaped_draft(monkeypatch):
    import ai_providers

    uid, headers = _user("empty-extraction@example.com")
    def fail_extract(*args, **kwargs):
        raise ai_providers.AIExtractionError("All providers unavailable")

    monkeypatch.setattr(ai_providers, "extract_cv_profile", fail_extract)
    cv_text = (
        "A general document describing unrelated topics with enough words to "
        "pass basic text extraction but containing no candidate profile facts."
    )
    response = client.post(
        "/api/cv/profile-extract",
        files={"file": ("profile.txt", cv_text.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert response.status_code == 422
    assert "no supported profile information" in response.text.lower()
    assert main.one(
        "SELECT user_id FROM candidate_profile_drafts WHERE user_id=?",
        (uid,),
    ) is None
    assert main.one(
        "SELECT id FROM cv_documents WHERE user_id=?",
        (uid,),
    ) is None


def test_incomplete_nested_draft_is_editable_but_cannot_be_confirmed():
    uid, headers = _user("incomplete-profile@example.com")
    saved = client.put(
        "/api/candidate/profile/draft",
        json={"experience": [{"title": "", "company": ""}]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text

    rejected = client.post(
        "/api/candidate/profile/confirm",
        json={},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert "experience[0]" in rejected.text
    assert main.one(
        "SELECT user_id FROM candidate_profiles WHERE user_id=?",
        (uid,),
    ) is None

    fixed = client.put(
        "/api/candidate/profile/draft",
        json={"headline": "Software Engineer", "experience": []},
        headers=headers,
    )
    assert fixed.status_code == 200, fixed.text
    confirmed = client.post(
        "/api/candidate/profile/confirm",
        json={},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text


def test_local_storage_roundtrip_and_traversal():
    s = storage.LocalStorage(os.path.join(_TMP, "ls"))
    s.put("cv/1/a.txt", b"hello")
    assert s.get("cv/1/a.txt") == b"hello" and s.exists("cv/1/a.txt")
    s.delete("cv/1/a.txt"); s.delete("cv/1/a.txt")  # second delete must not raise
    assert not s.exists("cv/1/a.txt")
    for bad in ("../x", "cv/../../x", "a//b", ""):
        with pytest.raises(storage.StorageError):
            s.put(bad, b"x")
    s.put("/abs/leading-slash.txt", b"x")            # a leading slash is stripped, never treated as an absolute path
    assert s.exists("abs/leading-slash.txt")


def test_upload_stores_file_and_row():
    uid, h = _user("a@example.com")
    r = client.post("/api/cv", files={"file": ("my cv.txt", CV, "text/plain")}, headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["filename"] == "my cv.txt" and doc["size_bytes"] == len(CV)
    assert cv_store.read_document_bytes(uid, doc["id"]) == CV
    row = cv_store.get_document(uid, doc["id"])
    assert row["storage_backend"] == "local" and "my cv" not in row["storage_key"]
    # pasted text has no original file -> no document row
    r2 = client.post("/api/cv", data={"text": CV.decode()}, headers=h)
    assert r2.status_code == 200 and r2.json()["document"] is None


def test_owner_isolation():
    uid1, h1 = _user("b@example.com")
    uid2, _ = _user("c@example.com")
    doc = cv_store.save_document(uid1, "cv.txt", CV)
    assert cv_store.get_document(uid2, doc["id"]) is None
    with pytest.raises(storage.StorageError):
        cv_store.read_document_bytes(uid2, doc["id"])


def test_extraction_review_flow():
    uid, _ = _user("d@example.com")
    doc = cv_store.save_document(uid, "cv.txt", CV)
    n = cv_store.add_extractions(uid, doc["id"], [
        {"field": "education.degree", "value": "B.Sc. in CSE", "source_snippet": "B.Sc. in Computer Science", "confidence": 0.96},
        {"field": "skills.name", "value": "Pythn", "confidence": 1.7},
        {"field": "experience.company", "value": "Foo"},
    ])
    assert n == 3
    rows = cv_store.list_extractions(uid, doc["id"])
    assert {r["status"] for r in rows} == {"pending_review"}
    assert max(r["confidence"] for r in rows) == 1.0          # clamped
    assert cv_store.confirmed_values(uid) == []                 # nothing reviewed yet -> nothing usable
    by = {r["field"]: r["id"] for r in rows}
    cv_store.review_extraction(uid, by["education.degree"], "confirm")
    cv_store.review_extraction(uid, by["skills.name"], "edit", "Python")
    cv_store.review_extraction(uid, by["experience.company"], "reject")
    assert sorted((v["field"], v["value"]) for v in cv_store.confirmed_values(uid)) == [("education.degree", "B.Sc. in CSE"), ("skills.name", "Python")]
    with pytest.raises(ValueError):
        cv_store.review_extraction(uid, by["skills.name"], "edit", "  ")
    uid2, _ = _user("e@example.com")
    with pytest.raises(LookupError):                              # other users cannot review my rows
        cv_store.review_extraction(uid2, by["skills.name"], "confirm")
    # re-extracting replaces only still-pending rows; the 3 reviewed ones survive (+1 new pending)
    cv_store.add_extractions(uid, doc["id"], [{"field": "x", "value": "y"}])
    assert len(cv_store.list_extractions(uid, doc["id"])) == 4
    assert len(cv_store.list_extractions(uid, doc["id"], status="pending_review")) == 1


def test_keeps_only_latest_documents():
    uid, _ = _user("f@example.com")
    ids = [cv_store.save_document(uid, f"cv{i}.txt", CV + bytes([i]))["id"] for i in range(5)]
    left = [d["id"] for d in cv_store.list_documents(uid)]
    assert len(left) == cv_store.KEEP_DOCUMENTS_PER_USER and ids[-1] in left and ids[0] not in left
    assert not storage.get_storage().exists(f"cv/{uid}/{ids[0]}.txt")   # old file removed from storage too


def test_account_deletion_removes_files():
    uid, h = _user("g@example.com")
    doc = cv_store.save_document(uid, "cv.txt", CV)
    key = doc["storage_key"]
    assert storage.get_storage().exists(key)
    assert client.delete("/api/me", headers=h).status_code == 200
    assert not storage.get_storage().exists(key)
    assert main.one("SELECT 1 FROM cv_documents WHERE user_id=?", (uid,)) is None


def test_profile_skills_sync_to_journey_and_blueprint_without_cv():
    uid, headers = _user("profile-skills@example.com")
    skills = [
        {"name": "Python", "category": "Language"},
        {"name": "SQL", "category": "Database"},
        {"name": "React.js", "category": "Frontend"},
        {"name": "Git & GitHub", "category": "Tool"},
    ]
    saved = client.put(
        "/api/candidate/profile/draft",
        json={"summary": "Python and SQL projects.", "skills": skills},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    confirmed = client.post("/api/candidate/profile/confirm", json={}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text

    state = client.get("/api/state", headers=headers).json()
    assert state["from_profile"] is True
    assert state["profile_skills"] == ["Python", "SQL", "React.js", "Git & GitHub"]
    assert set(state["confirmed"]) == {"python", "sql"}
    assert state["not_assessable_yet"] == ["React.js", "Git & GitHub"]
    profile_view = main.one("SELECT * FROM profiles WHERE user_id=?", (uid,))
    assert profile_view["source"] == "profile"
    assert json.loads(profile_view["redaction"]) == {
        "email": 0, "phone": 0, "url": 0, "address": 0, "name": 0,
    }

    blueprint = client.post("/api/assessment/blueprint", headers=headers)
    assert blueprint.status_code == 200, blueprint.text

    profile = client.get("/api/candidate/profile", headers=headers).json()["profile"]
    profile["skills"] = [
        skill for skill in profile["skills"] if skill["name"] != "SQL"
    ] + [{"name": "Statistics", "category": "Other", "status": "claimed"}]
    profile.pop("updated", None)
    updated = client.put("/api/candidate/profile", json=profile, headers=headers)
    assert updated.status_code == 200, updated.text
    state = client.get("/api/state", headers=headers).json()
    assert set(state["confirmed"]) == {"python", "statistics"}
    assert state["not_assessable_yet"] == ["React.js", "Git & GitHub"]

    added = client.put("/api/skills", json={"skills": ["visualization"]}, headers=headers)
    assert added.status_code == 200, added.text
    saved_profile = client.get("/api/candidate/profile", headers=headers).json()["profile"]
    assert {
        (skill["name"], skill["status"]) for skill in saved_profile["skills"]
    } >= {("Data Visualization", "claimed")}
    assert set(client.get("/api/state", headers=headers).json()["confirmed"]) == {
        "python", "statistics", "visualization",
    }


def _image_bytes(image_format, exif=None):
    image = Image.new("RGB", (20, 40), color=(60, 120, 180))
    data = io.BytesIO()
    if exif is None:
        image.save(data, format=image_format)
    else:
        image.save(data, format=image_format, exif=exif)
    return data.getvalue()


def test_avatar_upload_validation_privacy_replacement_and_account_deletion():
    uid, headers = _user("avatar-owner@example.com")
    other_uid, other_headers = _user("avatar-other@example.com")
    exif = Image.Exif()
    exif[0x010F] = "private camera metadata"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[1] = "N"
    gps[2] = (IFDRational(40, 1), IFDRational(0, 1), IFDRational(0, 1))
    gps[3] = "W"
    gps[4] = (IFDRational(73, 1), IFDRational(0, 1), IFDRational(0, 1))
    png = _image_bytes("PNG", exif=exif)
    assert Image.open(io.BytesIO(png)).getexif().get_ifd(ExifTags.IFD.GPSInfo)
    upload = client.post(
        "/api/candidate/avatar",
        files={"file": ("profile.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    row = main.one("SELECT * FROM candidate_avatars WHERE user_id=?", (uid,))
    first_key = row["storage_key"]
    stored = storage.get_storage().get(first_key)
    result = Image.open(io.BytesIO(stored))
    assert result.format == "WEBP"
    assert result.size == (512, 512)
    assert not result.getexif()
    assert upload.json()["has_avatar"] is True
    exported = client.get("/api/me/export", headers=headers).json()["candidate_avatar"]
    assert base64.b64decode(exported["data_base64"]) == stored

    denied = client.get("/api/candidate/avatar", headers=other_headers)
    assert denied.status_code == 404
    assert client.get("/api/state", headers=headers).json()["has_avatar"] is True
    assert client.get("/api/candidate/profile", headers=headers).json()["has_avatar"] is True
    assert client.get("/api/enterprise/me", headers=headers).json()["has_avatar"] is True

    for filename, payload, content_type in (
        ("bad.gif", _image_bytes("GIF"), "image/gif"),
        ("renamed.png", b"not an image", "image/png"),
    ):
        rejected = client.post(
            "/api/candidate/avatar",
            files={"file": (filename, payload, content_type)},
            headers=headers,
        )
        assert rejected.status_code == 415
    too_large = client.post(
        "/api/candidate/avatar",
        files={"file": ("large.png", b"x" * (3 * 1024 * 1024), "image/png")},
        headers=headers,
    )
    assert too_large.status_code == 413

    replacement = client.post(
        "/api/candidate/avatar",
        files={"file": ("replacement.png", _image_bytes("PNG"), "image/png")},
        headers=headers,
    )
    assert replacement.status_code == 200, replacement.text
    second_key = main.one(
        "SELECT storage_key FROM candidate_avatars WHERE user_id=?", (uid,)
    )["storage_key"]
    assert second_key != first_key
    assert not storage.get_storage().exists(first_key)

    assert client.delete("/api/me", headers=headers).status_code == 200
    assert not storage.get_storage().exists(second_key)
    assert main.one("SELECT 1 FROM candidate_avatars WHERE user_id=?", (uid,)) is None


def test_avatar_remove_clears_storage_and_metadata():
    uid, headers = _user("avatar-remove@example.com")
    uploaded = client.post(
        "/api/candidate/avatar",
        files={"file": ("profile.webp", _image_bytes("WEBP"), "image/webp")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    key = main.one(
        "SELECT storage_key FROM candidate_avatars WHERE user_id=?", (uid,)
    )["storage_key"]
    removed = client.delete("/api/candidate/avatar", headers=headers)
    assert removed.status_code == 200
    assert removed.json() == {
        "ok": True, "has_avatar": False, "avatar_updated": None,
    }
    assert not storage.get_storage().exists(key)
    assert main.one("SELECT 1 FROM candidate_avatars WHERE user_id=?", (uid,)) is None


def test_s3_backend_roundtrip(monkeypatch):
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    with moto.mock_aws():
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x"); monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="sb-test")
        s = storage.S3Storage(bucket="sb-test", region="us-east-1", prefix="dev/")
        s.put("cv/1/a.pdf", b"pdfbytes", "application/pdf")
        assert s.get("cv/1/a.pdf") == b"pdfbytes" and s.exists("cv/1/a.pdf")
        assert s.presigned_url("cv/1/a.pdf").startswith("https://")
        s.delete("cv/1/a.pdf")
        assert not s.exists("cv/1/a.pdf")