print("🔥🔥🔥 NEW AI GAMING CODE IS RUNNING 🔥🔥🔥")
"""Ai Gaming - a friendly multilingual Discord gaming companion."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import discord
from discord import app_commands
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError


BOT_NAME = "Ai Gaming"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_MESSAGE_LENGTH = 2_000
MAX_HISTORY_MESSAGES = 10
USER_COOLDOWN_SECONDS = 8
MAX_CONCURRENT_REQUESTS = 3
CONFIG_PATH = Path("data/bot_config.json")


SYSTEM_PROMPT = """You are Ai Gaming, a friendly, funny, energetic gaming companion on Discord.

Be warm, playful, encouraging, and concise.

Understand Egyptian Arabic, Modern Standard Arabic, Franco Arabic, English,
and mixed messages.

Reply in the same language and style used by the user.

You can discuss games, builds, strategies, esports, hardware, memes,
and everyday chat.

Never encourage cheating, harassment, self-harm, violence, or illegal activity.
"""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ai-gaming")


class BotConfig:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.enabled_channels: set[int] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        if not self.path.exists():
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            channels = raw.get("enabled_channels", [])
            self.enabled_channels = {
                int(channel_id) for channel_id in channels
            }
        except (OSError, ValueError, TypeError) as error:
            logger.warning(
                "Could not read bot config: %s",
                error,
            )

    async def save(self) -> None:
        async with self._lock:
            try:
                self.path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                temporary_path = self.path.with_suffix(".tmp")

                temporary_path.write_text(
                    json.dumps(
                        {
                            "enabled_channels": sorted(
                                self.enabled_channels
                            )
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                temporary_path.replace(self.path)

            except OSError as error:
                logger.error(
                    "Could not save bot config: %s",
                    error,
                )

    async def enable(self, channel_id: int) -> None:
        self.enabled_channels.add(channel_id)
        await self.save()

    async def disable(self, channel_id: int) -> None:
        self.enabled_channels.discard(channel_id)
        await self.save()

    def is_enabled(self, channel_id: int) -> bool:
        return channel_id in self.enabled_channels


class AiGamingBot(discord.Client):

    def __init__(self) -> None:

        intents = discord.Intents.default()

        intents.message_content = True
        intents.messages = True
        intents.guilds = True

        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

        self.config = BotConfig(CONFIG_PATH)

        api_key = os.getenv("OPENAI_API_KEY")

        # Diagnostic information only.
        # The actual API key is NEVER printed.
        print("")
        print("========== AI GAMING STARTUP CHECK ==========")
        print("OPENAI_API_KEY exists:", bool(api_key))
        print("OPENAI_API_KEY length:", len(api_key) if api_key else 0)
        print("OPENAI_MODEL:", OPENAI_MODEL)
        print("==============================================")
        print("")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing from Replit Secrets."
            )

        self.openai = AsyncOpenAI(
            api_key=api_key
        )

        self.history: dict[
            int,
            deque[dict[str, str]]
        ] = defaultdict(
            lambda: deque(
                maxlen=MAX_HISTORY_MESSAGES
            )
        )

        self.last_message_at: dict[int, float] = {}

        self.request_semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_REQUESTS
        )

        self.started_at = time.monotonic()

    async def setup_hook(self) -> None:

        await self.config.load()

        synced_commands = await self.tree.sync()

        logger.info(
            "Synced %d slash commands",
            len(synced_commands),
        )

    async def on_ready(self) -> None:

        if self.user is not None:

            logger.info(
                "Logged in as %s (%s) | serving %d guild(s)",
                self.user,
                self.user.id,
                len(self.guilds),
            )

    async def on_message(
        self,
        message: discord.Message,
    ) -> None:

        if message.author.bot:
            return

        if message.webhook_id is not None:
            return

        if self.user is None:
            return

        is_direct_mention = (
            self.user in message.mentions
        )

        is_enabled_channel = (
            message.guild is not None
            and self.config.is_enabled(
                message.channel.id
            )
        )

        if (
            not is_direct_mention
            and not is_enabled_channel
        ):
            return

        user_text = self._clean_message(
            message.content
        )

        if not user_text:
            user_text = (
                "Hey Ai Gaming, say hi and ask me "
                "what we're playing!"
            )

        if len(user_text) > MAX_MESSAGE_LENGTH:

            await message.reply(
                "That message is too long. "
                "Keep it under 2,000 characters!",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            return

        if not self._allowed_by_cooldown(
            message.author.id
        ):
            return

        async with self.request_semaphore:

            await self._answer_message(
                message,
                user_text,
            )

    def _clean_message(
        self,
        content: str,
    ) -> str:

        if self.user is not None:

            content = content.replace(
                f"<@{self.user.id}>",
                "",
            )

            content = content.replace(
                f"<@!{self.user.id}>",
                "",
            )

        return content.strip()

    def _allowed_by_cooldown(
        self,
        user_id: int,
    ) -> bool:

        now = time.monotonic()

        last_message = self.last_message_at.get(
            user_id,
            0,
        )

        if (
            now - last_message
            < USER_COOLDOWN_SECONDS
        ):
            return False

        self.last_message_at[user_id] = now

        return True

    async def _answer_message(
        self,
        message: discord.Message,
        user_text: str,
    ) -> None:

        channel_id = message.channel.id

        self.history[channel_id].append(
            {
                "role": "user",
                "content": user_text,
            }
        )

        try:

            logger.info(
                "Sending OpenAI request | channel=%s | model=%s",
                channel_id,
                OPENAI_MODEL,
            )

            async with message.channel.typing():

                completion = await self.openai.chat.completions.create(

                    model=OPENAI_MODEL,

                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT,
                        },
                        *list(
                            self.history[channel_id]
                        ),
                    ],

                    max_tokens=450,

                    temperature=0.85,
                )

            response_text = (
                completion
                .choices[0]
                .message
                .content
                or ""
            ).strip()

            if not response_text:

                response_text = (
                    "My gamer thoughts got stuck "
                    "in the loading screen. Try again!"
                )

            self.history[channel_id].append(
                {
                    "role": "assistant",
                    "content": response_text,
                }
            )

            await self._send_long_reply(
                message,
                response_text,
            )

            logger.info(
                "OpenAI request succeeded | channel=%s",
                channel_id,
            )

        except RateLimitError as error:

            logger.error("")
            logger.error(
                "========== OPENAI RATE LIMIT ERROR =========="
            )

            logger.error(
                "Type: %s",
                type(error).__name__,
            )

            logger.error(
                "Status code: %s",
                getattr(
                    error,
                    "status_code",
                    "unknown",
                ),
            )

            logger.error(
                "Message: %s",
                str(error),
            )

            logger.error(
                "Request ID: %s",
                getattr(
                    error,
                    "request_id",
                    "unknown",
                ),
            )

            logger.error(
                "=============================================="
            )

            logger.error("")

            await message.reply(
                "⚠️ OpenAI rejected the request. "
                "Check the Replit Console for the exact error.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except APIConnectionError as error:

            logger.error("")
            logger.error(
                "========== OPENAI CONNECTION ERROR =========="
            )

            logger.error(
                "Type: %s",
                type(error).__name__,
            )

            logger.error(
                "Message: %s",
                str(error),
            )

            logger.error(
                "=============================================="
            )

            logger.error("")

            await message.reply(
                "⚠️ I can't connect to OpenAI right now.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except APIError as error:

            logger.error("")
            logger.error(
                "========== OPENAI API ERROR =========="
            )

            logger.error(
                "Type: %s",
                type(error).__name__,
            )

            logger.error(
                "Status code: %s",
                getattr(
                    error,
                    "status_code",
                    "unknown",
                ),
            )

            logger.error(
                "Message: %s",
                str(error),
            )

            logger.error(
                "Request ID: %s",
                getattr(
                    error,
                    "request_id",
                    "unknown",
                ),
            )

            logger.error(
                "========================================"
            )

            logger.error("")

            await message.reply(
                "⚠️ OpenAI API error. "
                "Check the Replit Console.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except discord.Forbidden:

            logger.warning(
                "Discord permission error in channel %s",
                channel_id,
            )

        except discord.HTTPException as error:

            logger.error(
                "Discord HTTP error: %s",
                error,
            )

        except Exception as error:

            logger.exception("")
            logger.exception(
                "========== UNEXPECTED ERROR =========="
            )

            logger.exception(
                "Type: %s",
                type(error).__name__,
            )

            logger.exception(
                "Message: %s",
                str(error),
            )

            logger.exception(
                "======================================"
            )

    async def _send_long_reply(
        self,
        message: discord.Message,
        response_text: str,
    ) -> None:

        chunks = [
            response_text[index:index + 1900]
            for index in range(
                0,
                len(response_text),
                1900,
            )
        ]

        if not chunks:
            return

        await message.reply(
            discord.utils.escape_mentions(
                chunks[0]
            ),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        for chunk in chunks[1:]:

            await message.channel.send(
                discord.utils.escape_mentions(
                    chunk
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def shutdown(self) -> None:

        await self.openai.close()

        await self.close()


bot = AiGamingBot()


@bot.tree.command(
    name="ping",
    description="Check whether Ai Gaming is online.",
)
async def ping(
    interaction: discord.Interaction,
) -> None:

    latency_ms = round(
        bot.latency * 1000
    )

    await interaction.response.send_message(
        f"Pong! My ping is `{latency_ms}ms`. Ready to game.",
        ephemeral=True,
    )


@bot.tree.command(
    name="gaming",
    description="Enable or disable natural AI chat in this channel.",
)
@app_commands.describe(
    action="Choose what Ai Gaming should do in this channel."
)
@app_commands.choices(
    action=[
        app_commands.Choice(
            name="Enable chat here",
            value="enable",
        ),
        app_commands.Choice(
            name="Disable chat here",
            value="disable",
        ),
        app_commands.Choice(
            name="Show channel status",
            value="status",
        ),
    ]
)
@app_commands.guild_only()
async def gaming(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
) -> None:

    if (
        interaction.guild is None
        or not isinstance(
            interaction.channel,
            discord.TextChannel,
        )
    ):

        await interaction.response.send_message(
            "Please use this command in a regular server text channel.",
            ephemeral=True,
        )

        return

    if action.value in {
        "enable",
        "disable",
    }:

        member = interaction.user

        if (
            not isinstance(
                member,
                discord.Member,
            )
            or not member.guild_permissions.manage_guild
        ):

            await interaction.response.send_message(
                "Only server managers can enable or disable my automatic chat.",
                ephemeral=True,
            )

            return

    if action.value == "enable":

        await bot.config.enable(
            interaction.channel.id
        )

        await interaction.response.send_message(
            "Gaming mode is ON here. Talk naturally and I'll jump in with you.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    elif action.value == "disable":

        await bot.config.disable(
            interaction.channel.id
        )

        await interaction.response.send_message(
            "Gaming mode is OFF here. I'll still answer when someone mentions me directly.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    else:

        state = (
            "ON"
            if bot.config.is_enabled(
                interaction.channel.id
            )
            else "OFF"
        )

        await interaction.response.send_message(
            f"Gaming mode is **{state}** in this channel.",
            ephemeral=True,
        )


@bot.tree.command(
    name="status",
    description="Show Ai Gaming's current status.",
)
async def status(
    interaction: discord.Interaction,
) -> None:

    uptime_minutes = int(
        (
            time.monotonic()
            - bot.started_at
        ) // 60
    )

    enabled_count = len(
        bot.config.enabled_channels
    )

    await interaction.response.send_message(
        "\n".join(
            [
                f"**{BOT_NAME} status**",
                f"Online: `{bot.is_ready()}`",
                f"Uptime: `{uptime_minutes} minute(s)`",
                f"Servers: `{len(bot.guilds)}`",
                f"Gaming channels: `{enabled_count}`",
                f"AI model: `{OPENAI_MODEL}`",
            ]
        ),
        ephemeral=True,
    )


def main() -> None:

    missing_secrets = [
        key
        for key in (
            "DISCORD_TOKEN",
            "OPENAI_API_KEY",
        )
        if not os.getenv(key)
    ]

    if missing_secrets:

        missing = ", ".join(
            missing_secrets
        )

        raise RuntimeError(
            f"Missing required Replit Secret(s): {missing}"
        )

    try:

        bot.run(
            os.environ["DISCORD_TOKEN"],
            log_handler=None,
        )

    except KeyboardInterrupt:

        logger.info(
            "Ai Gaming stopped."
        )


if __name__ == "__main__":
    main()