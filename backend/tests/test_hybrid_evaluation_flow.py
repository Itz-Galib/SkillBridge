"""End-to-end checks for the hybrid descriptive evaluation -> admin approval loop."""
import os, sys, tempfile, json

_TMP = tempfile.mkdtemp(prefix="sb_hybrid_")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "test.db"))
os.environ["SEED_DEMO"] = "false"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)


def _candidate(email):
    r = client.post("/api/auth/register", json={"email": email, "name": "Hybrid Candidate",
                                                "password": "password123"})
    token = r.json().get("token") or client.post(
        "/api/auth/login", json={"email": email, "password": "password123"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.post("/api/consent", json={"lang": "en"}, headers=headers).status_code == 200
    uid = main.one("SELECT id FROM users WHERE email=?", (email,))["id"]
    return uid, headers


def _admin(email):
    r = client.post("/api/auth/register", json={"email": email, "name": "Hybrid Admin",
                                                "password": "password123", "role": "candidate"})
    token = r.json().get("token") or client.post(
        "/api/auth/login", json={"email": email, "password": "password123"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    uid = main.one("SELECT id FROM users WHERE email=?", (email,))["id"]
    main.run("UPDATE user_roles SET role='admin' WHERE user_id=?", (uid,))
    main.run("INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?) "
             "ON CONFLICT(user_id) DO UPDATE SET admin_role=excluded.admin_role",
             (uid, "super_admin", uid, 0))
    return uid, headers


def _seed_attempt(uid, aid):
    selected = [
        {"id": aid + "-mcq", "assessment_section": "fundamental", "assessment_topic": "DSA",
         "assessment_topic_key": "dsa", "question_type": "MCQ", "difficulty_level": "Easy",
         "question_text": "Which structure is LIFO?", "correct_answer": "Stack",
         "marks": 1, "time_allocation": 60},
        {"id": aid + "-desc", "assessment_section": "fundamental", "assessment_topic": "DSA",
         "assessment_topic_key": "dsa", "question_type": "Explanation", "difficulty_level": "Medium",
         "question_text": "Explain how a balanced binary search tree keeps search efficient.",
         "correct_answer": ("A balanced binary search tree keeps height O(log n) using rotations, "
                            "so search is O(log n); a skewed tree can become O(n)."),
         "reference_answer": "Mention rotations and the O(log n) vs O(n) worst-case contrast.",
         "grading_rubric": "Award marks for the balancing invariant and complexity contrast.",
         "marks": 2, "time_allocation": 120},
    ]
    state = {"graphrag": True, "blueprint_id": "hybrid-blueprint",
             "selected_questions": selected, "question_index": 0,
             "answers": {aid + "-mcq": "Stack",
                         aid + "-desc": "Balanced trees use rotations to keep height logarithmic, so search is O(log n). A skewed tree can degrade to O(n)."},
             "duration_seconds": 600, "question_time_seconds": {}, "seed": 1, "current": None}
    main.run("""INSERT INTO attempts(id,user_id,kind,seed,status,state,started,result,appeal)
                VALUES (?,?,?,?,?,?,?,NULL,NULL)""",
             (aid, uid, "pre", 1, "active", json.dumps(state), 0))
    main.finalize(dict(main.one("SELECT * FROM attempts WHERE id=?", (aid,))), state)


def test_descriptive_answer_waits_for_admin_then_changes_score():
    uid, headers = _candidate("hybrid-candidate@example.com")
    admin_uid, admin_headers = _admin("hybrid-admin@example.com")
    aid = "hybrid-attempt"
    main.run("DELETE FROM answer_evaluations WHERE attempt_id=?", (aid,))
    main.run("DELETE FROM attempts WHERE id=?", (aid,))
    _seed_attempt(uid, aid)

    result = json.loads(main.one("SELECT result FROM attempts WHERE id=?", (aid,))["result"])
    assert result["evaluation_summary"]["pending_admin_approval"] == 1
    subjective = next(q for q in result["question_results"] if q["question_id"] == aid + "-desc")
    assert subjective["awarded_marks"] is None
    assert subjective["objective"] is False
    # The MCQ is 1/1; the unapproved descriptive answer is excluded from the score.
    assert result["section_scores"]["fundamental"]["possible"] == 1
    assert result["overall"] == 100

    review = client.get("/api/admin/assessment-reviews", headers=admin_headers).json()
    item = next(x for x in review["items"] if x["id"] == aid)
    assert item["evaluations"][0]["status"] == "pending_approval"
    assert item["evaluations"][0]["ai_marks"] is not None

    run = client.post("/api/admin/attempts/" + aid + "/evaluate", json={}, headers=admin_headers)
    assert run.status_code == 200, run.text
    assert run.json()["evaluated"] == 1

    edit = client.patch("/api/admin/attempts/" + aid + "/evaluations/" + aid + "-desc",
                        json={"question_id": aid + "-desc", "marks": 1.5,
                              "admin_note": "Correct reasoning, concise.",
                              "additional_info": "Reviewer verified the complexity contrast.",
                              "status": "approved"}, headers=admin_headers)
    assert edit.status_code == 200, edit.text
    assert edit.json()["final_marks"] == 1.5
    assert edit.json()["status"] == "approved"

    too_many = client.patch("/api/admin/attempts/" + aid + "/evaluations/" + aid + "-desc",
                            json={"question_id": aid + "-desc", "marks": 9,
                                  "status": "approved"}, headers=admin_headers)
    assert too_many.status_code == 422

    approve = client.post("/api/admin/attempts/" + aid + "/review",
                          json={"decision": "approved", "reason": "Human review completed.",
                                "evaluations": [{"question_id": aid + "-desc", "marks": 1.5,
                                                 "admin_note": "Partial credit awarded."}]},
                          headers=admin_headers)
    assert approve.status_code == 200, approve.text
    result = json.loads(main.one("SELECT result FROM attempts WHERE id=?", (aid,))["result"])
    assert result["topics"]["dsa"]["n"] == 2
    assert result["section_scores"]["fundamental"]["possible"] == 3
    assert result["overall"] == 83  # (1 + 1.5) / 3
    candidate_view = client.get("/api/attempts/" + aid + "/result", headers=headers).json()
    subjective = next(q for q in candidate_view["question_results"] if q["question_id"] == aid + "-desc")
    assert subjective["awarded_marks"] == 1.5
    assert "ai_marks" not in subjective
