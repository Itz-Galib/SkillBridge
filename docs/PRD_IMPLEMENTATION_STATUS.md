# PRD implementation status

## Delivered in feature/prd-postgres-enterprise
- PostgreSQL 16 is the default Docker Compose database, with a health check and persistent volume.
- FastAPI database helpers support PostgreSQL through psycopg 3 and retain SQLite fallback for engine tests.
- Candidate/company role selection at registration; admin is not self-assignable.
- Company profile, job publishing, candidate applications and opt-in result sharing APIs.
- Recruiter application list is company-scoped; assessment scores are omitted unless the candidate opted in.
- Admin-only overview, user list and company-specific assessment request list.
- Idempotent synthetic demo seed for candidate, recruiter, admin and three explicitly mock jobs.
- Existing skill-assessment journey remains available to candidates.

## Delivered in `feature/ocr-creation-for-user-profile`
- PDF/DOCX CV parsing retains OCR fallback, private file storage and owner-scoped extraction evidence.
- Groq extraction validates the configured model and falls back to Gemini using the shared canonical profile schema.
- Candidates can edit a private draft, keep a CV name separate from account identity, and explicitly confirm before profile creation.
- Provider failures use the deterministic parser only when it finds profile information; unusable extraction returns a clear error.

## Next implementation slices
1. Add Alembic migrations, authorization tests, rate limits, email verification and structured error handling.
2. Add admin score-review queue, immutable official results and candidate appeals.
3. Add PII-redaction tests and optional pgvector RAG.
4. Add payment-provider abstraction and idempotent webhook handling for paid retakes.
5. Add safe isolated code execution, backups, observability, accessibility and privacy/legal review.

## Safety and correctness decisions
- Integrity events are review signals, not proof of cheating or automatic failure.
- No inference of honesty, ability, personality or intent from facial expression, voice tone, blinking or demographic traits.
- Candidate result sharing is opt-in and revocable.
- Seeded identities, companies and jobs are synthetic and labelled as demo content.
- Demo credentials are unsuitable for production. Disable SEED_DEMO on public deployments.


## Company / Recruiter workspace — PRD v2.0 implementation branch

Branch: `feature/company-recruiter-workspace` · Draft PR #2

### Delivered in this slice
- Responsive company dashboard, job management, applicant pipeline, applicant detail drawer, recruiter notes, analytics, team access and company settings.
- Organisation verification submission/status, platform verification decision endpoint and audit events.
- Structured job draft fields, revision snapshots, review-gated publishing, pause/close controls and platform moderation endpoints.
- Tenant-scoped company memberships with Owner, Recruiter, Hiring Manager and Viewer permissions.
- Application stage transitions with reason capture and private notes.
- Phase-two assessment configuration saved as a platform-review request; not delivered to candidates until approval and delivery integration exist.

### Explicit boundaries / remaining PRD work
- Company verification and job moderation decisions require an authorised Admin workflow; UI shows the review state and blocks public publishing/applicant access until verified.
- Invitation delivery is manual-token based until a verified email provider is configured.
- Candidate profile/CV evidence, immutable application snapshots, selected result-version consent, candidate notifications, and assessment execution/score integration are not yet implemented by this slice.
- Billing, invoices, subscription management, advanced team invitations/expiry, company-domain verification, audit-log viewer, export controls and production-grade security testing remain open.
- This branch is not a claim that the entire 38-page PRD is production-complete.
