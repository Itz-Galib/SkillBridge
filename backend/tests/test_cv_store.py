"""Tests for CV document storage + extraction drafts (SQLite + local storage; S3 via moto)."""
import os, sys, tempfile, importlib

_TMP = tempfile.mkdtemp(prefix="sb_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["STORAGE_LOCAL_DIR"] = os.path.join(_TMP, "cv_files")
os.environ["SEED_DEMO"] = "false"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("STORAGE_BACKEND", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

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
        "Rafi Ahmed\n"
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
                "education": [],
                "experience": [],
                "projects": [],
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
    assert draft.json()["profile"]["email"] == "profile-draft@example.com"
    assert draft.json()["extraction"]

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

    profile = client.get("/api/candidate/profile", headers=headers).json()["profile"]
    assert profile["skills"] == [
        {"name": "Python", "category": "Technical", "status": "claimed"}
    ]
    assert profile["certifications"][0]["name"] == "Python Certificate"
    assert profile["achievements"][0]["title"] == "Hackathon finalist"
    assert profile["languages"][0]["name"] == "English"


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