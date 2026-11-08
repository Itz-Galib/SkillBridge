"""CV document + extraction-draft persistence (SQLite now, PostgreSQL-compatible for later).

Three separate layers, as designed for the Candidate Profile feature:

  A. cv_documents    - the original uploaded file (bytes live in storage.py, metadata here)
  B. cv_extractions  - machine-extracted fields waiting for the candidate's review
  C. candidate_profiles (existing table) - only CONFIRMED data ends up here

Extracted data never becomes profile data by itself: every row starts as
'pending_review' and only the candidate can move it to confirmed / edited / rejected.

All SQL uses the same `?` placeholders and TEXT/REAL/INTEGER types as main.py, so it runs
unchanged on SQLite and on PostgreSQL (main.PgCompatConnection rewrites `?`).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from typing import Iterable, List, Optional

from storage import StorageError, get_storage

EXTRACTION_STATUSES = ("pending_review", "confirmed", "edited", "rejected")
REVIEW_ACTIONS = {"confirm": "confirmed", "edit": "edited", "reject": "rejected"}
KEEP_DOCUMENTS_PER_USER = int(os.getenv("KEEP_CV_DOCUMENTS_PER_USER", "3"))

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS cv_documents(
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, filename TEXT NOT NULL,
        content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
        storage_backend TEXT NOT NULL, storage_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'uploaded', created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_cv_documents_user ON cv_documents(user_id, created)",
    """CREATE TABLE IF NOT EXISTS cv_extractions(
        id TEXT PRIMARY KEY, document_id TEXT NOT NULL, user_id INTEGER NOT NULL,
        field TEXT NOT NULL, value TEXT NOT NULL DEFAULT '', edited_value TEXT,
        source TEXT NOT NULL DEFAULT 'uploaded_cv', source_snippet TEXT NOT NULL DEFAULT '',
        source_location TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending_review', created REAL NOT NULL, reviewed_at REAL)""",
    "CREATE INDEX IF NOT EXISTS idx_cv_extractions_user ON cv_extractions(user_id, document_id, status)",
]


# main.py imports this module, so import main lazily to avoid a circular import.
def _m():
    import main
    return main


def ensure_schema() -> None:
    """Create the tables. Safe to call on every start-up."""
    run = _m().run
    for statement in SCHEMA:
        run(statement)


# ------------------------------------------------------------------ documents
def _ext(filename: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,5})$", filename or "")
    return m.group(1).lower() if m else ""


def save_document(user_id: int, filename: str, data: bytes) -> dict:
    """Store the original file, then record it. If the DB insert fails the file is removed again."""
    m = _m()
    storage = get_storage()
    doc_id = uuid.uuid4().hex
    ext = _ext(filename)
    # Key contains no personal data (no user-supplied file name); the real name lives in the DB row.
    key = f"cv/{int(user_id)}/{doc_id}{ext}"
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    storage.put(key, data, content_type)
    try:
        m.run("INSERT INTO cv_documents(id,user_id,filename,content_type,size_bytes,sha256,storage_backend,storage_key,status,created) "
              "VALUES (?,?,?,?,?,?,?,?,?,?)",
              (doc_id, user_id, (filename or "cv")[:200], content_type, len(data),
               hashlib.sha256(data).hexdigest(), storage.name, key, "uploaded", time.time()))
    except Exception:
        storage.delete(key)
        raise
    _prune_old_documents(user_id)
    return get_document(user_id, doc_id)


def get_document(user_id: int, doc_id: str) -> Optional[dict]:
    row = _m().one("SELECT * FROM cv_documents WHERE id=? AND user_id=?", (doc_id, user_id))
    return dict(row) if row else None


def list_documents(user_id: int) -> List[dict]:
    rows = _m().allr("SELECT id,filename,content_type,size_bytes,sha256,status,created FROM cv_documents "
                     "WHERE user_id=? ORDER BY created DESC", (user_id,))
    return [dict(r) for r in rows]


def latest_document(user_id: int) -> Optional[dict]:
    row = _m().one("SELECT * FROM cv_documents WHERE user_id=? ORDER BY created DESC LIMIT 1", (user_id,))
    return dict(row) if row else None


def set_document_status(user_id: int, doc_id: str, status: str) -> None:
    _m().run("UPDATE cv_documents SET status=? WHERE id=? AND user_id=?", (status, doc_id, user_id))


def read_document_bytes(user_id: int, doc_id: str) -> bytes:
    """Return the original file. Only the owner can read it (user_id is part of the lookup)."""
    doc = get_document(user_id, doc_id)
    if not doc:
        raise StorageError("Document not found")
    return get_storage().get(doc["storage_key"])


def _delete_document_rows(user_id: int, doc: dict) -> None:
    m = _m()
    try:
        get_storage().delete(doc["storage_key"])
    except StorageError:
        pass  # a missing/unreachable file must not block deleting the database record
    m.run("DELETE FROM cv_extractions WHERE document_id=? AND user_id=?", (doc["id"], user_id))
    m.run("DELETE FROM cv_documents WHERE id=? AND user_id=?", (doc["id"], user_id))


def _prune_old_documents(user_id: int) -> None:
    rows = _m().allr("SELECT * FROM cv_documents WHERE user_id=? ORDER BY created DESC", (user_id,))
    for old in rows[KEEP_DOCUMENTS_PER_USER:]:
        _delete_document_rows(user_id, dict(old))


def delete_user_documents(user_id: int) -> int:
    """Privacy: remove every stored CV file and extraction for a user (account deletion)."""
    rows = _m().allr("SELECT * FROM cv_documents WHERE user_id=?", (user_id,))
    for r in rows:
        _delete_document_rows(user_id, dict(r))
    _m().run("DELETE FROM cv_extractions WHERE user_id=?", (user_id,))
    return len(rows)


# ------------------------------------------------------------------ extraction drafts
def add_extractions(user_id: int, document_id: str, items: Iterable[dict]) -> int:
    """Replace the pending draft for a document with freshly extracted fields.

    Each item: {field, value, source_snippet?, source_location?, confidence?, source?}
    Fields the candidate already reviewed (confirmed/edited/rejected) are kept.
    """
    m = _m()
    if not get_document(user_id, document_id):
        raise ValueError("Unknown document")
    m.run("DELETE FROM cv_extractions WHERE document_id=? AND user_id=? AND status='pending_review'", (document_id, user_id))
    n, now, seen = 0, time.time(), set()
    for it in items:
        field = str(it["field"])[:80]
        value = str(it.get("value", ""))[:4000]
        identity = (field, value)
        if identity in seen:
            continue
        seen.add(identity)
        conf = max(0.0, min(1.0, float(it.get("confidence", 0))))
        m.run("INSERT INTO cv_extractions(id,document_id,user_id,field,value,source,source_snippet,source_location,confidence,status,created) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (uuid.uuid4().hex, document_id, user_id, field, value,
               str(it.get("source", "uploaded_cv"))[:40], str(it.get("source_snippet", ""))[:600],
               str(it.get("source_location", ""))[:80], conf, "pending_review", now))
        n += 1
    set_document_status(user_id, document_id, "extracted")
    return n


def list_extractions(user_id: int, document_id: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
    sql, args = "SELECT * FROM cv_extractions WHERE user_id=?", [user_id]
    if document_id: sql += " AND document_id=?"; args.append(document_id)
    if status:
        if status not in EXTRACTION_STATUSES: raise ValueError("Invalid status")
        sql += " AND status=?"; args.append(status)
    return [dict(r) for r in _m().allr(sql + " ORDER BY field, created", tuple(args))]


def review_extraction(user_id: int, extraction_id: str, action: str, edited_value: Optional[str] = None) -> dict:
    """Candidate decision on one extracted field: confirm | edit | reject."""
    if action not in REVIEW_ACTIONS:
        raise ValueError("action must be confirm, edit or reject")
    if action == "edit" and not (edited_value or "").strip():
        raise ValueError("edited_value is required for edit")
    m = _m()
    row = m.one("SELECT id FROM cv_extractions WHERE id=? AND user_id=?", (extraction_id, user_id))
    if not row:
        raise LookupError("Extraction not found")
    m.run("UPDATE cv_extractions SET status=?, edited_value=?, reviewed_at=? WHERE id=? AND user_id=?",
          (REVIEW_ACTIONS[action], edited_value.strip()[:4000] if action == "edit" else None, time.time(), extraction_id, user_id))
    return dict(m.one("SELECT * FROM cv_extractions WHERE id=?", (extraction_id,)))


def confirmed_values(user_id: int, document_id: Optional[str] = None) -> List[dict]:
    """Only reviewed values (confirmed or edited) - the ONLY extraction data allowed into the profile."""
    out = []
    for r in list_extractions(user_id, document_id):
        if r["status"] == "confirmed":
            out.append({"field": r["field"], "value": r["value"]})
        elif r["status"] == "edited":
            out.append({"field": r["field"], "value": r["edited_value"]})
    return out