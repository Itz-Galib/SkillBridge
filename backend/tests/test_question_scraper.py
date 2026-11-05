import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import question_scraper as Q


def test_numbered_mcq_list():
    text = (
        "Top SQL Questions\n"
        "1. What does SQL stand for?\n"
        "A) Structured Query Language\n"
        "B) Simple Query Language\n"
        "C) Structured Question Language\n"
        "D) None of the above\n"
        "Answer: A\n"
        "2. Which command truncates a table?\n"
        "A) DELETE\nB) TRUNCATE\nC) DROP\nD) REMOVE\n"
        "Correct Answer: B\n"
    )
    records = Q._extract_from_text(text)
    assert len(records) == 2
    first, second = records
    assert first["question_text"].startswith("What does SQL stand for?")
    assert first["options"] == ["Structured Query Language", "Simple Query Language",
                                "Structured Question Language", "None of the above"]
    assert first["correct_answer"] == "Structured Query Language"
    assert second["correct_answer"] == "TRUNCATE"


def test_inline_options_not_confused_by_articles():
    text = (
        "3. Which of the following is a NoSQL database? A) MySQL B) MongoDB C) PostgreSQL D) SQLite\n"
        "Answer: B"
    )
    records = Q._extract_from_text(text)
    assert len(records) == 1
    rec = records[0]
    assert rec["question_text"] == "Which of the following is a NoSQL database?"
    assert rec["options"] == ["MySQL", "MongoDB", "PostgreSQL", "SQLite"]
    assert rec["correct_answer"] == "MongoDB"


def test_jsonld_quiz_schema():
    quiz = [{"@context": "https://schema.org", "@type": "Quiz",
             "hasPart": [{"@type": "Question", "name": "What is 2+2?",
                          "suggestedAnswer": [{"@type": "Answer", "text": "3"},
                                              {"@type": "Answer", "text": "4"}],
                          "acceptedAnswer": {"@type": "Answer", "text": "4"}}]}]
    records = Q._jsonld_questions(quiz)
    assert len(records) == 1
    assert records[0]["question_text"] == "What is 2+2?"
    assert records[0]["options"] == ["3", "4"]
    assert records[0]["correct_answer"] == "4"


def test_jsonld_faq_schema():
    faq = [{"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": "What is SQL?",
                            "acceptedAnswer": {"@type": "Answer", "text": "A query language."}}]}]
    records = Q._jsonld_questions(faq)
    assert len(records) == 1
    assert records[0]["correct_answer"] == "A query language."


def test_leading_header_is_dropped():
    text = "Interview Questions\n1. Explain ACID.\nA) Atomicity\nB) Consistency\nAnswer: A"
    records = Q._extract_from_text(text)
    assert records and records[0]["question_text"].startswith("Explain ACID.")
