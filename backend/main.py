"""SkillBridge Global R0 API (FastAPI). Scores come from stored answer keys + server logic; AI never grades."""
import os, io, re, json, time, uuid, base64, hmac, hashlib, secrets, sqlite3, zipfile, logging, asyncio
from pathlib import Path
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form, Request
from fastapi.responses import Response
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
EVIDENCE_RETENTION_DAYS = max(1, min(90, int(os.getenv("EVIDENCE_RETENTION_DAYS", "30"))))
MAX_EVIDENCE_BYTES = 180 * 1024
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
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, user_id INT, kind TEXT, seed INT, status TEXT, state TEXT, started REAL, finished REAL, result TEXT, appeal TEXT, session_id TEXT, last_event_seq INTEGER NOT NULL DEFAULT 0, last_heartbeat REAL, session_ip TEXT, session_ua TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, attempt_id TEXT, type TEXT, ts REAL, question_id TEXT, question_index INTEGER, seq INTEGER, client_ts REAL, metadata TEXT);
CREATE TABLE IF NOT EXISTS passports(id TEXT PRIMARY KEY, user_id INT, payload TEXT, signature TEXT, created REAL, revoked INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor INT, action TEXT, target TEXT);
CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER PRIMARY KEY, role TEXT NOT NULL DEFAULT 'candidate');
CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, owner_user_id INTEGER UNIQUE NOT NULL, name TEXT NOT NULL, website TEXT, verified INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, company_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL DEFAULT 'Remote', employment_type TEXT NOT NULL DEFAULT 'Full-time', skills TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', is_mock INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, candidate_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'submitted', results_shared INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, UNIQUE(job_id, candidate_id));
CREATE TABLE IF NOT EXISTS company_assessments(id TEXT PRIMARY KEY, company_id TEXT NOT NULL, job_id TEXT, title TEXT NOT NULL, requirements TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_profiles(user_id INTEGER PRIMARY KEY, headline TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', linkedin TEXT NOT NULL DEFAULT '', github TEXT NOT NULL DEFAULT '', portfolio TEXT NOT NULL DEFAULT '', education TEXT NOT NULL DEFAULT '[]', experience TEXT NOT NULL DEFAULT '[]', projects TEXT NOT NULL DEFAULT '[]', preferences TEXT NOT NULL DEFAULT '{}', updated REAL NOT NULL);
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
        c.execute("CREATE TABLE IF NOT EXISTS exam_evidence(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, event_id INTEGER, captured_at REAL NOT NULL, mime_type TEXT NOT NULL, image_data BYTEA NOT NULL, size_bytes INTEGER NOT NULL, expires_at REAL NOT NULL)")
        # Additive migration for existing databases.
        if DATABASE_URL:
            event_columns = {row["column_name"] for row in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='events'").fetchall()}
        else:
            event_columns = {row["name"] for row in c.execute("PRAGMA table_info(events)").fetchall()}
        if "question_id" not in event_columns:
            c.execute("ALTER TABLE events ADD COLUMN question_id TEXT")
        for column, definition in {
            "question_id": "TEXT", "question_index": "INTEGER", "seq": "INTEGER",
            "client_ts": "REAL", "metadata": "TEXT"
        }.items():
            if column not in event_columns:
                c.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
        if DATABASE_URL:
            attempt_columns = {row["column_name"] for row in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='attempts'").fetchall()}
        else:
            attempt_columns = {row["name"] for row in c.execute("PRAGMA table_info(attempts)").fetchall()}
        for column, definition in {
            "session_id": "TEXT", "last_event_seq": "INTEGER NOT NULL DEFAULT 0",
            "last_heartbeat": "REAL", "session_ip": "TEXT", "session_ua": "TEXT"
        }.items():
            if column not in attempt_columns:
                c.execute(f"ALTER TABLE attempts ADD COLUMN {column} {definition}")


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


async def _purge_expired_exam_evidence():
    while True:
        try:
            run("DELETE FROM exam_evidence WHERE expires_at<?", (time.time(),))
        except Exception:
            log.exception("Could not purge expired exam evidence")
        await asyncio.sleep(3600)


_evidence_cleanup_task = None


@app.on_event("startup")
def _startup():
    global _evidence_cleanup_task
    init_db()
    _evidence_cleanup_task = asyncio.create_task(_purge_expired_exam_evidence())


@app.on_event("shutdown")
async def _shutdown():
    if _evidence_cleanup_task:
        _evidence_cleanup_task.cancel()
        try:
            await _evidence_cleanup_task
        except asyncio.CancelledError:
            pass


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
    d["applications"] = [dict(r) for r in allr("SELECT * FROM applications WHERE candidate_id=?", (u["id"],))]
    audit(u["id"], "export")
    return d


@app.delete("/api/me")
def delete_me(u=Depends(current_user)):
    ids = [r["id"] for r in allr("SELECT id FROM attempts WHERE user_id=?", (u["id"],))]
    for i in ids: run("DELETE FROM events WHERE attempt_id=?", (i,))
    for t in ("consents", "profiles", "attempts", "passports", "candidate_profiles", "user_roles"):
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


@app.post("/api/cv")
async def upload_cv(text: str = Form(""), file: Optional[UploadFile] = File(None), u=Depends(current_user)):
    need_consent(u)
    raw = text
    if file is not None: raw = read_upload(file.filename or "", await file.read())
    raw = raw.strip()
    if len(raw) < 30: raise HTTPException(422, "CV text is too short")
    redacted, counts = E.redact(raw[:30000], [u["name"]])
    skills, source = (llm_extract(redacted) if ANTHROPIC_API_KEY else None), "llm"
    if not skills: skills, source = E.rule_extract(redacted), "rules"
    run("INSERT INTO profiles(user_id,redacted,redaction,extracted,source,confirmed) VALUES (?,?,?,?,?,NULL) "
        "ON CONFLICT(user_id) DO UPDATE SET redacted=excluded.redacted,redaction=excluded.redaction,extracted=excluded.extracted,source=excluded.source,confirmed=NULL",
        (u["id"], redacted, json.dumps(counts), json.dumps(skills), source))
    audit(u["id"], "cv_parsed", source)
    return {"redacted_preview": redacted[:1500], "redaction": counts, "skills": skills, "source": source,
            "taxonomy": [{"id": k, "name": v["name"]} for k, v in TAXONOMY.items()]}


class Confirm(BaseModel):
    skills: List[str]


@app.put("/api/skills")
def confirm_skills(b: Confirm, u=Depends(current_user)):
    need_consent(u)
    ids = [s for s in dict.fromkeys(b.skills) if s in TAXONOMY]
    run("INSERT INTO profiles(user_id,confirmed) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET confirmed=excluded.confirmed", (u["id"], json.dumps(ids)))
    return {"confirmed": ids}


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


def view(att, include_session=False):
    st = json.loads(att["state"])
    remaining = max(0, int(att["started"] + E.TEST_SECONDS - time.time()))
    out = {"attempt_id": att["id"], "kind": att["kind"], "status": att["status"], "remaining": remaining, "answered": len(st["answers"]), "total": len(E.SLOTS), "session_id": att.get("session_id") if include_session else None, "last_event_seq": int(att.get("last_event_seq") or 0)}
    if att["status"] == "active" and st["current"]:
        out["question"] = E.public_question(QBYID[st["current"]], st)
    return out


def save(att_id, st, **kw):
    sets, args = ["state=?"], [json.dumps(st)]
    for k, v in kw.items(): sets.append(f"{k}=?"); args.append(v)
    run(f"UPDATE attempts SET {', '.join(sets)} WHERE id=?", (*args, att_id))


def finalize(att, st):
    rows = allr("SELECT type, ts FROM events WHERE attempt_id=? ORDER BY ts, id", (att["id"],))
    ev = [{"type": r["type"], "server_ts": r["ts"]} for r in rows]
    res = E.score(st["answers"])
    integrity = E.trust_label(ev, st["answers"], exam_seconds=E.TEST_SECONDS)
    res.update({
        "trust": integrity["label"], "trust_points": integrity["points"],
        "trust_rule_version": integrity["rule_version"], "trust_reasons": integrity["reasons"],
        "trust_hard_flags": integrity["hard_flags"], "percentile": None,
        "kind": att["kind"], "date": time.strftime("%Y-%m-%d"),
    })
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


@app.post("/api/attempts")
def start_attempt(u=Depends(current_user)):
    need_consent(u)
    act = one("SELECT * FROM attempts WHERE user_id=? AND status='active'", (u["id"],))
    if act:
        if time.time() > act["started"] + E.TEST_SECONDS + E.GRACE:
            finalize(dict(act), json.loads(act["state"]))
        else:
            # A fresh page/session takes ownership; the previous tab is rejected from this point.
            sid = secrets.token_urlsafe(32)
            now = time.time()
            run("UPDATE attempts SET session_id=?,last_event_seq=0,last_heartbeat=? WHERE id=?", (sid, now, act["id"]))
            act = get_att(act["id"], u["id"])
            return view(dict(act), include_session=True)  # resume while invalidating any older tab
    done = allr("SELECT finished FROM attempts WHERE user_id=? AND status='done' ORDER BY finished DESC", (u["id"],))
    if len([d for d in done if d["finished"] > time.time() - 90 * 86400]) >= MAX_ATTEMPTS_90D:
        raise HTTPException(429, "attempt_cap")
    if done and COOLDOWN_HOURS > 0 and time.time() < done[0]["finished"] + COOLDOWN_HOURS * 3600:
        raise HTTPException(429, "cooldown")
    seed = secrets.randbelow(10**9)
    st = E.new_state(seed)
    q = E.pick_question(st, seen_before(u["id"]))
    st["current"], st["served_at"] = q["id"], time.time()
    aid = uuid.uuid4().hex[:12]
    session_id = secrets.token_urlsafe(32)
    started_at = time.time()
    run("INSERT INTO attempts(id,user_id,kind,seed,status,state,started,session_id,last_heartbeat,session_ip,session_ua) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, u["id"], "post" if done else "pre", seed, "active", json.dumps(st), started_at,
         session_id, started_at, "", ""))
    audit(u["id"], "attempt_start", aid)
    return view(get_att(aid, u["id"]), include_session=True)


@app.get("/api/attempts/{aid}")
def get_attempt(aid: str, u=Depends(current_user)): return view(get_att(aid, u["id"]))


class Ans(BaseModel):
    question_id: str
    answer: Optional[str] = None
    session_id: str = Field(min_length=20, max_length=100)


@app.post("/api/attempts/{aid}/answer")
def answer(aid: str, b: Ans, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "active": raise HTTPException(409, "Attempt already finished")
    _check_exam_session(att, b.session_id)
    require_screen_share(aid)
    st = json.loads(att["state"])
    if time.time() > att["started"] + E.TEST_SECONDS + E.GRACE:      # server owns the timer
        finalize(att, st); return view(get_att(aid, u["id"]))
    if b.question_id != st["current"]: raise HTTPException(409, "Question mismatch")
    q = QBYID[st["current"]]
    submitted_at = time.time()
    served_at = float(st.get("served_at") or submitted_at)
    E.record_answer(st, q, b.answer, submitted_at - served_at, answer_text=b.answer,
                    started_at=served_at, submitted_at=submitted_at)
    if st["index"] >= len(E.SLOTS):
        finalize(att, st)
    else:
        nq = E.pick_question(st, seen_before(u["id"]))
        st["current"], st["served_at"] = nq["id"], time.time()
        save(aid, st)
    return view(get_att(aid, u["id"]))                      # never reveals correctness during the exam


class SqlRun(BaseModel):
    sql: str = Field(max_length=12000)
    session_id: str = Field(min_length=20, max_length=100)


@app.post("/api/attempts/{aid}/sql-run")
def sql_run(aid: str, b: SqlRun, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    st = json.loads(att["state"])
    if att["status"] != "active" or not st["current"] or QBYID[st["current"]]["type"] != "sql": raise HTTPException(409, "No SQL question active")
    _check_exam_session(att, b.session_id)
    require_screen_share(aid)
    return E.run_query(att["seed"], b.sql)                  # visible dataset only; scoring uses hidden datasets


EVENT_TYPES = {
    "copy", "cut", "paste", "blur", "tab_hidden", "tab_visible", "fullscreen_exit",
    "screen_share_started", "screen_share_stopped", "screen_share_unavailable",
    "fullscreen_enter", "focus", "wrong_share_surface", "ai_gaze_away", "ai_multiple_faces", "ai_no_face", "ai_phone_detected", "ai_audio_level", "ai_model_unavailable"
}
EVENT_META_KEYS = {"surface", "editor", "reason", "visibility", "supported", "confidence", "model", "duration_ms", "local_only"}


class Ev(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    session_id: str = Field(min_length=20, max_length=100)
    seq: int = Field(ge=1)
    client_ts: Optional[float] = None
    metadata: dict = Field(default_factory=dict)


class Heartbeat(BaseModel):
    session_id: str = Field(min_length=20, max_length=100)
    seq: int = Field(ge=1)
    client_ts: Optional[float] = None

class BeaconEv(Ev):
    authorization: str = Field(min_length=20, max_length=4096)


def _request_fingerprint(request):
    return (request.client.host if request.client else "", request.headers.get("user-agent", "")[:300])


def _check_exam_session(att, session_id):
    if not att.get("session_id") or not hmac.compare_digest(str(att["session_id"]), str(session_id or "")):
        raise HTTPException(409, "exam_session_replaced")


def _accept_sequence(att, seq, aid, request, event_type, client_ts=None, metadata=None):
    now = time.time()
    _check_exam_session(att, att.get("_provided_session"))
    last = int(att.get("last_event_seq") or 0)
    if seq <= last:
        raise HTTPException(409, "event_sequence_replayed")
    if seq > last + 100:
        raise HTTPException(409, "event_sequence_gap")
    recent = one("SELECT COUNT(*) AS n FROM events WHERE attempt_id=? AND ts>?", (aid, now - 60))
    if recent and recent["n"] >= 180:
        raise HTTPException(429, "event_rate_limited")
    ip, ua = _request_fingerprint(request)
    prior_ip = att.get("session_ip") or ""
    if prior_ip and ip and prior_ip != ip:
        run("INSERT INTO events(attempt_id,type,ts,question_id,question_index,seq,client_ts,metadata) VALUES (?,?,?,?,?,?,?,?)",
            (aid, "network_ip_changed", now, None, None, None, None, json.dumps({"previous_ip_changed": True})))
    st = json.loads(att["state"])
    safe_meta = {k: (v[:100] if isinstance(v, str) else v) for k, v in list((metadata or {}).items())[:8]
                 if k in EVENT_META_KEYS and isinstance(v, (str, int, float, bool))}
    run("INSERT INTO events(attempt_id,type,ts,question_id,question_index,seq,client_ts,metadata) VALUES (?,?,?,?,?,?,?,?)",
        (aid, event_type, now, st.get("current"), st.get("index"), seq,
         float(client_ts) if client_ts is not None else None, json.dumps(safe_meta)))
    run("UPDATE attempts SET last_event_seq=?,session_ip=?,session_ua=? WHERE id=?", (seq, ip, ua, aid))
    return now


@app.post("/api/attempts/{aid}/event")
def event(aid: str, b: Ev, request: Request, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "active":
        raise HTTPException(409, "attempt_not_active")
    if b.type not in EVENT_TYPES:
        raise HTTPException(422, "unsupported_event_type")
    att["_provided_session"] = b.session_id
    now = _accept_sequence(att, b.seq, aid, request, b.type, b.client_ts, b.metadata)
    return {"ok": True, "server_ts": now, "seq": b.seq}


@app.post("/api/attempts/{aid}/event/beacon")
def event_beacon(aid: str, b: BeaconEv, request: Request):
    # sendBeacon cannot set Authorization headers; accept the same signed bearer token in its JSON body.
    u = current_user("Bearer " + b.authorization)
    att = get_att(aid, u["id"])
    if att["status"] != "active":
        raise HTTPException(409, "attempt_not_active")
    if b.type not in EVENT_TYPES:
        raise HTTPException(422, "unsupported_event_type")
    att["_provided_session"] = b.session_id
    now = _accept_sequence(att, b.seq, aid, request, b.type, b.client_ts, b.metadata)
    return {"ok": True, "server_ts": now, "seq": b.seq}


@app.post("/api/attempts/{aid}/evidence")
async def upload_exam_evidence(aid: str, request: Request, session_id: str = Form(...), seq: int = Form(...),
                              event_type: str = Form(...), confidence: float = Form(...),
                              image: UploadFile = File(...), u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "active":
        raise HTTPException(409, "attempt_not_active")
    _check_exam_session(att, session_id)
    if event_type not in {"ai_gaze_away", "ai_multiple_faces", "ai_no_face", "ai_phone_detected"}:
        raise HTTPException(422, "unsupported_evidence_event")
    if not 0 <= confidence <= 1:
        raise HTTPException(422, "invalid_confidence")
    if image.content_type != "image/jpeg":
        raise HTTPException(415, "jpeg_evidence_required")
    payload = await image.read(MAX_EVIDENCE_BYTES + 1)
    if not payload or len(payload) > MAX_EVIDENCE_BYTES:
        raise HTTPException(413, "evidence_too_large")
    if not payload.startswith(bytes([255, 216, 255])):
        raise HTTPException(415, "invalid_jpeg")
    count = one("SELECT COUNT(*) AS n FROM exam_evidence WHERE attempt_id=?", (aid,))
    if count and count["n"] >= 20:
        raise HTTPException(429, "evidence_limit_reached")
    att["_provided_session"] = session_id
    server_ts = _accept_sequence(att, seq, aid, request, event_type, None,
                                 {"confidence": round(confidence, 3), "model": "mediapipe_tasks_vision", "local_only": True})
    evidence_id = uuid.uuid4().hex
    expires = server_ts + EVIDENCE_RETENTION_DAYS * 86400
    event_row = one("SELECT id FROM events WHERE attempt_id=? AND seq=? ORDER BY id DESC LIMIT 1", (aid, seq))
    run("INSERT INTO exam_evidence(id,attempt_id,event_id,captured_at,mime_type,image_data,size_bytes,expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (evidence_id, aid, event_row["id"] if event_row else None, server_ts, "image/jpeg", payload, len(payload), expires))
    run("DELETE FROM exam_evidence WHERE expires_at<?", (server_ts,))
    return {"ok": True, "evidence_id": evidence_id, "server_ts": server_ts, "size_bytes": len(payload), "retention_days": EVIDENCE_RETENTION_DAYS}


@app.get("/api/attempts/{aid}/evidence/{evidence_id}")
def get_own_exam_evidence(aid: str, evidence_id: str, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    row = one("SELECT mime_type,image_data,expires_at FROM exam_evidence WHERE id=? AND attempt_id=?", (evidence_id, aid))
    if not row or row["expires_at"] < time.time():
        raise HTTPException(404, "evidence_not_found")
    return Response(content=bytes(row["image_data"]), media_type=row["mime_type"], headers={"Cache-Control": "no-store"})


@app.post("/api/attempts/{aid}/heartbeat")
def heartbeat(aid: str, b: Heartbeat, request: Request, u=Depends(current_user)):
    att = get_att(aid, u["id"])
    if att["status"] != "active":
        raise HTTPException(409, "attempt_not_active")
    _check_exam_session(att, b.session_id)
    now = time.time()
    previous = float(att.get("last_heartbeat") or att["started"])
    gap = now - previous
    att["_provided_session"] = b.session_id
    _accept_sequence(att, b.seq, aid, request, "heartbeat", b.client_ts, {})
    if gap > 30:
        st = json.loads(att["state"])
        run("INSERT INTO events(attempt_id,type,ts,question_id,question_index,seq,client_ts,metadata) VALUES (?,?,?,?,?,?,?,?)",
            (aid, "heartbeat_gap", now, st.get("current"), st.get("index"), None, None,
             json.dumps({"gap_seconds": round(gap, 1), "classification": "monitoring_gap_not_misconduct"})))
    run("UPDATE attempts SET last_heartbeat=? WHERE id=?", (now, aid))
    return {"ok": True, "server_ts": now, "gap_seconds": round(gap, 1) if gap > 30 else 0}


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
