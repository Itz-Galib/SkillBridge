import os
import sys
import io
import sqlite3
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_TMP = tempfile.mkdtemp(prefix="sb_clean_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["STORAGE_LOCAL_DIR"] = os.path.join(_TMP, "cv_files")
os.environ["SEED_DEMO"] = "false"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("STORAGE_BACKEND", None)

import ai_providers
import main
from main import (
    _assign_project_links,
    _merge_profiles,
    clean_profile,
    extract_profile_fields,
    profile_completeness,
    read_profile_cv,
)


FIXTURE_CV = """Rafi Ahmed
Email: rafi.cv@example.com | Phone: +880 1712-345678
https://github.com/rafi-ahmed
https://linkedin.com/in/rafi-ahmed

Education
B.Sc. in Computer Science and Engineering
North University

Work Experience
Software Engineer Intern
Skyscanner (Forage) - On Going
November 2026 - Present
Built APIs and web applications.

Projects
Job Aggregator & Expiration Tracking Platform
● Built a searchable jobs platform.
Tech Stack
React, FastAPI, PostgreSQL
Deen Essentials - E-Commerce Web Platform
• Product catalog and checkout platform.
Way to Deen - API-Driven Application
Platforms.

Skills
Git, Git & GitHub, JS, JavaScript (ES6+), React, React.js, Python, SQL
"""


def _ai_profile():
    profile = ai_providers.empty_profile()
    profile.update({
        "email": "account@example.com",
        "education": [
            {"institution": "North University", "degree": "BSc"},
        ],
        "experience": [
            {
                "company": "Skyscanner (Forage) - On Going",
                "title": "Software Engineer Intern",
                "start_date": "November 2026 - Present",
                "current": False,
            },
        ],
        "projects": [
            {"name": "Job Aggregator & Expiration Tracking Platform"},
            {"name": "Deen Essentials - E-Commerce Web Platform"},
            {"name": "Way to Deen - API-Driven Application"},
        ],
        "skills": [
            {"name": "Git", "category": "Technical"},
            {"name": "Git & GitHub", "category": "Tool"},
            {"name": "JS", "category": "Technical"},
            {"name": "JavaScript (ES6+)", "category": "Learning"},
            {"name": "React", "category": "Technical"},
            {"name": "React.js", "category": "Other"},
            {"name": "Python", "category": "Technical"},
            {"name": "SQL", "category": "Technical"},
        ],
    })
    return profile


def test_ai_lists_replace_regex_junk_and_clean_duplicate_skills_and_dates():
    offline, _ = extract_profile_fields(FIXTURE_CV, {"email": "account@example.com"})
    offline["education"].extend([
        {"institution": "North University", "degree": "BSc"},
        {"institution": "Loose line without a school", "degree": "BSc"},
    ])
    offline["experience"].append({
        "company": "Skyscanner (Forage)",
        "title": "Software Engineer Intern",
    })
    offline["projects"].extend([
        {"name": "• bullet junk"},
        {"name": "Tech Stack"},
        {"name": "[Page 2]"},
        {"name": "platforms."},
    ])

    profile = _merge_profiles(_ai_profile(), offline)

    assert len(profile["education"]) == 1
    assert profile["education"][0]["institution"] == "North University"
    assert len(profile["experience"]) == 1
    experience = profile["experience"][0]
    assert experience["company"] == "Skyscanner (Forage)"
    assert experience["current"] is True
    assert experience["duration_months"] >= 1
    assert [item["name"] for item in profile["projects"]] == [
        "Job Aggregator & Expiration Tracking Platform",
        "Deen Essentials - E-Commerce Web Platform",
        "Way to Deen - API-Driven Application",
    ]
    assert not any(
        item["name"].startswith(("•", "-", "*")) or "Tech Stack" in item["name"]
        for item in profile["projects"]
    )
    skill_names = [item["name"] for item in profile["skills"]]
    assert skill_names.count("Git & GitHub") == 1
    assert skill_names.count("JavaScript (ES6+)") == 1
    assert skill_names.count("React.js") == 1
    assert all(item["status"] == "claimed" for item in profile["skills"])
    assert all(item["category"] in {
        "Language", "Frontend", "Backend", "Database", "Tool", "Other",
    } for item in profile["skills"])


def test_offline_parser_also_drops_bullets_and_heading_noise_and_preserves_cv_email():
    offline, _ = extract_profile_fields(FIXTURE_CV, {"email": "account@example.com"})
    profile = clean_profile(offline)

    assert profile["email"] == "rafi.cv@example.com"
    assert len(profile["education"]) == 1
    assert not any(
        item["name"].startswith(("●", "•", "-", "*"))
        or "[Page" in item["name"]
        or "Tech Stack" in item["name"]
        or item["name"].casefold() == "platforms."
        for item in profile["projects"]
    )
    assert all(item["status"] == "claimed" for item in profile["skills"])


def test_document_links_are_normalized_and_project_links_match_repo_names():
    profile = clean_profile(_ai_profile())
    profile = _assign_project_links(profile, [
        "github.com/rafi-ahmed",
        "linkedin.com/in/rafi-ahmed",
        "github.com/rafi-ahmed/job-aggregator-expiration-tracking",
    ])

    assert profile["github"] == "https://github.com/rafi-ahmed"
    assert profile["linkedin"] == "https://linkedin.com/in/rafi-ahmed"
    assert profile["projects"][0]["url"] == (
        "https://github.com/rafi-ahmed/job-aggregator-expiration-tracking"
    )


def test_docx_relationship_links_are_added_to_extraction_text():
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Software developer with several years of project experience.</w:t></w:r></w:p></w:body>
    </w:document>"""
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="hyperlink" Target="https://github.com/rafi/job-aggregator" TargetMode="External"/>
    </Relationships>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)

    text, parser = read_profile_cv("resume.docx", buffer.getvalue())

    assert parser["links_found"] == ["https://github.com/rafi/job-aggregator"]
    assert "LINKS FOUND IN DOCUMENT" in text
    assert "https://github.com/rafi/job-aggregator" in text


def test_completeness_is_weighted_and_optional_references_do_not_change_total():
    profile = ai_providers.empty_profile()
    profile["name"] = "Rafi Ahmed"
    profile["skills"] = [{"name": "Python", "category": "Language", "status": "claimed"}]

    empty_refs = profile_completeness(profile)
    profile["references"] = [{"name": "Reference", "relationship": "Manager"}]
    with_refs = profile_completeness(profile)

    assert empty_refs["total"] == with_refs["total"]
    assert empty_refs["sections"]["References"] == 0
    assert with_refs["sections"]["References"] == 100
    assert empty_refs["sections"]["Key Skills"] == 100

    complete_profile = {
        "name": "Rafi Ahmed", "email": "rafi@example.test", "phone": "+12345678",
        "location": "Dhaka", "headline": "Software Engineer", "summary": "Builds software.",
        "experience": [{"company": "Example", "title": "Engineer"}],
        "education": [{"institution": "Example University", "degree": "BSc"}],
        "skills": [{"name": "Python"}],
        "preferences": {"job_types": ["Full-time"], "work_mode": "Remote", "locations": ["Dhaka"]},
        "certifications": [{"name": "Certificate"}],
        "projects": [{"name": "Project", "url": "https://example.test"}],
        "languages": [{"name": "English"}],
        "references": [],
        "achievements": [],
        "volunteer": [],
        "linkedin": "https://linkedin.com/in/rafi",
        "github": "https://github.com/rafi",
        "portfolio": "https://example.test",
    }
    assert profile_completeness(complete_profile)["total"] == 100


def test_existing_profile_table_migrates_cv_email_and_optional_sections(tmp_path, monkeypatch):
    database_path = tmp_path / "existing.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """CREATE TABLE candidate_profiles(
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            headline TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            linkedin TEXT NOT NULL DEFAULT '',
            github TEXT NOT NULL DEFAULT '',
            portfolio TEXT NOT NULL DEFAULT '',
            education TEXT NOT NULL DEFAULT '[]',
            experience TEXT NOT NULL DEFAULT '[]',
            projects TEXT NOT NULL DEFAULT '[]',
            skills TEXT NOT NULL DEFAULT '[]',
            certifications TEXT NOT NULL DEFAULT '[]',
            achievements TEXT NOT NULL DEFAULT '[]',
            languages TEXT NOT NULL DEFAULT '[]',
            preferences TEXT NOT NULL DEFAULT '{}',
            updated REAL NOT NULL
        )"""
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(main, "DB_PATH", str(database_path))
    main.init_db()
    columns = {row["name"] for row in main.allr("PRAGMA table_info(candidate_profiles)")}

    assert {"email", "profile_references", "volunteer"} <= columns
