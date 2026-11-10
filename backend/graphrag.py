"""GraphRAG assessment retrieval.

Question content is authored/approved in the existing question_bank. This module only
retrieves and assembles approved questions; it never invents answers or grades candidates.
Meilisearch and Neo4j are optional accelerators. The relational question bank remains the
source of truth and provides a deterministic fallback when either service is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Iterable

from pydantic import BaseModel, Field, ValidationError

from content import TAXONOMY

log = logging.getLogger("skillbridge.graphrag")

# Largest-remainder rounding for 25 questions: 10/8/7 is the closest integer split.
DIFFICULTY_TARGETS = {"Easy": 10, "Medium": 8, "Hard": 7}
FUNDAMENTAL_TARGETS = {
    "DSA": 5, "OOP": 4, "Networking": 4,
    "Coding": 5, "Database": 5, "Critical Thinking": 2,
}
FUNDAMENTAL_TOTAL = 25
# Per-topic quotas sum exactly to 25 and roll up to 10 Easy / 8 Medium / 7 Hard.
FUNDAMENTAL_DIFFICULTY_MATRIX = {
    "DSA": {"Easy": 2, "Medium": 2, "Hard": 1},
    "OOP": {"Easy": 2, "Medium": 1, "Hard": 1},
    "Networking": {"Easy": 2, "Medium": 1, "Hard": 1},
    "Coding": {"Easy": 2, "Medium": 2, "Hard": 1},
    "Database": {"Easy": 2, "Medium": 1, "Hard": 2},
    "Critical Thinking": {"Easy": 0, "Medium": 1, "Hard": 1},
}
PER_SKILL_TARGETS = {"Easy": 1, "Medium": 2, "Hard": 2}
QUESTION_PAGE_SIZE = 5

# The profile, GraphRAG retrieval, skill journey, admin coverage and company
# views all share content.TAXONOMY. GraphRAG therefore never introduces a second
# skill namespace: a skill id confirmed in the profile is always retrievable here.
SKILL_LABELS = {skill_id: spec["name"] for skill_id, spec in TAXONOMY.items()}
SKILL_ALIASES = {
    skill_id: list(dict.fromkeys(
        [alias.strip().lower() for alias in spec.get("aliases", []) if alias.strip()]
        + [skill_id.replace("_", " ")]
    ))
    for skill_id, spec in TAXONOMY.items()
}
# Extra retrieval phrases that are useful for question-bank matching but too
# generic to auto-claim a skill from free-text CV/profile content.
SKILL_INDEX_EXTRAS = {
    "python": ["python programming", "pandas dataframe"],
    "sql": ["sql query", "relational database"],
    "javascript": ["frontend", "front-end", "web development"],
    "ai_ml": ["model training", "neural network"],
    "visualization": ["chart", "charts", "reporting"],
}
# Extra phrases are used only for indexing/retrieval metadata; they never claim a
# profile skill by themselves. Profile/CV extraction uses SKILL_ALIASES above.
SKILL_INDEX_ALIASES = {
    skill_id: list(dict.fromkeys(SKILL_ALIASES[skill_id] + SKILL_INDEX_EXTRAS.get(skill_id, [])))
    for skill_id in SKILL_ALIASES
}

# Fundamental topics are deliberately assessed in their own section. When the
# same ids are profile skills we do not duplicate them in the CV pool.
FUNDAMENTAL_SKILL_IDS = {"dsa", "oop", "networking"}

FUNDAMENTAL_CATEGORY_MAP = {
    "DSA": {"category_ids": {"CAT-02"}, "terms": ("dsa", "data structure", "algorithm", "complexity", "array", "tree", "graph", "sorting")},
    "OOP": {"category_ids": {"CAT-05", "CAT-08", "CAT-03"}, "terms": ("oop", "object oriented", "class", "inheritance", "polymorphism", "encapsulation", "abstraction")},
    "Networking": {"category_ids": {"CAT-08"}, "terms": ("network", "tcp", "udp", "dns", "http", "osi", "subnet", "routing")},
    "Coding": {"category_ids": {"CAT-03"}, "terms": ("coding", "programming", "problem solving", "function", "debug", "code", "implementation")},
    "Database": {"category_ids": {"CAT-01", "CAT-08"}, "terms": ("database", "sql", "dbms", "query", "join", "index", "transaction", "normalization")},
    "Critical Thinking": {"category_ids": {"CAT-06", "CAT-07"}, "terms": ("critical thinking", "scenario", "reasoning", "trade-off", "decision", "troubleshoot", "case study")},
}

class CVSkill(BaseModel):
    skill_id: str
    label: str
    evidence: str = Field(default="", max_length=500)
    confidence: float = Field(ge=0, le=1, default=0.8)
    source: str = "cv"

class QuestionRecord(BaseModel):
    id: str
    category_id: str
    subcategory: str = ""
    question_type: str
    difficulty_level: str
    question_text: str
    options: list[str] = Field(default_factory=list)
    correct_answer: str = ""
    explanation: str = ""
    reference_answer: str = ""
    grading_rubric: str = ""
    marks: int = 1
    time_allocation: int = 60
    status: str = "active"
    version: int = 1

class RetrievalTrace(BaseModel):
    question_id: str
    section: str
    topic: str
    difficulty: str
    keyword_rank: int | None = None
    graph_rank: int | None = None
    selected_rank: int
    reasons: list[str] = Field(default_factory=list)

@lru_cache(maxsize=1)
def _spacy_nlp():
    import spacy
    model = os.getenv("SPACY_MODEL", "en_core_web_sm")
    try:
        return spacy.load(model, disable=["ner", "parser", "lemmatizer"])
    except OSError:
        return spacy.blank("en")

def _spacy_terms(text: str) -> set[str]:
    """Use a cached spaCy tokenizer; regex aliases remain the deterministic fallback."""
    try:
        doc = _spacy_nlp()(text[:30000])
        return {part for token in doc for part in _norm(token.text).split() if part}
    except Exception as exc:
        log.info("spaCy unavailable; using alias matcher (%s)", type(exc).__name__)
        return {part for token in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]*", text.lower()) for part in _norm(token).split() if part}

@lru_cache(maxsize=1)
def _meilisearch_client():
    import meilisearch
    url = os.getenv("MEILISEARCH_URL", "").rstrip("/")
    return meilisearch.Client(url, os.getenv("MEILISEARCH_API_KEY", "") or None)

@lru_cache(maxsize=1)
def _meilisearch_index():
    return _meilisearch_client().index(os.getenv("MEILISEARCH_QUESTION_INDEX", "skillbridge_questions"))

@lru_cache(maxsize=1)
def _neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.getenv("NEO4J_URI", ""),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "")))

def _alias_token_ok(alias: str, tokens: set[str]) -> bool:
    """Symbol aliases (C++, C#, .NET, Node.js) are matched by regex only."""
    if any(ch in alias for ch in "+#."):
        return True
    return set(_norm(alias).split()).issubset(tokens)


def extract_cv_skills(redacted_cv: str) -> list[CVSkill]:
    """Extract explicit technology mentions only; no inference from job title or seniority."""
    text = (redacted_cv or "")[:30000]
    tokens = _spacy_terms(text)
    lower = text.lower()
    found: list[CVSkill] = []
    for skill_id, aliases in SKILL_ALIASES.items():
        matches = [alias for alias in aliases if _alias_token_ok(alias, tokens)
                   and re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lower)]
        if not matches:
            continue
        alias = max(matches, key=len)
        pos = lower.find(alias)
        evidence = " ".join(text[max(0, pos-90):min(len(text), pos+len(alias)+110)].split())
        confidence = 0.95 if any(re.search(r"\b" + re.escape(a) + r"\b", lower) for a in matches) else 0.75
        found.append(CVSkill(
            skill_id=skill_id,
            label=SKILL_LABELS.get(skill_id, skill_id.replace("_", " ").title()),
            evidence=evidence,
            confidence=confidence,
        ))
    return found


def _skill_text(value: str) -> str:
    """Lower-case and collapse whitespace but keep technology symbols (+ # .)."""
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def canonical_skill_ids(value: str) -> list[str]:
    """Resolve a free-text profile skill name to canonical taxonomy ids.

    Profile skills are typed by candidates (for example "React", "Power BI",
    "Django" or "Data Structures & Algorithms"). Every downstream surface must
    agree on what those names mean, so this matcher is the single resolver.
    """
    text = _skill_text(value)
    if not text:
        return []
    exact: list[str] = []
    partial: list[str] = []
    for skill_id, aliases in SKILL_ALIASES.items():
        candidates = {
            _skill_text(skill_id.replace("_", " ")),
            _skill_text(SKILL_LABELS.get(skill_id, "")),
            *(_skill_text(alias) for alias in aliases),
        }
        candidates.discard("")
        if text in candidates:
            exact.append(skill_id)
            continue
        for alias in candidates:
            if any(ch in alias for ch in "+#."):
                if alias in text:
                    partial.append(skill_id)
                    break
            elif re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text):
                partial.append(skill_id)
                break
    return list(dict.fromkeys(exact + partial))


def canonical_skill_id(value: str) -> str | None:
    ids = canonical_skill_ids(value)
    return ids[0] if ids else None

def _record(row: Any) -> dict:
    data = dict(row)
    for key in ("options",):
        if isinstance(data.get(key), str):
            try: data[key] = json.loads(data[key] or "[]")
            except Exception: data[key] = []
    return data

def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

def _difficulty(q: dict) -> str:
    value = str(q.get("difficulty_level", "")).strip().lower()
    return {"easy": "Easy", "medium": "Medium", "moderate": "Medium", "hard": "Hard"}.get(value, value.title())

def _topic_match(q: dict, topic: str) -> bool:
    spec = FUNDAMENTAL_CATEGORY_MAP[topic]
    if q.get("category_id") not in spec["category_ids"]:
        return False
    fields = [str(q.get("subcategory", "")), str(q.get("question_text", ""))]
    if topic != "Coding":
        fields.append(str(q.get("question_type", "")))
    haystack = _norm(" ".join(fields))
    return any(_norm(term) in haystack for term in spec["terms"])

def _skill_match(q: dict, skill: CVSkill) -> bool:
    """Match a CV skill to its dedicated skill questions by subcategory only.

    Fundamental topics (DSA/OOP/Networking/Coding/Database/Critical Thinking) live in
    their own categories and are matched by `_topic_match`; matching CV skills against
    question text would pull fundamental questions (e.g. Database SQL) into the CV pool
    and starve retakes. The skill seed uses subcategory = skill name, so subcategory
    matching cleanly separates the two sections.
    """
    sub_raw = str(q.get("subcategory", "")).lower()
    sub = _norm(sub_raw)
    if not sub:
        return False
    aliases = [skill.skill_id.replace("_", " ")] + list(SKILL_ALIASES.get(skill.skill_id, []))
    def sub_contains(alias: str) -> bool:
        a_raw = alias.lower()
        # _norm strips + # . so symbol skills (C++, C#, .NET, Node.js) are matched
        # against the raw subcategory to avoid C++/C# collapsing to the same "c".
        if any(ch in a_raw for ch in "+#."):
            return a_raw in sub_raw
        a = _norm(alias)
        return bool(a) and (a == sub or bool(re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", sub)))
    return any(sub_contains(a) for a in aliases)


def _skill_pool(bank: list[dict], skill: CVSkill) -> list[dict]:
    """Return the question pool for a CV skill.

    Questions in the dedicated ``CAT-10 CV Technology Skills`` category are the
    canonical skill bank. If that bank has no questions for the skill we fall back
    to other categories that explicitly name the skill in their subcategory (for
    example a Python question filed under Coding). Preferring CAT-10 prevents the
    CV section from draining fundamental topic pools on retakes.
    """
    matched = [q for q in bank if _skill_match(q, skill)]
    dedicated = [q for q in matched if str(q.get("category_id", "")).upper() == "CAT-10"]
    return dedicated or matched


def skill_coverage(rows: Iterable[Any]) -> dict[str, dict]:
    """Report which taxonomy skills can be assessed from the active question bank.

    This is the same calculation used by the candidate blueprint, the admin
    coverage report and the candidate skill journey, so all three surfaces show
    the same "ready / pending" answer.
    """
    bank = []
    for row in rows:
        try:
            q = QuestionRecord.model_validate(_record(row))
        except (ValidationError, TypeError, ValueError):
            continue
        if q.status == "active" and q.question_text.strip():
            bank.append(q.model_dump())

    coverage: dict[str, dict] = {}
    for skill_id, label in SKILL_LABELS.items():
        skill = CVSkill(skill_id=skill_id, label=label)
        pool = _skill_pool(bank, skill)
        counts = {difficulty: sum(1 for q in pool if _difficulty(q) == difficulty)
                  for difficulty in PER_SKILL_TARGETS}
        missing = {difficulty: max(0, target - counts[difficulty])
                   for difficulty, target in PER_SKILL_TARGETS.items()}
        formats = sorted({_format_kind(q) for q in pool})
        covered = pool and not any(missing.values())
        coverage[skill_id] = {
            "skill_id": skill_id,
            "label": label,
            "covered": bool(covered),
            "active_questions": len(pool),
            "counts": counts,
            "missing": missing,
            "formats": formats,
            "is_fundamental_topic": skill_id in FUNDAMENTAL_SKILL_IDS,
        }
    return coverage

def _external_retrieval(query: str, category_id: str | None = None) -> tuple[list[str], list[str]]:
    """Return IDs ordered by Meilisearch and Neo4j. Missing services are a normal fallback."""
    keyword_ids: list[str] = []
    graph_ids: list[str] = []
    meili_url, meili_key = os.getenv("MEILISEARCH_URL", "").rstrip("/"), os.getenv("MEILISEARCH_API_KEY", "")
    if meili_url:
        try:
            index = _meilisearch_index()
            params = {"limit": 80}
            if category_id:
                params["filter"] = f'category_id = "{category_id}"'
            result = index.search(query, params)
            keyword_ids = [str(x["id"]) for x in result.get("hits", []) if x.get("id")]
        except Exception as exc:
            log.warning("Meilisearch retrieval fallback: %s", type(exc).__name__)
    neo_uri, neo_user, neo_password = (os.getenv("NEO4J_URI", ""), os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", ""))
    if neo_uri:
        try:
            driver = _neo4j_driver()
            records = driver.execute_query(
                "MATCH (s:Skill)-[:HAS_QUESTION]-(q:Question) "
                "WHERE toLower(s.name) CONTAINS toLower($query) "
                "   OR toLower(coalesce(s.label,'')) CONTAINS toLower($query) "
                "RETURN q.id AS id, 0 AS hops "
                "UNION "
                "MATCH p=(s:Skill)-[:RELATED_TO*0..2]-(t:Topic)-[:HAS_QUESTION]-(q:Question) "
                "WHERE toLower(s.name) CONTAINS toLower($query) "
                "   OR toLower(coalesce(s.label,'')) CONTAINS toLower($query) "
                "   OR toLower(t.name) CONTAINS toLower($query) "
                "RETURN q.id AS id, length(p) AS hops ORDER BY hops ASC LIMIT 80",
                query=query, database_=os.getenv("NEO4J_DATABASE", "neo4j")
            ).records
            graph_ids = [str(r["id"]) for r in records if r.get("id")]
        except Exception as exc:
            log.warning("Neo4j retrieval fallback: %s", type(exc).__name__)
    return keyword_ids, graph_ids

def index_question_bank(rows: Iterable[Any]) -> dict:
    """Upsert approved question metadata into Meilisearch and Neo4j."""
    docs, graph_count = [], 0
    for row in rows:
        q = _record(row)
        if q.get("status") != "active":
            continue
        text = " ".join([str(q.get("question_text", "")), str(q.get("subcategory", "")), str(q.get("grading_rubric", ""))])
        topics = [name for name in FUNDAMENTAL_TARGETS if _topic_match(q, name)]
        skills = [sid for sid, aliases in SKILL_INDEX_ALIASES.items()
                  if any(re.search(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])", text.lower())
                         for alias in aliases)]
        docs.append({"id": str(q["id"]), "category_id": q.get("category_id", ""),
            "subcategory": q.get("subcategory", ""), "question_text": q.get("question_text", ""),
            "difficulty_level": _difficulty(q), "question_type": q.get("question_type", ""),
            "search_text": text, "topics": topics, "skills": skills, "status": "active",
            "version": int(q.get("version") or 1)})
    meili_result = "not_configured"
    meili_url, meili_key = os.getenv("MEILISEARCH_URL", "").rstrip("/"), os.getenv("MEILISEARCH_API_KEY", "")
    if meili_url:
        try:
            client = _meilisearch_client()
            index = _meilisearch_index()
            tasks = [
                index.update_filterable_attributes(["category_id", "difficulty_level", "topics", "skills", "status"]),
                index.add_documents(docs, primary_key="id"),
            ]
            for task in tasks:
                if getattr(task, "task_uid", None) is not None:
                    client.wait_for_task(task.task_uid, timeout_in_ms=120000, interval_in_ms=250)
            meili_result = "indexed"
        except Exception as exc:
            meili_result = "error:" + type(exc).__name__
            log.warning("Meilisearch indexing failed: %s", type(exc).__name__)
    neo_result = "not_configured"
    neo_uri = os.getenv("NEO4J_URI", "")
    if neo_uri:
        try:
            relation_map = {
                "python": ["Coding", "DSA"], "javascript": ["Coding", "DSA"],
                "java": ["Coding", "OOP"], "ai_ml": ["Coding", "DSA"],
                "sql": ["Database"], "networking": ["Networking"],
                "dsa": ["DSA"], "oop": ["OOP"], "git": ["Coding"], "docker": ["Coding"],
            }
            relations = [{"skill": skill, "topic": topic} for doc in docs
                         for skill in doc["skills"] for topic in relation_map.get(skill, [])]
            driver = _neo4j_driver()
            with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
                session.run(
                    "UNWIND $docs AS doc MERGE (q:Question {id:doc.id}) "
                    "SET q.text=doc.question_text, q.category_id=doc.category_id, "
                    "q.difficulty=doc.difficulty_level, q.status='active', q.version=doc.version",
                    docs=docs)
                session.run(
                    "UNWIND $docs AS doc MATCH (q:Question {id:doc.id}) "
                    "UNWIND doc.topics AS topic MERGE (t:Topic {name:topic}) MERGE (t)-[:HAS_QUESTION]->(q)",
                    docs=docs)
                session.run(
                    "UNWIND $docs AS doc MATCH (q:Question {id:doc.id}) "
                    "UNWIND doc.skills AS skill MERGE (s:Skill {name:skill}) MERGE (s)-[:HAS_QUESTION]->(q)",
                    docs=docs)
                session.run(
                    "UNWIND $skills AS item MATCH (s:Skill {name:item.id}) SET s.label=item.label",
                    skills=[{"id": sid, "label": SKILL_LABELS.get(sid, sid.replace("_", " "))}
                            for sid in SKILL_LABELS])
                if relations:
                    session.run(
                        "UNWIND $relations AS rel MERGE (s:Skill {name:rel.skill}) "
                        "MERGE (t:Topic {name:rel.topic}) MERGE (s)-[:RELATED_TO]->(t)",
                        relations=relations)
            neo_result = "indexed"
            graph_count = len(docs)
        except Exception as exc:
            neo_result = "error:" + type(exc).__name__
            log.warning("Neo4j indexing failed: %s", type(exc).__name__)
    return {"active_questions": len(docs), "meilisearch": meili_result, "neo4j": neo_result, "graph_questions": graph_count}

def _rrf_order(keyword_ids: Iterable[str], graph_ids: Iterable[str]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for rank, qid in enumerate(keyword_ids, 1): scores[qid] += 1 / (60 + rank)
    for rank, qid in enumerate(graph_ids, 1): scores[qid] += 1 / (60 + rank)
    return scores

def _format_kind(q: dict) -> str:
    qtype = str(q.get("question_type", "")).lower()
    prompt = str(q.get("question_text", "")).lower()
    if "sql" in qtype:
        return "sql"
    if any(x in qtype for x in ("coding", "code", "programming")):
        return "coding"
    if "scenario" in qtype or "case study" in qtype or prompt.startswith(("scenario:", "you are ", "imagine ")):
        return "scenario"
    if any(x in qtype for x in ("explanation", "short answer", "descriptive", "essay")):
        return "explanation"
    return "mcq"

def _format_priority(fmt: str) -> int:
    """Rank formats so rarer deterministic kinds (coding/sql) win format-coverage ties."""
    if fmt in ("coding", "sql"):
        return 0
    if fmt in ("scenario", "explanation"):
        return 1
    return 2


def _allocate(pool: list[dict], count: int, difficulty: str, used: set[str], external: dict[str, float],
              preferred_formats: set[str] | None = None) -> list[dict]:
    candidates = [q for q in pool if q["id"] not in used and _difficulty(q) == difficulty]
    preferred_formats = preferred_formats or set()
    candidates.sort(key=lambda q: (0 if _format_kind(q) in preferred_formats else 1,
                                   _format_priority(_format_kind(q)),
                                   -(external.get(q["id"], 0)), q["id"]))
    selected = candidates[:count]
    used.update(q["id"] for q in selected)
    return selected

def build_assessment(rows: Iterable[Any], cv_skills: list[CVSkill], *, retrieval: bool = True,
                     exclude_ids: Iterable[str] | None = None) -> dict:
    """Build the fixed fundamental section plus five questions per CV skill.

    The selector fails closed with detailed pool shortages; it never silently changes
    quotas or invents questions. All question IDs remain traceable to the source bank.

    ``exclude_ids`` is the retake filter: question IDs a candidate has already seen in a
    previous attempt are removed from every pool, so a retake serves fresh questions.
    """
    started = time.perf_counter()
    bank = []
    for row in rows:
        try:
            q = QuestionRecord.model_validate(_record(row))
            if q.status == "active" and q.question_text.strip():
                bank.append(q.model_dump())
        except (ValidationError, TypeError, ValueError):
            continue
    # Seed the used-set with previously-seen questions (retake filter) so they can
    # never be re-selected, while still acting as the within-assessment dedup set.
    excluded: set[str] = {str(x) for x in (exclude_ids or []) if x}
    used: set[str] = set(excluded)
    selected: list[dict] = []
    trace: list[RetrievalTrace] = []
    shortages: list[dict] = []
    fundamental_formats: set[str] = set()

    # One dual-retrieval pass per fundamental topic.
    for topic, target in FUNDAMENTAL_TARGETS.items():
        spec = FUNDAMENTAL_CATEGORY_MAP[topic]
        pool = [q for q in bank if _topic_match(q, topic)]
        keyword_ids, graph_ids = _external_retrieval(topic) if retrieval else ([], [])
        external = _rrf_order(keyword_ids, graph_ids)
        for difficulty, count in FUNDAMENTAL_DIFFICULTY_MATRIX[topic].items():
            preferred = {"scenario", "explanation", "sql", "coding"} - fundamental_formats
            chosen = _allocate(pool, count, difficulty, used, external, preferred)
            for q in chosen:
                fundamental_formats.add(_format_kind(q))
                selected.append({**q, "assessment_section": "fundamental", "assessment_topic": topic,
                                 "assessment_topic_key": _norm(topic).replace(" ", "_"), "skill_id": None})
                trace.append(RetrievalTrace(question_id=q["id"], section="fundamental", topic=topic, difficulty=difficulty,
                    keyword_rank=(keyword_ids.index(q["id"])+1 if q["id"] in keyword_ids else None),
                    graph_rank=(graph_ids.index(q["id"])+1 if q["id"] in graph_ids else None),
                    selected_rank=len(selected), reasons=["approved_question_bank", "topic_match", "difficulty_quota"]))
            if len(chosen) < count:
                shortages.append({"section": "fundamental", "topic": topic, "difficulty": difficulty,
                                  "required": count, "available_selected": len(chosen)})
    # SQL is a CV-skill format (not a core fundamental), so it is preferred but not
    # hard-required; requiring it would exhaust the limited SQL pool across retakes.
    required_fundamental_formats = {"scenario", "explanation", "coding"}
    for missing_format in sorted(required_fundamental_formats - fundamental_formats):
        shortages.append({"section": "fundamental", "topic": "Question format mix", "difficulty": "Any",
                          "required": 1, "available_selected": 0, "format": missing_format})
    # CV skills are explicit mentions and should be confirmed by the candidate in the UI.
    # Skills without enough approved questions are reported instead of silently
    # dropped, so the candidate journey, admin coverage and company view agree on
    # exactly which skills can be verified today.
    cv_format_coverage = {}
    covered_skill_ids: list[str] = []
    uncovered_skills: list[dict] = []
    for skill in cv_skills:
        if skill.skill_id in FUNDAMENTAL_SKILL_IDS:
            uncovered_skills.append({
                "skill_id": skill.skill_id, "label": skill.label,
                "reason": "assessed_in_fundamental_section",
                "active_questions": 0, "missing": {}, "fatal": False,
            })
            continue
        pool = _skill_pool(bank, skill)
        remaining = [q for q in pool if q["id"] not in used]
        counts = {difficulty: sum(1 for q in remaining if _difficulty(q) == difficulty)
                  for difficulty in PER_SKILL_TARGETS}
        missing = {difficulty: max(0, target - counts[difficulty])
                   for difficulty, target in PER_SKILL_TARGETS.items()}
        if any(missing.values()):
            uncovered_skills.append({
                "skill_id": skill.skill_id, "label": skill.label,
                "reason": "question_bank_coverage",
                "active_questions": len(pool), "missing": missing, "fatal": False,
            })
            shortages.append({"section": "cv", "topic": skill.label, "difficulty": "Any",
                              "required": sum(PER_SKILL_TARGETS.values()),
                              "available_selected": len(remaining),
                              "reason": "question_bank_coverage", "missing": missing, "fatal": False})
            continue
        covered_skill_ids.append(skill.skill_id)
        cv_formats: set[str] = set()
        keyword_ids, graph_ids = _external_retrieval(skill.label) if retrieval else ([], [])
        external = _rrf_order(keyword_ids, graph_ids)
        for difficulty, count in PER_SKILL_TARGETS.items():
            preferred = ({"coding", "sql"} if not ({"coding", "sql"} & cv_formats) else set()) | ({"scenario", "explanation"} if not ({"scenario", "explanation"} & cv_formats) else set())
            chosen = _allocate(pool, count, difficulty, used, external, preferred)
            for q in chosen:
                cv_formats.add(_format_kind(q))
                selected.append({**q, "assessment_section": "cv", "assessment_topic": skill.label,
                                 "assessment_topic_key": skill.skill_id, "skill_id": skill.skill_id})
                trace.append(RetrievalTrace(question_id=q["id"], section="cv", topic=skill.label, difficulty=difficulty,
                    keyword_rank=(keyword_ids.index(q["id"])+1 if q["id"] in keyword_ids else None),
                    graph_rank=(graph_ids.index(q["id"])+1 if q["id"] in graph_ids else None),
                    selected_rank=len(selected), reasons=["approved_question_bank", "explicit_cv_skill_match", "difficulty_quota"]))
            if len(chosen) < count:
                shortages.append({"section": "cv", "topic": skill.label, "difficulty": difficulty,
                                  "required": count, "available_selected": len(chosen)})
        cv_format_coverage[skill.skill_id] = sorted(cv_formats)
        # Format mix is a quality preference, not a correctness requirement. With a
        # 10-question-per-skill bank the retake (the second 5) cannot always include
        # every format, so it is reported rather than treated as a blocking shortage.
        if not ({"coding", "sql"} & cv_formats):
            shortages.append({"section": "cv", "topic": skill.label, "difficulty": "Any", "required": 1,
                              "available_selected": 0, "format": "coding_or_sql", "fatal": False})
        if not ({"scenario", "explanation"} & cv_formats):
            shortages.append({"section": "cv", "topic": skill.label, "difficulty": "Any", "required": 1,
                              "available_selected": 0, "format": "scenario_or_explanation", "fatal": False})
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    fundamental = [q for q in selected if q["assessment_section"] == "fundamental"]
    cv_questions = [q for q in selected if q["assessment_section"] == "cv"]
    total_seconds = sum(int(q.get("time_allocation") or 60) for q in selected)
    fatal_shortages = [s for s in shortages if s.get("fatal", True)]
    return {
        "status": "ready" if not fatal_shortages else "question_bank_incomplete",
        "fundamental": fundamental, "cv_based": cv_questions,
        "questions": selected, "total_questions": len(selected),
        "fundamental_count": len(fundamental), "cv_count": len(cv_questions),
        "estimated_duration_seconds": total_seconds,
        "estimated_duration_minutes": round(total_seconds / 60, 1),
        "difficulty_targets": DIFFICULTY_TARGETS,
        "fundamental_difficulty_matrix": FUNDAMENTAL_DIFFICULTY_MATRIX,
        "fundamental_targets": FUNDAMENTAL_TARGETS,
        "per_skill_targets": PER_SKILL_TARGETS,
        "skills": [s.model_dump() for s in cv_skills],
        "covered_skill_ids": covered_skill_ids,
        "uncovered_skills": uncovered_skills,
        "excluded_ids": sorted(excluded),
        "fundamental_formats": sorted(fundamental_formats), "cv_format_coverage": cv_format_coverage,
        "shortages": shortages, "retrieval_trace": [t.model_dump() for t in trace],
        "retrieval_ms": elapsed_ms,
        "retrieval_providers": {
            "keyword": "meilisearch" if os.getenv("MEILISEARCH_URL") else "relational-fallback",
            "graph": "neo4j" if os.getenv("NEO4J_URI") else "relational-fallback",
        },
    }

def question_page(questions: list[dict], page: int, answers: dict | None = None) -> dict:
    total = len(questions)
    pages = [questions[i:i+QUESTION_PAGE_SIZE] for i in range(0, total, QUESTION_PAGE_SIZE)]
    page = max(0, min(page, max(0, len(pages)-1)))
    rows = []
    for offset, q in enumerate(pages[page]):
        item = {k: q.get(k) for k in ("id", "assessment_section", "assessment_topic", "skill_id",
            "question_type", "difficulty_level", "question_text", "options", "marks", "time_allocation")}
        item["question_number"] = page * QUESTION_PAGE_SIZE + offset + 1
        rows.append(item)
    return {"page": page, "page_size": QUESTION_PAGE_SIZE, "page_count": len(pages),
            "total_questions": total, "questions": rows,
            "answers": {q["id"]: (answers or {}).get(q["id"]) for q in pages[page]},
            "has_previous": page > 0, "has_next": page < len(pages)-1}
