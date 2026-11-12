"""Pure-Python core logic: PII redaction, skill extraction (rules), sandboxed SQL, adaptive selection,
server-side scoring, trust label, learning path, passport signing. No web framework imports -> unit-testable."""
import re, random, sqlite3, json, hmac, hashlib, difflib, math
from collections import Counter
from content import *

# ---------- PII redaction (runs BEFORE any text reaches an LLM) ----------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s\-().]{8,}\d(?![\w])")
ADDR_RE = re.compile(r"^.*\b(house|road|rd\.|block|sector|flat|village|post office)\b.*\d.*$", re.I | re.M)

def redact(text, known_names=(), assume_first_line_name=True):
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
    if assume_first_line_name:
        for i, ln in enumerate(lines):      # heuristic: a short first line without digits is usually the candidate's name
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

def record_answer(state, q, answer, seconds, answer_text=None, started_at=None, submitted_at=None):
    if q["type"] == "mcq":
        try: correct = int(answer) == q["answer"]
        except (TypeError, ValueError): correct = False
    else:
        correct = grade_sql(state["seed"], q, str(answer or ""))
    state["answers"].append({
        "qid": q["id"], "topic": q["topic"], "difficulty": q["difficulty"], "type": q["type"],
        "correct": correct, "seconds": round(max(0, seconds), 1),
        "answer": str(answer_text if answer_text is not None else answer or ""),
        "started_at": started_at, "submitted_at": submitted_at,
    })
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
    "rule_version": "phase2-v1",
    "normalize_per_30_minutes": True,
    "weights": {
        "external_clipboard": 2, "leave_window": 1,
        "fullscreen_exit": 1, "screen_share_stopped": 3,
        "wrong_share_surface": 3, "heartbeat_gap": 2,
        "ai_gaze_away": 0.5, "ai_multiple_faces": 1, "ai_no_face": 0.5, "ai_phone_detected": 1,
    },
    "caps": {
        "external_clipboard": 3, "leave_window": 4,
        "fullscreen_exit": 3, "screen_share_stopped": 2,
        "wrong_share_surface": 1, "heartbeat_gap": 3, "fast": 4,
        "ai_gaze_away": 2, "ai_multiple_faces": 1, "ai_no_face": 2, "ai_phone_detected": 1,
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
        if kind == "tab_visible":
            if current:
                current["end_ts"] = max(current["start_ts"], ts)
                current["types"].add(kind)
                current = None
            continue
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
    weights = {**TRUST_RULES.get("weights", {}), **(rules.get("weights") or {})}
    caps = {**TRUST_RULES.get("caps", {}), **(rules.get("caps") or {})}
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
    # Client-side AI signals are advisory only, capped and never treated as hard flags.
    for kind in ("ai_gaze_away", "ai_multiple_faces", "ai_no_face", "ai_phone_detected"):
        n = sum(1 for e in events if _event_value(e, "type") == kind)
        charged = min(n, caps.get(kind, 1))
        if charged:
            value = charged * weights.get(kind, 1)
            points += value
            reasons.append({"key": kind, "n": charged, "points": value, "advisory": True})
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

# ---------- descriptive / analytical answer evaluation (AI-assisted draft) ----------
# The official score never comes from a live model. This section produces an
# explainable, deterministic draft from the stored reference answer + rubric, and
# optionally fuses a validated LLM opinion as one evidence signal. The draft is
# always marked ``pending_approval``; an administrator must approve or edit it
# before any mark is counted in the candidate result.
DESCRIPTIVE_METHOD = "hybrid:keyword+similarity+llm"
DESCRIPTIVE_FALLBACK_METHOD = "hybrid:keyword+similarity"

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#._]*")
DESCRIPTIVE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "if", "in", "into", "is", "it", "its", "may", "might", "more", "most",
    "must", "of", "on", "or", "our", "should", "so", "some", "such", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "why", "will", "with", "would", "you", "your",
}
_GENERIC_RUBRIC_WORDS = {
    "award", "marks", "mark", "score", "scoring", "mention", "mentions", "expected",
    "expect", "explain", "explains", "explanation", "correct", "correctly", "answer",
    "answers", "candidate", "should", "needs", "required", "require", "clear",
    "clarity", "accurate", "accuracy", "identify", "identifies", "describe",
    "describes", "define", "defines", "definition", "use", "used", "using", "good",
    "well", "strong", "weak", "appropriate", "relevant", "basic", "simple",
}
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"(give|award)\s+(me\s+)?(full|maximum|max|all)\s+marks", re.I),
    re.compile(r"(you\s+are|act\s+as)\s+(now\s+)?(an?\s+)?(admin|grader|system)", re.I),
    re.compile(r"(reveal|show|print)\s+(the\s+)?(system\s+prompt|answer\s+key|rubric)", re.I),
)


_STEM_SYNONYMS = {
    "logarithm": "log", "logarithmic": "log", "logarithmically": "log",
    "balanc": "balance", "complexities": "complexity", "queries": "query", "indices": "index",
}


def _stem(word):
    """Small, deterministic stemmer so paraphrase matching is not exact-word only."""
    w = (word or "").lower().strip("._-")
    if not w:
        return w
    if len(w) <= 4:
        return _STEM_SYNONYMS.get(w, w)
    if w.endswith("ies") and len(w) > 5:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if w.endswith("ing") and len(w) > 6:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 5:
        w = w[:-2]
    if w.endswith("e") and len(w) > 5:
        w = w[:-1]
    return _STEM_SYNONYMS.get(w, w)


def _words(text):
    return [m.group(0).lower().strip(".-") for m in _WORD_RE.finditer(text or "")
            if m.group(0).strip(".-")]


def _content_tokens(text):
    return [_stem(w) for w in _words(text) if w not in DESCRIPTIVE_STOPWORDS and len(w) > 2]


def _normalized_tokens(text):
    return " ".join(_content_tokens(text))


def _phrases(tokens, n):
    return {" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def extract_expected_keywords(reference="", rubric="", limit=24):
    """Extract weighted expected concepts from the stored model answer and rubric.

    Unigrams are always retained; a small number of high-value two-word phrases
    are added so concept phrases can match without dominating the set. Returns
    display terms the administrator can audit rather than opaque model features.
    """
    reference = reference or ""
    rubric = rubric or ""
    ref_tokens = _content_tokens(reference)
    rub_tokens = _content_tokens(rubric)
    if not ref_tokens and not rub_tokens:
        return []
    terms = {}

    def add(display, key, weight):
        key = " ".join(key.split())
        if not key or len(key) < 3:
            return
        if key in terms:
            terms[key]["weight"] = max(terms[key]["weight"], float(weight))
            return
        terms[key] = {"term": display, "key": key, "weight": float(weight)}

    counts = Counter(ref_tokens)
    for token, count in counts.items():
        if token not in _GENERIC_RUBRIC_WORDS:
            add(token, token, 1.0 + min(count - 1, 3) * 0.15)
    # A rubric keyword is a strong signal even when the model answer words it differently.
    for token in dict.fromkeys(rub_tokens):
        if token not in _GENERIC_RUBRIC_WORDS:
            add(token, token, 1.35)
    unigrams = sorted((x for x in terms.values() if " " not in x["key"]),
                      key=lambda x: (-x["weight"], x["key"]))[:16]
    phrases = []
    for phrase in dict.fromkeys(_phrases(ref_tokens, 2)):
        parts = phrase.split()
        if any(part in _GENERIC_RUBRIC_WORDS for part in parts):
            continue
        phrases.append({"term": phrase, "key": phrase, "weight": 1.6})
    phrases = phrases[:8]
    combined = unigrams + phrases
    combined.sort(key=lambda x: (-x["weight"], -len(x["key"]), x["key"]))
    return combined[:max(1, int(limit))]


def keyword_coverage(answer, keywords):
    """Measure which expected concepts the candidate actually expressed."""
    answer_tokens = _content_tokens(answer)
    token_set = set(answer_tokens)
    phrase_sets = {n: _phrases(answer_tokens, n) for n in (2, 3, 4)}
    matched, missing, total, hit = [], [], 0.0, 0.0
    for item in keywords or []:
        weight = float(item.get("weight", 1.0))
        total += weight
        parts = item["key"].split()
        found = (item["key"] in phrase_sets.get(len(parts), set())
                 if len(parts) > 1 else item["key"] in token_set)
        if found:
            hit += weight
            matched.append(item["term"])
        else:
            missing.append(item["term"])
    return {
        "score": round(hit / total, 4) if total else 0.0,
        "matched": matched,
        "missing": missing,
        "expected": [x["term"] for x in (keywords or [])],
        "total_weight": round(total, 3),
        "matched_weight": round(hit, 3),
    }


def text_similarity(answer, reference):
    """Blend token cosine and sequence similarity for paraphrase-tolerant matching."""
    a, b = _content_tokens(answer), _content_tokens(reference)
    if not a or not b:
        return {"score": 0.0, "cosine": 0.0, "sequence": 0.0}
    ca, cb = Counter(a), Counter(b)
    dot = sum(ca[t] * cb.get(t, 0) for t in ca)
    denom = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    cosine = dot / denom if denom else 0.0
    sequence = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return {
        "score": round(0.6 * cosine + 0.4 * sequence, 4),
        "cosine": round(cosine, 4),
        "sequence": round(sequence, 4),
    }


def _round_marks(value, max_marks):
    max_marks = float(max_marks) if max_marks else 1.0
    granularity = 0.25 if max_marks <= 2 else 0.5 if max_marks <= 10 else 1.0
    value = max(0.0, min(max_marks, float(value or 0)))
    rounded = round(value / granularity) * granularity
    return round(max(0.0, min(max_marks, rounded)), 2)


def _validate_llm_grading(llm, max_marks):
    """Only accept a bounded, structured LLM opinion; ignore everything else."""
    if not isinstance(llm, dict) or llm.get("error"):
        return None
    raw = llm.get("marks", llm.get("score"))
    if raw is None:
        return None
    try:
        marks = float(raw)
    except (TypeError, ValueError):
        return None
    if (float(raw) > float(max_marks) * 5 and float(raw) <= 100 and max_marks <= 10):
        # A large value is almost certainly a 0-100 score; small overshoots are clamped.
        marks = float(raw) * float(max_marks) / 100.0
    marks = max(0.0, min(float(max_marks), marks))
    confidence = llm.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    def _clean_list(value, limit=12):
        if not isinstance(value, list):
            return []
        return [str(x).strip()[:160] for x in value if str(x).strip()][:limit]
    return {
        "marks": round(marks, 2),
        "percentage": round(100 * marks / float(max_marks), 1) if max_marks else 0.0,
        "rationale": str(llm.get("rationale", "")).strip()[:1200],
        "matched_points": _clean_list(llm.get("matched_points")),
        "missing_points": _clean_list(llm.get("missing_points")),
        "confidence": round(confidence, 3),
        "flags": _clean_list(llm.get("flags")),
        "provider": str(llm.get("provider", ""))[:40],
        "model": str(llm.get("model", ""))[:80],
    }


def evaluate_descriptive_answer(answer, *, reference="", rubric="", question="", max_marks=1, llm=None):
    """Produce an explainable partial-mark draft for one descriptive response.

    Weights when an LLM opinion is available: keyword 30%, similarity 20%, LLM 50%.
    Without an LLM the evidence is renormalised to keyword 60% / similarity 40%.
    The returned ``ai_marks`` is never authoritative: ``status`` is always
    ``pending_approval`` and ``requires_admin_approval`` is always true.
    """
    answer = str(answer or "")
    reference = str(reference or "")
    rubric = str(rubric or "")
    max_marks = float(max_marks or 1)
    keywords = extract_expected_keywords(reference, rubric)
    coverage = keyword_coverage(answer, keywords)
    similarity = text_similarity(answer, reference)
    llm_view = _validate_llm_grading(llm, max_marks)

    flags = []
    if not answer.strip():
        flags.append("empty_answer")
    elif len(answer.split()) < 5:
        flags.append("short_answer")
    if coverage["score"] < 0.25 and similarity["score"] < 0.2:
        flags.append("low_relevance")
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(answer):
            flags.append("possible_prompt_injection")
            break
    if isinstance(llm, dict) and llm.get("error"):
        flags.append("llm_unavailable")
    if coverage["score"] < 0.2:
        flags.append("low_keyword_coverage")

    if llm_view:
        pct = (0.30 * coverage["score"] + 0.20 * similarity["score"]
               + 0.50 * (llm_view["marks"] / max_marks if max_marks else 0))
        deterministic = 0.60 * coverage["score"] + 0.40 * similarity["score"]
        if abs((llm_view["marks"] / max_marks if max_marks else 0) - deterministic) > 0.35:
            flags.append("llm_disagrees_with_evidence")
        method = DESCRIPTIVE_METHOD
        weights = {"keyword": 0.30, "similarity": 0.20, "llm": 0.50}
    else:
        pct = 0.60 * coverage["score"] + 0.40 * similarity["score"]
        method = DESCRIPTIVE_FALLBACK_METHOD
        weights = {"keyword": 0.60, "similarity": 0.40, "llm": 0.0}

    if not answer.strip():
        pct = 0.0
    percentage = round(100 * max(0.0, min(1.0, pct)), 1)
    ai_marks = _round_marks(max_marks * percentage / 100.0, max_marks)

    if answer.strip():
        confidence = 0.30 + 0.25 * coverage["score"] + 0.20 * similarity["score"]
        if llm_view:
            confidence += 0.20 * llm_view["confidence"]
        if "llm_disagrees_with_evidence" in flags:
            confidence -= 0.15
        confidence = round(max(0.05, min(0.97, confidence)), 3)
    else:
        confidence = 1.0

    rationale_bits = []
    if coverage["matched"]:
        rationale_bits.append("Matched expected concepts: " + ", ".join(coverage["matched"][:6]) + ".")
    if coverage["missing"]:
        rationale_bits.append("Not evidenced: " + ", ".join(coverage["missing"][:6]) + ".")
    rationale_bits.append(
        f"Reference similarity {similarity['score'] * 100:.0f}% "
        f"(cosine {similarity['cosine'] * 100:.0f}%, sequence {similarity['sequence'] * 100:.0f}%)."
    )
    if llm_view and llm_view["rationale"]:
        rationale_bits.append("LLM reviewer: " + llm_view["rationale"])
    if not answer.strip():
        rationale_bits.append("No answer was submitted.")
    rationale_bits.append(
        f"Hybrid draft {ai_marks}/{max_marks:g} ({percentage:g}%). "
        "An administrator must approve or edit this draft before it affects the score."
    )

    return {
        "status": "pending_approval",
        "method": method,
        "max_marks": round(max_marks, 2),
        "ai_marks": ai_marks,
        "percentage": percentage,
        "components": {
            "keyword": {"score": round(coverage["score"], 4), "weight": weights["keyword"]},
            "similarity": {"score": round(similarity["score"], 4), "weight": weights["similarity"],
                           "cosine": similarity["cosine"], "sequence": similarity["sequence"]},
            "llm": {"score": round(llm_view["marks"] / max_marks, 4) if llm_view and max_marks else 0.0,
                    "weight": weights["llm"], "used": bool(llm_view),
                    "confidence": llm_view["confidence"] if llm_view else None},
        },
        "keywords": coverage,
        "llm": llm_view,
        "rationale": " ".join(rationale_bits),
        "confidence": confidence,
        "flags": sorted(set(flags)),
        "requires_admin_approval": True,
    }


def compute_result_scores(rows, fundamental_weight=0.6):
    """Recompute topic/section/overall scores from human-approved awarded marks.

    ``rows`` entries: ``topic_key``, ``topic`` (label), ``section``
    (``fundamental``/``cv``), ``marks`` (maximum) and ``awarded_marks``.
    A ``None`` awarded mark means "not approved yet" and is excluded from both the
    earned and possible totals, so an AI draft can never move a score.
    """
    topics = {}
    fundamental_earned = fundamental_possible = 0.0
    cv_earned = cv_possible = 0.0
    for row in rows or []:
        awarded = row.get("awarded_marks")
        if awarded is None:
            continue
        marks = float(row.get("marks") or 1)
        awarded = max(0.0, min(marks, float(awarded)))
        topic = row.get("topic_key") or "general"
        metric = topics.setdefault(topic, {
            "earned": 0.0, "possible": 0.0, "n": 0,
            "label": row.get("topic") or topic,
            "section": "fundamental" if row.get("section") == "fundamental" else "cv",
        })
        metric["earned"] += awarded
        metric["possible"] += marks
        metric["n"] += 1
        if row.get("section") == "fundamental":
            fundamental_earned += awarded
            fundamental_possible += marks
        else:
            cv_earned += awarded
            cv_possible += marks
    topic_scores = {}
    for topic, metric in topics.items():
        pct = round(100 * metric["earned"] / metric["possible"]) if metric["possible"] else None
        topic_scores[topic] = {
            "score": pct, "level": level_of(pct or 0), "n": metric["n"],
            "label": metric.get("label", topic), "section": metric.get("section", "cv"),
        }
    fundamental_pct = round(100 * fundamental_earned / fundamental_possible) if fundamental_possible else None
    cv_pct = round(100 * cv_earned / cv_possible) if cv_possible else None
    if fundamental_pct is not None and cv_pct is not None:
        overall = round(fundamental_weight * fundamental_pct + (1 - fundamental_weight) * cv_pct)
    else:
        overall = fundamental_pct if fundamental_pct is not None else cv_pct
    return {
        "overall": overall,
        "level": level_of(overall or 0),
        "topics": topic_scores,
        "section_scores": {
            "fundamental": {"score": fundamental_pct, "weight": fundamental_weight,
                            "earned": round(fundamental_earned, 2),
                            "possible": round(fundamental_possible, 2),
                            "label": "Fundamentals (DSA, SQL, Networking, OOP, Database, Coding, Critical Thinking)"},
            "cv": {"score": cv_pct, "weight": round(1 - fundamental_weight, 2),
                   "earned": round(cv_earned, 2), "possible": round(cv_possible, 2),
                   "label": "CV & development skills (AI/ML, Git, Docker and other confirmed CV skills)"},
        },
    }

# ---------- gap map + learning path ----------
def gap_map(topic_scores):
    rows = []
    for r in JOB["required"]:
        s = (topic_scores or {}).get(r["skill"])
        score = s.get("score") if isinstance(s, dict) else None
        rows.append({"skill": r["skill"], "name": TAXONOMY[r["skill"]]["name"], "threshold": r["threshold"],
                     "score": score,
                     "status": "unverified" if score is None else ("met" if score >= r["threshold"] else "below")})
    return rows

def build_path(topic_scores):
    items, gaps = [], []
    for r in JOB["required"]:
        s = (topic_scores or {}).get(r["skill"])
        score = s.get("score") if isinstance(s, dict) else None
        if score is not None and score >= r["threshold"]: continue
        gaps.append(r["skill"])
        need = 1 if (score is None or score < 40) else 2
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
