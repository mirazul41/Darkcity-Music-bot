
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
