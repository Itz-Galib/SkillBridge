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
    assert set(state["confirmed"]) == {"python", "sql", "javascript", "git"}
    assert state["not_assessable_yet"] == []
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
    assert set(state["confirmed"]) == {"python", "statistics", "javascript", "git"}
    assert state["not_assessable_yet"] == []

    added = client.put("/api/skills", json={"skills": ["visualization"]}, headers=headers)
    assert added.status_code == 200, added.text
    saved_profile = client.get("/api/candidate/profile", headers=headers).json()["profile"]
    assert {
        (skill["name"], skill["status"]) for skill in saved_profile["skills"]
    } >= {("Data Visualization", "claimed")}
    assert set(client.get("/api/state", headers=headers).json()["confirmed"]) == {
        "python", "statistics", "visualization", "javascript", "git",
    }

    reanalysis = client.post(
        "/api/cv",
        data={"text": "Work summary: Python SQL and Java experience in production applications."},
        headers=headers,
    )
    assert reanalysis.status_code == 200, reanalysis.text
    before_confirm = main.one("SELECT * FROM profiles WHERE user_id=?", (uid,))
    assert before_confirm["source"] == "rules"
    assert "Java" in before_confirm["redacted"]
    confirmed_cv_skills = client.put(
        "/api/skills", json={"skills": ["java"]}, headers=headers
    )
    assert confirmed_cv_skills.status_code == 200, confirmed_cv_skills.text
    after_confirm = main.one("SELECT * FROM profiles WHERE user_id=?", (uid,))
    assert after_confirm["redacted"] == before_confirm["redacted"]
    assert after_confirm["source"] == before_confirm["source"]
    assert "java" in json.loads(after_confirm["confirmed"])
    updated_profile = client.get(
        "/api/candidate/profile", headers=headers
    ).json()["profile"]
    assert any(skill["name"] == "Java" for skill in updated_profile["skills"])


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


def test_avatar_rejects_excessive_dimensions_before_decode(monkeypatch):
    import enterprise
    from fastapi import HTTPException

    class OversizedImage:
        format = "PNG"
        width = 12_000
        height = 12_000
        loaded = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def load(self):
            self.loaded = True

    image = OversizedImage()
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: image)
    with pytest.raises(HTTPException) as error:
        enterprise._process_candidate_avatar(b"small compressed image")
    assert error.value.status_code == 413
    assert image.loaded is False


def test_avatar_storage_cleanup_failures_do_not_misreport_replacement_or_half_delete(monkeypatch):
    uid, headers = _user("avatar-storage-failure@example.com")
    cv_document = cv_store.save_document(uid, "cv.txt", CV)
    uploaded = client.post(
        "/api/candidate/avatar",
        files={"file": ("profile.png", _image_bytes("PNG"), "image/png")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    old_key = main.one(
        "SELECT storage_key FROM candidate_avatars WHERE user_id=?", (uid,)
    )["storage_key"]
    backend = storage.get_storage()

    def fail_delete(_key):
        raise storage.StorageError("simulated storage outage")

    monkeypatch.setattr(backend, "delete", fail_delete)
    replacement = client.post(
        "/api/candidate/avatar",
        files={"file": ("replacement.png", _image_bytes("PNG"), "image/png")},
        headers=headers,
    )
    assert replacement.status_code == 200, replacement.text
    new_key = main.one(
        "SELECT storage_key FROM candidate_avatars WHERE user_id=?", (uid,)
    )["storage_key"]
    assert new_key != old_key

    deletion = client.delete("/api/me", headers=headers)
    assert deletion.status_code == 503
    assert main.one("SELECT 1 FROM users WHERE id=?", (uid,)) is not None
    assert main.one("SELECT 1 FROM candidate_avatars WHERE user_id=?", (uid,)) is not None
    assert main.one("SELECT 1 FROM cv_documents WHERE id=?", (cv_document["id"],)) is not None
    assert storage.get_storage().exists(cv_document["storage_key"])


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


def _add_cv_skill_questions(skill, label, base_id):
    """Insert one Easy, two Medium and two Hard approved CAT-10 questions."""
    for index, difficulty in enumerate(("Easy", "Medium", "Medium", "Hard", "Hard"), 1):
        main.run(
            """INSERT INTO question_bank(id,category_id,subcategory,question_type,difficulty_level,
               question_text,options,correct_answer,explanation,reference_answer,grading_rubric,marks,
               time_allocation,status,created_by,created_at,version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (f"{base_id}-{index}", "CAT-10", label, "MCQ", difficulty,
             f"{label} test question {index}", json.dumps(["A", "B", "C", "D"]), "A",
             "Test explanation", "", "Test rubric", 1, 60, "active", "Test", 0, 1),
        )


def test_skill_journey_and_blueprint_use_one_canonical_skill_catalogue():
    uid, headers = _user("journey-alignment@example.com")
    skills = [
        {"name": "Django", "category": "Backend"},
        {"name": "React", "category": "Frontend"},
        {"name": "Power BI", "category": "Tool"},
        {"name": "C++", "category": "Language"},
        {"name": "Quantum Basket Weaving", "category": "Other"},
    ]
    saved = client.put(
        "/api/candidate/profile/draft",
        json={"summary": "Python and Django services.", "skills": skills},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/candidate/profile/confirm", json={}, headers=headers).status_code == 200

    state = client.get("/api/state", headers=headers).json()
    assert set(state["confirmed"]) == {"python", "javascript", "visualization", "cpp"}
    assert state["not_assessable_yet"] == ["Quantum Basket Weaving"]

    _add_cv_skill_questions("python", "Python", "test-align-python")
    journey = client.get("/api/skill-journey", headers=headers)
    assert journey.status_code == 200, journey.text
    journey = journey.json()
    statuses = {item["name"]: item["status"] for item in journey["journey"]}
    labels = {item["name"]: item["label"] for item in journey["journey"]}
    assert statuses["Django"] == "ready_to_verify"
    assert labels["Django"] == "Python"
    assert statuses["React"] == "question_bank_pending"
    assert statuses["Power BI"] == "question_bank_pending"
    assert statuses["Quantum Basket Weaving"] == "not_assessable"
    assert "python" in journey["coverage_summary"]["covered_skill_ids"]

    blueprint = client.post("/api/assessment/blueprint", headers=headers)
    assert blueprint.status_code == 200, blueprint.text
    blueprint = blueprint.json()
    assert blueprint["skills"]
    assert {item["skill_id"] for item in blueprint["skills"]} == {
        "python", "javascript", "visualization", "cpp",
    }
    assert "python" in blueprint["covered_skill_ids"]
    assert {"javascript", "visualization", "cpp"} <= {
        item["skill_id"] for item in blueprint["uncovered_skills"]
    }
    # The Python skill has a complete 1/2/2 quota and is selected even when the
    # fundamental section is still incomplete in this test database.
    assert blueprint["cv_count"] == 5


def test_company_and_admin_views_share_the_candidate_skill_alignment():
    uid, headers = _user("company-align-candidate@example.com")
    saved = client.put(
        "/api/candidate/profile/draft",
        json={
            "summary": "Python and Docker projects.",
            "skills": [
                {"name": "Python", "category": "Language"},
                {"name": "Docker", "category": "Tool"},
                {"name": "Power BI", "category": "Tool"},
            ],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/candidate/profile/confirm", json={}, headers=headers).status_code == 200
    _add_cv_skill_questions("python", "Python", "test-company-python")
    main.run(
        """INSERT INTO attempts(id,user_id,kind,seed,status,state,started,finished,result,appeal)
           VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
        ("company-align-attempt", uid, "pre", 1, "done", "{}", 0, 1,
         json.dumps({"overall": 80, "topics": {
             "python": {"score": 80, "level": "Advanced", "n": 5},
         }})),
    )

    company_reg = client.post(
        "/api/auth/register",
        json={"email": "company-align@example.com", "name": "Acme", "password": "password123", "role": "company"},
    )
    assert company_reg.status_code == 200, company_reg.text
    company_headers = {"Authorization": "Bearer " + company_reg.json()["token"]}
    assert client.post("/api/consent", json={"lang": "en"}, headers=company_headers).status_code == 200
    company_uid = main.one("SELECT id FROM users WHERE email=?", ("company-align@example.com",))["id"]
    main.run("INSERT INTO companies(id,owner_user_id,name,website,verified,created) VALUES (?,?,?,?,?,?)",
             ("company-align", company_uid, "Acme", "https://acme.test", 1, 0))
    main.run("""INSERT INTO jobs(id,company_id,title,description,location,employment_type,skills,status,is_mock,created)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("job-align", "company-align", "Backend Engineer", "Build services", "Remote", "Full-time",
              json.dumps(["Python", "Docker", "SQL"]), "published", 0, 0))
    main.run("INSERT INTO applications(id,job_id,candidate_id,status,results_shared,created) VALUES (?,?,?,?,?,?)",
             ("app-align", "job-align", uid, "submitted", 1, 0))

    detail = client.get("/api/company/applications/app-align", headers=company_headers)
    assert detail.status_code == 200, detail.text
    detail = detail.json()
    statuses = {skill["required"]: skill["status"] for skill in detail["candidate_skills"]}
    assert statuses == {"Python": "verified", "Docker": "claimed", "SQL": "missing"}
    assert detail["skill_evidence_summary"] == {"verified": 1, "claimed": 1, "missing": 1, "not_shared": 0}

    admin_reg = client.post(
        "/api/auth/register",
        json={"email": "alignment-admin@example.com", "name": "Admin", "password": "password123", "role": "candidate"},
    )
    admin_headers = {"Authorization": "Bearer " + admin_reg.json()["token"]}
    admin_uid = main.one("SELECT id FROM users WHERE email=?", ("alignment-admin@example.com",))["id"]
    main.run("UPDATE user_roles SET role='admin' WHERE user_id=?", (admin_uid,))
    main.run("INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?)",
             (admin_uid, "super_admin", admin_uid, 0))
    coverage = client.get("/api/admin/assessment/graphrag/status", headers=admin_headers)
    assert coverage.status_code == 200, coverage.text
    coverage = coverage.json()
    assert "python" in [item["skill_id"] for item in coverage["covered_skills"]]
    assert "docker" in [item["skill_id"] for item in coverage["uncovered_skills"]]


def test_admin_approval_updates_learning_path_and_creates_passport():
    uid, headers = _user("approval-flow-candidate@example.com")
    saved = client.put(
        "/api/candidate/profile/draft",
        json={"summary": "Python services.", "skills": [{"name": "Python", "category": "Language"}]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert client.post("/api/candidate/profile/confirm", json={}, headers=headers).status_code == 200
    _add_cv_skill_questions("python", "Python", "test-approval-python")
    main.run(
        """INSERT INTO attempts(id,user_id,kind,seed,status,state,started,finished,result,appeal)
           VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
        ("approval-flow-attempt", uid, "pre", 1, "done", "{}", 0, 1,
         json.dumps({
             "overall": 40, "level": "Beginner", "trust": "High trust", "date": "2026-01-01",
             "review_status": "pending_human_review", "manual_review_required": True,
             "topics": {
                 "python": {"score": 40, "level": "Beginner", "n": 5, "label": "Python"},
                 "dsa": {"score": 90, "level": "Advanced", "n": 5, "label": "DSA"},
             },
         })),
    )

    before = client.get("/api/learning/dashboard", headers=headers)
    assert before.status_code == 200, before.text
    before = before.json()
    assert before["approved"] is False
    assert before["passport"] is None
    assert "python" in before["gaps"]
    assert any(r["topic"] == "python" for r in before["resources"])

    admin_reg = client.post(
        "/api/auth/register",
        json={"email": "approval-flow-admin@example.com", "name": "Approver", "password": "password123", "role": "candidate"},
    )
    admin_headers = {"Authorization": "Bearer " + admin_reg.json()["token"]}
    admin_uid = main.one("SELECT id FROM users WHERE email=?", ("approval-flow-admin@example.com",))["id"]
    main.run("UPDATE user_roles SET role='admin' WHERE user_id=?", (admin_uid,))
    main.run("INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?)",
             (admin_uid, "super_admin", admin_uid, 0))

    approved = client.post(
        "/api/admin/attempts/approval-flow-attempt/review",
        json={"decision": "approved", "reason": "Human reviewer confirmed the recorded answers."},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    approved = approved.json()
    assert approved["learning_path_updated"] is True
    assert approved["passport_id"], approved

    after = client.get("/api/learning/dashboard", headers=headers).json()
    assert after["approved"] is True
    assert after["review_status"] == "approved"
    assert after["passport"]["id"] == approved["passport_id"]
    assert after["result_summary"]["overall"] == 40
    assert after["result_summary"]["review_status"] == "approved"
    assert any(item["skill"] == "python" and item["status"] == "below" for item in after["performance"])
    assert any(item["skill"] == "dsa" and item["status"] == "met" for item in after["performance"])

    state = client.get("/api/state", headers=headers).json()
    auto = [p for p in state["passports"] if p["id"] == approved["passport_id"]]
    assert auto and auto[0]["auto"] is True
    assert auto[0]["source_attempt"] == "approval-flow-attempt"

    public = client.get(f"/api/public/passport/{approved['passport_id']}").json()
    assert public["revoked"] is False and public["signature_valid"] is True
    assert public["passport"]["verification"] == "admin_approved"
    assert public["passport"]["attempt_id"] == "approval-flow-attempt"
    assert {s["skill"] for s in public["passport"]["skills"]} == {"python", "dsa"}

    invalidated = client.post(
        "/api/admin/attempts/approval-flow-attempt/review",
        json={"decision": "invalidated", "reason": "Reviewer found a duplicate submission."},
        headers=admin_headers,
    )
    assert invalidated.status_code == 200, invalidated.text
    assert client.get(f"/api/public/passport/{approved['passport_id']}").json()["revoked"] is True
    after_invalid = client.get("/api/learning/dashboard", headers=headers).json()
    assert after_invalid["approved"] is False
    assert after_invalid["review_status"] == "invalidated"


def test_graphrag_scorecard_is_60_fundamental_40_cv_with_sql_as_fundamental():
    uid, headers = _user("scorecard-weighting@example.com")
    selected = [
        {"id": "weight-f1", "assessment_section": "fundamental", "assessment_topic": "DSA",
         "assessment_topic_key": "dsa", "question_type": "MCQ", "difficulty_level": "Easy",
         "question_text": "DSA question", "correct_answer": "A", "marks": 1, "time_allocation": 60},
        {"id": "weight-f2", "assessment_section": "fundamental", "assessment_topic": "OOP",
         "assessment_topic_key": "oop", "question_type": "MCQ", "difficulty_level": "Easy",
         "question_text": "OOP question", "correct_answer": "A", "marks": 1, "time_allocation": 60},
        {"id": "weight-sql", "assessment_section": "cv", "assessment_topic": "SQL",
         "assessment_topic_key": "sql", "skill_id": "sql", "question_type": "MCQ",
         "difficulty_level": "Easy", "question_text": "SQL question", "correct_answer": "A",
         "marks": 1, "time_allocation": 60},
        {"id": "weight-c1", "assessment_section": "cv", "assessment_topic": "Python",
         "assessment_topic_key": "python", "skill_id": "python", "question_type": "MCQ",
         "difficulty_level": "Easy", "question_text": "Python question 1", "correct_answer": "A",
         "marks": 1, "time_allocation": 60},
        {"id": "weight-c2", "assessment_section": "cv", "assessment_topic": "Python",
         "assessment_topic_key": "python", "skill_id": "python", "question_type": "MCQ",
         "difficulty_level": "Easy", "question_text": "Python question 2", "correct_answer": "A",
         "marks": 1, "time_allocation": 60},
    ]
    answers = {
        "weight-f1": "A", "weight-f2": "B", "weight-sql": "A",
        "weight-c1": "A", "weight-c2": "B",
    }
    aid = "weighting-attempt"
    main.run(
        """INSERT INTO attempts(id,user_id,kind,seed,status,state,started,result,appeal)
           VALUES (?,?,?,?,?,?,?,NULL,NULL)""",
        (aid, uid, "pre", 1, "active", "{}", 0),
    )
    st = {
        "graphrag": True, "blueprint_id": "weighting-blueprint", "selected_questions": selected,
        "question_index": 0, "answers": answers, "duration_seconds": 600,
        "question_time_seconds": {}, "seed": 1, "current": None,
    }
    main.finalize(dict(main.one("SELECT * FROM attempts WHERE id=?", (aid,))), st)
    result = json.loads(main.one("SELECT result FROM attempts WHERE id=?", (aid,))["result"])
    assert result["scoring_version"] == "r1-60-40"
    assert result["section_scores"]["fundamental"]["possible"] == 3
    assert result["section_scores"]["cv"]["possible"] == 2
    assert result["section_scores"]["fundamental"]["score"] == 67
    assert result["section_scores"]["cv"]["score"] == 50
    assert result["overall"] == 60
    assert result["topics"]["sql"]["section"] == "fundamental"
    assert result["topics"]["python"]["section"] == "cv"


def test_retake_locked_until_current_result_has_human_review():
    uid, headers = _user("retake-lock@example.com")
    main.run(
        """INSERT INTO attempts(id,user_id,kind,seed,status,state,started,finished,result,appeal)
           VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
        ("retake-lock-attempt", uid, "pre", 1, "done", "{}", 0, 1,
         json.dumps({"overall": 50, "topics": {"python": {"score": 50, "level": "Intermediate", "n": 5}}})),
    )
    blocked = client.post("/api/attempts", headers=headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == "result_awaiting_review"
def test_honesty_score_default_review_and_decision():
    uid, headers = _user("honesty-candidate@example.com")
    admin_reg = client.post(
        "/api/auth/register",
        json={"email": "honesty-admin@example.com", "name": "Honesty Reviewer",
              "password": "password123", "role": "candidate"},
    )
    admin_headers = {"Authorization": "Bearer " + admin_reg.json()["token"]}
    admin_uid = main.one("SELECT id FROM users WHERE email=?", ("honesty-admin@example.com",))["id"]
    main.run("UPDATE user_roles SET role='admin' WHERE user_id=?", (admin_uid,))
    main.run("INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?)",
             (admin_uid, "super_admin", admin_uid, 0))

    attempt_id = "honesty-flow-attempt"
    main.run(
        """INSERT INTO attempts(id,user_id,kind,seed,status,state,started,finished,result,appeal)
           VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
        (attempt_id, uid, "pre", 1, "done", json.dumps({"answers": []}), 100, 700,
         json.dumps({"overall": 70, "trust": "Medium", "trust_points": 3.5,
                     "trust_hard_flags": [], "topics": {}})),
    )
    for i, event_type in enumerate(["ai_gaze_away", "ai_audio_level", "ai_audio_level", "paste"]):
        main.run("INSERT INTO events(attempt_id,type,ts) VALUES (?,?,?)", (attempt_id, event_type, 200 + i))

    detail = client.get("/api/admin/integrity/attempts/" + attempt_id, headers=admin_headers)
    assert detail.status_code == 200, detail.text
    detail = detail.json()
    assert detail["honesty"]["auto_score"] == 65
    assert detail["honesty"]["score"] == 65
    assert detail["honesty"]["status"] == "pending"
    assert detail["honesty"]["source"] == "system"
    flags = {f["type"]: f["count"] for f in detail["flags"]}
    assert flags["ai_audio_level"] == 2 and flags["paste"] == 1

    queue = client.get("/api/admin/integrity/attempts", headers=admin_headers)
    assert queue.status_code == 200, queue.text
    row = next(x for x in queue.json()["items"] if x["id"] == attempt_id)
    assert row["honesty_score"] == 65
    assert row["honesty_status"] == "pending"

    saved = client.post(
        "/api/admin/integrity/attempts/" + attempt_id + "/honesty",
        json={"decision": "pending", "score": 80, "reason": "Adjusted after reading the timeline."},
        headers=admin_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["honesty"]["score"] == 80

    missing_reason = client.post(
        "/api/admin/integrity/attempts/" + attempt_id + "/honesty",
        json={"decision": "approved", "reason": ""},
        headers=admin_headers,
    )
    assert missing_reason.status_code == 422

    approved = client.post(
        "/api/admin/integrity/attempts/" + attempt_id + "/honesty",
        json={"decision": "approved", "reason": "Timeline reviewed; score accepted."},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["honesty"]["status"] == "approved"
    assert approved.json()["honesty"]["score"] == 80

    reloaded = client.get("/api/admin/integrity/attempts/" + attempt_id, headers=admin_headers).json()
    assert reloaded["honesty"]["source"] == "admin"
    assert reloaded["honesty"]["status"] == "approved"
    assert reloaded["honesty"]["score"] == 80

    rejected = client.post(
        "/api/admin/integrity/attempts/" + attempt_id + "/honesty",
        json={"decision": "rejected", "reason": "Integrity review rejected this score."},
        headers=admin_headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["honesty"]["status"] == "rejected"
