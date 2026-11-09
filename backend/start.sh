#!/bin/sh
# SkillBridge start script (Render / single container).
set -e

# Seed demo/test data once. Idempotent: no-op when the demo user already exists.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[start] seeding demo data..."
  python /app/seed_demo.py || echo "[start] core demo seed skipped"
  python /app/seed_enterprise.py || echo "[start] enterprise demo seed skipped"
fi

# GraphRAG question bank + graph edges. Idempotent and cheap (single connection,
# ON CONFLICT DO NOTHING), so it runs on every start to keep the bank populated.
echo "[start] seeding GraphRAG question bank (idempotent)..."
python /app/seed_graphrag_questions.py || echo "[start] GraphRAG seed skipped"
echo "[start] GraphRAG question seed completed."

PORT="${PORT:-8000}"
echo "[start] starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn serve:app --host 0.0.0.0 --port "${PORT}" --workers 1
