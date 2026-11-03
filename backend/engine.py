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
    state["answers"].append({"qid": q["id"], "topic": q["topic"], "difficulty": q["difficulty"], "type": q["type"], "correct": correct, "seconds": round(seconds, 1)})
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

TRUST_RULES = {
    "rule_version": "phase1-v1",
    "normalize_per_30_minutes": True,
    "weights": {
        "external_clipboard": 2, "leave_window": 1,
        "fullscreen_exit": 1, "screen_share_stopped": 3,
        "wrong_share_surface": 3, "heartbeat_gap": 2,
    },
    "caps": {
        "external_clipboard": 3, "leave_window": 4,
        "fullscreen_exit": 3, "screen_share_stopped": 2,
        "wrong_share_surface": 1, "heartbeat_gap": 3, "fast": 4,
    },
}

def _event_value(event, key, default=None):
    return event.get(key, default) if isinstance(event, dict) else getattr(event, key, default)

def _group_integrity_incidents(events, window_seconds=3):
    """Collapse related visibility/blur/fullscreen reports into one leave-window incident."""
    ordered = sorted(
        [e for e in events if _event_value(e, "type") not in {"heartbeat", "screen_share_started"}],
        key=lambda e: float(_event_value(e, "server_ts", _event_value(e, "ts", 0)) or 0),
    )
    incidents, current = [], None
    for event in ordered:
        kind = _event_value(event, "type", "")
        ts = float(_event_value(event, "server_ts", _event_value(event, "ts", 0)) or 0)
        if kind in {"tab_hidden", "blur", "fullscreen_exit"}:
            if current and ts - current["end_ts"] <= window_seconds:
                current["end_ts"] = max(current["end_ts"], ts)
                current["types"].add(kind)
            else:
                current = {"start_ts": ts, "end_ts": ts, "types": {kind}}
                incidents.append(current)
        else:
            current = None
            incidents.append({"start_ts": ts, "end_ts": ts, "types": {kind}})
    return incidents

def trust_label(events, answers, rules=None, exam_seconds=1200, question_times=None):
    """Explainable, versioned integrity score. Advisory only; never changes technical scoring."""
    rules = rules or TRUST_RULES
    events = list(events or []) if not isinstance(events, dict) else [
        {"type": key, "server_ts": 0, "count": value} for key, value in events.items() for _ in range(int(value or 0))
    ]
    incidents = _group_integrity_incidents(events)
    counts = {}
    points = 0
    reasons = []
    hard_flags = set()
    weights, caps = rules.get("weights", {}), rules.get("caps", {})
    clipboard_n = sum(1 for e in events if _event_value(e, "type") in {"copy", "cut", "paste"} and _event_value(e, "external", True))
    clipboard_charged = min(clipboard_n, caps.get("external_clipboard", 3))
    if clipboard_charged:
        value = clipboard_charged * weights.get("external_clipboard", 2)
        points += value
        reasons.append({"key": "external_clipboard", "n": clipboard_charged, "points": value})
    leave_incidents = [i for i in incidents if i["types"] & {"tab_hidden", "blur"}]
    for incident in leave_incidents:
        duration = max(0, incident["end_ts"] - incident["start_ts"])
        value = min(caps.get("leave_window", 4), weights.get("leave_window", 1) + int(duration // 60))
        points += value
        reasons.append({"key": "leave_window", "n": 1, "points": value, "duration_seconds": round(duration, 1)})
    fs_events = []
    for event in events:
        if _event_value(event, "type") != "fullscreen_exit":
            continue
        ts = float(_event_value(event, "server_ts", 0) or 0)
        grouped = any(
            _event_value(other, "type") in {"tab_hidden", "blur"}
            and abs(float(_event_value(other, "server_ts", 0) or 0) - ts) <= 3
            for other in events
        )
        if not grouped:
            fs_events.append(event)
    fs_n = min(len(fs_events), caps.get("fullscreen_exit", 3))
    if fs_n:
        value = fs_n * weights.get("fullscreen_exit", 1)
        points += value
        reasons.append({"key": "fullscreen_exit", "n": fs_n, "points": value})
    for kind, reason_key in (("screen_share_stopped", "screen_share_stopped"),
                             ("screen_share_unavailable", "screen_share_stopped")):
        n = sum(1 for e in events if _event_value(e, "type") == kind)
        if n:
            charged = min(n, caps.get("screen_share_stopped", 2))
            value = charged * weights.get("screen_share_stopped", 3)
            points += value
            reasons.append({"key": reason_key, "n": charged, "points": value})
    for kind, reason_key in (("wrong_share_surface", "wrong_share_surface"), ("heartbeat_gap", "heartbeat_gap")):
        n = sum(1 for e in events if _event_value(e, "type") == kind)
        if n:
            charged = min(n, caps.get(reason_key, 3))
            value = charged * weights.get(reason_key, 2 if reason_key == "heartbeat_gap" else 1)
            points += value
            reasons.append({"key": reason_key, "n": charged, "points": value})
    for event in events:
        flag = _event_value(event, "hard_flag")
        if flag:
            hard_flags.add(str(flag))
    fast = 0
    question_times = question_times or {}
    for answer in answers:
        if answer.get("type") != "mcq" or not answer.get("correct") or answer.get("difficulty", 0) < 2:
            continue
        prior = question_times.get(answer.get("qid"), [])
        threshold = sorted(prior)[max(0, int(len(prior) * 0.05) - 1)] if len(prior) >= 30 else 3
        if float(answer.get("seconds", 999999)) < threshold:
            fast += 1
    fast = min(fast, caps.get("fast", 4))
    if fast:
        value = fast
        points += value
        reasons.append({"key": "fast", "n": fast, "points": value, "threshold": "question_p05_or_3s_fallback"})
    if rules.get("normalize_per_30_minutes", True) and exam_seconds > 0:
        points = round(points * (1800 / exam_seconds), 2)
    label = "Needs review" if hard_flags or points >= 7 else "Medium" if points >= 3 else "High trust"
    return {
        "label": label, "points": points, "rule_version": rules.get("rule_version", "unknown"),
        "reasons": reasons, "hard_flags": sorted(hard_flags),
    }

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
