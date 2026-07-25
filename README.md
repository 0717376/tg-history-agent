# tg-history-agent

**English** | [Русский](README.ru.md)

An AI agent that answers questions about your Telegram group's history — with links to the exact messages. Ask it "what did we decide about the DB schema?" and it iteratively searches the full history (keyword FTS, no embeddings), reads context around the hits, reconstructs reply threads, and replies with `t.me/c/...` links that open right in the group.

Built on the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk/overview) — runs on your Claude subscription via Claude CLI OAuth, no API keys required.

## How it works

```
Telethon (your account) ──► SQLite FTS5 index ──► agent tools ──► Telegram bot
     backfill + live            messages,           12 search        private chat
     new/edit/delete            topics, members     tools            + group @mentions
```

1. **Indexer** — a [Telethon](https://github.com/LonamiWebs/Telethon) client under *your* personal account (you are a member of the group) backfills the entire history into SQLite with FTS5, then keeps listening live: new messages, edits, deletions, forum topics, reactions, pinned flags, document filenames.
2. **Agent** — a Claude agent whose only tools are 12 search primitives over that index (`tools=[]` strips every built-in Claude Code tool — without it the agent has a shell inside your container and burns turns on subagent orchestration). It searches *iteratively*: tries several query variants (FTS5 prefix wildcards to compensate for morphology), greps exact substrings (error codes, URLs), expands hits via context/threads, checks activity spikes, and only then answers.
3. **Bot** — a plain Bot API long-polling bot. Private chat is gated by an allowlist and/or group membership (member snapshot taken via Telethon — the bot does not even need to be a member of the group). Inside the group it answers anyone who @mentions it. Streaming replies via `sendMessageDraft`. Every question is logged to a `usage` table; `/status` shows who has been asking to ids listed in `TG_ALLOWED_IDS`.

### Agent tools

| Tool | Purpose |
|---|---|
| `search` | FTS5 full-text search: filters by topic/sender/date, pagination, relevance or date ordering |
| `grep` | exact substring match — error codes, URLs, function names, word fragments |
| `context` | the conversation around a message (same forum topic) |
| `thread` | full reply chain: up to the root and down through all replies |
| `read` | batch-read messages by ids or id range |
| `topics` | forum topics with message counts |
| `senders` | participants with message counts |
| `activity` | per-day/week histogram — find discussion spikes, then dig in |
| `pinned` | pinned messages |
| `files` | documents by filename or caption |
| `top_reacted` | messages the group reacted to most |
| `stats` | index overview |

Every hit comes with a ready-made `https://t.me/c/...` link (topic-aware for forums) that works for any group member.

## Setup

Requirements: [uv](https://github.com/astral-sh/uv), an authenticated [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (subscription), a Telegram account that is a member of the target group.

```bash
cp .env.example .env
# 1. TG_API_ID / TG_API_HASH — from https://my.telegram.org → API development tools
uv sync

# 2. One-time login with your account (two-step, no interactive stdin needed):
uv run python -m app.login +79991234567          # sends the code
uv run python -m app.login +79991234567 12345    # signs in (add 2FA password as a 3rd arg if set)

# 3. Pick the group id, put it into TG_TARGET_CHAT:
uv run python -m app.chats

# 4. TG_BOT_TOKEN — create a bot via @BotFather
uv run python -m app.main                        # backfill starts, bot goes live
```

`uv run python -m app.profile` prints an index summary (volume, topics, authors, media).

## Deployment (Docker)

```bash
docker compose up -d --build
```

The compose file mounts `./data` (index + sessions) and your `~/.claude` (Claude CLI OAuth credentials — token refresh persists there). Do the Telethon login on any machine and ship `data/user.session` along with the project; the backfill resumes from wherever it stopped.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | — | my.telegram.org application keys |
| `TG_TARGET_CHAT` | — | group to index: `-100…` id or `@username` |
| `TG_BOT_TOKEN` | — | bot token; empty disables the bot (indexer still runs) |
| `TG_ALLOWED_IDS` | — | private-chat access: ids and/or the literal `group` (any group member may DM the bot) |
| `CLAUDE_MODEL` | `sonnet` | agent model |
| `SESSION_FRESH_HOURS` | `6` | idle time after which a chat gets a fresh agent session |

## Notes & limitations

- One group per instance (the schema is ready for more, the config is not).
- Keyword search only — no embeddings by design; the agent's iterative querying covers most of the semantic gap.
- Media content is not indexed (photo captions and document filenames are).
- Draft streaming needs Bot API 9.5+ clients; falls back to a typing indicator silently.
- Agent prompts are in Russian — adjust `app/agent.py` for other languages.
- Live updates can be dropped by Telegram (`PersistentTimestampOutdatedError`), so the indexer re-checks the tail of the history every 5 minutes and fills the gaps.

## License

MIT
