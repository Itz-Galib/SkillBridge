"""SkillBridge Global R0 API (FastAPI). Scores come from stored answer keys + server logic; AI never grades."""
import os, io, re, json, time, uuid, base64, hmac, hashlib, secrets, sqlite3, zipfile, logging
from datetime import date
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import engine as E
from content import TAXONOMY, JOB, QBYID, TESTABLE

log = logging.getLogger("skillbridge")
DB_PATH = os.getenv("DB_PATH", "/data/skillbridge.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
COOLDOWN_HOURS = float(os.getenv("RETEST_COOLDOWN_HOURS", "0"))   # spec default is 14 days (336h); 0 for demos
MAX_ATTEMPTS_90D = int(os.getenv("MAX_ATTEMPTS_90D", "3"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
CONSENT_VERSION = "r0-2"


def _secret() -> str:
    if os.getenv("SECRET_KEY"): return os.environ["SECRET_KEY"]
    p = Path(DB_PATH).parent / ".secret"
    try:
        if not p.exists(): p.write_text(secrets.token_hex(32))
        return p.read_text().strip()
    except OSError:
        return secrets.token_hex(32)


SECRET = _secret()

# ------------------------------------------------------------------ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, salt TEXT, pw_hash TEXT, created REAL);
CREATE TABLE IF NOT EXISTS consents(id INTEGER PRIMARY KEY, user_id INT, version TEXT, lang TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY, redacted TEXT, redaction TEXT, extracted TEXT, source TEXT, confirmed TEXT);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, user_id INT, kind TEXT, seed INT, status TEXT, state TEXT, started REAL, finished REAL, result TEXT, appeal TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, attempt_id TEXT, type TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS passports(id TEXT PRIMARY KEY, user_id INT, payload TEXT, signature TEXT, created REAL, revoked INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor INT, action TEXT, target TEXT);
CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER PRIMARY KEY, role TEXT NOT NULL DEFAULT 'candidate');
CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, owner_user_id INTEGER UNIQUE NOT NULL, name TEXT NOT NULL, website TEXT, verified INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, company_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL DEFAULT 'Remote', employment_type TEXT NOT NULL DEFAULT 'Full-time', skills TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', is_mock INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, candidate_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'submitted', results_shared INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, UNIQUE(job_id, candidate_id));
CREATE TABLE IF NOT EXISTS company_assessments(id TEXT PRIMARY KEY, company_id TEXT NOT NULL, job_id TEXT, title TEXT NOT NULL, requirements TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_profiles(user_id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', headline TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', linkedin TEXT NOT NULL DEFAULT '', github TEXT NOT NULL DEFAULT '', portfolio TEXT NOT NULL DEFAULT '', education TEXT NOT NULL DEFAULT '[]', experience TEXT NOT NULL DEFAULT '[]', projects TEXT NOT NULL DEFAULT '[]', skills TEXT NOT NULL DEFAULT '[]', certifications TEXT NOT NULL DEFAULT '[]', achievements TEXT NOT NULL DEFAULT '[]', languages TEXT NOT NULL DEFAULT '[]', profile_references TEXT NOT NULL DEFAULT '[]', volunteer TEXT NOT NULL DEFAULT '[]', preferences TEXT NOT NULL DEFAULT '{}', updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assessment_blueprints(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assessment_answer_events(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, question_id TEXT NOT NULL, revision INTEGER NOT NULL, answer_hash TEXT NOT NULL, answer_length INTEGER NOT NULL, created REAL NOT NULL);
"""


class PgCompatConnection:
    """Small DB-API adapter so the prototype can keep parameterized SQL on PostgreSQL."""
    def __init__(self, connection):
        self.connection = connection
    def execute(self, sql, args=()):
        return self.connection.execute(sql.replace("?", "%s"), args)
    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
    def commit(self):
        self.connection.commit()
    def close(self):
        self.connection.close()
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()


def db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        return PgCompatConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row))
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        schema = SCHEMA
        if DATABASE_URL:
            schema = schema.replace("users(id INTEGER PRIMARY KEY", "users(id BIGSERIAL PRIMARY KEY")
            schema = schema.replace("consents(id INTEGER PRIMARY KEY", "consents(id BIGSERIAL PRIMARY KEY")
            schema = schema.replace("events(id INTEGER PRIMARY KEY", "events(id BIGSERIAL PRIMARY KEY")
            schema = schema.replace("audit(id INTEGER PRIMARY KEY", "audit(id BIGSERIAL PRIMARY KEY")
        c.executescript(schema)
    if DATABASE_URL:
        profile_columns = {
            row["column_name"]
            for row in allr(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='candidate_profiles'"
            )
        }
    else:
        profile_columns = {
            row["name"] for row in allr("PRAGMA table_info(candidate_profiles)")
        }
    if "name" not in profile_columns:
        run(
            "ALTER TABLE candidate_profiles ADD COLUMN name "
            "TEXT NOT NULL DEFAULT ''"
        )
    for column in ("email", "profile_references", "volunteer"):
        if column not in profile_columns:
            default = "" if column == "email" else "[]"
            run(
                f"ALTER TABLE candidate_profiles ADD COLUMN {column} "
                f"TEXT NOT NULL DEFAULT '{default}'"
            )
    import cv_store  # CV document + extraction-draft tables (imported here to avoid a circular import)
    cv_store.ensure_schema()


def one(sql, args=()):
    c = db()
    try: return c.execute(sql, args).fetchone()
    finally: c.close()


def allr(sql, args=()):
    c = db()
    try: return c.execute(sql, args).fetchall()
    finally: c.close()


def run(sql, args=()):
    c = db()
    try:
        if DATABASE_URL:
            if sql.lstrip().upper().startswith("INSERT INTO USERS("):
                cur = c.execute(sql.replace("?", "%s") + " RETURNING id", args)
                result = cur.fetchone()["id"]
            else:
                c.execute(sql.replace("?", "%s"), args)
                result = None
            c.commit()
            return result
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def audit(actor, action, target=""):
    run("INSERT INTO audit(ts,actor,action,target) VALUES (?,?,?,?)", (time.time(), actor, action, str(target)))


# ------------------------------------------------------------------ auth
def hash_pw(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def make_token(uid):
    body = base64.urlsafe_b64encode(json.dumps({"uid": uid, "exp": time.time() + 7 * 86400}).encode()).decode()
    return body + "." + hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401, "Not signed in")
    try:
        body, sig = authorization[7:].split(".")
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()): raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body))
        if data["exp"] < time.time(): raise ValueError
    except Exception:
        raise HTTPException(401, "Invalid or expired session")
    u = one("SELECT * FROM users WHERE id=?", (data["uid"],))
    if not u: raise HTTPException(401, "Unknown user")
    # Suspended accounts lose access on their next authenticated request.
    try:
        status = one("SELECT status FROM admin_user_status WHERE user_id=?", (data["uid"],))
        if status and status["status"] == "suspended":
            raise HTTPException(403, "account_suspended")
    except sqlite3.OperationalError:
        pass  # Admin schema may not have been initialised during an early migration.
    return dict(u)


def need_consent(u):
    if not one("SELECT 1 FROM consents WHERE user_id=? AND version=?", (u["id"], CONSENT_VERSION)):
        raise HTTPException(403, "consent_required")


# ------------------------------------------------------------------ app
app = FastAPI(title="SkillBridge Global R0 API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(","),
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup(): init_db()


@app.get("/api/health")
def health(): return {"ok": True, "llm_configured": bool(ANTHROPIC_API_KEY), "version": "r0"}


class Reg(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: str = "candidate"


class Login(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def register(b: Reg):
    email = b.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): raise HTTPException(422, "Invalid email")
    if one("SELECT 1 FROM users WHERE email=?", (email,)): raise HTTPException(409, "Email already registered")
    salt = secrets.token_hex(16)
    if b.role not in ("candidate", "company"):
        raise HTTPException(422, "Role must be candidate or company")
    uid = run("INSERT INTO users(email,name,salt,pw_hash,created) VALUES (?,?,?,?,?)", (email, b.name.strip(), salt, hash_pw(b.password, salt), time.time()))
    run("INSERT INTO user_roles(user_id,role) VALUES (?,?)", (uid, b.role))
    audit(uid, "register")
    return {"token": make_token(uid)}


@app.post("/api/auth/login")
def login(b: Login):
    u = one("SELECT * FROM users WHERE email=?", (b.email.strip().lower(),))
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(b.password, u["salt"])): raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(u["id"])}


# ------------------------------------------------------------------ state / consent / data rights
def latest_done(uid, kind=None):
    q = "SELECT * FROM attempts WHERE user_id=? AND status='done'" + (" AND kind=?" if kind else "") + " ORDER BY finished DESC LIMIT 1"
    return one(q, (uid, kind) if kind else (uid,))


@app.get("/api/state")
def state(u=Depends(current_user)):
    prof = one("SELECT * FROM profiles WHERE user_id=?", (u["id"],))
    atts = allr("SELECT id,kind,status,result,finished FROM attempts WHERE user_id=? ORDER BY started", (u["id"],))
    pp = allr("SELECT id,created,revoked FROM passports WHERE user_id=? ORDER BY created DESC", (u["id"],))
    return {
        "user": {"name": u["name"], "email": u["email"]},
        "consented": bool(one("SELECT 1 FROM consents WHERE user_id=? AND version=?", (u["id"], CONSENT_VERSION))),
        "has_cv": bool(prof and prof["extracted"]),
        "confirmed": json.loads(prof["confirmed"]) if prof and prof["confirmed"] else None,
        "attempts": [{"id": a["id"], "kind": a["kind"], "status": a["status"], "overall": (json.loads(a["result"])["overall"] if a["result"] else None)} for a in atts],
        "passports": [{"id": p["id"], "created": p["created"], "revoked": bool(p["revoked"])} for p in pp],
    }


class ConsentBody(BaseModel):
    lang: str = "en"


@app.post("/api/consent")
def consent(b: ConsentBody, u=Depends(current_user)):
    run("INSERT INTO consents(user_id,version,lang,ts) VALUES (?,?,?,?)", (u["id"], CONSENT_VERSION, b.lang[:5], time.time()))
    audit(u["id"], "consent", CONSENT_VERSION)
    return {"ok": True}


@app.get("/api/me/export")
def export_data(u=Depends(current_user)):
    d = {"user": {"name": u["name"], "email": u["email"]}}
    for t in ("consents", "profiles", "attempts", "passports"):
        d[t] = [dict(r) for r in allr(f"SELECT * FROM {t} WHERE user_id=?", (u["id"],))]
    cp = one("SELECT * FROM candidate_profiles WHERE user_id=?", (u["id"],))
    d["candidate_profile"] = dict(cp) if cp else None
    import cv_store
    d["cv_documents"] = cv_store.list_documents(u["id"])
    d["cv_extractions"] = cv_store.list_extractions(u["id"])
    d["applications"] = [dict(r) for r in allr("SELECT * FROM applications WHERE candidate_id=?", (u["id"],))]
    audit(u["id"], "export")
    return d


@app.delete("/api/me")
def delete_me(u=Depends(current_user)):
    ids = [r["id"] for r in allr("SELECT id FROM attempts WHERE user_id=?", (u["id"],))]
    for i in ids: run("DELETE FROM events WHERE attempt_id=?", (i,))
    import cv_store
    cv_store.delete_user_documents(u["id"])   # removes the stored CV files and extraction drafts too
    for t in (
    "consents",
    "profiles",
    "attempts",
    "passports",
    "candidate_profiles",
    "candidate_profile_drafts",
    "user_roles",
    ):
        run(f"DELETE FROM {t} WHERE user_id=?", (u["id"],))
    run("DELETE FROM applications WHERE candidate_id=?", (u["id"],))
    run("DELETE FROM users WHERE id=?", (u["id"],))
    audit(u["id"], "delete_account")
    return {"deleted": True}


# ------------------------------------------------------------------ CV intelligence
def read_upload(name: str, data: bytes) -> str:
    n = name.lower()
    if len(data) > 2_000_000: raise HTTPException(413, "File too large (max 2 MB)")
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if n.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
        return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
    if n.endswith(".txt") or n.endswith(".md"): return data.decode("utf8", "ignore")
    raise HTTPException(415, "Upload PDF, DOCX or TXT (or paste text)")


def llm_extract(redacted: str):
    """Optional LLM extraction. Input is already PII-redacted. Output is schema-validated; one retry; else fall back to rules."""
    system = (Path(__file__).parent / "prompts" / "skill_extract.v1.md").read_text()
    for _ in range(2):
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30,
                           headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                           json={"model": LLM_MODEL, "max_tokens": 800, "system": system, "messages": [{"role": "user", "content": "<cv>\n" + redacted[:12000] + "\n</cv>"}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"]).strip()
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            skills = E.validate_llm_skills(json.loads(text).get("skills"))
            if skills: return skills
        except Exception as ex:
            log.warning("LLM extraction failed: %s", type(ex).__name__)
    return None


_URL_RE = re.compile(
    r"(?i)(?<![a-z0-9@._-])(?:https?://|www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}"
    r"(?![a-z0-9.-])"
    r"(?:/[^\s<>\"']*)?"
)


def _normalize_profile_url(value: str) -> str:
    value = (value or "").strip().rstrip(".,;:!?)]}")
    if not value:
        return ""
    if value.startswith("//"):
        value = value[2:]
    value = re.sub(r"^https?://", "", value, flags=re.I)
    if value.lower().startswith("www."):
        value = value[4:]
    parsed = urlparse("https://" + value)
    if not parsed.hostname or "." not in parsed.hostname:
        return ""
    return "https://" + parsed.netloc.lower() + parsed.path + (
        "?" + parsed.query if parsed.query else ""
    ) + ("#" + parsed.fragment if parsed.fragment else "")


def _urls_in_text(text: str) -> list[str]:
    return list(dict.fromkeys(
        normalized
        for match in _URL_RE.findall(text or "")
        if (normalized := _normalize_profile_url(match))
    ))


def _append_document_links(text: str, links: list[str]) -> str:
    links = list(dict.fromkeys(url for url in links if url))
    if not links:
        return text
    return text.rstrip() + "\n\nLINKS FOUND IN DOCUMENT:\n" + "\n".join(links)


def read_profile_cv(filename: str, data: bytes):
    """
    Read a PDF or DOCX CV.
    Embedded PDF text is preferred; OCR is only used for image-based pages.
    """
    name = (filename or "").lower()

    if len(data) > 5_000_000:
        raise HTTPException(413, "CV file must be 5 MB or smaller")

    if name.endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "Invalid PDF file")

        try:
            import fitz
            import pytesseract
            from PIL import Image

            pdf = fitz.open(stream=data, filetype="pdf")

            if pdf.needs_pass:
                raise HTTPException(422, "Password-protected PDFs are not supported")

            if pdf.page_count > 10:
                raise HTTPException(413, "CV PDF must contain 10 pages or fewer")

            page_texts = []
            ocr_pages = []
            links = []

            for page_number, page in enumerate(pdf, start=1):
                text = (page.get_text("text") or "").strip()
                links.extend(_urls_in_text(text))

                # Only OCR pages that have little or no embedded text.
                if len(text) < 40:
                    try:
                        pixmap = page.get_pixmap(
                            matrix=fitz.Matrix(2, 2),
                            alpha=False,
                        )

                        image = Image.open(
                            io.BytesIO(pixmap.tobytes("png"))
                        )

                        # Prefer eng+ben when available; fall back to eng so
                        # missing language packs do not kill extraction.
                        preferred = os.getenv("TESSERACT_LANG", "eng+ben")
                        ocr_text = ""
                        for lang in (preferred, "eng"):
                            try:
                                ocr_text = pytesseract.image_to_string(
                                    image,
                                    lang=lang,
                                ).strip()
                                if ocr_text:
                                    break
                            except Exception as lang_ex:
                                log.warning(
                                    "OCR lang=%s failed on page %s: %s",
                                    lang,
                                    page_number,
                                    type(lang_ex).__name__,
                                )
                        text = ocr_text or text
                        if ocr_text:
                            ocr_pages.append(page_number)

                    except Exception as ex:
                        log.warning(
                            "OCR failed on page %s: %s",
                            page_number,
                            type(ex).__name__,
                        )

                if text:
                    page_texts.append(
                        f"[Page {page_number}]\n{text}"
                    )

            pdf.close()

            raw_text = "\n\n".join(page_texts)
            try:
                from pypdf import PdfReader

                for pdf_page in PdfReader(io.BytesIO(data)).pages:
                    for annotation in pdf_page.get("/Annots", []):
                        annotation = annotation.get_object()
                        action = annotation.get("/A")
                        if action:
                            target = action.get_object().get("/URI")
                            if target:
                                normalized = _normalize_profile_url(str(target))
                                if normalized:
                                    links.append(normalized)
            except Exception as ex:
                log.warning("PDF link extraction failed: %s", type(ex).__name__)

            links = list(dict.fromkeys(links))
            raw_text = _append_document_links(raw_text, links)

            if len(raw_text.strip()) < 30:
                raise HTTPException(
                    422,
                    "Could not read enough text from this PDF. Please try another CV or enter your profile manually.",
                )

            return raw_text, {
                "parser": "pymupdf",
                "ocr_used": bool(ocr_pages),
                "ocr_pages": ocr_pages,
                "links_found": links,
            }

        except HTTPException:
            raise
        except Exception as ex:
            log.warning("PDF parsing failed: %s", type(ex).__name__)
            raise HTTPException(422, "Could not read this PDF file")

    if name.endswith(".docx"):
        if not data.startswith(b"PK"):
            raise HTTPException(415, "Invalid DOCX file")

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()

                if "word/document.xml" not in names:
                    raise HTTPException(422, "Invalid DOCX document")

                if len(names) > 500:
                    raise HTTPException(422, "DOCX contains too many internal files")

                total_size = sum(item.file_size for item in archive.infolist())

                if total_size > 20_000_000:
                    raise HTTPException(413, "DOCX expands beyond the allowed size")

                xml = archive.read("word/document.xml")
                relations = (
                    archive.read("word/_rels/document.xml.rels")
                    if "word/_rels/document.xml.rels" in names
                    else b""
                )

            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
            namespace = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }

            paragraphs = []

            for paragraph in root.findall(".//w:p", namespace):
                parts = [
                    node.text or ""
                    for node in paragraph.findall(".//w:t", namespace)
                ]

                line = "".join(parts).strip()

                if line:
                    paragraphs.append(line)

            raw_text = "\n".join(paragraphs)
            links = _urls_in_text(raw_text)
            if relations:
                relation_root = ET.fromstring(relations)
                for relation in relation_root:
                    target = relation.attrib.get("Target", "")
                    if (
                        relation.attrib.get("TargetMode") == "External"
                        or target.lower().startswith(("http://", "https://", "www."))
                    ):
                        normalized = _normalize_profile_url(target)
                        if normalized:
                            links.append(normalized)
            links = list(dict.fromkeys(links))
            raw_text = _append_document_links(raw_text, links)

            if len(raw_text.strip()) < 30:
                raise HTTPException(422, "Could not extract enough text from this DOCX")

            return raw_text, {
                "parser": "docx_xml",
                "ocr_used": False,
                "ocr_pages": [],
                "links_found": links,
            }

        except HTTPException:
            raise
        except Exception as ex:
            log.warning("DOCX parsing failed: %s", type(ex).__name__)
            raise HTTPException(422, "Could not read this DOCX file")

    if name.endswith((".txt", ".md")):
        try:
            raw_text = data.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            raise HTTPException(422, "Text CV must be encoded as UTF-8")
        if len(raw_text) < 30:
            raise HTTPException(422, "Could not extract enough text from this CV")
        links = _urls_in_text(raw_text)
        raw_text = _append_document_links(raw_text, links)
        return raw_text, {
            "parser": "utf8_text",
            "ocr_used": False,
            "ocr_pages": [],
            "links_found": links,
        }

    raise HTTPException(415, "Please upload a PDF, DOCX, TXT or MD file")


def extract_profile_fields(raw_text: str, identity: Optional[dict] = None):
    """
    Offline rule-based profile extraction used as the primary fallback when
    AI providers are unavailable. Fills as many fields as the CV text supports
    without inventing facts. identity may supply name/email from the account.
    """
    identity = identity or {}

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in (raw_text or "").splitlines()
    ]
    lines = [line for line in lines if line]
    text = "\n".join(lines)
    low = text.lower()

    profile = {
        "name": str(identity.get("name") or "").strip(),
        "email": str(identity.get("email") or "").strip(),
        "phone": "",
        "headline": "",
        "location": "",
        "summary": "",
        "linkedin": "",
        "github": "",
        "portfolio": "",
        "education": [],
        "experience": [],
        "projects": [],
        "skills": [],
        "certifications": [],
        "achievements": [],
        "languages": [],
        "references": [],
        "volunteer": [],
        "preferences": {
            "job_types": [],
            "work_mode": "",
            "locations": [],
        },
    }
    evidence = []

    def add_evidence(field, value, confidence, snippet=""):
        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        evidence.append({
            "field": field,
            "value": json.dumps(value, ensure_ascii=False)
            if isinstance(value, (list, dict))
            else str(value),
            "confidence": confidence,
            "source_snippet": (snippet or str(value))[:600],
            "source_location": "CV text",
            "source": "deterministic_parser",
        })

    # ---- Contact ----
    email_match = re.search(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        text,
    )
    if email_match:
        profile["email"] = email_match.group(0)
        add_evidence("email", profile["email"], 0.99, email_match.group(0))

    phone_match = re.search(
        r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)",
        text,
    )
    if phone_match:
        profile["phone"] = phone_match.group(0).strip()
        add_evidence("phone", profile["phone"], 0.85, phone_match.group(0))

    for url in _urls_in_text(text):
        parsed_url = urlparse(url)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if parsed_url.hostname == "linkedin.com" and len(path_parts) >= 2 and path_parts[0].lower() == "in":
            profile["linkedin"] = url
            add_evidence("linkedin", url, 0.98, url)
        elif (
            parsed_url.hostname == "github.com"
            and len(path_parts) == 1
            and path_parts[0].lower() not in {"features", "topics", "settings", "organizations"}
        ):
            profile["github"] = url
            add_evidence("github", url, 0.98, url)

    portfolio_match = re.search(
        r"(?i)\bportfolio\b\s*[:|,-]?\s*((?:https?://|www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s<>\"']*)?)",
        text,
    )
    if portfolio_match:
        profile["portfolio"] = _normalize_profile_url(portfolio_match.group(1))
        add_evidence("portfolio", profile["portfolio"], 0.9, portfolio_match.group(1))

    # ---- Name (first non-contact short line) ----
    if not profile["name"]:
        for line in lines[:8]:
            if "@" in line or re.search(r"\d{3,}", line):
                continue
            if re.search(r"https?://|www\.", line, re.I):
                continue
            words = line.split()
            if 1 <= len(words) <= 5 and len(line) <= 60:
                if re.match(r"^[A-Za-z .'\-]+$", line):
                    profile["name"] = line.strip()
                    add_evidence("name", profile["name"], 0.70, line)
                    break

    # ---- Location (city/country style lines near the top) ----
    location_pattern = re.compile(
        r"\b("
        r"Dhaka|Chittagong|Chattogram|Sylhet|Khulna|Rajshahi|Barisal|"
        r"Rangpur|Mymensingh|Gazipur|Narayanganj|"
        r"Bangladesh|India|Pakistan|Nepal|Sri Lanka|"
        r"Remote|Hybrid|"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*[A-Z]{2,}"
        r")\b",
    )
    for line in lines[:15]:
        if re.search(
            r"(?i)\b(permanent address|address|house|road|rd\.|flat|block|sector|village)\b",
            line,
        ):
            continue
        location_hint = location_pattern.search(line)
        if location_hint:
            profile["location"] = location_hint.group(0)
            add_evidence("location", profile["location"], 0.55, location_hint.group(0))
            break

    # ---- Section split ----
    section_aliases = {
        "summary": [
            "summary", "professional summary", "profile", "career objective",
            "objective", "about me", "about", "professional profile",
        ],
        "education": [
            "education", "academic qualification", "academic qualifications",
            "academic background", "academics",
        ],
        "experience": [
            "experience", "work experience", "employment history",
            "professional experience", "work history", "employment",
            "internships", "internship experience",
        ],
        "projects": [
            "projects", "personal projects", "academic projects",
            "key projects", "selected projects", "project experience",
        ],
        "skills": [
            "skills", "technical skills", "core skills", "key skills",
            "technologies", "tech stack", "tools", "competencies",
        ],
        "certifications": [
            "certifications", "certificates", "licenses", "credentials",
            "professional certifications",
        ],
        "achievements": [
            "achievements", "awards", "honors", "accomplishments",
            "awards and achievements",
        ],
        "languages": [
            "languages", "language proficiency", "spoken languages",
        ],
    }

    def normalise_heading(line: str) -> str:
        return re.sub(r"[^a-z ]", "", line.lower()).strip()

    heading_lookup = {}
    for section, aliases in section_aliases.items():
        for alias in aliases:
            heading_lookup[normalise_heading(alias)] = section

    sections: dict = {}
    current_section = None
    for line in lines:
        heading = normalise_heading(line)
        if heading in heading_lookup and len(line) < 80:
            current_section = heading_lookup[heading]
            sections.setdefault(current_section, [])
            continue
        # Standalone ALL-CAPS heading ends the previous section if unknown
        if (
            current_section
            and len(line) < 50
            and line.isupper()
            and re.search(r"[A-Z]", line)
            and heading not in heading_lookup
        ):
            current_section = None
            continue
        if current_section:
            sections.setdefault(current_section, []).append(line)

    # ---- Summary / headline ----
    summary_lines = [
        line for line in sections.get("summary", [])
        if not re.match(
            r"(?i)^\s*(?:gender|dob|date of birth|religion|permanent address)\s*[:\-]",
            line,
        )
    ]
    if summary_lines:
        summary = " ".join(summary_lines)[:3000]
        profile["summary"] = summary
        add_evidence("summary", summary, 0.75, " ".join(summary_lines)[:600])
        # First sentence often works as headline
        first = re.split(r"[.!?]\s+", summary, maxsplit=1)[0].strip()
        if 10 <= len(first) <= 120:
            profile["headline"] = first
            add_evidence("headline", first, 0.55, first)

    if not profile["headline"]:
        title_match = re.search(
            r"\b("
            r"(?:Senior |Junior |Lead |Principal |Staff )?"
            r"(?:Software|Full[- ]?Stack|Frontend|Backend|Data|Machine Learning|ML|AI|"
            r"DevOps|Cloud|Mobile|Android|iOS|Web|QA|Security)?"
            r"\s*(?:Engineer|Developer|Analyst|Scientist|Intern|Designer|Architect|"
            r"Manager|Consultant|Specialist)"
            r")\b",
            text,
            re.I,
        )
        if title_match:
            profile["headline"] = title_match.group(1).strip()
            add_evidence("headline", profile["headline"], 0.60, title_match.group(0))

    # ---- Education ----
    degree_re = re.compile(
        r"\b("
        r"B\.?Sc\.?|M\.?Sc\.?|B\.?S\.?|M\.?S\.?|B\.?A\.?|M\.?A\.?|"
        r"Bachelors?(?:\s+of\s+Science|\s+of\s+Arts)?|"
        r"Masters?(?:\s+of\s+Science|\s+of\s+Arts)?|"
        r"Ph\.?D\.?|Doctorate|Diploma|HSC|SSC|A[- ]?Level|O[- ]?Level|"
        r"Bachelor(?:'s)?|Master(?:'s)?"
        r")\b",
        re.I,
    )
    institution_words = (
        "university", "college", "institute", "school", "academy",
        "polytechnic", "varsity",
    )
    year_re = re.compile(r"\b(19|20)\d{2}\b")

    education_lines = sections.get("education", [])
    if not education_lines:
        # Fallback: scan whole text for degree + institution patterns
        for line in lines:
            if degree_re.search(line) and any(w in line.lower() for w in institution_words):
                education_lines.append(line)

    seen_edu = set()
    for line in education_lines:
        key = line.lower()[:80]
        if key in seen_edu:
            continue
        seen_edu.add(key)

        degree_m = degree_re.search(line)
        years = year_re.findall(line)
        # findall with groups returns tuples; normalize
        year_nums = re.findall(r"\b(?:19|20)\d{2}\b", line)
        institution = ""
        for word in institution_words:
            if word in line.lower():
                # take a chunk around the institution word
                institution = line[:200]
                break
        if not institution and degree_m:
            institution = line[:200]

        if not (degree_m or any(w in line.lower() for w in institution_words)):
            continue

        item = {
            "degree": degree_m.group(0) if degree_m else "",
            "field_of_study": "",
            "institution": institution[:160] if institution else line[:160],
            "location": "",
            "start_date": year_nums[0] if len(year_nums) >= 2 else "",
            "end_date": year_nums[-1] if year_nums else "",
            "graduation_year": year_nums[-1] if year_nums else "",
            "grade": "",
            "description": line[:500],
        }
        # Field of study heuristics
        field_m = re.search(
            r"(?:in|of)\s+([A-Za-z][A-Za-z &/]{2,40})(?:\s+from|\s*$|,)",
            line,
            re.I,
        )
        if field_m:
            item["field_of_study"] = field_m.group(1).strip()[:80]

        grade_m = re.search(
            r"(?:CGPA|GPA|Grade)\s*[:=]?\s*([0-9.]+(?:\s*/\s*[0-9.]+)?)",
            line,
            re.I,
        )
        if grade_m:
            item["grade"] = grade_m.group(1).strip()

        profile["education"].append(item)
        add_evidence("education", item, 0.70, line)

    # ---- Experience ----
    experience_lines = sections.get("experience", [])
    # Group consecutive lines into job blocks separated by blank-ish boundaries
    job_blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in experience_lines:
        # New block often starts with a title-like short line or company + date
        looks_like_header = (
            len(line) < 100
            and (
                bool(year_re.search(line))
                or bool(re.search(
                    r"\b(intern|engineer|developer|analyst|manager|assistant|"
                    r"associate|officer|consultant|trainee)\b",
                    line,
                    re.I,
                ))
            )
        )
        if looks_like_header and current_block and len(current_block) >= 1:
            job_blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)
    if current_block:
        job_blocks.append(current_block)

    date_chunk_re = re.compile(
        r"(?:,?\s*)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        r"|\d{1,2}[/-]\d{1,2})?[.\s/-]*(?:19|20)\d{2}\s*[-–—to]+\s*"
        r"(?:Present|Current|Now|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\.?\s*(?:19|20)\d{2}|(?:19|20)\d{2})",
        re.I,
    )

    for block in job_blocks[:12]:
        header = block[0]
        body = " ".join(block[1:])[:2000] if len(block) > 1 else ""
        years = re.findall(r"\b(?:19|20)\d{2}\b", header + " " + body)
        is_current = bool(re.search(r"\b(present|current|now|ongoing)\b", header + " " + body, re.I))

        # Strip trailing date ranges so company parsing stays clean
        header_no_dates = date_chunk_re.sub("", header).strip(" |,-–—")
        if not header_no_dates:
            header_no_dates = header

        title_m = re.search(
            r"\b("
            r"(?:Senior |Junior |Lead |Principal |Staff |Associate )?"
            r"(?:Software |Full[- ]?Stack |Frontend |Backend |Data |ML |AI )?"
            r"(?:Engineer|Developer|Analyst|Scientist|Intern|Designer|"
            r"Architect|Manager|Consultant|Specialist|Officer|Assistant|"
            r"Trainee|Coordinator)"
            r"(?:\s+Intern)?"
            r")\b",
            header_no_dates,
            re.I,
        )
        company = ""
        # Prefer explicit separators: @, " at ", |, " - "
        company_m = re.search(
            r"(?:@|\bat\b|\||\s[-–—]\s)\s*([A-Za-z0-9][A-Za-z0-9 .,&'()-]{1,60})",
            header_no_dates,
            re.I,
        )
        if company_m:
            company = company_m.group(1).strip(" ,-|–—")
            # Drop residual date tokens from company
            company = re.sub(
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
                "",
                company,
                flags=re.I,
            ).strip(" ,-|")
            company = re.sub(r"\b(?:19|20)\d{2}\b", "", company).strip(" ,-|")
        elif title_m:
            # Remainder of the header after the title is often the company
            after = header_no_dates[title_m.end():].strip(" ,-|–—@")
            after = re.sub(r"^(?:at\s+)", "", after, flags=re.I).strip()
            if after and not re.match(r"^(?:19|20)\d{2}", after):
                company = after[:120]

        job_title = title_m.group(0).strip() if title_m else header_no_dates[:120]
        # Prefer longer title phrase when "Software Engineer Intern" style
        long_title = re.search(
            r"\b((?:Senior |Junior |Lead |Principal |Staff |Associate )?"
            r"[A-Za-z][A-Za-z /&-]{0,40}"
            r"(?:Engineer|Developer|Analyst|Scientist|Intern|Designer|"
            r"Architect|Manager|Consultant|Specialist|Officer|Assistant|"
            r"Trainee|Coordinator)(?:\s+Intern)?)\b",
            header_no_dates,
            re.I,
        )
        if long_title and len(long_title.group(1)) > len(job_title):
            job_title = long_title.group(1).strip()

        item = {
            "job_title": job_title[:120],
            "company": company[:120],
            "location": "",
            "employment_type": "",
            "start_date": years[0] if years else "",
            "end_date": "Present" if is_current else (years[-1] if years else ""),
            "is_current": is_current,
            "duration_months": 0,
            "description": body or header[:500],
            "achievements": [],
            "technologies": [],
        }
        if re.search(r"\bintern\b", header, re.I):
            item["employment_type"] = "Internship"
        elif re.search(r"\bfull[- ]?time\b", header, re.I):
            item["employment_type"] = "Full-time"
        elif re.search(r"\bpart[- ]?time\b", header, re.I):
            item["employment_type"] = "Part-time"

        profile["experience"].append(item)
        add_evidence("experience", item, 0.60, header)

    # ---- Projects ----
    for line in sections.get("projects", [])[:20]:
        cleaned = line.strip()
        if len(cleaned) < 3:
            continue
        item = {
            "name": cleaned[:160],
            "url": "",
        }
        profile["projects"].append(item)
        add_evidence("projects", item, 0.55, line)

    # ---- Skills (taxonomy + free-form from skills section) ----
    skill_seen = set()
    for sid, meta in TAXONOMY.items():
        for alias in meta.get("aliases", []) + [meta.get("name", "")]:
            alias = (alias or "").strip()
            if len(alias) < 2:
                continue
            if re.search(
                r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])",
                low,
            ):
                if sid in skill_seen:
                    break
                skill_seen.add(sid)
                item = {
                    "name": meta["name"],
                    "category": "Technical",
                    "source_snippet": alias[:120],
                    "status": "claimed",
                }
                profile["skills"].append(item)
                add_evidence("skills", item, 0.70, alias)
                break

    # Extra skills from the skills section (comma / bullet separated)
    for line in sections.get("skills", []):
        parts = re.split(r"[,|•·/]| {2,}", line)
        for part in parts:
            name = part.strip(" -•*·\t")
            if not (2 <= len(name) <= 40):
                continue
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 .+#\-]{1,38}$", name):
                continue
            key = name.lower()
            if key in skill_seen:
                continue
            skill_seen.add(key)
            item = {
                "name": name,
                "category": "Technical",
                "source_snippet": name,
                "status": "claimed",
            }
            profile["skills"].append(item)
            add_evidence("skills", item, 0.55, name)

    # ---- Certifications ----
    for line in sections.get("certifications", [])[:15]:
        cleaned = line.lstrip("•-*–—0123456789. ").strip()
        if len(cleaned) < 3:
            continue
        item = {
            "name": cleaned[:160],
            "issuer": "",
            "issue_date": "",
            "expiry_date": "",
            "credential_id": "",
            "credential_url": "",
        }
        years = re.findall(r"\b(?:19|20)\d{2}\b", cleaned)
        if years:
            item["issue_date"] = years[0]
        issuer_m = re.search(r"\b(?:by|from|issued by)\s+([A-Za-z0-9 .,&-]{2,60})", cleaned, re.I)
        if issuer_m:
            item["issuer"] = issuer_m.group(1).strip()
        profile["certifications"].append(item)
        add_evidence("certifications", item, 0.55, line)

    # ---- Achievements ----
    for line in sections.get("achievements", [])[:15]:
        cleaned = line.lstrip("•-*–—0123456789. ").strip()
        if len(cleaned) < 3:
            continue
        years = re.findall(r"\b(?:19|20)\d{2}\b", cleaned)
        item = {
            "title": cleaned[:160],
            "description": cleaned[:500],
            "date": years[0] if years else "",
        }
        profile["achievements"].append(item)
        add_evidence("achievements", item, 0.50, line)

    # ---- Languages ----
    for line in sections.get("languages", [])[:10]:
        parts = re.split(r"[,|•·;]", line)
        for part in parts:
            name = part.strip(" -•*")
            if not (2 <= len(name) <= 40):
                continue
            # Strip proficiency in parentheses
            prof = ""
            pm = re.search(r"\(([^)]+)\)", name)
            if pm:
                prof = pm.group(1).strip()
                name = name[: pm.start()].strip()
            if not name:
                continue
            item = {"name": name[:60], "proficiency": prof[:40]}
            profile["languages"].append(item)
            add_evidence("languages", item, 0.55, part)

    from ai_providers import normalize_profile

    canonical = normalize_profile({**profile, "evidence": []})["profile"]
    return canonical, evidence


_SKILL_CANONICAL = {
    "git": "Git & GitHub",
    "github": "Git & GitHub",
    "gitlab": "Git & GitHub",
    "js": "JavaScript (ES6+)",
    "javascript": "JavaScript (ES6+)",
    "es6": "JavaScript (ES6+)",
    "ecmascript6": "JavaScript (ES6+)",
    "react": "React.js",
    "reactjs": "React.js",
    "node": "Node.js",
    "nodejs": "Node.js",
    "typescript": "TypeScript",
    "ts": "TypeScript",
}

_TAXONOMY_SKILL_CATEGORIES = {
    "sql": "Database",
    "mongodb": "Database",
    "neo4j": "Database",
    "redis": "Database",
    "python": "Language",
    "javascript": "Language",
    "java": "Language",
    "cpp": "Language",
    "csharp": "Language",
    "html_css": "Frontend",
    "docker": "Tool",
    "kubernetes": "Tool",
    "aws": "Tool",
    "azure": "Tool",
    "gcp": "Tool",
    "git": "Tool",
    "excel": "Tool",
    "visualization": "Tool",
    "linux": "Tool",
}


def _skill_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _skill_aliases() -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    aliases = {}
    for taxonomy_id, entry in TAXONOMY.items():
        canonical = _SKILL_CANONICAL.get(taxonomy_id, entry["name"])
        category = _TAXONOMY_SKILL_CATEGORIES.get(taxonomy_id, "Other")
        for alias in [entry["name"], *entry.get("aliases", [])]:
            aliases[_skill_key(alias)] = (canonical, category)
    for alias, canonical in _SKILL_CANONICAL.items():
        category = "Tool" if canonical == "Git & GitHub" else "Language"
        if canonical == "React.js":
            category = "Frontend"
        elif canonical == "Node.js":
            category = "Backend"
        aliases[_skill_key(alias)] = (canonical, category)
        aliases[_skill_key(canonical)] = (canonical, category)
    return aliases, {canonical: category for canonical, category in aliases.values()}


def _profile_item_name(field: str, item: dict) -> str:
    return str(item.get({
        "education": "institution",
        "experience": "company",
        "projects": "name",
        "skills": "name",
        "certifications": "name",
        "achievements": "title",
        "languages": "name",
        "volunteer": "organization",
    }.get(field, "name"), "") or "").strip()


def _dedupe_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").casefold(), flags=re.UNICODE)


def _date_parts(value: str) -> tuple[Optional[int], Optional[int]]:
    value = (value or "").strip()
    month_names = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(19|20)\d{2}\b",
        value,
        re.I,
    )
    if match:
        year = int(re.search(r"(?:19|20)\d{2}", match.group(0)).group(0))
        month = month_names[match.group(1)[:3].lower()]
        return year, month
    year_match = re.search(r"\b((?:19|20)\d{2})\b", value)
    if year_match:
        return int(year_match.group(1)), None
    return None, None


def _experience_duration(item: dict) -> tuple[int, bool]:
    month = (
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+"
    )
    year_or_month = rf"(?:{month})?(?:19|20)\d{{2}}"
    text = " ".join(
        str(item.get(field) or "")
        for field in ("start_date", "end_date", "description")
    )
    date_range = re.search(
        rf"(?i)(?P<start>{year_or_month})\s*(?:[-–—]|\bto\b)\s*"
        rf"(?P<end>Present|Current|Now|{year_or_month})",
        text,
    )
    current = bool(item.get("current")) or bool(
        re.search(r"\b(present|current|now|ongoing|on going)\b",
                  " ".join(str(item.get(field) or "") for field in ("company", "end_date", "description")),
                  re.I)
    )
    start = item.get("start_date", "")
    end = item.get("end_date", "")
    if date_range:
        start = date_range.group("start").strip()
        end = date_range.group("end").strip()
        item["start_date"] = start[:20]
        item["end_date"] = end[:20]
        current = current or bool(re.search(r"\b(present|current|now)\b", end, re.I))
    if current and not end:
        end = "Present"
        item["end_date"] = end
    start_year, start_month = _date_parts(str(start))
    if current:
        end_year, end_month = date.today().year, date.today().month
    else:
        end_year, end_month = _date_parts(str(end))
    if start_year is None or end_year is None:
        return 0, current
    if start_month is None or end_month is None:
        months = (end_year - start_year) * 12 + 1
    else:
        months = (end_year - start_year) * 12 + end_month - start_month + 1
    return max(0, min(months, 600)), current


def clean_profile(profile: dict) -> dict:
    """Canonicalize CV fields, remove parser noise, and deduplicate sections."""
    from ai_providers import empty_profile, normalize_profile

    canonical = {**empty_profile(), **(profile or {})}
    canonical["projects"] = [
        {"name": item.get("name", ""), "url": item.get("url") or item.get("project_url", "")}
        for item in canonical.get("projects", [])
        if isinstance(item, dict)
    ]
    normalized_experience = []
    for source_item in canonical.get("experience", []):
        if not isinstance(source_item, dict):
            normalized_experience.append(source_item)
            continue
        item = dict(source_item)
        item["duration_months"], item["current"] = _experience_duration(item)
        normalized_experience.append(item)
    canonical["experience"] = normalized_experience
    cleaned = normalize_profile({**canonical, "evidence": []})["profile"]

    def normalized_name(value: str) -> str:
        value = re.sub(r"\s+", " ", (value or "")).strip()
        if (
            re.match(r"^[\s●•*\-−]", value)
            or "[page" in value.casefold()
            or "tech stack" in value.casefold()
            or value.casefold().strip(" :-") == "tech stack"
            or (field == "projects" and value.endswith((".", ":", ";")))
            or len(value) < 3
            or len(value) > 120
        ):
            return ""
        return value

    for field in (
        "education", "experience", "projects", "skills", "certifications",
        "achievements", "languages", "volunteer",
    ):
        results, seen = [], []
        for original in cleaned.get(field, []):
            item = dict(original)
            if field == "experience":
                company = item.get("company", "")
                stripped = re.sub(
                    r"\s*[-–—]\s*(?:on\s*going|ongoing|present|current)\s*$",
                    "",
                    company,
                    flags=re.I,
                ).strip()
                if stripped != company.strip():
                    item["company"] = stripped
                    item["current"] = True
                item["duration_months"], item["current"] = _experience_duration(item)
            name = normalized_name(_profile_item_name(field, item))
            if not name:
                continue
            if field == "education":
                institution = item.get("institution", "")
                if not re.search(
                    r"\b(university|college|institute|school|academy|polytechnic)\b",
                    institution,
                    re.I,
                ):
                    continue
            if field == "skills":
                aliases, canonical_categories = _skill_aliases()
                alias = aliases.get(_skill_key(name))
                if alias:
                    name, category = alias
                else:
                    name = name[:1].upper() + name[1:]
                    category = canonical_categories.get(name, "Other")
                item["name"] = name
                item["category"] = category if category in {
                    "Language", "Frontend", "Backend", "Database", "Tool", "Other",
                } else "Other"
                item["status"] = "claimed"
            elif field == "volunteer":
                item["organization"] = name
            elif field == "education":
                item["institution"] = name
            elif field == "experience":
                item["company"] = name
            elif field == "achievements":
                item["title"] = name
            else:
                item["name"] = name
            key = _dedupe_key(name)
            if any(key == other or key in other or other in key for other in seen):
                continue
            seen.append(key)
            if field == "skills" and len(results) >= 60:
                continue
            if field == "projects":
                item = {"name": item["name"], "url": _normalize_profile_url(item.get("url", ""))}
            results.append(item)
        cleaned[field] = results

    for field in ("linkedin", "github", "portfolio"):
        cleaned[field] = _normalize_profile_url(cleaned.get(field, ""))
    for item in cleaned["certifications"]:
        item["credential_url"] = _normalize_profile_url(item.get("credential_url", ""))
    return cleaned


def _merge_profiles(ai_profile: dict, deterministic: dict) -> dict:
    """Use AI lists when valid; fall back to offline lists only when empty."""
    from ai_providers import empty_profile

    ai = clean_profile(ai_profile or {})
    offline = clean_profile(deterministic or {})
    merged = empty_profile()
    for field in (
        "name", "email", "phone", "headline", "location", "summary",
        "linkedin", "github", "portfolio",
    ):
        merged[field] = offline.get(field) or ai.get(field) or ""
    for field in (
        "education", "experience", "projects", "skills", "certifications",
        "achievements", "languages", "volunteer",
    ):
        merged[field] = ai[field] if ai.get(field) else offline.get(field, [])
    merged["references"] = []
    preferences = ai.get("preferences") or {}
    offline_preferences = offline.get("preferences") or {}
    for key in ("job_types", "work_mode", "locations"):
        merged["preferences"][key] = preferences.get(key) or offline_preferences.get(key) or (
            [] if key != "work_mode" else ""
        )
    return clean_profile(merged)


def _assign_project_links(profile: dict, links: list[str]) -> dict:
    """Attach document links only when the project title matches their URL."""
    projects = profile.get("projects", [])
    for project in projects:
        project["url"] = ""
    for url in links:
        normalized = _normalize_profile_url(url)
        if not normalized:
            continue
        parsed = urlparse(normalized)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [part for part in parsed.path.split("/") if part]
        if host == "linkedin.com":
            if len(parts) >= 2 and parts[0].lower() == "in":
                profile["linkedin"] = normalized
            continue
        if host == "github.com" and len(parts) == 1:
            profile["github"] = normalized
            continue

        link_terms = {
            token for token in re.split(r"[^a-z0-9]+", host + " " + " ".join(parts))
            if len(token) > 2
        }
        best, best_score = None, 0
        for project in projects:
            project_terms = {
                token for token in re.split(r"[^a-z0-9]+", project.get("name", "").casefold())
                if len(token) > 2
            }
            score = len(link_terms & project_terms)
            if score > best_score:
                best, best_score = project, score
        if best and best_score:
            best["url"] = normalized
    return clean_profile(profile)


def profile_completeness(profile: dict) -> dict:
    """Measure filled profile sections only; this is not an employability score."""
    profile = profile or {}
    preferences = profile.get("preferences") or {}

    def scalar_pct(values: list) -> int:
        return round(100 * sum(bool(value) for value in values) / len(values)) if values else 0

    def items_pct(items: list, fields: tuple[str, ...]) -> int:
        if not items:
            return 0
        expected = len(items) * len(fields)
        filled = sum(
            bool(item.get(field))
            for item in items
            if isinstance(item, dict)
            for field in fields
        )
        return round(100 * filled / expected) if expected else 0

    sections = {
        "Personal Details": scalar_pct([
            profile.get("name"), profile.get("email"), profile.get("phone"),
            profile.get("location"),
        ]),
        "Career Snapshot": scalar_pct([profile.get("headline"), profile.get("summary")]),
        "Work Experience": items_pct(profile.get("experience", []), ("company", "title")),
        "Education": items_pct(profile.get("education", []), ("institution", "degree")),
        "Key Skills": 100 if profile.get("skills") else 0,
        "Availability & Job Preferences": scalar_pct([
            preferences.get("job_types"), preferences.get("work_mode"),
            preferences.get("locations"),
        ]),
        "Training & Certification": items_pct(
            profile.get("certifications", []), ("name",),
        ),
        "Projects": items_pct(profile.get("projects", []), ("name", "url")),
        "Language Skills": items_pct(profile.get("languages", []), ("name",)),
        "References": items_pct(profile.get("references", []), ("name", "relationship")),
        "Club & Volunteer Experience": max(
            items_pct(profile.get("achievements", []), ("title",)),
            items_pct(profile.get("volunteer", []), ("organization", "role")),
        ),
        "Portfolio & Social Links": scalar_pct([
            profile.get("linkedin"), profile.get("github"), profile.get("portfolio"),
        ]),
    }
    weights = {
        "Personal Details": 10,
        "Career Snapshot": 10,
        "Work Experience": 15,
        "Education": 10,
        "Key Skills": 15,
        "Availability & Job Preferences": 10,
        "Training & Certification": 5,
        "Projects": 15,
        "Language Skills": 3,
        "References": 0,
        "Club & Volunteer Experience": 3,
        "Portfolio & Social Links": 4,
    }
    optional_empty = {
        "Club & Volunteer Experience"
    } if not profile.get("achievements") and not profile.get("volunteer") else set()
    active_weight = sum(
        weight for name, weight in weights.items()
        if name not in optional_empty
    )
    total = round(
        sum(weights[name] * pct for name, pct in sections.items())
        / active_weight
    ) if active_weight else 0
    return {"total": total, "sections": sections}


def profile_review_summary(profile: dict, evidence: list[dict]) -> dict:
    """Return only review flags, not source snippets or the stored evidence list."""
    low_confidence = []
    for item in evidence:
        try:
            if float(item.get("confidence", 0)) < 0.6:
                field = str(item.get("field", "")).strip()
                if field and field not in low_confidence:
                    low_confidence.append(field)
        except (TypeError, ValueError):
            continue

    profile = profile or {}
    preferences = profile.get("preferences") or {}
    checks = [
        ("Personal Details", any(profile.get(key) for key in ("name", "email", "phone", "location"))),
        ("Career Snapshot", any(profile.get(key) for key in ("headline", "summary"))),
        ("Work Experience", bool(profile.get("experience"))),
        ("Education", bool(profile.get("education"))),
        ("Key Skills", bool(profile.get("skills"))),
        ("Availability & Job Preferences", any(
            preferences.get(key) for key in ("job_types", "work_mode", "locations")
        )),
        ("Training & Certification", bool(profile.get("certifications"))),
        ("Projects", bool(profile.get("projects"))),
        ("Language Skills", bool(profile.get("languages"))),
        ("References", bool(profile.get("references"))),
        ("Club & Volunteer Experience", bool(profile.get("achievements") or profile.get("volunteer"))),
        ("Portfolio & Social Links", any(profile.get(key) for key in ("linkedin", "github", "portfolio"))),
    ]
    return {
        "low_confidence_fields": low_confidence,
        "missing_fields": [name for name, found in checks if not found],
    }


def _cv_name_suggestion(raw_text: str, account_name: str = "") -> str:
    """Return a short, explicit name-like CV heading for candidate review."""
    non_name_headings = {
        "cv", "resume", "curriculum vitae", "profile", "professional profile",
        "summary", "contact", "skills", "education", "experience", "projects",
        "certifications", "achievements", "languages", "work experience",
    }
    for line in (raw_text or "").splitlines()[:8]:
        candidate = re.sub(r"\s+", " ", line).strip(" \t|,;")
        if (
            1 <= len(candidate.split()) <= 5
            and len(candidate) <= 80
            and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", candidate)
            and candidate.casefold() not in non_name_headings
            and not re.search(
                r"\b(engineer|developer|analyst|scientist|intern|designer|manager|"
                r"consultant|architect|specialist|officer|objective|experience)\b",
                candidate,
                re.I,
            )
            and candidate.casefold() != account_name.strip().casefold()
        ):
            return candidate
    return ""


def _profile_value_for_evidence(profile: dict, field: str):
    value = profile
    for token in re.findall(r"[A-Za-z_]+|\d+", field.replace(".", ".")):
        if isinstance(value, dict):
            value = value.get(token)
        elif isinstance(value, list) and token.isdigit():
            index = int(token)
            value = value[index] if index < len(value) else None
        else:
            return None
        if value is None:
            return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value) if value not in (None, "") else None


@app.post("/api/cv/profile-extract")
async def upload_cv_profile(
    file: UploadFile = File(...),
    u=Depends(current_user),
):
    need_consent(u)

    role = one(
        "SELECT role FROM user_roles WHERE user_id=?",
        (u["id"],),
    )

    if role and role["role"] != "candidate":
        raise HTTPException(403, "Only candidates can create candidate profiles")

    # A confirmed profile must be updated through the normal profile endpoint.
    existing = one(
        "SELECT user_id FROM candidate_profiles WHERE user_id=?",
        (u["id"],),
    )

    if existing:
        raise HTTPException(
            409,
            "Your profile is already confirmed. Update it from My Profile.",
        )

    filename = file.filename or "cv"

    # Parse first. Do not store files that fail basic validation/parsing.
    file_bytes = await file.read(5_000_001)
    if len(file_bytes) > 5_000_000:
        raise HTTPException(413, "CV file must be 5 MB or smaller")

    raw_text, parser_info = read_profile_cv(filename, file_bytes)

    identity = {
        "name": u.get("name", "") or "",
        "email": u.get("email", "") or "",
    }
    deterministic_profile, deterministic_evidence = extract_profile_fields(
        raw_text,
        identity=identity,
    )
    suggested_name = _cv_name_suggestion(raw_text, identity["name"])
    if suggested_name:
        deterministic_evidence.append({
            "field": "name_suggestion",
            "value": suggested_name,
            "source_snippet": suggested_name,
            "source": "uploaded_cv",
            "confidence": 0.6,
        })

    # Keep extraction available offline when the deterministic parser finds
    # meaningful, evidence-backed fields; otherwise return an explicit failure.
    extraction_result = None
    ai_error = None
    links = parser_info.get("links_found", [])
    cv_body = raw_text.split("\n\nLINKS FOUND IN DOCUMENT:", 1)[0]
    redacted_text, _ = E.redact(cv_body, known_names=[identity["name"]])
    if links:
        redacted_text = _append_document_links(redacted_text, links)
    from ai_providers import AIExtractionError, extract_cv_profile

    try:
        extraction_result = extract_cv_profile(
            redacted_text,
            identity=identity,
        )
    except AIExtractionError as ex:
        ai_error = str(ex)
        log.warning("CV AI extraction unavailable, using offline parser: %s", ai_error)

    if extraction_result:
        profile = _merge_profiles(
            extraction_result["profile"],
            deterministic_profile,
        )
        evidence = []
        for item in extraction_result.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            value = _profile_value_for_evidence(profile, field)
            if value:
                evidence.append({
                    **item,
                    "value": value,
                    "confidence": float(item.get("confidence", 0.85)),
                    "source": item.get("source", "ai_extraction"),
                })
        ai_collections = {
            "education", "experience", "projects", "skills",
            "certifications", "achievements", "languages",
        }
        for item in deterministic_evidence:
            field = str(item.get("field", ""))
            collection = field.split(".", 1)[0]
            if collection not in ai_collections or not extraction_result["profile"].get(collection):
                if field in {"phone", "email", "linkedin", "github", "portfolio", "name"} or collection not in ai_collections:
                    evidence.append(item)
        method = "cv_ai"
        ai_provider = extraction_result.get("provider", "")
        ai_model = extraction_result.get("model", "")
        message = (
            "CV processed with AI. Please review and correct your profile before confirming."
        )
    else:
        profile = clean_profile(deterministic_profile)
        evidence = deterministic_evidence
        method = "cv_offline"
        ai_provider = ""
        ai_model = ""
        message = (
            "AI providers were unavailable, so your profile was built with the offline parser. "
            "Please review carefully and fill any missing fields before confirming."
        )

    profile["name"] = suggested_name or profile.get("name") or identity["name"]
    profile["email"] = deterministic_profile.get("email") or identity["email"]
    for field in ("linkedin", "github", "portfolio"):
        profile[field] = deterministic_profile.get(field, "")
    profile = _assign_project_links(profile, links)
    profile["email"] = deterministic_profile.get("email") or identity["email"]
    profile = clean_profile(profile)

    meaningful_fields = (
        "phone", "headline", "location", "summary", "linkedin", "github",
        "portfolio", "education", "experience", "projects", "skills",
        "certifications", "achievements", "languages",
    )
    if not any(profile.get(field) for field in meaningful_fields):
        detail = (
            "The CV text was read, but no supported profile information could be extracted. "
            "Please try another CV or create your profile manually."
        )
        if ai_error:
            detail += f" AI extraction status: {ai_error}"
        raise HTTPException(422, detail)

    import cv_store
    from storage import StorageError

    try:
        document = cv_store.save_document(
            u["id"],
            filename,
            file_bytes,
        )

        cv_store.add_extractions(
            u["id"],
            document["id"],
            evidence,
        )
        stored_evidence = cv_store.list_extractions(
            u["id"], document["id"]
        )

    except StorageError as ex:
        log.error("CV storage failed: %s", type(ex).__name__)
        raise HTTPException(
            503,
            "Could not store the CV. Please try again.",
        )

    now = time.time()

    # Store the extracted profile as a private draft.
    # This does NOT create a candidate_profiles row.
    run(
        """
        INSERT INTO candidate_profile_drafts(
            user_id,
            profile_json,
            status,
            method,
            document_id,
            created,
            updated
        )
        VALUES (?, ?, 'draft', ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            profile_json=excluded.profile_json,
            status='draft',
            method=excluded.method,
            document_id=excluded.document_id,
            updated=excluded.updated
        """,
        (
            u["id"],
            json.dumps(profile, ensure_ascii=False),
            method,
            document["id"],
            now,
            now,
        ),
    )

    audit(
        u["id"],
        "candidate_profile_cv_extracted",
        document["id"],
    )

    return {
        "ok": True,
        "status": "draft",
        "method": method,
        "profile": profile,
        "document": {
            "id": document["id"],
            "filename": document["filename"],
            "size_bytes": document["size_bytes"],
            "status": document["status"],
        },
        "extraction": {
            "parser": parser_info["parser"],
            "ocr_used": parser_info["ocr_used"],
            "ocr_pages": parser_info["ocr_pages"],
            "ai_provider": ai_provider,
            "ai_model": ai_model,
            "ai_error": ai_error,
            "fields_found": len(stored_evidence),
            **profile_review_summary(profile, stored_evidence),
        },
        "completeness": profile_completeness(profile),
        "message": message,
    }


@app.post("/api/cv")
async def upload_cv(text: str = Form(""), file: Optional[UploadFile] = File(None), u=Depends(current_user)):
    need_consent(u)
    raw, file_bytes = text, None
    if file is not None:
        file_bytes = await file.read()
        raw = read_upload(file.filename or "", file_bytes)
    raw = raw.strip()
    if len(raw) < 30: raise HTTPException(422, "CV text is too short")
    document = None
    if file_bytes is not None:
        # Keep the original file (local disk now, S3-compatible bucket later - see storage.py).
        import cv_store
        from storage import StorageError
        try:
            document = cv_store.save_document(u["id"], file.filename or "cv", file_bytes)
        except StorageError as ex:
            log.error("CV storage failed: %s", ex)
            raise HTTPException(503, "Could not store the CV file, please try again")
    redacted, counts = E.redact(raw[:30000], [u["name"]])
    skills, source = (llm_extract(redacted) if ANTHROPIC_API_KEY else None), "llm"
    if not skills: skills, source = E.rule_extract(redacted), "rules"
    run("INSERT INTO profiles(user_id,redacted,redaction,extracted,source,confirmed) VALUES (?,?,?,?,?,NULL) "
        "ON CONFLICT(user_id) DO UPDATE SET redacted=excluded.redacted,redaction=excluded.redaction,extracted=excluded.extracted,source=excluded.source,confirmed=NULL",
        (u["id"], redacted, json.dumps(counts), json.dumps(skills), source))
    audit(u["id"], "cv_parsed", source)
    return {"redacted_preview": redacted[:1500], "redaction": counts, "skills": skills, "source": source,
            "document": {k: document[k] for k in ("id", "filename", "size_bytes", "status")} if document else None,
            "taxonomy": [{"id": k, "name": v["name"]} for k, v in TAXONOMY.items()]}


class Confirm(BaseModel):
    skills: List[str]


@app.put("/api/skills")
def confirm_skills(b: Confirm, u=Depends(current_user)):
    need_consent(u)
    ids = [s for s in dict.fromkeys(b.skills) if s in TAXONOMY]
    run("INSERT INTO profiles(user_id,confirmed) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET confirmed=excluded.confirmed", (u["id"], json.dumps(ids)))
    return {"confirmed": ids}

# ------------------------------------------------------------------ GraphRAG assessment blueprint
@app.post("/api/assessment/blueprint")
def generate_assessment_blueprint(u=Depends(current_user)):
    """Retrieve an auditable question set from the approved bank; no LLM question fabrication."""
    need_consent(u)
    prof = one("SELECT redacted,confirmed FROM profiles WHERE user_id=?", (u["id"],))
    if not prof or not prof["redacted"]:
        raise HTTPException(409, "Upload and analyse your CV before generating a CV-based assessment")
    confirmed = set(json.loads(prof["confirmed"] or "[]")) if prof["confirmed"] else set()
    if not confirmed:
        raise HTTPException(409, "Confirm your CV skills before generating the assessment")
    from graphrag import CVSkill, build_assessment, extract_cv_skills
    extracted = extract_cv_skills(prof["redacted"])
    skills = [s for s in extracted if s.skill_id in confirmed]
    if not skills:
        raise HTTPException(422, "No confirmed technical skills were found in the CV evidence")
    import enterprise as admin_data
    admin_data._seed_question_bank()
    rows = allr("SELECT * FROM question_bank WHERE status='active' ORDER BY created_at DESC")
    result = build_assessment(rows, skills, retrieval=True)
    blueprint_id = uuid.uuid4().hex
    snapshot = {**result, "user_id": u["id"], "blueprint_id": blueprint_id}
    run("INSERT INTO assessment_blueprints(id,user_id,status,payload,created) VALUES (?,?,?,?,?)",
        (blueprint_id, u["id"], result["status"], json.dumps(snapshot), time.time()))
    audit(u["id"], "assessment_blueprint_generated",
          f"{blueprint_id};questions={result['total_questions']};status={result['status']};ms={result['retrieval_ms']}")
    return {
        "blueprint_id": blueprint_id, "status": result["status"],
        "fundamental_count": result["fundamental_count"], "cv_count": result["cv_count"],
        "total_questions": result["total_questions"], "skills": result["skills"],
        "estimated_duration_seconds": result["estimated_duration_seconds"],
        "estimated_duration_minutes": result["estimated_duration_minutes"],
        "difficulty_targets": result["difficulty_targets"],
        "fundamental_targets": result["fundamental_targets"],
        "fundamental_difficulty_matrix": result["fundamental_difficulty_matrix"],
        "per_skill_targets": result["per_skill_targets"], "shortages": result["shortages"],
        "retrieval_ms": result["retrieval_ms"], "retrieval_providers": result["retrieval_providers"],
        "message": "Assessment question bank is ready." if result["status"] == "ready"
            else "The approved question bank does not yet contain enough questions for every required quota. No questions were fabricated."
    }


@app.get("/api/admin/assessment/graphrag/status")
def admin_graphrag_status(u=Depends(current_user)):
    import enterprise as admin_data
    admin_data.admin_only(u, "questions")
    import graphrag as G
    rows = allr("SELECT category_id,difficulty_level,COUNT(*) AS n FROM question_bank WHERE status='active' GROUP BY category_id,difficulty_level")
    active_rows = allr("SELECT * FROM question_bank WHERE status='active'")
    fundamental_check = G.build_assessment(active_rows, [], retrieval=False)
    return {
        "active_question_count": one("SELECT COUNT(*) AS n FROM question_bank WHERE status='active'")["n"],
        "question_distribution": [dict(r) for r in rows],
        "fundamental_readiness": fundamental_check["status"],
        "fundamental_selected_count": fundamental_check["fundamental_count"],
        "fundamental_shortages": fundamental_check["shortages"],
        "fundamental_targets": G.FUNDAMENTAL_TARGETS,
        "fundamental_difficulty_matrix": G.FUNDAMENTAL_DIFFICULTY_MATRIX,
        "cv_questions_per_skill": G.PER_SKILL_TARGETS,
        "meilisearch_configured": bool(os.getenv("MEILISEARCH_URL")),
        "neo4j_configured": bool(os.getenv("NEO4J_URI")),
        "note": "Only active, curator-approved question-bank records are eligible for retrieval."
    }


@app.post("/api/admin/assessment/graphrag/reindex")
def admin_graphrag_reindex(u=Depends(current_user)):
    import enterprise as admin_data
    admin_data.admin_only(u, "questions")
    import graphrag as G
    admin_data._seed_question_bank()
    rows = allr("SELECT * FROM question_bank WHERE status='active'")
    result = G.index_question_bank(rows)
    audit(u["id"], "graphrag_question_bank_reindex",
          f"active={result['active_questions']};meili={result['meilisearch']};neo4j={result['neo4j']}")
    return result


@app.get("/api/assessment/blueprint/{blueprint_id}")
def get_assessment_blueprint(blueprint_id: str, u=Depends(current_user)):
    row = one("SELECT status,payload,created FROM assessment_blueprints WHERE id=? AND user_id=?",
              (blueprint_id, u["id"]))
    if not row:
        raise HTTPException(404, "Assessment blueprint not found")
    payload = json.loads(row["payload"])
    return {"blueprint_id": blueprint_id, "status": row["status"],
            "total_questions": payload["total_questions"],
            "fundamental_count": payload["fundamental_count"], "cv_count": payload["cv_count"],
            "estimated_duration_seconds": payload["estimated_duration_seconds"],
            "retrieval_ms": payload["retrieval_ms"], "shortages": payload["shortages"],
            "created": row["created"]}




# ------------------------------------------------------------------ job + gap
def topic_scores(uid):
    a = latest_done(uid)
    return json.loads(a["result"])["topics"] if a else {}


@app.get("/api/job")
def job(u=Depends(current_user)):
    prof = one("SELECT confirmed FROM profiles WHERE user_id=?", (u["id"],))
    claimed = set(json.loads(prof["confirmed"])) if prof and prof["confirmed"] else set()
    rows = E.gap_map(topic_scores(u["id"]))
    for r in rows: r["claimed"] = r["skill"] in claimed
    return {"job": {"id": JOB["id"], "title": JOB["title"], "company": JOB["company"], "mock": True,
                    "preferred": [{"skill": s, "name": TAXONOMY[s]["name"], "claimed": s in claimed} for s in JOB["preferred"]]}, "gap": rows}


# ------------------------------------------------------------------ assessment
def seen_before(uid):
    s = set()
    for a in allr("SELECT state FROM attempts WHERE user_id=? AND status='done'", (uid,)): s |= set(json.loads(a["state"])["used"])
    return s


def view(att):
    st = json.loads(att["state"])
    if st.get("graphrag"):
        import graphrag as G
        questions = st["selected_questions"]
        duration = int(st["duration_seconds"])
        remaining = max(0, int(att["started"] + duration - time.time()))
        index = int(st.get("question_index", 0))
        current = questions[index] if questions else None
        page = G.question_page(questions, index // G.QUESTION_PAGE_SIZE, st.get("answers", {}))
        public = None
        if current:
            public = {k: current.get(k) for k in ("id", "assessment_section", "assessment_topic",
                "skill_id", "question_type", "difficulty_level", "marks", "time_allocation")}
            public["prompt"] = current.get("question_text", "")
            public["options"] = current.get("options", [])
            if "sql" in str(current.get("question_type", "")).lower():
                public["schema"] = E.SQL_SCHEMA
            public["index"] = index
            public["total"] = len(questions)
        return {"attempt_id": att["id"], "kind": att["kind"], "status": att["status"],
            "remaining": remaining, "answered": len(st.get("answers", {})), "total": len(questions),
            "graphrag": True, "blueprint_id": st["blueprint_id"], "question": public,
            "page": page, "question_index": index, "duration_seconds": duration,
            "manual_review_count": sum(1 for q in questions if not (
                str(q.get("question_type", "")).lower() in ("mcq", "multiple choice") or
                ("sql" in str(q.get("question_type", "")).lower() and bool(str(q.get("reference_answer", "")).strip()))))}
    remaining = max(0, int(att["started"] + E.TEST_SECONDS - time.time()))
    out = {"attempt_id": att["id"], "kind": att["kind"], "status": att["status"], "remaining": remaining, "answered": len(st["answers"]), "total": len(E.SLOTS)}
    if att["status"] == "active" and st["current"]:
        out["question"] = E.public_question(QBYID[st["current"]], st)
    return out


def save(att_id, st, **kw):
    sets, args = ["state=?"], [json.dumps(st)]
    for k, v in kw.items(): sets.append(f"{k}=?"); args.append(v)
    run(f"UPDATE attempts SET {', '.join(sets)} WHERE id=?", (*args, att_id))


def finalize(att, st):
    event_rows = allr("SELECT type, ts FROM events WHERE attempt_id=? ORDER BY ts, id", (att["id"],))
    ev = [{"type": r["type"], "server_ts": r["ts"]} for r in event_rows]
    exam_seconds = int(st.get("duration_seconds") or E.TEST_SECONDS)
    if st.get("graphrag"):
        now = time.time()
        current_id = st.get("current")
        if current_id:
            elapsed = max(0, now - float(st.get("question_viewed_at") or now))
            times = st.setdefault("question_time_seconds", {})
            times[current_id] = round(float(times.get(current_id, 0)) + elapsed, 1)
        answers = st.get("answers", {})
        topics = {}
        review_rows, trust_answers = [], []
        for q in st["selected_questions"]:
            raw_answer = answers.get(q["id"], "")
            qtype = str(q.get("question_type", "")).lower()
            is_mcq = qtype in ("mcq", "multiple choice")
            is_sql_auto = "sql" in qtype and bool(str(q.get("reference_answer", "")).strip())
            objective = is_mcq or is_sql_auto
            if is_mcq:
                correct = (str(raw_answer).strip().casefold() == str(q.get("correct_answer", "")).strip().casefold()) if raw_answer != "" else False
            elif is_sql_auto:
                correct = E.grade_sql(st["seed"], {"ref": q["reference_answer"]}, str(raw_answer or ""))
            else:
                correct = None
            topic = q.get("assessment_topic") or "General"
            metric = topics.setdefault(topic, {"earned": 0, "possible": 0, "n": 0})
            if objective:
                metric["possible"] += int(q.get("marks") or 1)
                metric["n"] += 1
                if correct: metric["earned"] += int(q.get("marks") or 1)
            review_rows.append({"question_id": q["id"], "topic": topic, "question": q["question_text"],
                "question_type": q.get("question_type"), "answer": raw_answer,
                "correct_answer": (q.get("correct_answer") if is_mcq else q.get("reference_answer")) if objective else None,
                "explanation": q.get("explanation") if objective else None,
                "review_status": ("correct" if correct else "incorrect") if objective else "pending_human_review",
                "time_seconds": st.get("question_time_seconds", {}).get(q["id"], 0)})
            trust_answers.append({"qid": q["id"], "type": "mcq" if is_mcq else ("sql" if is_sql_auto else "subjective"), "correct": bool(correct),
                "difficulty": {"Easy": 1, "Medium": 2, "Hard": 3}.get(q.get("difficulty_level"), 2),
                "seconds": st.get("question_time_seconds", {}).get(q["id"], 0)})
        topic_scores = {}
        for topic, metric in topics.items():
            pct = round(100 * metric["earned"] / metric["possible"]) if metric["possible"] else None
            topic_scores[topic] = {"score": pct, "level": E.level_of(pct or 0), "n": metric["n"]}
        possible = sum(x["possible"] for x in topics.values())
        earned = sum(x["earned"] for x in topics.values())
        overall = round(100 * earned / possible) if possible else None
        integrity = E.trust_label(ev, trust_answers, exam_seconds=exam_seconds)
        manual = any(x["review_status"] == "pending_human_review" for x in review_rows)
        res = {"overall": overall, "level": E.level_of(overall or 0), "topics": topic_scores,
            "trust": integrity["label"], "trust_points": integrity["points"],
            "trust_rule_version": integrity["rule_version"], "trust_reasons": integrity["reasons"],
            "trust_hard_flags": integrity["hard_flags"], "percentile": None,
            "kind": att["kind"], "date": time.strftime("%Y-%m-%d"),
            "review_status": "pending_human_review" if manual else "auto_graded",
            "manual_review_required": manual, "question_results": review_rows,
            "blueprint_id": st["blueprint_id"], "total_questions": len(st["selected_questions"]),
            "answered_questions": len(answers)}
    else:
        res = E.score(st["answers"])
        integrity = E.trust_label(ev, st["answers"], exam_seconds=exam_seconds)
        res.update({"trust": integrity["label"], "trust_points": integrity["points"],
            "trust_rule_version": integrity["rule_version"], "trust_reasons": integrity["reasons"],
            "trust_hard_flags": integrity["hard_flags"], "percentile": None,
            "kind": att["kind"], "date": time.strftime("%Y-%m-%d")})
    run("UPDATE attempts SET status='done', finished=?, result=?, state=? WHERE id=?", (time.time(), json.dumps(res), json.dumps(st), att["id"]))
    audit(att["user_id"], "attempt_done", att["id"])


def get_att(att_id, uid):
    a = one("SELECT * FROM attempts WHERE id=? AND user_id=?", (att_id, uid))
    if not a: raise HTTPException(404, "Attempt not found")
    return dict(a)


def require_screen_share(att_id):
    latest = one(
        "SELECT type FROM events WHERE attempt_id=? AND type IN ('screen_share_started','screen_share_stopped','screen_share_unavailable') ORDER BY ts DESC, id DESC LIMIT 1",
        (att_id,),
    )
    if not latest or latest["type"] != "screen_share_started":
        raise HTTPException(409, "screen_share_required")


class StartAttempt(BaseModel):
    blueprint_id: Optional[str] = None


@app.post("/api/attempts")
def start_attempt(b: Optional[StartAttempt] = None, u=Depends(current_user)):
    need_consent(u)
    act = one("SELECT * FROM attempts WHERE user_id=? AND status='active'", (u["id"],))
    if act:
        if time.time() > act["started"] + E.TEST_SECONDS + E.GRACE:
            finalize(dict(act), json.loads(act["state"]))
        else:
            return view(dict(act))                       # resume after a dropped connection

    seed = secrets.randbelow(10**9)
    if b and b.blueprint_id:
        blueprint = one("SELECT status,payload FROM assessment_blueprints WHERE id=? AND user_id=?",
                        (b.blueprint_id, u["id"]))
        if not blueprint:
            raise HTTPException(404, "Assessment blueprint not found")
        if blueprint["status"] != "ready":
            raise HTTPException(409, "The approved question bank is incomplete for this assessment")
        payload = json.loads(blueprint["payload"])
        selected = payload.get("questions") or []
        if not selected or len(selected) != payload.get("total_questions"):
            raise HTTPException(409, "Assessment blueprint validation failed")
        st = {"graphrag": True, "blueprint_id": b.blueprint_id, "selected_questions": selected,
              "question_index": 0, "answers": {}, "duration_seconds": int(payload["estimated_duration_seconds"]),
              "served_at": time.time(), "question_viewed_at": time.time(), "question_time_seconds": {}, "seed": seed}
        st["current"] = selected[0]["id"]
    else:
        st = E.new_state(seed)
        q = E.pick_question(st, seen_before(u["id"]))
        st["current"], st["served_at"] = q["id"], time.time()
    aid = uuid.uuid4().hex[:12]
    now = time.time()
    # Serialize attempt creation per candidate. This prevents double-clicks or
    # parallel requests from opening two active exams or spending one credit twice.
    with db() as conn:
        if DATABASE_URL:
            conn.execute("SELECT id FROM users WHERE id=? FOR UPDATE", (u["id"],)).fetchone()
        else:
            conn.execute("BEGIN IMMEDIATE")
        active_now = conn.execute("SELECT * FROM attempts WHERE user_id=? AND status='active'", (u["id"],)).fetchone()
        if active_now:
            raise HTTPException(409, "active_attempt_exists")
        done = conn.execute("SELECT finished FROM attempts WHERE user_id=? AND status='done' ORDER BY finished DESC", (u["id"],)).fetchall()
        recent = [d for d in done if d["finished"] and d["finished"] > now - 90 * 86400]
        kind = "post" if done else "pre"
        policy = conn.execute("SELECT cooldown_hours,max_attempts_90d,credits_per_attempt FROM learning_retake_policy WHERE id=1").fetchone()
        cooldown_hours = int(policy["cooldown_hours"]) if policy else int(COOLDOWN_HOURS)
        max_attempts = int(policy["max_attempts_90d"]) if policy else int(MAX_ATTEMPTS_90D)
        credit_cost = int(policy["credits_per_attempt"]) if policy else 1
        if len(recent) >= max_attempts:
            raise HTTPException(429, "attempt_cap")
        if done and cooldown_hours > 0 and now < done[0]["finished"] + cooldown_hours * 3600:
            raise HTTPException(429, "cooldown")
        if kind == "post":
            balance_row = conn.execute("SELECT COALESCE(SUM(delta),0) AS balance FROM billing_credit_ledger WHERE user_id=?", (u["id"],)).fetchone()
            balance = int(balance_row["balance"] or 0)
            if balance < credit_cost:
                raise HTTPException(402, "retake_credit_required")
        conn.execute("INSERT INTO attempts(id,user_id,kind,seed,status,state,started) VALUES (?,?,?,?,?,?,?)",
                     (aid, u["id"], kind, seed, "active", json.dumps(st), now))
        if kind == "post":
            conn.execute("INSERT INTO billing_credit_ledger(id,user_id,delta,entry_type,source_ref,reason,actor_id,created) VALUES (?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, u["id"], -credit_cost, "retake_consumption", aid,
                          "Retake credit consumed for official assessment attempt", u["id"], now))
    audit(u["id"], "attempt_start", aid)
    return view(get_att(aid, u["id"]))

@app.get("/api/attempts/{aid}")
def get_attempt(aid: str, u=Depends(current_user)): return view(get_att(aid, u["id"]))


class Ans(BaseModel):
    question_id: str
    answer: Optional[str] = None


@app.post("/api/attempts/{aid}/answer")
def answer(aid: str, b: Ans, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "active": raise HTTPException(409, "Attempt already finished")
    require_screen_share(aid)
    st = json.loads(att["state"])
    if time.time() > att["started"] + E.TEST_SECONDS + E.GRACE:      # server owns the timer
        finalize(att, st); return view(get_att(aid, u["id"]))
    if st.get("graphrag"):
        question = next((q for q in st["selected_questions"] if q["id"] == b.question_id), None)
        if not question:
            raise HTTPException(404, "Question is not part of this assessment")
        if b.question_id != st.get("current"):
            raise HTTPException(409, "Navigate to this question before saving its answer")
        answer_value = str(b.answer or "")
        revision = one("SELECT COUNT(*) AS n FROM assessment_answer_events WHERE attempt_id=? AND question_id=?",
                       (aid, b.question_id))["n"] + 1
        run("INSERT INTO assessment_answer_events(id,attempt_id,question_id,revision,answer_hash,answer_length,created) VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, aid, b.question_id, revision,
             hashlib.sha256(answer_value.encode("utf-8")).hexdigest(), len(answer_value), time.time()))
        st.setdefault("answers", {})[b.question_id] = answer_value
        st["current"] = b.question_id
        save(aid, st)
        return view(get_att(aid, u["id"]))
    if b.question_id != st["current"]: raise HTTPException(409, "Question mismatch")
    q = QBYID[st["current"]]
    E.record_answer(st, q, b.answer, time.time() - (st["served_at"] or time.time()))
    if st["index"] >= len(E.SLOTS):
        finalize(att, st)
    else:
        nq = E.pick_question(st, seen_before(u["id"]))
        st["current"], st["served_at"] = nq["id"], time.time()
        save(aid, st)
    return view(get_att(aid, u["id"]))                      # never reveals correctness during the exam


class NavigateQuestion(BaseModel):
    index: int = Field(ge=0)


@app.post("/api/attempts/{aid}/navigate")
def navigate_question(aid: str, b: NavigateQuestion, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    st = json.loads(att["state"])
    if att["status"] != "active" or not st.get("graphrag"):
        raise HTTPException(409, "Question navigation is unavailable")
    require_screen_share(aid)
    if time.time() > att["started"] + int(st["duration_seconds"]) + E.GRACE:
        finalize(att, st)
        return view(get_att(aid, u["id"]))
    if b.index >= len(st["selected_questions"]):
        raise HTTPException(422, "Question index is outside this assessment")
    now = time.time()
    previous_id = st.get("current")
    if previous_id:
        elapsed = max(0, now - float(st.get("question_viewed_at") or now))
        times = st.setdefault("question_time_seconds", {})
        times[previous_id] = round(float(times.get(previous_id, 0)) + elapsed, 1)
    st["question_index"] = b.index
    st["current"] = st["selected_questions"][b.index]["id"]
    st["question_viewed_at"] = now
    run("INSERT INTO events(attempt_id,type,ts) VALUES (?,?,?)", (aid, "question_navigate", now))
    save(aid, st)
    return view(get_att(aid, u["id"]))


@app.post("/api/attempts/{aid}/submit")
def submit_graphrag_attempt(aid: str, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    st = json.loads(att["state"])
    if att["status"] != "active" or not st.get("graphrag"):
        raise HTTPException(409, "This is not an active GraphRAG assessment")
    require_screen_share(aid)
    if time.time() > att["started"] + int(st["duration_seconds"]) + E.GRACE:
        finalize(att, st)
        return view(get_att(aid, u["id"]))
    unanswered = len(st["selected_questions"]) - len(st.get("answers", {}))
    if unanswered:
        raise HTTPException(409, {"message": "Please answer all questions before submitting.", "unanswered": unanswered})
    finalize(att, st)
    return view(get_att(aid, u["id"]))


class SqlRun(BaseModel):
    sql: str


@app.post("/api/attempts/{aid}/sql-run")
def sql_run(aid: str, b: SqlRun, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    st = json.loads(att["state"])
    require_screen_share(aid)
    if st.get("graphrag"):
        q = next((x for x in st["selected_questions"] if x["id"] == st.get("current")), None)
        if att["status"] != "active" or not q or "sql" not in str(q.get("question_type", "")).lower():
            raise HTTPException(409, "No SQL question active")
        return E.run_query(att["seed"], b.sql)
    if att["status"] != "active" or not st["current"] or QBYID[st["current"]]["type"] != "sql": raise HTTPException(409, "No SQL question active")
    return E.run_query(att["seed"], b.sql)                  # visible dataset only; scoring uses hidden datasets


class Ev(BaseModel):
    type: str = Field(min_length=1, max_length=40)


@app.post("/api/attempts/{aid}/event")
def event(aid: str, b: Ev, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    allowed = {
        "copy", "cut", "paste", "blur", "tab_hidden", "tab_visible", "fullscreen_exit",
        "screen_share_started", "screen_share_stopped", "screen_share_unavailable",
    }
    if att["status"] == "active" and b.type in allowed:
        run("INSERT INTO events(attempt_id,type,ts) VALUES (?,?,?)", (aid, b.type, time.time()))
    return {"ok": True}


@app.get("/api/attempts/{aid}/result")
def result(aid: str, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "done": raise HTTPException(409, "Attempt not finished")
    r = json.loads(att["result"]); r["appeal"] = att["appeal"]; r["attempt_id"] = aid
    return r


class Appeal(BaseModel):
    note: str = Field(min_length=3, max_length=1000)


@app.post("/api/attempts/{aid}/appeal")
def appeal(aid: str, b: Appeal, u=Depends(current_user)):
    get_att(aid, u["id"])
    run("UPDATE attempts SET appeal=? WHERE id=?", (b.note, aid))     # queued for human review; never changes score automatically
    audit(u["id"], "appeal", aid)
    return {"ok": True}


# ------------------------------------------------------------------ learning path + uplift
@app.get("/api/learning-path")
def learning_path(u=Depends(current_user)):
    a = latest_done(u["id"])
    if not a: raise HTTPException(409, "Take the assessment first")
    return {"based_on": a["id"], **E.build_path(json.loads(a["result"])["topics"])}


@app.get("/api/uplift")
def uplift(u=Depends(current_user)):
    pre = one("SELECT result FROM attempts WHERE user_id=? AND status='done' AND kind='pre' ORDER BY finished LIMIT 1", (u["id"],))
    post = latest_done(u["id"], "post")
    if not pre or not post: return {"available": False}
    a, b = json.loads(pre["result"]), json.loads(post["result"])
    topics = {t: {"pre": a["topics"][t]["score"], "post": b["topics"][t]["score"], "delta": b["topics"][t]["score"] - a["topics"][t]["score"]} for t in a["topics"] if t in b["topics"]}
    return {"available": True, "overall": {"pre": a["overall"], "post": b["overall"], "delta": b["overall"] - a["overall"]}, "topics": topics,
            "note": "Single-user result from a prototype question pool; not a validated learning-effect estimate."}


# ------------------------------------------------------------------ Skill Passport
class PassBody(BaseModel):
    topics: List[str]
    show_name: bool = False


@app.post("/api/passport")
def make_passport(b: PassBody, u=Depends(current_user)):
    need_consent(u)
    a = latest_done(u["id"])
    if not a: raise HTTPException(409, "Take the assessment first")
    r = json.loads(a["result"])
    skills = [{"skill": t, "name": TAXONOMY[t]["name"], "score": r["topics"][t]["score"], "level": r["topics"][t]["level"], "trust": r["trust"], "date": r["date"]}
              for t in b.topics if t in r["topics"]]
    if not skills: raise HTTPException(422, "Choose at least one verified skill")
    up = uplift(u)
    pid = secrets.token_urlsafe(9)
    payload = {"pid": pid, "issued": time.strftime("%Y-%m-%d"), "expires": time.strftime("%Y-%m-%d", time.localtime(time.time() + 365 * 86400)),
               "holder": u["name"] if b.show_name else "Anonymous candidate", "skills": skills,
               "uplift": up["overall"] if up["available"] else None, "issuer": "SkillBridge Global R0 prototype"}
    run("INSERT INTO passports(id,user_id,payload,signature,created) VALUES (?,?,?,?,?)", (pid, u["id"], json.dumps(payload), E.sign_payload(payload, SECRET), time.time()))
    audit(u["id"], "passport_create", pid)
    return {"id": pid}


@app.delete("/api/passport/{pid}")
def revoke_passport(pid: str, u=Depends(current_user)):
    p = one("SELECT 1 FROM passports WHERE id=? AND user_id=?", (pid, u["id"]))
    if not p: raise HTTPException(404, "Not found")
    run("UPDATE passports SET revoked=1 WHERE id=?", (pid,))
    audit(u["id"], "passport_revoke", pid)
    return {"revoked": True}


@app.get("/api/public/passport/{pid}")
def public_passport(pid: str):
    p = one("SELECT * FROM passports WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Passport not found")
    payload = json.loads(p["payload"])
    valid = hmac.compare_digest(p["signature"], E.sign_payload(payload, SECRET))
    if p["revoked"]: return {"revoked": True, "signature_valid": valid}
    return {"revoked": False, "expired": payload["expires"] < time.strftime("%Y-%m-%d"), "signature_valid": valid, "signature": p["signature"][:16], "passport": payload}


# Initialise the base schema before enterprise/admin modules create their extension tables.
init_db()

# PRD expansion: company, job and recruiter/admin APIs.
from enterprise import router as enterprise_router
app.include_router(enterprise_router)