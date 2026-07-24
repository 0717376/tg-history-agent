#!/bin/sh
# Сид конфига Claude CLI из read-only маунта (креды живут в примонтированном ~/.claude).
if [ -f /seed/.claude.json ]; then
  cp -f /seed/.claude.json /root/.claude.json
fi

exec uv run python -m app.main
