"""Ai Gaming - Discord AI Gaming Bot."""

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


# =========================================================
# SETTINGS
# =========================================================

BOT_NAME = "Ai Gaming"

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free",
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MAX_MESSAGE_LENGTH = 2_000
MAX_HISTORY_MESSAGES = 10
USER_COOLDOWN_SECONDS = 8
MAX_CONCURRENT_REQUESTS = 3

CONFIG_PATH = Path("data/bot_config.json")


# =========================================================
# AI PERSONALITY
# =========================================================

SYSTEM_PROMPT = """You are Ai Gaming, a friendly, funny and energetic gaming companion on Discord.

Personality:
- Be friendly, funny, energetic and helpful.
- Talk like a gaming friend.
- Celebrate wins and joke naturally.
- Give useful gaming advice.
- Keep replies reasonably short for Discord.
- Never be hateful, sexually explicit or unsafe.
- Do not encourage cheating, harassment, self-harm, violence or illegal activity.

Languages:
- Egyptian Arabic
- Modern Standard Arabic
- Franco Arabic
- English
- Mixed Arabic and English

Always reply in the same language and style the user uses.

If the user speaks Egyptian Arabic, reply naturally in Egyptian Arabic.
If the user uses Franco Arabic, reply in readable Franco Arabic.
If the user mixes languages, you can mix naturally.

Discord behavior:
- Answer directly.
- Avoid unnecessary introductions.
- Avoid excessive emojis.
- Keep replies suitable for Discord.
"""


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ai-gaming")


# =========================================================
# CONFIGURATION
# =========================================================

class BotConfig:

    def __init__(self, path: Path) -> None:
        self.path = path
        self.enabled_channels: set[int] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> None:

        if not self.path.exists():
            return

        try:

            raw = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )

            channels = raw.get(
                "enabled_channels",
                []
            )

            self.enabled_channels = {
                int(channel_id)
                for channel_id in channels
            }

        except (
            OSError,
            ValueError,
            TypeError,
        ) as error:

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

                temporary_path = (
                    self.path.with_suffix(".tmp")
                )

                temporary_path.write_text(
                    json.dumps(
                        {
                            "enabled_channels":
                            sorted(
                                self.enabled_channels
                            )
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                temporary_path.replace(
                    self.path
                )

            except OSError as error:

                logger.error(
                    "Could not save config: %s",
                    error,
                )

    async def enable(
        self,
        channel_id: int,
    ) -> None:

        self.enabled_channels.add(
            channel_id
        )

        await self.save()

    async def disable(
        self,
        channel_id: int,
    ) -> None:

        self.enabled_channels.discard(
            channel_id
        )

        await self.save()

    def is_enabled(
        self,
        channel_id: int,
    ) -> bool:

        return (
            channel_id
            in self.enabled_channels
        )


# =========================================================
# BOT
# =========================================================

class AiGamingBot(discord.Client):

    def __init__(self) -> None:

        intents = discord.Intents.default()

        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.voice_states = True

        super().__init__(
            intents=intents
        )

        self.tree = app_commands.CommandTree(
            self
        )

        self.config = BotConfig(
            CONFIG_PATH
        )

        # OpenRouter
        self.ai = AsyncOpenAI(
            api_key=os.environ[
                "OPENROUTER_API_KEY"
            ],
            base_url=OPENROUTER_BASE_URL,
        )

        self.history: dict[
            int,
            deque[dict[str, str]]
        ] = defaultdict(
            lambda: deque(
                maxlen=MAX_HISTORY_MESSAGES
            )
        )

        self.last_message_at: dict[
            int,
            float
        ] = {}

        self.request_semaphore = (
            asyncio.Semaphore(
                MAX_CONCURRENT_REQUESTS
            )
        )

        self.started_at = (
            time.monotonic()
        )

    # =====================================================
    # STARTUP
    # =====================================================

    async def setup_hook(self) -> None:

        await self.config.load()

        synced_commands = (
            await self.tree.sync()
        )

        logger.info(
            "Synced %d slash commands",
            len(synced_commands),
        )

    async def on_ready(self) -> None:

        if self.user is not None:

            logger.info(
                "Logged in as %s (%s) | %d server(s)",
                self.user,
                self.user.id,
                len(self.guilds),
            )


    # =====================================================
    # CHAT
    # =====================================================

    async def on_message(
        self,
        message: discord.Message,
    ) -> None:

        # Ignore bots
        if message.author.bot:
            return

        # Ignore webhooks
        if message.webhook_id is not None:
            return

        if self.user is None:
            return

        # =================================================
        # MENTION ONLY
        # =================================================

        if self.user not in message.mentions:
            return

        user_text = self._clean_message(
            message.content
        )

        if not user_text:

            user_text = (
                "Hey Ai Gaming, say hi!"
            )

        if len(user_text) > MAX_MESSAGE_LENGTH:

            await message.reply(
                "Your message is too long 😅 "
                "Keep it under 2,000 characters.",
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

        last = self.last_message_at.get(
            user_id,
            0,
        )

        if (
            now - last
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

            async with message.channel.typing():

                completion = (
                    await self.ai.chat.completions.create(
                        model=OPENROUTER_MODEL,

                        messages=[
                            {
                                "role": "system",
                                "content": SYSTEM_PROMPT,
                            },

                            *list(
                                self.history[
                                    channel_id
                                ]
                            ),
                        ],

                        max_tokens=450,
                        temperature=0.85,
                    )
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
                    "in the loading screen 😅"
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

        except RateLimitError:

            logger.exception(
                "OpenRouter rate limit"
            )

            await message.reply(
                "The free AI is busy right now 😅 "
                "Try again in a few seconds.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except APIConnectionError:

            logger.exception(
                "OpenRouter connection error"
            )

            await message.reply(
                "My AI connection lagged 😅 "
                "Try again in a moment.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except APIError:

            logger.exception(
                "OpenRouter API error"
            )

            await message.reply(
                "The AI server had a problem. "
                "Try again shortly.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except discord.Forbidden:

            logger.warning(
                "Missing permission in channel %s",
                channel_id,
            )

        except discord.HTTPException:

            logger.exception(
                "Discord rejected reply"
            )

        except Exception:

            logger.exception(
                "Unexpected AI error"
            )

    async def _send_long_reply(
        self,
        message: discord.Message,
        response_text: str,
    ) -> None:

        chunks = [
            response_text[i:i + 1_900]
            for i in range(
                0,
                len(response_text),
                1_900,
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

        await self.ai.close()
        await self.close()


# =========================================================
# CREATE BOT
# =========================================================

bot = AiGamingBot()


# =========================================================
# /PING
# =========================================================

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
        f"Pong! `{latency_ms}ms` 🎮",
        ephemeral=True,
    )


# =========================================================
# /JOIN
# =========================================================

@bot.tree.command(
    name="join",
    description="Make Ai Gaming join your voice channel.",
)
@app_commands.guild_only()
async def join(
    interaction: discord.Interaction,
) -> None:

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ استخدم الأمر داخل السيرفر.",
            ephemeral=True,
        )

        return

    member = interaction.user

    if not isinstance(
        member,
        discord.Member,
    ):

        await interaction.response.send_message(
            "❌ مش قادر أحدد بياناتك.",
            ephemeral=True,
        )

        return

    # =====================================================
    # CHECK USER VOICE CHANNEL
    # =====================================================

    if (
        member.voice is None
        or member.voice.channel is None
    ):

        await interaction.response.send_message(
            "🎙️ ادخل Voice Channel الأول "
            "وبعدين استخدم `/join`.",
            ephemeral=True,
        )

        return

    voice_channel = member.voice.channel

    # =====================================================
    # CONNECT
    # =====================================================

    try:

        existing_voice = (
            interaction.guild.voice_client
        )

        # -----------------------------------------------
        # Already connected
        # -----------------------------------------------

        if existing_voice is not None:

            if (
                existing_voice.channel is not None
                and existing_voice.channel.id
                == voice_channel.id
            ):

                await interaction.response.send_message(
                    f"🎙️ أنا موجود بالفعل في "
                    f"**{voice_channel.name}**.",
                    ephemeral=True,
                )

                return

            # Move bot
            await existing_voice.move_to(
                voice_channel
            )

            await interaction.response.send_message(
                f"🎙️ نقلت نفسي إلى "
                f"**{voice_channel.name}**.",
            )

            return

        # -----------------------------------------------
        # Connect to Voice
        # -----------------------------------------------

        await voice_channel.connect(
            timeout=30,
            reconnect=True,
        )

        await interaction.response.send_message(
            f"🎙️ دخلت **{voice_channel.name}** معاك!",
        )

        logger.info(
            "Joined voice channel: %s (%s)",
            voice_channel.name,
            voice_channel.id,
        )

    # =====================================================
    # PERMISSION ERROR
    # =====================================================

    except discord.Forbidden:

        logger.exception(
            "VOICE FORBIDDEN"
        )

        await interaction.response.send_message(
            "❌ البوت معندوش صلاحية دخول الـVoice.\n\n"
            "فعّل له:\n"
            "✅ View Channel\n"
            "✅ Connect\n"
            "✅ Speak",
            ephemeral=True,
        )

    # =====================================================
    # TIMEOUT
    # =====================================================

    except asyncio.TimeoutError:

        logger.exception(
            "VOICE CONNECTION TIMEOUT"
        )

        await interaction.response.send_message(
            "❌ الاتصال بالـVoice أخد وقت طويل وفشل.\n"
            "جرب `/join` تاني.",
            ephemeral=True,
        )

    # =====================================================
    # CLIENT ERROR
    # =====================================================

    except discord.ClientException as error:

        logger.exception(
            "VOICE CLIENT ERROR: %s",
            error,
        )

        await interaction.response.send_message(
            "❌ حصلت مشكلة في اتصال الـVoice:\n"
            f"`{type(error).__name__}: {error}`",
            ephemeral=True,
        )

    # =====================================================
    # GENERAL ERROR
    # =====================================================

    except Exception as error:

        logger.exception(
            "VOICE JOIN ERROR: %s",
            error,
        )

        await interaction.response.send_message(
            "❌ خطأ Voice:\n"
            f"`{type(error).__name__}: {error}`",
            ephemeral=True,
        )


# =========================================================
# /LEAVE
# =========================================================

@bot.tree.command(
    name="leave",
    description="Make Ai Gaming leave the voice channel.",
)
@app_commands.guild_only()
async def leave(
    interaction: discord.Interaction,
) -> None:

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ استخدم الأمر داخل السيرفر.",
            ephemeral=True,
        )

        return

    voice_client = (
        interaction.guild.voice_client
    )

    if voice_client is None:

        await interaction.response.send_message(
            "أنا مش داخل أي Voice Channel حاليًا.",
            ephemeral=True,
        )

        return

    try:

        await voice_client.disconnect(
            force=True
        )

        await interaction.response.send_message(
            "🚪 خرجت من الـVoice Channel.",
        )

    except Exception as error:

        logger.exception(
            "VOICE LEAVE ERROR: %s",
            error,
        )

        await interaction.response.send_message(
            "❌ حصل خطأ وأنا بحاول أخرج:\n"
            f"`{type(error).__name__}: {error}`",
            ephemeral=True,
        )


# =========================================================
# /GAMING
# =========================================================

@bot.tree.command(
    name="gaming",
    description="Manage Ai Gaming settings.",
)
@app_commands.describe(
    action="Choose an action.",
)
@app_commands.choices(
    action=[
        app_commands.Choice(
            name="Enable",
            value="enable",
        ),
        app_commands.Choice(
            name="Disable",
            value="disable",
        ),
        app_commands.Choice(
            name="Status",
            value="status",
        ),
    ]
)
@app_commands.guild_only()
async def gaming(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
) -> None:

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ استخدم الأمر داخل السيرفر.",
            ephemeral=True,
        )

        return

    if not isinstance(
        interaction.channel,
        discord.TextChannel,
    ):

        await interaction.response.send_message(
            "❌ استخدم الأمر في Text Channel.",
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
                "❌ الأمر ده للـServer Managers فقط.",
                ephemeral=True,
            )

            return

    if action.value == "enable":

        await bot.config.enable(
            interaction.channel.id
        )

        await interaction.response.send_message(
            "✅ تم الحفظ.\n"
            "⚠️ البوت هيفضل يرد فقط عند الـMention.",
        )

    elif action.value == "disable":

        await bot.config.disable(
            interaction.channel.id
        )

        await interaction.response.send_message(
            "✅ تم التعطيل.\n"
            "البوت هيرد فقط عند الـMention.",
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
            f"Gaming mode: **{state}**\n"
            "Chat mode: **Mention Only**",
            ephemeral=True,
        )


# =========================================================
# /STATUS
# =========================================================

@bot.tree.command(
    name="status",
    description="Show Ai Gaming status.",
)
async def status(
    interaction: discord.Interaction,
) -> None:

    uptime_minutes = int(
        (
            time.monotonic()
            - bot.started_at
        )
        // 60
    )

    voice_status = "Not connected"

    if interaction.guild is not None:

        voice_client = (
            interaction.guild.voice_client
        )

        if (
            voice_client is not None
            and voice_client.channel is not None
        ):

            voice_status = (
                voice_client.channel.name
            )

    await interaction.response.send_message(
        "\n".join(
            [
                f"**{BOT_NAME}**",
                f"Online: `{bot.is_ready()}`",
                f"Uptime: `{uptime_minutes} minute(s)`",
                f"Servers: `{len(bot.guilds)}`",
                f"AI model: `{OPENROUTER_MODEL}`",
                "Chat: `Mention Only`",
                f"Voice: `{voice_status}`",
            ]
        ),
        ephemeral=True,
    )


# =========================================================
# START BOT
# =========================================================

def main() -> None:

    missing_secrets = [
        key
        for key in (
            "DISCORD_TOKEN",
            "OPENROUTER_API_KEY",
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