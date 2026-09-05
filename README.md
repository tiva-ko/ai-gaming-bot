# Ai Gaming

Ai Gaming is a friendly, funny, multilingual Discord gaming companion built with
Python, `discord.py`, and the OpenAI API.

It understands English, Arabic, Egyptian Arabic, Franco Arabic, and mixed
messages. It automatically answers in the same language, script, and general
energy that the user used.

## Features

- Natural conversation in one or more selected Discord channels
- Direct mention support in any channel
- Egyptian Arabic, Arabic, Franco Arabic, English, and mixed-language replies
- Friendly, energetic gaming personality
- `/ping` latency check
- `/gaming` to enable, disable, or inspect automatic chat for the current channel
- `/status` bot status and configuration summary
- Per-user cooldown and concurrent-request limit to reduce spam and API cost
- Ignores bots and never replies to itself
- JSON persistence for enabled channels across restarts
- No secrets in source code

## 1. Add the required Replit Secrets

In the Replit Secrets tool, add:

- `DISCORD_TOKEN` — the token from your Discord Developer Portal bot
- `OPENAI_API_KEY` — your OpenAI API key

The bot refuses to start if either secret is missing.

## 2. Create and invite the Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application named `Ai Gaming`.
3. Open **Bot**, create the bot, and copy its token into the `DISCORD_TOKEN`
   Replit Secret. Never commit or paste this token into source code.
4. Under **Bot > Privileged Gateway Intents**, enable **Message Content Intent**.
5. Open **OAuth2 > URL Generator** and select:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channels`, `Send Messages`, `Read Message History`
6. Open the generated URL and invite the bot to your server.

## 3. Run it

The project includes a Replit workflow named **Ai Gaming Bot** that runs:

```bash
python bot.py
```

Start that workflow to keep the bot connected continuously. The service does
not need a web port; it stays alive through Discord's gateway connection.

For a one-off local run:

```bash
python -m pip install -r requirements.txt
python bot.py
```

## 4. Enable natural chat

In the Discord channel where you want automatic replies, a server manager runs:

```text
/gaming enable
```

Then users can talk naturally without commands. To turn it off:

```text
/gaming disable
```

The bot will still answer when directly mentioned, even in a disabled channel.
Use `/gaming status` to check the current channel.

## Project files

- `bot.py` — bot startup, Discord events, slash commands, OpenAI conversation,
  anti-spam, and channel configuration
- `requirements.txt` — Python dependencies
- `data/bot_config.json` — created automatically for enabled-channel settings;
  it is local configuration and is ignored by Git

## Troubleshooting

- If slash commands are not visible immediately, wait a few minutes for Discord
  to register global commands and confirm the bot has the
  `applications.commands` scope.
- If natural chat does not work, verify **Message Content Intent** is enabled
  in the Discord Developer Portal and that the bot can read message history.
- If the bot starts and exits, check that both required Replit Secrets exist and
  that the Discord token was not regenerated after it was saved.