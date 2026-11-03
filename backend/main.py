"""SkillBridge Global R0 API (FastAPI). Scores come from stored answer keys + server logic; AI never grades."""
import os, io, re, json, time, uuid, base64, hmac, hashlib, secrets, sqlite3, zipfile, logging
from pathlib import Path
from typing import Optional, List

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
CONSENT_VERSION = "r0-1"


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
CREATE TABLE IF NOT EXISTS candidate_profiles(user_id INTEGER PRIMARY KEY, headline TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', linkedin TEXT NOT NULL DEFAULT '', github TEXT NOT NULL DEFAULT '', portfolio TEXT NOT NULL DEFAULT '', education TEXT NOT NULL DEFAULT '[]', experience TEXT NOT NULL DEFAULT '[]', projects TEXT NOT NULL DEFAULT '[]', preferences TEXT NOT NULL DEFAULT '{}', updated REAL NOT NULL);
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
