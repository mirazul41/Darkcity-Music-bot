
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
    # Prefer YouTube for search, then fall back to SoundCloud if YouTube
    # blocks the Render server. Direct supported audio URLs still work.
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
    """
    Find a track with yt-dlp.
    For song-name searches, try YouTube first and fall back to SoundCloud
    if YouTube refuses the request. This does not bypass login/access controls.
    """
    loop = asyncio.get_running_loop()

    def _search_one(source_query: str):
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(source_query, download=False)

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
                "webpage_url": info.get("webpage_url", source_query),
                "duration": info.get("duration"),
            }

    def _search():
