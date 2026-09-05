from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
import wave
from collections import defaultdict, deque
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import voice_recv
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

import speech_recognition as sr
import edge_tts


# =========================================================
# SETTINGS
# =========================================================

BOT_NAME = "Ai Gaming"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free",
)

MAX_MESSAGE_LENGTH = 2000
MAX_HISTORY_MESSAGES = 10

USER_COOLDOWN_SECONDS = 5
MAX_CONCURRENT_REQUESTS = 3

CONFIG_PATH = Path("data/bot_config.json")

# Voice settings
VOICE_LANGUAGE = "ar-EG"
TTS_VOICE_AR = "ar-EG-ShakirNeural"
TTS_VOICE_EN = "en-US-GuyNeural"

# Don't process bot's own voice
IGNORE_BOTS = True


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ai-gaming")


# =========================================================
# AI PERSONALITY
# =========================================================

SYSTEM_PROMPT = """
You are Ai Gaming, a friendly, funny and energetic gaming companion on Discord.

Personality:
- Friendly
- Funny
- Energetic
- Helpful
- Talk like a gaming friend
- Give useful gaming advice
- Celebrate wins
- Joke naturally

Languages:
- Egyptian Arabic
- Modern Standard Arabic
- Franco Arabic
- English
- Mixed Arabic and English

Always answer using the same language/style the user uses.

If the user speaks Egyptian Arabic:
Reply naturally in Egyptian Arabic.

If the user speaks English:
Reply in English.

If the user uses Franco Arabic:
Reply in readable Franco Arabic.

Keep voice replies short and natural.

Do not use:
- Huge explanations
- Excessive emojis
- Unnecessary introductions

Never encourage:
- Cheating
- Harassment
- Violence
- Illegal activity
- Self-harm
- Sexually explicit content
"""


# =========================================================
# CONFIG
# =========================================================

class BotConfig:

    def __init__(self, path: Path):
        self.path = path
        self.enabled_channels: set[int] = set()
        self._lock = asyncio.Lock()

    async def load(self):
        if not self.path.exists():
            return

        try:
            raw = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )

            self.enabled_channels = {
                int(x)
                for x in raw.get(
                    "enabled_channels",
                    []
                )
            }

        except Exception as error:
            logger.warning(
                "Could not load config: %s",
                error
            )

    async def save(self):

        async with self._lock:

            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temp = self.path.with_suffix(
                ".tmp"
            )

            temp.write_text(
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

            temp.replace(
                self.path
            )

    async def enable(
        self,
        channel_id: int,
    ):

        self.enabled_channels.add(
            channel_id
        )

        await self.save()

    async def disable(
        self,
        channel_id: int,
    ):

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
# VOICE AI SINK
# =========================================================

class VoiceAISink(voice_recv.AudioSink):

    def __init__(
        self,
        bot: "AiGamingBot",
    ):

        super().__init__()

        self.bot = bot

        self.buffers: dict[int, bytearray] = defaultdict(
            bytearray
        )

        self.members: dict[int, discord.Member] = {}

        self.processing: set[int] = set()

        self.last_packet: dict[int, float] = {}

        self.voice_loop = asyncio.get_running_loop()

    def wants_opus(self) -> bool:
        return False

    def write(
        self,
        user,
        data,
    ):

        if user is None:
            return

        if not isinstance(
            user,
            discord.Member,
        ):
            return

        if IGNORE_BOTS and user.bot:
            return

        pcm = data.pcm

        if not pcm:
            return

        user_id = user.id

        self.members[user_id] = user

        self.buffers[user_id].extend(
            pcm
        )

        self.last_packet[user_id] = time.monotonic()

        # Keep approximately the latest 8 seconds.
        # Discord PCM is 48kHz, stereo, 16-bit.
        max_bytes = (
            48_000
            * 2
            * 2
            * 8
        )

        if len(
            self.buffers[user_id]
        ) > max_bytes:

            self.buffers[user_id] = bytearray(
                self.buffers[user_id][-max_bytes:]
            )

        # Process after enough audio accumulated.
        if (
            len(self.buffers[user_id])
            >=
            48_000 * 2 * 2 * 1.5
        ):

            if user_id not in self.processing:

                asyncio.run_coroutine_threadsafe(
                    self._process_user_when_silent(
                        user_id
                    ),
                    self.voice_loop,
                )

    async def _process_user_when_silent(
        self,
        user_id: int,
    ):

        await asyncio.sleep(0.9)

        last = self.last_packet.get(
            user_id,
            0
        )

        # If user is still talking, wait.
        if (
            time.monotonic() - last
            < 0.75
        ):
            return

        if user_id in self.processing:
            return

        self.processing.add(
            user_id
        )

        try:

            audio = bytes(
                self.buffers.get(
                    user_id,
                    b""
                )
            )

            self.buffers[user_id].clear()

            if len(audio) < 30000:
                return

            member = self.members.get(
                user_id
            )

            if member is None:
                return

            await self.bot.process_voice_audio(
                member,
                audio,
            )

        except Exception:

            logger.exception(
                "Voice processing error"
            )

        finally:

            self.processing.discard(
                user_id
            )

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(
        self,
        member: discord.Member,
    ):

        if member.bot:
            return

        user_id = member.id

        if user_id in self.processing:
            return

        asyncio.run_coroutine_threadsafe(
            self._process_user_after_stop(
                user_id
            ),
            self.voice_loop,
        )

    async def _process_user_after_stop(
        self,
        user_id: int,
    ):

        await asyncio.sleep(
            0.4
        )

        if user_id in self.processing:
            return

        audio = bytes(
            self.buffers.get(
                user_id,
                b""
            )
        )

        if len(audio) < 30000:
            return

        self.processing.add(
            user_id
        )

        try:

            self.buffers[user_id].clear()

            member = self.members.get(
                user_id
            )

            if member:

                await self.bot.process_voice_audio(
                    member,
                    audio,
                )

        except Exception:

            logger.exception(
                "Voice speech stop error"
            )

        finally:

            self.processing.discard(
                user_id
            )

    def cleanup(self):

        self.buffers.clear()
        self.members.clear()
        self.processing.clear()


# =========================================================
# BOT
# =========================================================

class AiGamingBot(discord.Client):

    def __init__(self):

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

        self.history = defaultdict(
            lambda: deque(
                maxlen=MAX_HISTORY_MESSAGES
            )
        )

        self.last_message_at = {}

        self.request_semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_REQUESTS
        )

        self.started_at = time.monotonic()

        self.voice_sinks: dict[
            int,
            VoiceAISink
        ] = {}

        self.voice_locks: dict[
            int,
            asyncio.Lock
        ] = defaultdict(
            asyncio.Lock
        )

    # =====================================================
    # STARTUP
    # =====================================================

    async def setup_hook(self):

        await self.config.load()

        for command in (
            self.ping,
            self.join,
            self.leave,
            self.gaming,
            self.status,
        ):
            command.binding = self
            self.tree.add_command(command)

        synced = await self.tree.sync()

        logger.info(
            "Synced %d slash commands",
            len(synced),
        )

    async def on_ready(self):

        if self.user:

            logger.info(
                "Logged in as %s (%s)",
                self.user,
                self.user.id,
            )

            logger.info(
                "Servers: %d",
                len(self.guilds),
            )

    # =====================================================
    # NORMAL CHAT
    # =====================================================

    async def on_message(
        self,
        message: discord.Message,
    ):

        if message.author.bot:
            return

        if message.webhook_id is not None:
            return

        if self.user is None:
            return

        # Mention only
        if self.user not in message.mentions:
            return

        user_text = self.clean_message(
            message.content
        )

        if not user_text:

            user_text = (
                "Hey Ai Gaming, say hi!"
            )

        if len(user_text) > MAX_MESSAGE_LENGTH:

            await message.reply(
                "Your message is too long 😅",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            return

        if not self.allowed_cooldown(
            message.author.id
        ):
            return

        async with self.request_semaphore:

            await self.answer_message(
                message,
                user_text,
            )

    def clean_message(
        self,
        content: str,
    ):

        if self.user:

            content = content.replace(
                f"<@{self.user.id}>",
                "",
            )

            content = content.replace(
                f"<@!{self.user.id}>",
                "",
            )

        return content.strip()

    def allowed_cooldown(
        self,
        user_id: int,
    ):

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

    # =====================================================
    # AI TEXT
    # =====================================================

    async def ask_ai(
        self,
        text: str,
        channel_id: int | None = None,
    ):

        history = []

        if channel_id is not None:

            history = list(
                self.history[
                    channel_id
                ]
            )

        completion = (
            await self.ai.chat.completions.create(
                model=OPENROUTER_MODEL,

                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },

                    *history,

                    {
                        "role": "user",
                        "content": text,
                    },
                ],

                max_tokens=300,

                temperature=0.85,
            )
        )

        return (
            completion
            .choices[0]
            .message
            .content
            or ""
        ).strip()

    async def answer_message(
        self,
        message: discord.Message,
        user_text: str,
    ):

        channel_id = message.channel.id

        self.history[channel_id].append(
            {
                "role": "user",
                "content": user_text,
            }
        )

        try:

            async with message.channel.typing():

                response = await self.ask_ai(
                    user_text,
                    channel_id,
                )

            if not response:

                response = (
                    "My gamer thoughts "
                    "got stuck in loading 😅"
                )

            self.history[channel_id].append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )

            await self.send_long_reply(
                message,
                response,
            )

        except RateLimitError:

            await message.reply(
                "الـAI مشغول دلوقتي 😅 "
                "جرب كمان شوية.",
                mention_author=False,
            )

        except APIConnectionError:

            await message.reply(
                "اتصال الـAI عمل Lag 😂 "
                "جرب تاني.",
                mention_author=False,
            )

        except APIError:

            logger.exception(
                "OpenRouter API error"
            )

            await message.reply(
                "حصلت مشكلة في سيرفر الـAI 😅",
                mention_author=False,
            )

        except Exception:

            logger.exception(
                "Chat error"
            )

    async def send_long_reply(
        self,
        message,
        text,
    ):

        chunks = [
            text[i:i + 1900]
            for i in range(
                0,
                len(text),
                1900,
            )
        ]

        for chunk in chunks:

            await message.channel.send(
                discord.utils.escape_mentions(
                    chunk
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    # =====================================================
    # VOICE AI
    # =====================================================

    async def process_voice_audio(
        self,
        member: discord.Member,
        pcm_audio: bytes,
    ):

        guild = member.guild

        voice_client = (
            guild.voice_client
        )

        if voice_client is None:
            return

        if not isinstance(
            voice_client,
            voice_recv.VoiceRecvClient,
        ):
            return

        lock = self.voice_locks[
            guild.id
        ]

        if lock.locked():
            return

        async with lock:

            try:

                wav_path = (
                    await self.pcm_to_wav(
                        pcm_audio
                    )
                )

                text = await asyncio.to_thread(
                    self.speech_to_text,
                    wav_path,
                )

                try:
                    os.remove(
                        wav_path
                    )
                except OSError:
                    pass

                if not text:
                    return

                logger.info(
                    "Voice %s: %s",
                    member.display_name,
                    text,
                )

                # Ignore very short accidental sounds
                if len(text.strip()) < 2:
                    return

                response = await self.ask_ai(
                    text
                )

                if not response:
                    return

                logger.info(
                    "AI Voice Reply: %s",
                    response,
                )

                await self.speak_in_voice(
                    guild,
                    response,
                )

            except Exception:

                logger.exception(
                    "Voice AI processing failed"
                )

    async def pcm_to_wav(
        self,
        pcm_audio: bytes,
    ):

        fd, path = tempfile.mkstemp(
            suffix=".wav"
        )

        os.close(fd)

        def write_wav():

            with wave.open(
                path,
                "wb",
            ) as wav:

                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(48000)

                wav.writeframes(
                    pcm_audio
                )

        await asyncio.to_thread(
            write_wav
        )

        return path

    def speech_to_text(
        self,
        wav_path: str,
    ):

        recognizer = sr.Recognizer()

        try:

            with sr.AudioFile(
                wav_path
            ) as source:

                audio = recognizer.record(
                    source
                )

            # Google Speech Recognition
            # Free endpoint
            text = recognizer.recognize_google(
                audio,
                language="ar-EG",
            )

            return text.strip()

        except sr.UnknownValueError:

            return ""

        except sr.RequestError as error:

            logger.error(
                "Speech recognition error: %s",
                error,
            )

            return ""

    async def speak_in_voice(
        self,
        guild: discord.Guild,
        text: str,
    ):

        voice_client = (
            guild.voice_client
        )

        if voice_client is None:
            return

        if not isinstance(
            voice_client,
            discord.VoiceClient,
        ):
            return

        # Stop current audio
        if voice_client.is_playing():

            voice_client.stop()

        # Choose language
        if self.looks_arabic(text):

            tts_voice = TTS_VOICE_AR

        else:

            tts_voice = TTS_VOICE_EN

        mp3_path = None

        try:

            fd, mp3_path = tempfile.mkstemp(
                suffix=".mp3"
            )

            os.close(fd)

            communicate = edge_tts.Communicate(
                text,
                tts_voice,
            )

            await communicate.save(
                mp3_path
            )

            source = discord.FFmpegPCMAudio(
                mp3_path,
                options="-vn",
            )

            voice_client.play(
                source,
                after=lambda error: self.voice_playback_finished(
                    error,
                    mp3_path,
                ),
            )

        except Exception:

            logger.exception(
                "TTS playback error"
            )

            if mp3_path:

                try:
                    os.remove(
                        mp3_path
                    )
                except OSError:
                    pass

    def voice_playback_finished(
        self,
        error,
        path,
    ):

        if error:

            logger.error(
                "Voice playback error: %s",
                error,
            )

        try:

            if path:
                os.remove(path)

        except OSError:
            pass

    def looks_arabic(
        self,
        text: str,
    ):

        arabic_count = sum(
            1
            for char in text
            if (
                "\u0600"
                <= char
                <= "\u06ff"
            )
        )

        return (
            arabic_count
            >= max(
                1,
                len(text) // 10
            )
        )

    # =====================================================
    # PING
    # =====================================================

    @app_commands.command(
        name="ping",
        description="Check whether Ai Gaming is online.",
    )
    async def ping(
        self,
        interaction: discord.Interaction,
    ):

        latency_ms = round(
            bot.latency * 1000
        )

        await interaction.response.send_message(
            f"Pong! `{latency_ms}ms` 🎮",
            ephemeral=True,
        )

    # =====================================================
    # JOIN
    # =====================================================

    @app_commands.command(
        name="join",
        description="Join your voice channel and start AI voice.",
    )
    @app_commands.guild_only()
    async def join(
        self,
        interaction: discord.Interaction,
    ):

        guild = interaction.guild

        if guild is None:

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

        if (
            member.voice is None
            or member.voice.channel is None
        ):

            await interaction.response.send_message(
                "🎙️ ادخل Voice Channel الأول.",
                ephemeral=True,
            )

            return

        voice_channel = member.voice.channel

        try:

            existing = (
                guild.voice_client
            )

            # -----------------------------------------
            # Already connected
            # -----------------------------------------

            if existing is not None:

                if (
                    existing.channel
                    and existing.channel.id
                    == voice_channel.id
                ):

                    voice_client = existing

                else:

                    await existing.move_to(
                        voice_channel
                    )

                    voice_client = existing

            else:

                # IMPORTANT:
                # VoiceRecvClient is required
                # for receiving microphone audio.
                voice_client = await voice_channel.connect(
                    cls=voice_recv.VoiceRecvClient,
                    timeout=30,
                    reconnect=True,
                )

            # -----------------------------------------
            # Start receiving audio
            # -----------------------------------------

            old_sink = self.voice_sinks.get(
                guild.id
            )

            if old_sink:

                try:
                    voice_client.stop_listening()
                except Exception:
                    pass

                old_sink.cleanup()

            sink = VoiceAISink(
                self
            )

            self.voice_sinks[
                guild.id
            ] = sink

            voice_client.listen(
                sink
            )

            await interaction.response.send_message(
                f"🎙️ دخلت **{voice_channel.name}**!\n"
                "🤖 **Ai Gaming Voice AI شغال.**\n"
                "اتكلم وأنا هرد عليك 🔊",
            )

            logger.info(
                "Joined voice channel %s in guild %s",
                voice_channel.name,
                guild.id,
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                "❌ البوت محتاج صلاحيات:\n"
                "✅ View Channel\n"
                "✅ Connect\n"
                "✅ Speak",
                ephemeral=True,
            )

        except Exception as error:

            logger.exception(
                "Voice join error"
            )

            await interaction.response.send_message(
                "❌ خطأ Voice:\n"
                f"`{type(error).__name__}: {error}`",
                ephemeral=True,
            )

    # =====================================================
    # LEAVE
    # =====================================================

    @app_commands.command(
        name="leave",
        description="Leave the voice channel.",
    )
    @app_commands.guild_only()
    async def leave(
        self,
        interaction: discord.Interaction,
    ):

        guild = interaction.guild

        if guild is None:

            await interaction.response.send_message(
                "❌ استخدم الأمر داخل السيرفر.",
                ephemeral=True,
            )

            return

        voice_client = (
            guild.voice_client
        )

        if voice_client is None:

            await interaction.response.send_message(
                "أنا مش داخل Voice Channel.",
                ephemeral=True,
            )

            return

        try:

            if isinstance(
                voice_client,
                voice_recv.VoiceRecvClient,
            ):

                try:
                    voice_client.stop_listening()
                except Exception:
                    pass

            sink = self.voice_sinks.pop(
                guild.id,
                None
            )

            if sink:

                try:
                    sink.cleanup()
                except Exception:
                    pass

            await voice_client.disconnect(
                force=True
            )

            await interaction.response.send_message(
                "🚪 خرجت من الـVoice.",
            )

        except Exception as error:

            logger.exception(
                "Voice leave error"
            )

            await interaction.response.send_message(
                "❌ حصل خطأ:\n"
                f"`{type(error).__name__}: {error}`",
                ephemeral=True,
            )

    # =====================================================
    # GAMING
    # =====================================================

    @app_commands.command(
        name="gaming",
        description="Manage Ai Gaming settings.",
    )
    @app_commands.describe(
        action="Choose an action."
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
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ):

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

            await self.config.enable(
                interaction.channel.id
            )

            await interaction.response.send_message(
                "✅ تم التفعيل."
            )

        elif action.value == "disable":

            await self.config.disable(
                interaction.channel.id
            )

            await interaction.response.send_message(
                "✅ تم التعطيل."
            )

        else:

            state = (
                "ON"
                if self.config.is_enabled(
                    interaction.channel.id
                )
                else "OFF"
            )

            await interaction.response.send_message(
                f"Gaming mode: **{state}**\n"
                "Chat mode: **Mention Only**",
                ephemeral=True,
            )

    # =====================================================
    # STATUS
    # =====================================================

    @app_commands.command(
        name="status",
        description="Show Ai Gaming status.",
    )
    async def status(
        self,
        interaction: discord.Interaction,
    ):

        uptime_minutes = int(
            (
                time.monotonic()
                - self.started_at
            )
            // 60
        )

        voice_status = "Not connected"

        if interaction.guild:

            voice_client = (
                interaction.guild.voice_client
            )

            if (
                voice_client
                and voice_client.channel
            ):

                voice_status = (
                    voice_client.channel.name
                )

        await interaction.response.send_message(
            "\n".join(
                [
                    f"**{BOT_NAME}**",
                    f"Online: `{self.is_ready()}`",
                    f"Uptime: `{uptime_minutes} minute(s)`",
                    f"Servers: `{len(self.guilds)}`",
                    f"AI model: `{OPENROUTER_MODEL}`",
                    "Chat: `Mention Only`",
                    f"Voice: `{voice_status}`",
                    f"Voice AI: `Enabled`",
                ]
            ),
            ephemeral=True,
        )

    # =====================================================
    # SHUTDOWN
    # =====================================================

    async def close(
        self,
    ):

        for sink in self.voice_sinks.values():

            try:
                sink.cleanup()
            except Exception:
                pass

        self.voice_sinks.clear()

        await self.ai.close()

        await super().close()


# =========================================================
# CREATE BOT
# =========================================================

bot = AiGamingBot()


# =========================================================
# MAIN
# =========================================================

def main():

    missing = [
        key
        for key in (
            "DISCORD_TOKEN",
            "OPENROUTER_API_KEY",
        )
        if not os.getenv(key)
    ]

    if missing:

        raise RuntimeError(
            "Missing Replit Secret(s): "
            + ", ".join(missing)
        )

    bot.run(
        os.environ["DISCORD_TOKEN"],
        log_handler=None,
    )


if __name__ == "__main__":
    main()