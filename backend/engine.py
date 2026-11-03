"""Pure-Python core logic: PII redaction, skill extraction (rules), sandboxed SQL, adaptive selection,
server-side scoring, trust label, learning path, passport signing. No web framework imports -> unit-testable."""
import re, random, sqlite3, json, hmac, hashlib
from content import *

# ---------- PII redaction (runs BEFORE any text reaches an LLM) ----------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s\-().]{8,}\d(?![\w])")
ADDR_RE = re.compile(r"^.*\b(house|road|rd\.|block|sector|flat|village|post office)\b.*\d.*$", re.I | re.M)

def redact(text, known_names=()):
    counts = {"email": 0, "phone": 0, "url": 0, "address": 0, "name": 0}
    def sub(rx, tag, key, s):
        s, n = rx.subn(tag, s); counts[key] += n; return s
    text = sub(EMAIL_RE, "[EMAIL]", "email", text)
    text = sub(URL_RE, "[URL]", "url", text)
    text = sub(PHONE_RE, "[PHONE]", "phone", text)
    text = sub(ADDR_RE, "[ADDRESS]", "address", text)
    for part in {p for n in known_names for p in n.split() if len(p) >= 3}:
        text, n = re.subn(re.escape(part), "[NAME]", text, flags=re.I); counts["name"] += n
    lines = text.splitlines()
    for i, ln in enumerate(lines):          # heuristic: a short first line without digits is usually the candidate's name
        if ln.strip():
            if len(ln.split()) <= 4 and not re.search(r"\d|\[", ln) and not any(a in ln.lower() for al in TAXONOMY.values() for a in al["aliases"]):
                lines[i] = "[NAME]"; counts["name"] += 1
            break
    return "\n".join(lines), counts

# ---------- skill extraction (offline rule-based fallback) ----------
def rule_extract(text):
    low, out = text.lower(), []
    for sid, s in TAXONOMY.items():
        for alias in s["aliases"]:
            m = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", low)
            if m:
                a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
                out.append({"id": sid, "name": s["name"], "evidence": " ".join(text[a:b].split()), "status": "claimed"})
                break
    return out

def validate_llm_skills(items):
    """Schema validation for LLM output: only taxonomy ids, short evidence strings."""
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        sid = it.get("id") if isinstance(it, dict) else None
        if sid in TAXONOMY and sid not in seen:
            seen.add(sid)
            out.append({"id": sid, "name": TAXONOMY[sid]["name"], "evidence": str(it.get("evidence", ""))[:160], "status": "claimed"})
    return out

# ---------- SQL sandbox ----------
def build_db(seed):
    rng = random.Random(seed)
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
                      "CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, amount INTEGER, status TEXT);")
    names = rng.sample(NAMES, 20)
    cities = CITIES + [rng.choice(CITIES) for _ in range(15)]; rng.shuffle(cities)   # every city appears, counts vary per dataset
    for i, (n, c) in enumerate(zip(names, cities), 1):
        con.execute("INSERT INTO customers VALUES (?,?,?)", (i, n, c))
    oid = 1
    for cid in range(1, 18):                 # customers 18..20 never order
        for _ in range(rng.randint(1, 4)):
            con.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, cid, rng.randrange(500, 9000, 50), rng.choice(["completed"] * 5 + ["cancelled", "pending"])))
            oid += 1
    con.commit()
    return con

PROGRESS_LIMIT = 3000   # x1000 VM instructions

def run_query(seed, sql, maxrows=200):
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql or len(sql) > 2000: return {"columns": [], "rows": [], "error": "Empty or too long query"}
    if not re.match(r"(?is)^(select|with)\b", sql): return {"columns": [], "rows": [], "error": "Only SELECT queries are allowed"}
    con = build_db(seed)
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, "SQLITE_RECURSIVE"): allowed.add(sqlite3.SQLITE_RECURSIVE)
    con.set_authorizer(lambda action, a, b, c, d: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    ticks = [0]
    def prog():
        ticks[0] += 1
        return 1 if ticks[0] > PROGRESS_LIMIT else 0
    con.set_progress_handler(prog, 1000)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(maxrows)]
        return {"columns": cols, "rows": rows, "error": None}
    except Exception as e:
        return {"columns": [], "rows": [], "error": str(e)[:200]}
    finally:
        con.close()

def _norm(rows):
    return [tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows]

def grade_sql(seed, q, user_sql):
    """Correct only if the result matches the reference on TWO different generated datasets (anti-hardcoding)."""
    ordered = "order by" in q["ref"].lower()
    for s in (seed, seed + 7919):
        ref, got = run_query(s, q["ref"]), run_query(s, user_sql)
        if ref["error"] or got["error"]: return False
        a, b = _norm(ref["rows"]), _norm(got["rows"])
        if not ordered: a, b = sorted(a, key=repr), sorted(b, key=repr)
        if a != b: return False
    return True

# ---------- adaptive selection + scoring (stored answer key, never live AI) ----------
SLOTS = ["sql", "python", "statistics", "visualization", "sql", "statistics", "python", "visualization", "sql", "sql"]
SQL_KINDS = ["mcq", "sql", "mcq", "sql"]
TEST_SECONDS, GRACE = 1200, 30

def new_state(seed):
    return {"seed": seed, "index": 0, "level": 2, "used": [], "sql_taken": 0, "answers": [], "current": None, "served_at": None}

def pick_question(state, seen_before):
    i, topic = state["index"], SLOTS[state["index"]]
    kind = SQL_KINDS[state["sql_taken"]] if topic == "sql" else None
    pool = [q for q in QUESTIONS if q["topic"] == topic and q["id"] not in state["used"] and (kind is None or q["type"] == kind)]
    fresh = [q for q in pool if q["id"] not in seen_before] or pool
    rng = random.Random(f"{state['seed']}-{i}")
    fresh.sort(key=lambda q: (abs(q["difficulty"] - state["level"]), rng.random()))
    return fresh[0]

def public_question(q, state):
    d = {"id": q["id"], "topic": q["topic"], "difficulty": q["difficulty"], "type": q["type"], "prompt": q["prompt"],
         "index": state["index"], "total": len(SLOTS)}
    if q["type"] == "mcq": d["options"] = q["options"]
    else: d["schema"] = SQL_SCHEMA
    return d

def record_answer(state, q, answer, seconds):
    if q["type"] == "mcq":
        try: correct = int(answer) == q["answer"]
        except (TypeError, ValueError): correct = False
    else:
        correct = grade_sql(state["seed"], q, str(answer or ""))
    state["answers"].append({"qid": q["id"], "topic": q["topic"], "difficulty": q["difficulty"], "type": q["type"], "answer": str(answer or ""), "correct": correct, "seconds": round(seconds, 1)})
    state["used"].append(q["id"])
    if q["topic"] == "sql": state["sql_taken"] += 1
    state["level"] = max(1, min(3, state["level"] + (1 if correct else -1)))
    state["index"] += 1
    state["current"] = None
    return correct

def level_of(pct): return "Advanced" if pct >= 70 else "Intermediate" if pct >= 40 else "Beginner"

def score(answers):
    topics = {}
    for a in answers:
        t = topics.setdefault(a["topic"], {"earned": 0, "possible": 0, "n": 0})
        t["possible"] += a["difficulty"]; t["n"] += 1
        if a["correct"]: t["earned"] += a["difficulty"]
    out = {k: {"score": round(100 * v["earned"] / v["possible"]), "level": level_of(100 * v["earned"] / v["possible"]), "n": v["n"]} for k, v in topics.items() if v["possible"]}
    ep, pp = sum(v["earned"] for v in topics.values()), sum(v["possible"] for v in topics.values())
    overall = round(100 * ep / pp) if pp else 0
    return {"overall": overall, "level": level_of(overall), "topics": out}

def trust_label(events, answers):
    """Never pass/fail: High / Medium / Needs review, with human-readable reasons. A human reviews 'Needs review'."""
    paste, blur = events.get("paste", 0), events.get("blur", 0)
    fast = sum(1 for a in answers if a["type"] == "mcq" and a["correct"] and a["difficulty"] >= 2 and a["seconds"] < 3)
    pts = 2 * paste + blur + 2 * fast
    reasons = []
    if paste: reasons.append({"key": "paste", "n": paste})
    if blur: reasons.append({"key": "blur", "n": blur})
    if fast: reasons.append({"key": "fast", "n": fast})
    return ("High" if pts <= 1 else "Medium" if pts <= 4 else "Needs review"), reasons

# ---------- gap map + learning path ----------
def gap_map(topic_scores):
    rows = []
    for r in JOB["required"]:
        s = (topic_scores or {}).get(r["skill"])
        rows.append({"skill": r["skill"], "name": TAXONOMY[r["skill"]]["name"], "threshold": r["threshold"],
                     "score": s["score"] if s else None,
                     "status": "unverified" if s is None else ("met" if s["score"] >= r["threshold"] else "below")})
    return rows

def build_path(topic_scores):
    items, gaps = [], []
    for r in JOB["required"]:
        s = (topic_scores or {}).get(r["skill"])
        if s is not None and s["score"] >= r["threshold"]: continue
        gaps.append(r["skill"])
        need = 1 if (s is None or s["score"] < 40) else 2
        cands = sorted([x for x in RESOURCES if x["topic"] == r["skill"] and x["level"] >= 1], key=lambda x: (abs(x["level"] - need), x["level"]))
        if not cands:
            items.append({"topic": r["skill"], "none": True})   # honest "no resource found"
        for x in cands[:2]:
            items.append({"topic": r["skill"], "none": False, **x})
    items.sort(key=lambda x: (x.get("level", 9)))
    return {"gaps": gaps, "items": items}

# ---------- Skill Passport signing ----------
def sign_payload(payload, secret):
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
