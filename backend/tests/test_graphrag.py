import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphrag import (
    CVSkill, FUNDAMENTAL_DIFFICULTY_MATRIX, FUNDAMENTAL_TARGETS,
    build_assessment, extract_cv_skills, question_page,
)


def question(qid, category, subcategory, prompt, difficulty, qtype="MCQ", seconds=60):
    return {
        "id": qid, "category_id": category, "subcategory": subcategory,
        "question_type": qtype, "difficulty_level": difficulty,
        "question_text": prompt, "options": ["A", "B", "C", "D"],
        "correct_answer": "A", "explanation": "Test explanation",
        "reference_answer": "", "grading_rubric": "", "marks": 1,
        "time_allocation": seconds, "status": "active", "version": 1,
    }


def complete_bank():
    categories = {
        "DSA": ("CAT-02", "Data Structures", "algorithm data structure"),
        "OOP": ("CAT-05", "OOP Concepts", "object oriented programming inheritance"),
        "Networking": ("CAT-08", "Computer Networks", "network TCP DNS"),
        "Coding": ("CAT-03", "General Programming", "implement code function"),
        "Database": ("CAT-01", "Database SQL", "database SQL query"),
        "Critical Thinking": ("CAT-06", "Scenario-Based", "scenario decision troubleshooting"),
    }
    rows = []
    n = 0
    special_formats = {
        "DSA": {"Easy": "Coding"}, "OOP": {"Easy": "Explanation"},
        "Networking": {"Medium": "Explanation"}, "Coding": {"Easy": "Coding"},
        "Database": {"Easy": "SQL"}, "Critical Thinking": {"Medium": "Scenario-Based"},
    }
    for topic, counts in FUNDAMENTAL_DIFFICULTY_MATRIX.items():
        category, subcategory, prompt = categories[topic]
        for difficulty, count in counts.items():
            for index in range(count):
                n += 1
                qtype = special_formats.get(topic, {}).get(difficulty, "MCQ")
                if topic == "DSA" and index > 0: qtype = "MCQ"
                rows.append(question(f"fund-{n}", category, subcategory, f"{prompt}: explain case {n}", difficulty, qtype))
    cv_formats = {"Easy": ["Coding"], "Medium": ["Scenario-Based", "MCQ"], "Hard": ["Explanation", "Coding"]}
    for difficulty, count in {"Easy": 1, "Medium": 2, "Hard": 2}.items():
        for i in range(count):
            n += 1
            rows.append(question(f"py-{n}", "CAT-03", "Python", f"Python practical problem {i}", difficulty, cv_formats[difficulty][i], 90))
    return rows


def test_fundamental_and_cv_quotas_are_exact_and_non_overlapping():
    result = build_assessment(complete_bank(), [CVSkill(skill_id="python", label="Python")], retrieval=False)
    assert result["status"] == "ready", result["shortages"]
    assert result["fundamental_count"] == 25
    assert result["cv_count"] == 5
    assert result["total_questions"] == 30
    assert len({q["id"] for q in result["questions"]}) == 30
    assert result["estimated_duration_seconds"] == 25 * 60 + 5 * 90
    for topic, count in FUNDAMENTAL_TARGETS.items():
        assert sum(q["assessment_topic"] == topic for q in result["fundamental"]) == count
    for difficulty, count in {"Easy": 10, "Medium": 8, "Hard": 7}.items():
        assert sum(q["difficulty_level"] == difficulty for q in result["fundamental"]) == count
    assert [sum(q["difficulty_level"] == d for q in result["cv_based"]) for d in ("Easy", "Medium", "Hard")] == [1, 2, 2]
    assert {"scenario", "explanation", "sql", "coding"} <= set(result["fundamental_formats"])
    assert {"coding", "scenario"} <= set(result["cv_format_coverage"]["python"])


def test_incomplete_bank_fails_closed_with_shortage_details():
    result = build_assessment(complete_bank()[:7], [CVSkill(skill_id="python", label="Python")], retrieval=False)
    assert result["status"] == "question_bank_incomplete"
    assert result["total_questions"] < 30
    assert result["shortages"]


def test_cv_skill_extraction_only_returns_explicit_stack_mentions():
    skills = extract_cv_skills("Built REST APIs using Python, FastAPI, PostgreSQL and Docker.")
    ids = {s.skill_id for s in skills}
    assert {"python", "sql", "docker"} <= ids


def test_question_page_limits_to_five_and_keeps_answers_private():
    result = build_assessment(complete_bank(), [CVSkill(skill_id="python", label="Python")], retrieval=False)
    page = question_page(result["questions"], 1, {"fund-1": "answer"})
    assert page["page_size"] == 5
    assert len(page["questions"]) == 5
    assert "correct_answer" not in page["questions"][0]
    assert page["has_previous"] is True


def test_synthetic_seed_builds_complete_45_question_assessment():
    import seed_graphrag_questions as seed

    rows = []
    for index, item in enumerate(seed.FUNDAMENTALS, 1):
        topic, category, difficulty, qtype, prompt, answer, options, explanation, reference, rubric, seconds = item
        rows.append({
            "id": f"demo-gr-f-{index:02d}", "category_id": category, "subcategory": topic,
            "difficulty_level": difficulty, "question_type": qtype, "question_text": prompt,
            "correct_answer": answer, "options": options, "explanation": explanation,
            "reference_answer": reference, "grading_rubric": rubric, "marks": 1,
            "time_allocation": seconds, "status": "active", "version": 1,
        })
    for index, item in enumerate(seed.CV_QUESTIONS, 1):
        skill, difficulty, qtype, prompt, answer, options, explanation, reference, rubric, seconds = item
        rows.append({
            "id": f"demo-gr-cv-{index:02d}", "category_id": "CAT-10", "subcategory": skill,
            "difficulty_level": difficulty, "question_type": qtype, "question_text": prompt,
            "correct_answer": answer, "options": options, "explanation": explanation,
            "reference_answer": reference, "grading_rubric": rubric, "marks": 1,
            "time_allocation": seconds, "status": "active", "version": 1,
        })

    skills = [
        CVSkill(skill_id="python", label="Python"),
        CVSkill(skill_id="git", label="Git"),
        CVSkill(skill_id="ai_ml", label="AI & Machine Learning"),
        CVSkill(skill_id="sql", label="SQL"),
    ]
    result = build_assessment(rows, skills, retrieval=False)
    assert result["status"] == "ready", result["shortages"]
    assert result["fundamental_count"] == 25
    assert result["cv_count"] == 20
    assert result["total_questions"] == 45
    assert len({q["id"] for q in result["questions"]}) == 45
    assert result["fundamental_formats"] and {"scenario", "explanation", "sql", "coding"} <= set(result["fundamental_formats"])
    assert set(result["cv_format_coverage"]) == {"python", "git", "ai_ml", "sql"}
    for skill_id, formats in result["cv_format_coverage"].items():
        assert {"coding", "sql"} & set(formats), (skill_id, formats)
        assert {"scenario", "explanation"} & set(formats), (skill_id, formats)
    for skill in skills:
        skill_questions = [q for q in result["cv_based"] if q["skill_id"] == skill.skill_id]
        assert len(skill_questions) == 5
        assert [sum(q["difficulty_level"] == d for q in skill_questions) for d in ("Easy", "Medium", "Hard")] == [1, 2, 2]
    assert result["estimated_duration_seconds"] == sum(q["time_allocation"] for q in rows)
