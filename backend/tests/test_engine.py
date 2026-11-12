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
    assert result["rule_version"] == "phase2-v1"
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


def test_trust_integrity_events_are_advisory_and_explainable():
    events = {"copy": 1, "cut": 1, "paste": 1, "tab_hidden": 2, "fullscreen_exit": 1, "screen_share_stopped": 1}
    label = trust_label(events, [])
    assert label["label"] == "Needs review"
    assert label["points"] >= 7
    assert label["rule_version"] == "phase2-v1"
    assert trust_label({}, [])["label"] == "High trust"

def test_path_honest():
    p = build_path({"sql": {"score": 80}, "python": {"score": 20}, "statistics": {"score": 80}, "visualization": {"score": 80}})
    assert p["gaps"] == ["python"] and all(i["url"].startswith("https://") and i["licence"] for i in p["items"])
    assert build_path({k: {"score": 99} for k in TESTABLE})["items"] == []


def test_pending_review_scores_do_not_crash_gap_or_learning_path():
    # GraphRAG topic scores can be None when every selected question is waiting
    # for human review. The gap map and learning path must treat that as
    # unverified rather than comparing None with a threshold.
    topics = {
        "sql": {"score": None, "level": "Beginner", "n": 3},
        "python": {"score": 70, "level": "Advanced", "n": 2},
    }
    rows = gap_map(topics)
    assert {row["skill"]: row["status"] for row in rows}["sql"] == "unverified"
    assert {row["skill"]: row["status"] for row in rows}["python"] == "met"
    path = build_path(topics)
    assert "sql" in path["gaps"]

def test_sign():
    assert sign_payload({"a": 1}, "k") == sign_payload({"a": 1}, "k") != sign_payload({"a": 2}, "k")


def test_trust_caps_external_clipboard_events():
    events = [{"type": "paste", "server_ts": i, "external": True} for i in range(10)]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False,
        "weights": {"external_clipboard": 2}, "caps": {"external_clipboard": 3}})
    assert result["points"] == 6
    assert result["reasons"] == [{"key": "external_clipboard", "n": 3, "points": 6}]

def test_trust_groups_tab_blur_fullscreen_into_one_incident():
    events = [
        {"type": "tab_hidden", "server_ts": 100},
        {"type": "blur", "server_ts": 101},
        {"type": "fullscreen_exit", "server_ts": 102},
    ]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    assert [r["key"] for r in result["reasons"]] == ["leave_window"]
    assert result["points"] == 1

def test_trust_hard_flags_require_review():
    result = trust_label([{"type": "identity_mismatch", "server_ts": 1, "hard_flag": "identity_mismatch"}], [])
    assert result["label"] == "Needs review"
    assert result["hard_flags"] == ["identity_mismatch"]

def test_trust_heartbeat_gap_duration_and_caps():
    events = [{"type": "heartbeat_gap", "server_ts": i} for i in range(5)]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    assert result["points"] == 6
    assert result["reasons"] == [{"key": "heartbeat_gap", "n": 3, "points": 6}]

def test_trust_fast_answer_fallback_and_percentile():
    answer = {"qid": "q1", "type": "mcq", "correct": True, "difficulty": 2, "seconds": 2}
    fallback = trust_label([], [answer], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    assert fallback["points"] == 1
    slow_prior = trust_label([], [answer], {"rule_version": "test-v1", "normalize_per_30_minutes": False},
                             question_times={"q1": [4] * 30})
    assert slow_prior["points"] == 1
    fast_prior = trust_label([], [answer], {"rule_version": "test-v1", "normalize_per_30_minutes": False},
                             question_times={"q1": [1] * 30})
    assert fast_prior["points"] == 0

def test_technical_score_is_independent_of_trust_result():
    answers = [{"qid": "q", "topic": "sql", "difficulty": 2, "type": "mcq", "correct": True, "seconds": 1}]
    before = score(answers)
    trust_label([{"type": "paste", "server_ts": 1}], answers)
    after = score(answers)
    assert before == after


def test_trust_leave_window_duration_points():
    events = [
        {"type": "tab_hidden", "server_ts": 100},
        {"type": "blur", "server_ts": 101},
        {"type": "fullscreen_exit", "server_ts": 102},
        {"type": "tab_visible", "server_ts": 221},
    ]
    result = trust_label(events, [], {"rule_version": "test-v1", "normalize_per_30_minutes": False})
    leave = next(reason for reason in result["reasons"] if reason["key"] == "leave_window")
    assert leave["duration_seconds"] == 121
    assert leave["points"] == 3
    assert len([r for r in result["reasons"] if r["key"] == "fullscreen_exit"]) == 0


def test_ai_proctor_signals_are_capped_advisory_and_never_hard_flags():
    events = (
        [{"type": "ai_gaze_away", "server_ts": i, "metadata": {"confidence": 0.9}} for i in range(8)]
        + [{"type": "ai_multiple_faces", "server_ts": 20, "metadata": {"confidence": 0.8}}]
        + [{"type": "ai_phone_detected", "server_ts": 21, "metadata": {"confidence": 0.7}}]
    )
    result = trust_label(events, [], {"rule_version": "phase2-test", "normalize_per_30_minutes": False})
    reasons = {r["key"]: r for r in result["reasons"]}
    assert reasons["ai_gaze_away"]["n"] == 2
    assert reasons["ai_gaze_away"]["advisory"] is True
    assert reasons["ai_multiple_faces"]["advisory"] is True
    assert reasons["ai_phone_detected"]["advisory"] is True
    assert result["hard_flags"] == []
    assert result["label"] != "Needs review"
