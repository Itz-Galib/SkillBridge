import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine import *
from content import *

def test_redaction():
    cv = "Rafi Ahmed\nEmail: rafi@example.com  Phone +880 1712-345678\nHouse 12, Road 5, Dhaka\nSkills: SQL, Python\nhttps://github.com/rafi"
    out, c = redact(cv, ["Rafi Ahmed"])
    assert "rafi@example.com" not in out and "1712" not in out and "github.com" not in out and "Rafi" not in out
    assert "SQL" in out and c["email"] == 1 and c["phone"] == 1

def test_extract():
    ids = {s["id"] for s in rule_extract("Built dashboards in Tableau; MySQL joins; pandas; mysql")}
    assert {"sql", "python", "visualization"} <= ids and "excel" not in ids

def test_sql_sandbox():
    assert run_query(1, "DROP TABLE customers")["error"]
    assert run_query(1, "SELECT 1; DELETE FROM orders")["error"]
    assert run_query(1, "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r")["error"]  # infinite loop must be stopped
    assert run_query(1, "SELECT COUNT(*) FROM customers")["rows"] == [[20]]

def test_reference_queries_grade_true_and_wrong_false():
    for q in QUESTIONS:
        if q["type"] == "sql":
            assert grade_sql(5, q, q["ref"]), q["id"]
            assert not grade_sql(5, q, "SELECT 0"), q["id"]
    q = QBYID["sql-p1"]
    assert not grade_sql(5, q, f"SELECT {run_query(5, q['ref'])['rows'][0][0]}")  # hard-coded answer fails on 2nd dataset

def test_full_attempt_and_adaptivity():
    st = new_state(42); seen = set()
    while st["index"] < len(SLOTS):
        q = pick_question(st, seen)
        ans = q["answer"] if q["type"] == "mcq" else q["ref"]
        assert record_answer(st, q, ans, 10)
    r = score(st["answers"])
    assert r["overall"] == 100 and r["level"] == "Advanced" and set(r["topics"]) == set(TESTABLE)
    assert len(set(st["used"])) == len(SLOTS)
    # retest avoids previously seen questions
    st2 = new_state(43); seen = set(st["used"]); used2 = []
    while st2["index"] < len(SLOTS):
        q = pick_question(st2, seen); used2.append(q["id"]); record_answer(st2, q, 0 if q["type"] == "mcq" else "SELECT 0", 10)
    assert not (set(used2) & seen), "retest should use unseen items while pool allows"
    assert score(st2["answers"])["overall"] < 100

def test_trust_never_fails():
    result = trust_label({"paste": 5, "blur": 9}, [])
    assert result["label"] == "Needs review" and result["points"] > 0
    assert result["rule_version"] == "phase1-v1"
    assert trust_label({}, [])["label"] == "High trust"


def test_integrity_incidents_are_grouped_and_advisory():
    events = [
        {"type": "tab_hidden", "server_ts": 100},
        {"type": "blur", "server_ts": 101},
        {"type": "fullscreen_exit", "server_ts": 102},
        {"type": "tab_visible", "server_ts": 221},
    ]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    assert result["label"] == "Medium"
    assert result["reasons"][0]["key"] == "leave_window"
    assert result["reasons"][0]["duration_seconds"] == 121
    assert not result["hard_flags"]


def test_integrity_clipboard_events_are_capped():
    events = [{"type": "paste", "server_ts": i} for i in range(10)]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False,
        "weights": {"external_clipboard": 2}, "caps": {"external_clipboard": 3}})
    assert result["points"] == 6
    assert result["reasons"] == [{"key": "external_clipboard", "n": 3, "points": 6}]


def test_integrity_hard_flag_requires_human_review():
    result = trust_label([{"type": "identity_mismatch", "server_ts": 1, "hard_flag": "identity_mismatch"}], [])
    assert result["label"] == "Needs review"
    assert result["hard_flags"] == ["identity_mismatch"]


def test_integrity_fast_answer_fallback():
    answer = {"qid": "q1", "type": "mcq", "correct": True, "difficulty": 2, "seconds": 2}
    result = trust_label([], [answer], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    assert result["points"] == 1
    assert result["reasons"][0]["key"] == "fast"


def test_technical_score_is_independent_of_integrity_events():
    answers = [{"qid": "q", "topic": "sql", "difficulty": 2, "type": "mcq", "correct": True, "seconds": 1}]
    before = score(answers)
    trust_label([{"type": "paste", "server_ts": 1}], answers)
    assert score(answers) == before

def test_path_honest():
    p = build_path({"sql": {"score": 80}, "python": {"score": 20}, "statistics": {"score": 80}, "visualization": {"score": 80}})
    assert p["gaps"] == ["python"] and all(i["url"].startswith("https://") and i["licence"] for i in p["items"])
    assert build_path({k: {"score": 99} for k in TESTABLE})["items"] == []

def test_sign():
    assert sign_payload({"a": 1}, "k") == sign_payload({"a": 1}, "k") != sign_payload({"a": 2}, "k")
