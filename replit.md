# Ai Gaming Discord Bot

Ai Gaming is a continuously running Python Discord bot that chats with gamers through OpenAI.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `python bot.py` — run the Ai Gaming Discord bot
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required Replit Secrets: `DISCORD_TOKEN`, `OPENAI_API_KEY`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)
- Bot: Python, discord.py, OpenAI API

## Where things live

- `bot.py` — Discord gateway client, AI conversations, slash commands, anti-spam
- `requirements.txt` — Python runtime dependencies
- `README.md` — Discord setup and usage instructions
- `data/bot_config.json` — generated local channel configuration

## Architecture decisions

- OpenAI is called from the bot process with the `OPENAI_API_KEY` Replit Secret; no key is hardcoded.
- Natural chat is opt-in per channel through `/gaming enable`, while direct mentions work everywhere.
- Enabled channel IDs are stored in a small ignored JSON file so a restart does not silently change behavior.
- Message history is kept in memory per channel and is never written to disk.

## Product

- Multilingual gaming conversation with Egyptian Arabic, Arabic, Franco Arabic, English, and mixed-language support.
- `/ping`, `/gaming`, and `/status` slash commands.
- Per-user cooldown, request concurrency limit, and bot self-message filtering.

## User preferences

- Keep the bot simple, reliable, and secret-safe.

## Gotchas

- Discord **Message Content Intent** must be enabled for natural, non-mention chat.
- The bot needs both `bot` and `applications.commands` OAuth scopes.
- Slash command registration is global and may take a few minutes to appear.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
