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
3. **Gap** shows the MOCK role vs. your claimed skills. 4. **Verify**: 10 adaptive questions (MCQ + hands-on SQL), server-timed.
5. Result with topic scores and trust label. 6. **Learn**: cited resources for weak topics. 7. **Re-test** → uplift. 8. **Passport**: choose skills, share link, revoke.

## Verify exam integrity controls

The candidate Verify flow now requests browser screen sharing and full-screen mode before starting or resuming an attempt. Candidates choose the screen, window or tab in the browser's native permission picker. If sharing stops or full-screen mode is exited, the UI warns the candidate and disables SQL execution and answer submission until the required state is restored.

During an active attempt, the frontend blocks ordinary copy, cut, paste, keyboard clipboard shortcuts and the context menu, and records copy/cut/paste attempts, window blur, hidden-tab events, full-screen exits and screen-sharing interruptions. The backend stores event counts with the attempt and includes them in the advisory trust label. Integrity signals do not change the score or automatically fail a candidate; the existing appeal and human-review approach remains.

**Browser-enforced limits:** browsers do not let a website silently start screen sharing. The candidate must approve the browser prompt, and the browser may allow sharing a single tab/window instead of the entire desktop. A web page cannot reliably prevent screenshots, another device, OS-level shortcuts, developer tools, browser extensions or a determined user from bypassing client-side controls. The API also checks that the latest reported screen-sharing event is active before accepting an answer or running SQL. This is a consistency check, not cryptographic proof: browser telemetry is client-reported and a determined user can forge requests. These controls are deterrents and review signals, not a secure remote-proctoring system. No camera or microphone is requested, and the screen stream is not recorded or uploaded by this application. Use HTTPS (or localhost for development) and test in supported desktop browsers.

## Phase 1 exam-integrity update (in progress)

The current branch adds a versioned, explainable integrity result (phase1-v1) that is stored separately from the technical assessment score. It groups tab-hidden, blur and fullscreen-exit reports into a single leave-window incident when they occur close together; a return-to-tab event closes the incident so away duration can contribute points. Clipboard, screen-share interruption, heartbeat-gap and wrong-share-surface rules have configurable weights/caps in backend/engine.py. The result stores the label, points, rule version, reason list and hard-flag list. The current hard-flag hook is ready for later evidence phases; this branch does not yet collect camera/identity evidence.

**Important: Phase 1 is not complete yet.** Server-side answer timing and the exam deadline were already implemented before this update. The current event API is authenticated and checks attempt ownership, but the remaining Phase 1 work still includes per-attempt session binding and single-session invalidation, event sequence validation and anomaly persistence, rate limiting and richer event metadata, 10-second heartbeats and lazy heartbeat-gap creation, event buffering/sendBeacon delivery, strict full-monitor surface enforcement, clipboard-origin tracking for the code editor, and the full pre-exam system-check page. Until those are implemented and tested, browser telemetry remains candidate-reported and must not be treated as proof of misconduct.

Trust labels are review signals only. They must not change the technical score or automatically fail a candidate. Existing screen-share/fullscreen checks are browser-level checks, not proof that the candidate is using only one device or sharing the entire desktop.

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
