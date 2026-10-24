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
        # If the user gave a URL, try that URL directly.
        is_url = query.strip().lower().startswith(("http://", "https://"))
        if is_url:
            try:
                return _search_one(query.strip())
            except Exception:
                logging.exception("Direct audio URL extraction failed")
                return None

        # Search YouTube first. Render's IP may be blocked by YouTube.
        try:
            result = _search_one(f"ytsearch1:{query}")
            if result and result.get("url"):
                return result
        except Exception as exc:
            logging.warning("YouTube search failed; trying SoundCloud fallback: %s", exc)

        # SoundCloud fallback for song-name searches.
        try:
            result = _search_one(f"scsearch1:{query}")
            if result and result.get("url"):
                return result
        except Exception:
            logging.exception("SoundCloud fallback search failed")

        return None

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
