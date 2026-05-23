#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -U pip
  ./.venv/bin/pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "ВНИМАНИЕ: создан .env из шаблона, заполни TG_API_ID/TG_API_HASH/GROQ_API_KEYS и перезапусти"
  exit 1
fi
exec ./.venv/bin/python -m app.main
