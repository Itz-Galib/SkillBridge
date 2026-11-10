"""Idempotent demo/test seeder for SkillBridge R0.

Creates a single demo account that has already walked the full loop (consent ->
CV -> skills -> pre assessment -> post assessment -> Skill Passport), so a
reviewer can open every screen without going through the flow by hand.

Run:  python seed_demo.py   (no-op if the demo user already exists)

This is demo data only. It uses the same engine (engine.py) and database schema
(main.py) as the real app, so everything it writes is valid and consistent.
"""
import json
import secrets
import time
import uuid

import engine as E
from main import CONSENT_VERSION, SECRET, hash_pw, init_db, one, run

DEMO_EMAIL = "demo@skillbridge.test"
DEMO_NAME = "Demo Candidate"
DEMO_PASSWORD = "demo12345"

# The same synthetic CV the UI offers via the "Use sample CV" button.
CV_TEXT = """Sadia Rahman
House 14, Road 3, Mirpur, Dhaka
sadia.rahman@example.com | +880 1712-345678 | https://github.com/sadia-demo

Education: BSc in Computer Science, final year.
Projects: Sales dashboard in Power BI; customer analysis with pandas and MySQL joins; basic statistics for a class survey.
Skills: SQL, Python, Excel, Git. Presentation and report writing for class projects.
(Synthetic sample CV for demo purposes.)"""

# PII already redacted (mirrors what the /api/cv endpoint would store).
REDACTED_CV = """[NAME]
[ADDRESS]
[EMAIL] | [PHONE] | [URL]

Education: BSc in Computer Science, final year.
Projects: Sales dashboard in Power BI; customer analysis with pandas and MySQL joins; basic statistics for a class survey.
Skills: SQL, Python, Excel, Git. Presentation and report writing for class projects.
(Synthetic sample CV for demo purposes.)"""

REDACTION = json.dumps({"email": 1, "phone": 1, "url": 1, "address": 1, "name": 2})

EXTRACTED = json.dumps([
    {"id": "sql", "name": "SQL", "evidence": "customer analysis with pandas and MySQL joins", "status": "claimed"},
    {"id": "python", "name": "Python", "evidence": "customer analysis with pandas", "status": "claimed"},
    {"id": "excel", "name": "Excel", "evidence": "Skills: SQL, Python, Excel, Git", "status": "claimed"},
    {"id": "git", "name": "Git", "evidence": "Skills: SQL, Python, Excel, Git", "status": "claimed"},
])

CONFIRMED = json.dumps(["sql", "python", "excel", "git"])


def _answer_for(q, correct):
    """Return a correct or intentionally wrong answer for a question."""
    if q["type"] == "mcq":
        if correct:
            return str(q["answer"])
        return str((q["answer"] + 1) % len(q["options"]))
    return q["ref"] if correct else "SELECT 1"


def _simulate(seed, seen_before, correct_prefix):
    """Run a full 10-question attempt through the real engine and return its state."""
    st = E.new_state(seed)
    for i in range(len(E.SLOTS)):
        q = E.pick_question(st, seen_before)
        E.record_answer(st, q, _answer_for(q, i < correct_prefix), 12.0)
    return st


def _store_attempt(uid, kind, seed, st, started, finished):
    """Write a completed attempt and return (attempt_id, result)."""
    res = E.score(st["answers"])
    trust = E.trust_label({"paste": 0, "blur": 0}, st["answers"])
    res.update({"trust": trust["label"], "trust_points": trust["points"],
                "trust_rule_version": trust["rule_version"],
                "trust_reasons": trust["reasons"], "trust_hard_flags": trust["hard_flags"],
                "percentile": None, "kind": kind, "date": time.strftime("%Y-%m-%d")})
    aid = uuid.uuid4().hex[:12]
    run("INSERT INTO attempts(id,user_id,kind,seed,status,state,started,finished,result,appeal) "
        "VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        (aid, uid, kind, seed, "done", json.dumps(st), started, finished, json.dumps(res)))
    return aid, res


def seed():
    init_db()
    if one("SELECT id FROM users WHERE email=?", (DEMO_EMAIL,)):
        print("[seed] demo user already exists; nothing to do")
        return

    salt = secrets.token_hex(16)
    uid = run("INSERT INTO users(email,name,salt,pw_hash,created) VALUES (?,?,?,?,?)",
              (DEMO_EMAIL, DEMO_NAME, salt, hash_pw(DEMO_PASSWORD, salt), time.time()))
    run("INSERT INTO consents(user_id,version,lang,ts) VALUES (?,?,?,?)",
        (uid, CONSENT_VERSION, "en", time.time()))
    run("INSERT INTO profiles(user_id,redacted,redaction,extracted,source,confirmed) "
        "VALUES (?,?,?,?,?,?)",
        (uid, REDACTED_CV, REDACTION, EXTRACTED, "rules", CONFIRMED))

    now = time.time()
    seen = set()

    # Pre assessment: weaker (first 4 questions correct).
    st_pre = _simulate(12345, seen, correct_prefix=4)
    seen.update(st_pre["used"])
    _, pre_res = _store_attempt(uid, "pre", 12345, st_pre, now - 3600, now - 3600)

    # Post assessment: stronger (9 of 10 correct) -> visible uplift.
    st_post = _simulate(54321, seen, correct_prefix=9)
    _, post_res = _store_attempt(uid, "post", 54321, st_post, now, now)

    # Skill Passport built from the post result.
    skills = [{"skill": t, "name": E.TAXONOMY[t]["name"], "score": post_res["topics"][t]["score"],
               "level": post_res["topics"][t]["level"], "trust": post_res["trust"], "date": post_res["date"]}
              for t in ("sql", "python", "statistics", "visualization") if t in post_res["topics"]]
    pid = secrets.token_urlsafe(9)
    payload = {"pid": pid, "issued": time.strftime("%Y-%m-%d"),
               "expires": time.strftime("%Y-%m-%d", time.localtime(time.time() + 365 * 86400)),
               "holder": DEMO_NAME, "skills": skills,
               "uplift": {"pre": pre_res["overall"], "post": post_res["overall"],
                          "delta": post_res["overall"] - pre_res["overall"]},
               "issuer": "SkillBridge Global R0 prototype"}
    run("INSERT INTO passports(id,user_id,payload,signature,created) VALUES (?,?,?,?,?)",
        (pid, uid, json.dumps(payload), E.sign_payload(payload, SECRET), time.time()))

    print(f"[seed] created demo user  {DEMO_EMAIL} / {DEMO_PASSWORD}  (uid={uid}, passport={pid})")
    print(f"[seed] pre overall={pre_res['overall']}%  post overall={post_res['overall']}%")


if __name__ == "__main__":
    seed()
