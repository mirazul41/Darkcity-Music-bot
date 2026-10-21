
import os
import asyncio
import logging
from collections import deque
from typing import Optional
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv
from flask import Flask

# Load environment variables
load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN missing. Add it to your .env file "
        "or hosting environment variables."
    )

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# Flask web server for Render Web Service
app = Flask(__name__)


@app.route("/")
def home():
    return "Dark City Music Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "online"}, 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# Start web server before starting Discord bot
web_thread = Thread(target=run_web, daemon=True)
web_thread.start()

# Discord bot setup
intents = discord.Intents.default()
bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Music queues, one per server
queues: dict[int, deque] = {}
now_playing: dict[int, str] = {}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


def get_queue(guild_id: int) -> deque:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


async def search_track(query: str) -> Optional[dict]:
    """Search for a playable track using yt-dlp."""
    loop = asyncio.get_running_loop()

    def _search():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)

            if not info:
                return None

            if "entries" in info:
                info = next(
                    (entry for entry in info["entries"] if entry),
                    None
                )

            if not info:
                return None

            return {
                "title": info.get("title", "Unknown title"),
                "url": info.get("url"),
                "webpage_url": info.get("webpage_url", query),
                "duration": info.get("duration"),
            }

    return await loop.run_in_executor(None, _search)


def format_duration(seconds):
    if seconds is None:
        return "Unknown"

    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


async def start_next(
    guild: discord.Guild,
    text_channel: discord.abc.Messageable
):
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return

    queue = get_queue(guild.id)

    if not queue:
        now_playing.pop(guild.id, None)
        return

    track = queue.popleft()
    now_playing[guild.id] = track["title"]

    try:
        # Refresh the stream URL before playback
        refreshed = await search_track(track["webpage_url"])

        if not refreshed or not refreshed.get("url"):
            await text_channel.send(
                f"❌ Audio পাওয়া যায়নি: **{track['title']}**। "
                "পরের গান চেষ্টা করা হচ্ছে।"
            )
            await start_next(guild, text_channel)
            return

        source = discord.FFmpegPCMAudio(
            refreshed["url"],
            **FFMPEG_OPTIONS
        )
        source = discord.PCMVolumeTransformer(
            source,
            volume=0.5
        )

        def after_playing(error):
            if error:
                logging.error("Audio playback error: %s", error)

            future = asyncio.run_coroutine_threadsafe(
                start_next(guild, text_channel),
                bot.loop
            )

            try:
                future.result()
            except Exception:
                logging.exception("Couldn't start next track")

        vc.play(source, after=after_playing)

        await text_channel.send(
            f"🎶 Now playing: **{track['title']}** "
            f"(`{format_duration(track.get('duration'))}`)"
        )

    except Exception:
        logging.exception("Playback failed")

        now_playing.pop(guild.id, None)

        await text_channel.send(
            "❌ গান চালানো যায়নি। Render Logs দেখো। "
            "FFmpeg, audio source অথবা hosting restriction "
            "কারণ হতে পারে।"
        )


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logging.info("Synced %s slash command(s)", len(synced))
    except Exception:
        logging.exception("Slash command sync failed")

    logging.info(
        "Dark City Music Bot is online as %s",
        bot.user
    )


def get_member_voice(interaction: discord.Interaction):
    if (
        not interaction.guild
        or not isinstance(interaction.user, discord.Member)
    ):
        return None

    if interaction.user.voice:
        return interaction.user.voice.channel

    return None


@bot.tree.command(
    name="join",
    description="Join your current voice channel"
)
@app_commands.guild_only()
async def join(interaction: discord.Interaction):
    channel = get_member_voice(interaction)

    if channel is None:
        await interaction.response.send_message(
            "আগে একটি Voice Channel-এ ঢোকো, তারপর `/join` দাও।",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        vc = interaction.guild.voice_client

        if vc and vc.is_connected():
            if vc.channel != channel:
                await vc.move_to(channel)
        else:
            await channel.connect()

        await interaction.followup.send(
            f"✅ **{channel.name}**-এ ঢুকেছি!"
        )

    except Exception:
        logging.exception("Join voice channel failed")

        await interaction.followup.send(
            "❌ Voice Channel-এ ঢোকা যায়নি। "
            "Bot-এর Connect/Speak permission এবং PyNaCl দেখো।"
        )


@bot.tree.command(
    name="play",
    description="Search and play a song by name"
)
@app_commands.describe(
    song="Song name or a supported audio URL"
)
@app_commands.guild_only()
async def play(
    interaction: discord.Interaction,
    song: str
):
    channel = get_member_voice(interaction)

    if channel is None:
        await interaction.response.send_message(
            "আগে Voice Channel-এ ঢোকো, তারপর `/play` দাও।",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        vc = interaction.guild.voice_client

        if not vc or not vc.is_connected():
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)

        track = await search_track(song)

        if not track or not track.get("url"):
            await interaction.followup.send(
                "❌ Play করার মতো audio পাওয়া যায়নি। "
                "অন্য নাম দিয়ে চেষ্টা করো।"
            )
            return

        get_queue(interaction.guild_id).append(track)

        if not vc.is_playing() and not vc.is_paused():
            await start_next(
                interaction.guild,
                interaction.channel
            )

        await interaction.followup.send(
            f"➕ Queue-তে যোগ হয়েছে: **{track['title']}**"
        )

    except Exception:
        logging.exception("Play command failed")

        await interaction.followup.send(
            "❌ গান খুঁজতে বা চালাতে সমস্যা হয়েছে। "
            "Render Logs দেখো।"
        )


@bot.tree.command(
    name="pause",
    description="Pause the current song"
)
@app_commands.guild_only()
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message(
            "⏸️ গান pause করা হয়েছে।"
        )
    else:
        await interaction.response.send_message(
            "এখন কোনো গান চলছে না।",
            ephemeral=True
        )


@bot.tree.command(
    name="resume",
    description="Resume the paused song"
)
@app_commands.guild_only()
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message(
            "▶️ গান আবার চালু হয়েছে।"
        )
    else:
        await interaction.response.send_message(
            "কোনো গান pause করা নেই।",
            ephemeral=True
        )


@bot.tree.command(
    name="skip",
    description="Skip the current song"
)
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    if vc and (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message(
            "⏭️ গান skip করা হয়েছে।"
        )
        vc.stop()
    else:
        await interaction.response.send_message(
            "এখন কোনো গান চলছে না।",
            ephemeral=True
        )


@bot.tree.command(
    name="stop",
    description="Stop music and clear the queue"
)
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    get_queue(interaction.guild_id).clear()

    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()

    now_playing.pop(interaction.guild_id, None)

    await interaction.response.send_message(
        "⏹️ গান বন্ধ এবং Queue পরিষ্কার করা হয়েছে।"
    )


@bot.tree.command(
    name="queue",
    description="Show queued songs"
)
@app_commands.guild_only()
async def queue(interaction: discord.Interaction):
    tracks = list(get_queue(interaction.guild_id))
    current = now_playing.get(interaction.guild_id)

    lines = []

    if current:
        lines.append(f"🎶 **Now playing:** {current}")

    lines.extend(
        f"{i}. {track['title']}"
        for i, track in enumerate(tracks[:10], start=1)
    )

    if not lines:
        lines = ["Queue খালি।"]

    await interaction.response.send_message(
        "\n".join(lines)
    )


@bot.tree.command(
    name="leave",
    description="Leave the voice channel and clear queue"
)
@app_commands.guild_only()
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    get_queue(interaction.guild_id).clear()
    now_playing.pop(interaction.guild_id, None)

    if vc:
        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await vc.disconnect(force=True)

        await interaction.response.send_message(
            "👋 Voice Channel থেকে বের হয়েছি।"
        )
    else:
        await interaction.response.send_message(
            "আমি কোনো Voice Channel-এ নেই।",
            ephemeral=True
        )


# Start Discord bot exactly once, at the end
bot.run(TOKEN)
