# SkillBridge Global: R0 prototype

AI-native skill verification loop for one role (**Junior Data Analyst**):
**CV → skills → gap → adaptive SQL/MCQ test → learning path → re-test → Skill Passport**, with Bangla/English UI, consent screen, trust label and PII redaction.
Built from the *SkillBridge Global BRD & PRD v1.0* (Release **R0**, PRD §11) and the *SkillBridge BD* report.

- **Frontend:** React 18 + Vite (served by nginx)
- **Backend:** FastAPI + PostgreSQL 16 (Docker volume); SQLite remains available for lightweight local engine tests when DATABASE_URL is unset
- **Run / deploy:** Docker Compose

## Run

```bash
cp .env.example .env          # change SECRET_KEY and POSTGRES_PASSWORD outside local demos
# Docker Compose wires the backend to PostgreSQL
docker compose up --build
# open http://localhost:8080   (health check: http://localhost:8080/api/health)
```

Dev without Docker:

```bash
cd backend && pip install -r requirements.txt && DB_PATH=./dev.db uvicorn main:app --reload      # :8000
cd frontend && npm install && npm run dev                                                         # :5173 (proxies /api)
```

Deploy on any VPS with Docker: clone, create `.env` (strong `SECRET_KEY`), `docker compose up -d --build`, put HTTPS (Caddy/Traefik/Cloudflare) in front of port 8080. Set `RETEST_COOLDOWN_HOURS=336` for the spec's 14-day cooldown (0 is for demos).

## Deploy: Vercel frontend + Render API

The frontend and FastAPI backend are separate deployments. The Vercel project should use the `frontend` directory; Render runs the API and managed PostgreSQL.

### 1. Deploy the backend on Render

1. Open Render Dashboard → **New → Blueprint** and connect this repository. Render reads the root `render.yaml`.
2. Review the resources before confirming. The Blueprint creates a Render web service and a **paid, persistent PostgreSQL database** on the lowest paid database tier. The web service is configured on Render's free tier and may sleep when idle.
3. The backend service uses `backend/Dockerfile`, exposes `/api/health`, receives `DATABASE_URL` from the managed database, generates `SECRET_KEY`, and allows the current Vercel origin through CORS.
4. Wait for the Render service to finish deploying. Its default URL is `https://skillbridge.onrender.com`. Verify `https://skillbridge.onrender.com/api/health` returns JSON with `"ok": true`.

### 2. Connect the Vercel frontend

In Vercel → SkillBridge project → **Settings → Environment Variables**, add:

- **Name:** `VITE_API_BASE_URL`
- **Value:** `https://skillbridge.onrender.com`
- **Environments:** Production, Preview, and Development as needed.

Keep **Root Directory** set to `frontend`. Redeploy the Vercel project so Vite includes the new build-time variable. The frontend API client now prefixes every `/api` request with this origin. If you use a custom Vercel domain, add that exact origin to Render's `CORS_ORIGINS` environment variable (comma-separated), then redeploy the backend.

### 3. Create your Super Admin account

After the Render backend is live, open the service's **Shell** and run:

```bash
cd /app
python create_admin.py
```

Enter your own email and a unique password of at least 14 characters when prompted. Do not put the password in source code or commit it. Then sign in from the Vercel site; the Admin Console is shown for the provisioned admin account.

> ⚠️ The Blueprint currently sets `SEED_DEMO=true`, so the synthetic demo accounts (including `admin@test.com` / `12345678`) are seeded on the Render service startup via `start.sh`. **Set `SEED_DEMO=false` before any public launch** — these are known, hardcoded local-demo credentials and must never exist on a publicly reachable deployment.

### 4. Production settings

- Keep `SECRET_KEY` generated and stable; do not rotate it casually because it signs active login tokens and Skill Passports.
- Keep `DATABASE_URL` connected to the managed PostgreSQL database. Do not use SQLite on an ephemeral web-service filesystem for production.
- The Blueprint sets `RETEST_COOLDOWN_HOURS=336`. `SEED_DEMO` is enabled for local/demo testing; disable it before a public launch.
- Add any additional frontend domains to `CORS_ORIGINS` and redeploy the API.
- Add `ANTHROPIC_API_KEY` in Render only if you want the optional LLM extraction path.

## Demo path (about 5 minutes)
1. Create account → accept consent. 2. **Use sample CV** → Analyse (see what was redacted) → confirm skills.
3. **Gap** shows the MOCK role vs. your claimed skills. 4. **Verify**: a server-timed assessment with 25 fundamental questions plus five questions per confirmed CV skill; the duration is calculated from question time allocations.
5. Result with topic scores and trust label. 6. **Learn**: cited resources for weak topics. 7. **Re-test** → uplift. 8. **Passport**: choose skills, share link, revoke.

## What is real vs. mock (honest list)
| Area | R0 status |
|---|---|
| Server-side scoring from stored answer keys, adaptive difficulty | **Real** (`backend/engine.py`, unit-tested) |
| SQL sandbox (SELECT-only, authorizer, op limit, graded on 2 generated datasets) | **Real**, in-process SQLite. Python sandbox: not in R0 |
| PII redaction before AI | **Real** (regex + name heuristics; not perfect) |
| Skill extraction | Rule-based offline by default. **LLM path exists** (schema-validated, falls back to rules) and runs only if `ANTHROPIC_API_KEY` is set. Not exercised in CI |
| Trust label (paste, tab-blur, fast-perfect signals) | **Real**, never pass/fail; appeal is stored |
| Learning path | Curated index, topic/level retrieval with source + licence. **Not** pgvector RAG yet. Curator must verify links/licences |
| Skill Passport | **Real**: HMAC-signed, revocable, public verify page. Signature is server-secret HMAC, not public-key |
| Job profile, question pool (25 items), percentile | **MOCK / tiny**. Percentile intentionally not shown |
| Bangla | UI + explanations. Question text is English only |
| Not built (roadmap) | Recruiter/company/admin roles, camera proctoring, AI interviewer, MCP servers, pgvector, billing, SSO, virus scan |

## GraphRAG-powered candidate assessment

The assessment engine now has a GraphRAG retrieval path built around the existing, curator-governed `question_bank`. It retrieves and assembles approved questions; it does **not** fabricate assessment questions or answers at exam time.

### Assessment blueprint

- **Fundamental section:** 25 questions across DSA (5), OOP (4), Networking (4), Coding (5), Database (5), and Critical Thinking (2).
- **Difficulty:** 40/30/30 is not an exact integer split for 25 questions. The engine uses the nearest whole-question allocation: 10 Easy, 8 Medium, 7 Hard. The per-topic matrix is returned with every blueprint.
- **CV section:** five questions per candidate-confirmed CV skill: 1 Easy, 2 Medium, 2 Hard. CV skill extraction uses the redacted CV and the expanded technology taxonomy.
- **Question formats:** the approved bank must cover scenario-based, explanation, SQL and coding questions in the fundamental section. Each CV skill must have practical (coding/SQL) and scenario/explanation coverage.
- **Duration:** the server calculates the assessment duration by summing each selected question's configured `time_allocation`; it is not a fixed exam duration.
- **Candidate experience:** five questions are shown per page in a table, with previous/next page navigation, per-question answer saving, a server-side timer and post-submit answer review. Correct answers are not exposed during the exam.
- **Auditability:** each generated blueprint stores the selected question IDs, CV skill evidence, retrieval trace, provider status, quota gaps and retrieval latency. Attempt answers and submission results are stored in the existing attempt record. MCQs and SQL questions with a canonical reference query are automatically graded; SQL questions without a reference query, coding, scenario and explanation responses are flagged for human review.

### Retrieval architecture

```
Redacted CV
   └─ spaCy tokenization + explicit skill alias extraction
        └─ Pydantic CVSkill schema + candidate skill confirmation
             └─ Assessment planner / quota validation
                  ├─ Meilisearch: fast keyword recall
                  └─ Neo4j: Skill → RELATED_TO → Topic → HAS_QUESTION
                       └─ Reciprocal-rank fusion + deterministic quota selection
                            └─ Approved question-bank snapshot → Assessment attempt
```

The relational question bank remains the source of truth. Meilisearch and Neo4j are optional retrieval accelerators; if either is not configured or unavailable, the selector falls back to deterministic question-bank filtering and reports the active provider status. The retrieval step is designed to complete quickly, but actual latency depends on question-bank size and external service availability; the measured `retrieval_ms` is stored and returned rather than promising a fixed 1–2 minute SLA.

### Required question-bank preparation

The selector fails closed when a quota is missing. Admins must create/import questions, set the correct category and subcategory, difficulty, question type, answer key/reference answer, explanation/rubric and per-question time allocation, then mark reviewed questions **Active**. For SQL questions to be automatically graded, put a canonical SQL query in the Reference answer field; the SQL grader checks it against two generated datasets using the built-in customers/orders schema. Suggested subcategory labels include Python, Git, AI & Machine Learning, SQL, DSA, OOP, Networking and the other exact technology names found in CVs. Do not activate unreviewed questions. The Admin → Question Governance screen exposes GraphRAG readiness and a **Rebuild search + graph index** action.

### Seed a synthetic GraphRAG assessment bank for testing

The repository includes an explicit, repeatable seed script with **45 synthetic questions**:
- 25 fundamental questions across DSA (5), OOP (4), Networking (4), Coding (5), Database (5) and Critical Thinking (2).
- 5 questions each for Python, Git, AI & Machine Learning, and SQL.
- Per-skill difficulty: 1 Easy, 2 Medium, 2 Hard.
- Includes MCQ, coding, SQL, scenario and explanation formats, answer keys/reference solutions, rubrics and per-question time allocations.

Run this only against a **development or staging database**. If your Render Free service does not provide Shell access, you can use the temporary startup option instead:

1. In Render → your SkillBridge backend service → **Environment**, add or change `SEED_GRAPHRAG_QUESTIONS` to `true`.
2. Save the change and let Render redeploy the backend. The startup script runs `python /app/seed_graphrag_questions.py` before starting the API and prints the seed summary in deployment logs.
3. After the logs confirm that 45 questions were inserted/verified, change `SEED_GRAPHRAG_QUESTIONS` back to `false` and redeploy. The script is disabled by default and does not run on ordinary restarts after that.
4. Rebuild search + graph index from Admin → Question Governance.

The seed uses stable IDs and `ON CONFLICT DO NOTHING`, so a repeated run does not duplicate or overwrite these seeded records. If the seed command fails, startup stops and the deployment logs show the error; correct the database/configuration issue before disabling the flag. This startup option requires the Render service to use the repository's `start.sh` entrypoint (the supplied Render Blueprint does).

The script is idempotent: it uses stable question IDs and `ON CONFLICT DO NOTHING`, so rerunning it will not duplicate or overwrite existing questions. It does not create candidate accounts or write candidate data. Existing question-bank records are preserved.

After seeding:
1. Sign in with an admin account that has Question Governance permission.
2. Open **Admin → Question Governance** and check that the 25-question fundamental quota is ready.
3. Select **Rebuild search + graph index**. If Meilisearch/Neo4j are not configured, the relational retrieval fallback remains available for testing.
4. Upload or use a test CV that explicitly mentions Python, Git, machine learning and SQL, confirm those detected skills, and generate the assessment.
5. Confirm the blueprint contains 45 questions, with 25 fundamental and five per confirmed skill. The seed's CI test validates this exact test scenario.

Do not run this seed on a production database containing live assessment content unless you explicitly want these demo questions to become active and visible in its question bank.

### Configure external retrieval services

Set these on the backend service (Render for the current deployment):

- `MEILISEARCH_URL`, `MEILISEARCH_API_KEY`, `MEILISEARCH_QUESTION_INDEX`
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- `SPACY_MODEL` (defaults to `en_core_web_sm`; if the model is not installed, spaCy's blank English tokenizer and deterministic alias matching are used)

After configuring both services, sign in as an admin, open **Question Governance**, and select **Rebuild search + graph index**. The Admin GraphRAG status endpoint reports question coverage and service configuration. Keep service credentials in Render environment variables, never in the frontend or repository.

### API endpoints

- `POST /api/assessment/blueprint` — create and persist a candidate-specific blueprint after CV skill confirmation.
- `GET /api/assessment/blueprint/{blueprint_id}` — retrieve blueprint readiness metadata for its owner.
- `POST /api/attempts` with `{"blueprint_id":"..." }` — start/resume the official attempt and consume existing retake credits under the existing policy.
- `POST /api/attempts/{attempt_id}/answer` — save a question answer without revealing correctness.
- `POST /api/attempts/{attempt_id}/navigate` — navigate to a question index.
- `POST /api/attempts/{attempt_id}/submit` — submit the assessment; requires every question to be answered unless the server timer expires.
- `GET /api/admin/assessment/graphrag/status` and `POST /api/admin/assessment/graphrag/reindex` — admin readiness and index maintenance.

This implementation is an initial GraphRAG assessment slice. Meilisearch/Neo4j must be provisioned and configured separately, the approved question bank must meet every quota, and production end-to-end testing is still required before public use.

## Layout
```
backend/   main.py (API) · engine.py (scoring, redaction, SQL sandbox) · content.py (taxonomy, questions, resources) · prompts/ · tests/
frontend/  src/App.jsx (all screens) · i18n.js (EN/BN) · styles.css · nginx.conf · Dockerfile
docker-compose.yml · .env.example · .github/workflows/ci.yml
```

## Tests
`cd backend && python -m pytest -q tests` covers redaction, SQL sandbox (including DROP and infinite recursion), grading on hidden datasets, adaptivity, retest freshness, trust label, learning-path honesty, signing.

## Compliance notes
Planning prototype, not legal advice. Confirm privacy/employment-AI obligations with counsel per market (BRD §12). Third-party resource terms must be checked before launch.


## PRD implementation branch: PostgreSQL + hiring workflow

Branch: feature/prd-postgres-enterprise

This branch adds a PostgreSQL 16 service, persistent storage, a health check, a psycopg-backed database adapter (SQLite fallback for engine tests), and the first company/job/application API slice.

### Hiring API slice
- Candidate and company account roles; public registration cannot assign the admin role.
- Company profile and job publishing.
- Candidate job applications and opt-in, revocable sharing of assessment results with the employer.
- Recruiter application listing; assessment scores are omitted unless the candidate has opted in.
- Admin-only overview, user list and company-specific assessment request list.

### Synthetic demo data
When SEED_DEMO=true, synthetic demo accounts are seeded (password demo12345):
- Candidate: demo@skillbridge.test
- Recruiter: recruiter@skillbridge.test
- Admin: admin@skillbridge.test

A second admin account for reaching the Admin Console is also seeded:
- Admin: admin@test.com / password 12345678 (local demo only)

Demo jobs are explicitly marked MOCK and are not real vacancies. These credentials are local-demo-only; do not enable public demo accounts in production.

### Known remaining PRD work
This is an initial vertical slice, not a completed production platform. Alembic migrations, complete authorization/security tests, DeepSeek provider integration, pgvector RAG, payment webhooks, official score review, OCR fallback, hardened code execution and end-to-end production deployment remain future work.


## Candidate workspace UI

The candidate experience now has a dedicated responsive workspace with:
- Overview dashboard and progress cards.
- Editable professional profile: headline, contact/location, summary, LinkedIn/GitHub/portfolio.
- Repeatable education, experience and project entries.
- Job-search preferences (employment type, work mode and locations).
- CV upload/analysis and skill confirmation, preserving the existing skill-verification journey.
- Job marketplace search/filter, applications list and candidate-controlled assessment-result sharing.
- Skill Gap, assessment, learning path, re-test, Skill Passport, and data export/deletion sections.
- Responsive desktop/mobile navigation and explicit MOCK labelling for synthetic jobs.

Candidate profile fields are persisted through `/api/candidate/profile` and the `candidate_profiles` PostgreSQL table. This is still an implementation milestone: verify the full PostgreSQL runtime, authorization, browser accessibility and end-to-end flows before production use.
