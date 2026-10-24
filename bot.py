
import os
import asyncio
import logging
from collections import deque
from threading import Thread
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN missing. Set it in Render Environment Variables."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("darkcity-music")

# Render health-check web server
app = Flask(__name__)


@app.route("/")
def home():
    return "Dark City Music Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "online"}, 200


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


Thread(target=run_web, daemon=True).start()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

queues: dict[int, deque[dict[str, Any]]] = {}
now_playing: dict[int, str] = {}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 15,
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


def get_queue(guild_id: int) -> deque[dict[str, Any]]:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def _extract_track(query: str) -> dict[str, Any] | None:
    query = query.strip()
    if not query:
        return None

    if query.startswith(("https://", "http://")):
        candidates = [query]
    else:
        candidates = [
            f"ytsearch1:{query}",
            f"scsearch1:{query}",
        ]

    for candidate in candidates:
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                data = ydl.extract_info(candidate, download=False)

            if not data:
                continue

            if "entries" in data:
                entries = data.get("entries") or []
                data = next(
                    (entry for entry in entries if entry),
                    None,
                )

            if not data:
                continue

            audio_url = data.get("url")
            if audio_url:
                return {
                    "title": data.get("title") or "Unknown title",
                    "url": audio_url,
                    "webpage_url": data.get("webpage_url") or query,
                }

        except Exception as exc:
            log.warning(
                "Audio lookup failed for %s: %s",
                candidate,
                exc,
            )

    return None


async def search_track(query: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_extract_track, query)


async def play_next(
    guild: discord.Guild,
    text_channel: discord.abc.Messageable,
):
    voice = guild.voice_client

    if not voice or voice.is_playing() or voice.is_paused():
        return

    queue = get_queue(guild.id)

    if not queue:
        now_playing.pop(guild.id, None)
        return

    track = queue.popleft()
    now_playing[guild.id] = track["title"]

    try:
        source = discord.FFmpegPCMAudio(
            track["url"],
            **FFMPEG_OPTIONS,
        )

        def after_play(error: Exception | None):
            if error:
                log.error("Player error: %s", error)

            future = asyncio.run_coroutine_threadsafe(
                play_next(guild, text_channel),
                bot.loop,
            )

            try:
                future.result()
            except Exception:
                log.exception("Failed to start next queued track")

        voice.play(source, after=after_play)

        await text_channel.send(
            f"🎶 **Now playing:** "
            f"[{track['title']}]({track['webpage_url']})"
        )

    except Exception as exc:
        log.exception("Could not play track")
        await text_channel.send(
            f"❌ গান চালানো যায়নি: `{exc}`"
        )
        await play_next(guild, text_channel)


async def ensure_voice(
    interaction: discord.Interaction,
) -> discord.VoiceClient | None:
    if not interaction.guild:
        await interaction.followup.send(
            "এই কমান্ড সার্ভারের ভেতরে ব্যবহার করো।",
            ephemeral=True,
        )
        return None

    member = interaction.user

    if (
        not isinstance(member, discord.Member)
        or not member.voice
        or not member.voice.channel
    ):
        await interaction.followup.send(
            "আগে একটি voice channel-এ join করো।",
            ephemeral=True,
        )
        return None

    voice = interaction.guild.voice_client

    if voice is None:
        voice = await member.voice.channel.connect()
    elif voice.channel != member.voice.channel:
        await voice.move_to(member.voice.channel)

    return voice


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash commands", len(synced))
    except Exception:
        log.exception("Could not sync slash commands")

    log.info("Bot online as %s", bot.user)


@bot.tree.command(
    name="join",
    description="Join your voice channel",
)

async def ensure_voice(
    interaction: discord.Interaction,
) -> discord.VoiceClient | None:
    if not interaction.guild:
        await interaction.followup.send(
            "❌ এই কমান্ড সার্ভারের ভেতরে ব্যবহার করো।",
            ephemeral=True,
        )
        return None

    member = interaction.user

    if (
        not isinstance(member, discord.Member)
        or member.voice is None
        or member.voice.channel is None
    ):
        await interaction.followup.send(
            "❌ আগে Discord-এর একটি voice channel-এ ঢোকো।",
            ephemeral=True,
        )
        return None

    channel = member.voice.channel
    voice = interaction.guild.voice_client

    try:
        if voice is None:
            voice = await asyncio.wait_for(
                channel.connect(timeout=20, reconnect=True),
                timeout=25,
            )
        elif not voice.is_connected():
            voice = await asyncio.wait_for(
                channel.connect(timeout=20, reconnect=True),
                timeout=25,
            )
        elif voice.channel != channel:
            await asyncio.wait_for(
                voice.move_to(channel),
                timeout=20,
            )

        return voice

    except Exception as exc:
        log.exception("Voice connection failed")
        await interaction.followup.send(
            f"❌ Voice channel-এ ঢোকা যায়নি: "
            f"`{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )
        return None


@bot.tree.command(
    name="join",
    description="Join your voice channel",
)
async def join(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        voice = await ensure_voice(interaction)

        if voice and voice.is_connected():
            await interaction.followup.send(
                f"✅ **{voice.channel}**-এ ঢুকেছি!",
                ephemeral=True,
            )

    except Exception as exc:
        log.exception("Join command failed")

        await interaction.followup.send(
            f"❌ Join failed: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )


@bot.tree.command(
    name="play",
    description="Play a song by name or URL",
)
@app_commands.describe(query="Song name or supported URL")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)

    if not interaction.guild or not interaction.channel:
        await interaction.followup.send(
            "এই কমান্ড সার্ভারের ভেতরে ব্যবহার করো।"
        )
        return

    voice = await ensure_voice(interaction)
    if not voice:
        return

    await interaction.followup.send("🔎 গান খুঁজছি...")

    track = await search_track(query)

    if not track:
        await interaction.followup.send(
            "❌ গানটি পাওয়া যায়নি অথবা সোর্সটি Render থেকে "
            "access করা যাচ্ছে না। অন্য নাম বা URL দিয়ে চেষ্টা করো।"
        )
        return

    queue = get_queue(interaction.guild.id)
    queue.append(track)

    if voice.is_playing() or voice.is_paused():
        await interaction.followup.send(
            f"➕ Queue-তে যোগ হয়েছে: **{track['title']}** "
            f"(position {len(queue)})"
        )
    else:
        await play_next(interaction.guild, interaction.channel)


@bot.tree.command(
    name="pause",
    description="Pause the current song",
)
async def pause(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None

    if voice and voice.is_playing():
        voice.pause()
        await interaction.response.send_message(
            "⏸️ গান pause করা হয়েছে।"
        )
    else:
        await interaction.response.send_message(
            "এখন কোনো গান চলছে না।",
            ephemeral=True,
        )


@bot.tree.command(
    name="resume",
    description="Resume the paused song",
)
async def resume(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None

    if voice and voice.is_paused():
        voice.resume()
        await interaction.response.send_message(
            "▶️ গান আবার চালু হয়েছে।"
        )
    else:
        await interaction.response.send_message(
            "কোনো paused গান নেই।",
            ephemeral=True,
        )


@bot.tree.command(
    name="skip",
    description="Skip the current song",
)
async def skip(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None

    if voice and (voice.is_playing() or voice.is_paused()):
        voice.stop()
        await interaction.response.send_message(
            "⏭️ গান skip করা হয়েছে।"
        )
    else:
        await interaction.response.send_message(
            "এখন কোনো গান চলছে না।",
            ephemeral=True,
        )


@bot.tree.command(
    name="stop",
    description="Stop music and clear the queue",
)
async def stop(interaction: discord.Interaction):
    if interaction.guild:
        get_queue(interaction.guild.id).clear()

        voice = interaction.guild.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()

        now_playing.pop(interaction.guild.id, None)

    await interaction.response.send_message(
        "⏹️ গান বন্ধ এবং queue clear করা হয়েছে।"
    )


@bot.tree.command(
    name="queue",
    description="Show the music queue",
)
async def queue_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "সার্ভারের ভেতরে ব্যবহার করো।",
            ephemeral=True,
        )
        return

    queue = get_queue(interaction.guild.id)
    current = now_playing.get(interaction.guild.id)

    lines = (
        [f"🎶 **Now playing:** {current}"]
        if current
        else []
    )

    lines.extend(
        f"{i}. {track['title']}"
        for i, track in enumerate(queue, start=1)
    )

    if not lines:
        lines = ["Queue খালি।"]

    await interaction.response.send_message(
        "\n".join(lines)[:1900]
    )


@bot.tree.command(
    name="leave",
    description="Leave voice channel and clear queue",
)
async def leave(interaction: discord.Interaction):
    if interaction.guild:
        get_queue(interaction.guild.id).clear()
        now_playing.pop(interaction.guild.id, None)

        voice = interaction.guild.voice_client
        if voice:
            await voice.disconnect()

    await interaction.response.send_message(
        "👋 Voice channel ছেড়ে চলে এসেছি।"
    )


if __name__ == "__main__":
    bot.run(TOKEN)
