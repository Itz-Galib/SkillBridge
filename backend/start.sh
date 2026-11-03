#!/bin/sh
# SkillBridge start script (Render / single container).
set -e

# Seed demo/test data once. Idempotent: no-op when the demo user already exists.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[start] seeding demo data..."
  python /app/seed_demo.py || echo "[start] core demo seed skipped"
  python /app/seed_enterprise.py || echo "[start] enterprise demo seed skipped"
fi

# Optional one-time GraphRAG question-bank seed. This inserts 45 synthetic,
# active demo questions using stable IDs and ON CONFLICT DO NOTHING.
# Keep disabled by default; enable only for a development/staging database.
if [ "${SEED_GRAPHRAG_QUESTIONS:-false}" = "true" ]; then
  echo "[start] SEED_GRAPHRAG_QUESTIONS=true; seeding synthetic GraphRAG assessment questions..."
  python /app/seed_graphrag_questions.py
  echo "[start] GraphRAG question seed completed."
fi

PORT="${PORT:-8000}"
echo "[start] starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn serve:app --host 0.0.0.0 --port "${PORT}" --workers 1
