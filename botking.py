from discord import app_commands, Intents, Client, Interaction
import discord
import random
import asyncio
from collections import deque
import os
import json
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Per-guild prefix storage

PREFIXES_FILE = "prefixes.json"

def _load_prefixes() -> dict[int, str]:
    try:
        with open(PREFIXES_FILE) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_prefixes() -> None:
    with open(PREFIXES_FILE, "w") as f:
        json.dump({str(k): v for k, v in guild_prefixes.items()}, f)

guild_prefixes: dict[int, str] = _load_prefixes()

# ---------------------------------------------------------------------------

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="user-read-currently-playing user-read-playback-state user-modify-playback-state",
    cache_path=".spotify_cache"
))

FFMPEG_EXE = os.getenv("FFMPEG_PATH", "ffmpeg")
FFMPEG_OPTS = {
    'executable': FFMPEG_EXE,
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
}

# ---------------------------------------------------------------------------

class Bot(Client):
    def __init__(self, *, intents: Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.jam_task: asyncio.Task | None = None
        self.jam_paused: bool = False  # True while a YouTube-queued song is playing mid-JAM
        self.paused: bool = False

    async def setup_hook(self) -> None:
        await self.tree.sync()

intents = Intents.default()
intents.message_content = True
bot = Bot(intents=intents)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content == "!sync":
        await bot.tree.sync(guild=message.guild)
        await message.channel.send(f"Commands synced in **{message.guild.name}** ✓")
        return

    content = message.content.lower()
    if content.startswith("botking play "):
        query = message.content[len("botking play "):].strip()
        if not query:
            return

        if not message.author.voice:
            await message.channel.send("You need to be in a voice channel!")
            return

        channel = message.author.voice.channel
        vc = message.guild.voice_client
        if vc:
            await vc.move_to(channel)
        else:
            vc = await channel.connect()

        await message.channel.send(f"Searching **{query}**...")
        try:
            url = await asyncio.to_thread(fetch_audio_url, query)
        except Exception:
            await message.channel.send(f"Nothing found for `{query}`.")
            return

        if vc.is_playing():
            vc.stop()
        if bot.jam_task:
            bot.jam_task.cancel()
            bot.jam_task = None
        vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
        await message.channel.send(f"Now playing: **{query}** :notes:")
        return

    if message.guild:
        pfx = guild_prefixes.get(message.guild.id)
        if pfx and message.content.startswith(pfx):
            parts = message.content[len(pfx):].strip().split(maxsplit=1)
            if parts:
                await handle_prefix_command(message, parts[0].lower(), parts[1] if len(parts) > 1 else "")

@bot.event
async def on_ready():
    print(f"Logged in as: {bot.user}", flush=True)
    cmds = [c.name for c in bot.tree.get_commands()]
    print(f"Registered slash commands: {cmds}", flush=True)

@bot.tree.command()
async def listo(interaction: Interaction):
    await interaction.response.send_message("Ready bbto!")

@bot.tree.command()
async def ruleta(interaction: Interaction):
    side1 = 'CT :black_large_square:'
    side2 = 'TT :yellow_square:'
    middle = ':game_die: DICE :game_die:'
    result = random.choices([side1, side2, middle], [48, 48, 4], k=1)[0]
    if result == middle:
        await interaction.response.send_message('> Bet on **' + result + '**\n> this ain\'t coke bro\n> GL bbto!! :fingers_crossed::skin-tone-2:')
    else:
        await interaction.response.send_message('> Bet on **' + result + '**\n> GL bbto!!')

# ---------------------------------------------------------------------------
# Audio / Spotify helpers

def fetch_audio_url(query: str) -> str:
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(f"ytsearch:{query}", download=False)
        return info['entries'][0]['url']

def search_spotify_track(query: str) -> dict | None:
    results = sp.search(query, type='track', limit=1)
    items = results['tracks']['items']
    if not items:
        return None
    t = items[0]
    return {
        'uri':    t['uri'],
        'name':   t['name'],
        'artist': t['artists'][0]['name'],
    }

def current_track() -> dict | None:
    playback = sp.current_playback()
    if not playback or not playback.get('item'):
        return None
    item = playback['item']
    return {
        'id':     item['id'],
        'name':   item['name'],
        'artist': item['artists'][0]['name'],
    }

INACTIVITY_TIMEOUT = 5 * 60  # seconds of silence before disconnecting

async def sync_loop(vc: discord.VoiceClient, initial_id: str, channel: discord.TextChannel):
    last_id = initial_id
    idle_since = None
    while True:
        await asyncio.sleep(5)
        try:
            if not vc.is_connected():
                break

            if bot.paused:
                continue

            # YouTube queue takes priority: wait for current track to finish, then play next
            if bot.jam_paused:
                if not vc.is_playing():
                    if song_queue:
                        asyncio.create_task(play_next_jam(vc, channel))
                    else:
                        bot.jam_paused = False  # queue empty, hand back control to Spotify sync
                continue

            track = await asyncio.to_thread(current_track)
            if not track and not vc.is_playing():
                if idle_since is None:
                    idle_since = asyncio.get_event_loop().time()
                elif asyncio.get_event_loop().time() - idle_since >= INACTIVITY_TIMEOUT:
                    print("[jam] Inactivity timeout — disconnecting.", flush=True)
                    await vc.disconnect()
                    break
                last_id = None
                continue

            idle_since = None
            if not track or track['id'] == last_id:
                continue

            last_id = track['id']
            print(f"[jam] Switching to: {track['name']} — {track['artist']}", flush=True)
            url = await asyncio.to_thread(fetch_audio_url, f"{track['artist']} - {track['name']}")
            if vc.is_playing():
                vc.stop()
            vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
            await channel.send(f":notes: Now playing: **{track['name']}** — {track['artist']}")
        except Exception as e:
            print(f"[jam] sync error: {e}", flush=True)

# ---------------------------------------------------------------------------
# Playback queues

song_queue: deque[str] = deque()

async def play_next(vc: discord.VoiceClient, channel: discord.TextChannel):
    if not song_queue or not vc.is_connected():
        return
    query = song_queue.popleft()
    try:
        url = await asyncio.to_thread(fetch_audio_url, query)
        vc.play(
            discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
            after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, channel), bot.loop)
        )
        await channel.send(f":notes: Now playing: **{query}**")
        print(f"[queue] Playing: {query}", flush=True)
    except Exception as e:
        print(f"[queue] error: {e}", flush=True)
        await play_next(vc, channel)

async def play_next_jam(vc: discord.VoiceClient, channel: discord.TextChannel):
    """Play the next YouTube-queued track during a JAM; hands back to Spotify sync when empty."""
    if not song_queue or not vc.is_connected():
        bot.jam_paused = False
        return
    query = song_queue.popleft()
    try:
        url = await asyncio.to_thread(fetch_audio_url, query)
        vc.play(
            discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
            after=lambda e: asyncio.run_coroutine_threadsafe(play_next_jam(vc, channel), bot.loop)
        )
        await channel.send(f":notes: Now playing: **{query}**")
        print(f"[jam-queue] Playing: {query}", flush=True)
    except Exception as e:
        print(f"[jam-queue] error: {e}", flush=True)
        await play_next_jam(vc, channel)

# ---------------------------------------------------------------------------
# Help

def build_help_embed(lang: str) -> discord.Embed:
    if lang == "en":
        embed = discord.Embed(title="botking — commands", color=0x1DB954)
        embed.add_field(name="🎵  Music", value=(
            "`/jam` — Sync your Spotify into the voice channel in real time\n"
            "`/py <song>` — In JAM: queue on Spotify (no desync). Otherwise: play via YouTube\n"
            "`/yt <song>` — Queue a YouTube track (works in JAM, accepts desync)\n"
            "`/skip` — Skip the current track\n"
            "`/pause` — Pause / resume\n"
            "`/stop` — Stop playback and clear queue, stay in channel\n"
            "`/clear` — Clear the YouTube queue\n"
            "`/exit` — Disconnect from voice channel\n"
            "`botking play <song>` — Immediately play a song (text command)"
        ), inline=False)
        embed.add_field(name="🎲  Fun", value=(
            "`/ruleta` — Pick a CS2 side (CT / TT / Dice)\n"
            "`/listo` — Ready check"
        ), inline=False)
    else:
        embed = discord.Embed(title="botking — comandos", color=0x1DB954)
        embed.add_field(name="🎵  Música", value=(
            "`/jam` — Sincroniza tu Spotify al canal de voz en tiempo real\n"
            "`/py <canción>` — En JAM: encola en Spotify (sin desync). Si no: reproduce por YouTube\n"
            "`/yt <canción>` — Encola un tema de YouTube (funciona en JAM, acepta desync)\n"
            "`/skip` — Saltea el tema actual\n"
            "`/pause` — Pausar / reanudar\n"
            "`/stop` — Para la reproducción y limpia la cola, queda en el canal\n"
            "`/clear` — Limpia la cola de YouTube\n"
            "`/exit` — Desconectar del canal de voz\n"
            "`botking play <canción>` — Reproduce de inmediato (comando de texto)"
        ), inline=False)
        embed.add_field(name="🎲  Diversión", value=(
            "`/ruleta` — Elige un lado en CS2 (CT / TT / Dados)\n"
            "`/listo` — Listo"
        ), inline=False)
    return embed


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.lang = "en"

    @discord.ui.button(label="Ver en Español", style=discord.ButtonStyle.secondary)
    async def toggle_lang(self, interaction: Interaction, button: discord.ui.Button):
        self.lang = "es" if self.lang == "en" else "en"
        button.label = "Ver en Español" if self.lang == "en" else "View in English"
        await interaction.response.edit_message(embed=build_help_embed(self.lang), view=self)

# ---------------------------------------------------------------------------
# Voice commands

@bot.tree.command(description="Play a song or queue it if something is already playing")
@app_commands.describe(song="Song name or artist to search")
async def py(interaction: Interaction, song: str):
    if not interaction.user.voice:
        await interaction.response.send_message("You need to be in a voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    # JAM mode: queue directly in Spotify to avoid desync
    if bot.jam_task and not bot.jam_task.done():
        track_info = await asyncio.to_thread(search_spotify_track, song)
        if not track_info:
            await interaction.followup.send(f"Couldn't find `{song}` on Spotify. Try `/yt` to search YouTube instead.")
            return
        try:
            await asyncio.to_thread(sp.add_to_queue, track_info['uri'])
        except Exception as e:
            await interaction.followup.send(f"Failed to queue on Spotify: `{e}`")
            return
        await interaction.followup.send(f"Added to Spotify queue: **{track_info['name']}** — {track_info['artist']} :notes:")
        return

    # Normal mode
    if vc.is_playing():
        song_queue.append(song)
        await interaction.followup.send(f"Added to queue (#{len(song_queue)}): **{song}** :notes:")
        return

    try:
        url = await asyncio.to_thread(fetch_audio_url, song)
    except Exception:
        await interaction.followup.send(f"Nothing found for `{song}`.")
        return

    vc.play(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
        after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, interaction.channel), bot.loop)
    )
    await interaction.followup.send(f"Now playing: **{song}** :notes:")

@bot.tree.command(description="Queue a YouTube track (accepts desync inside a JAM)")
@app_commands.describe(song="Song name or artist to search on YouTube")
async def yt(interaction: Interaction, song: str):
    if not interaction.user.voice:
        await interaction.response.send_message("You need to be in a voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    if bot.jam_task and not bot.jam_task.done():
        song_queue.append(song)
        bot.jam_paused = True
        await interaction.followup.send(f"Added to YouTube JAM queue (#{len(song_queue)}): **{song}** :notes:")
        return

    if vc.is_playing():
        song_queue.append(song)
        await interaction.followup.send(f"Added to queue (#{len(song_queue)}): **{song}** :notes:")
        return

    try:
        url = await asyncio.to_thread(fetch_audio_url, song)
    except Exception:
        await interaction.followup.send(f"Nothing found for `{song}`.")
        return

    vc.play(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
        after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, interaction.channel), bot.loop)
    )
    await interaction.followup.send(f"Now playing: **{song}** :notes:")

@bot.tree.command(description="Skip the current track")
async def skip(interaction: Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
        return
    in_jam = bot.jam_task and not bot.jam_task.done()
    if song_queue:
        await interaction.response.send_message(f"Skipping... next up: **{song_queue[0]}** :track_next:")
    elif in_jam:
        await interaction.response.send_message("Skipping... Spotify takes back control :headphones:")
    else:
        await interaction.response.send_message("Skipping... no more tracks in the queue.")
    vc.stop()

@bot.tree.command(description="Connect and sync whatever is playing on your Spotify in real time")
async def jam(interaction: Interaction):
    song_queue.clear()
    bot.jam_paused = False
    if not interaction.user.voice:
        await interaction.response.send_message("You need to be in a voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    track = await asyncio.to_thread(current_track)

    if track:
        url = await asyncio.to_thread(fetch_audio_url, f"{track['artist']} - {track['name']}")
        if vc.is_playing():
            vc.stop()
        vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
        await interaction.followup.send(f"Now playing: **{track['name']}** — {track['artist']}\nSyncing with your Spotify JAM :headphones:")
    else:
        await interaction.followup.send("Nothing playing on Spotify right now. I'll start when something does :headphones:")

    if bot.jam_task:
        bot.jam_task.cancel()
    bot.jam_task = asyncio.create_task(sync_loop(vc, track['id'] if track else None, interaction.channel))

@bot.tree.command(description="Pause or resume the current track")
async def pause(interaction: Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
        return
    if vc.is_paused():
        vc.resume()
        bot.paused = False
        await interaction.response.send_message("Resumed :arrow_forward:")
    elif vc.is_playing():
        vc.pause()
        bot.paused = True
        await interaction.response.send_message("Paused :pause_button:")
    else:
        await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)

@bot.tree.command(description="Stop playback and clear the queue, but stay in the channel")
async def stop(interaction: Interaction):
    if bot.jam_task:
        bot.jam_task.cancel()
        bot.jam_task = None
    bot.jam_paused = False
    bot.paused = False
    song_queue.clear()
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("Stopped :stop_button:")
    else:
        await interaction.response.send_message("Nothing to stop.", ephemeral=True)

@bot.tree.command(description="Clear the YouTube queue (in JAM mode, Spotify sync resumes immediately)")
async def clear(interaction: Interaction):
    if not song_queue and not bot.jam_paused:
        await interaction.response.send_message("The queue is already empty.", ephemeral=True)
        return
    song_queue.clear()
    bot.jam_paused = False
    await interaction.response.send_message("Queue cleared :wastebasket:")

@bot.tree.command(name="exit", description="Disconnect the bot from the voice channel")
async def exit_vc(interaction: Interaction):
    if bot.jam_task:
        bot.jam_task.cancel()
        bot.jam_task = None
    bot.jam_paused = False
    bot.paused = False
    song_queue.clear()
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Bye!")
    else:
        await interaction.response.send_message("I'm not in any voice channel.", ephemeral=True)

# ---------------------------------------------------------------------------

@bot.tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: Interaction):
    await interaction.response.send_message(embed=build_help_embed("en"), view=HelpView())

@bot.tree.command(name="prefix", description="Set a text prefix for commands (e.g. ! → !py, !skip). Leave empty to remove.")
@app_commands.describe(prefix="Prefix character(s) to use. Leave empty to remove the current prefix.")
async def set_prefix(interaction: Interaction, prefix: str = ""):
    if prefix == "/":
        await interaction.response.send_message("Can't use `/` as a prefix — it conflicts with slash commands.", ephemeral=True)
        return
    if not prefix:
        guild_prefixes.pop(interaction.guild_id, None)
        _save_prefixes()
        await interaction.response.send_message("Prefix removed. Slash commands only.", ephemeral=True)
        return
    guild_prefixes[interaction.guild_id] = prefix
    _save_prefixes()
    cmds = " ".join(f"`{prefix}{c}`" for c in ("py", "yt", "jam", "skip", "pause", "stop", "clear", "exit", "help"))
    await interaction.response.send_message(f"Prefix set to `{prefix}` — {cmds}", ephemeral=True)

# ---------------------------------------------------------------------------

async def handle_prefix_command(message: discord.Message, cmd: str, args: str):
    ch = message.channel
    vc = message.guild.voice_client

    if cmd == "help":
        await ch.send(embed=build_help_embed("en"), view=HelpView())
        return

    if cmd == "skip":
        if not vc or not vc.is_playing():
            await ch.send("Nothing is playing right now.")
            return
        in_jam = bot.jam_task and not bot.jam_task.done()
        if song_queue:
            await ch.send(f"Skipping... next up: **{song_queue[0]}** :track_next:")
        elif in_jam:
            await ch.send("Skipping... Spotify takes back control :headphones:")
        else:
            await ch.send("Skipping... no more tracks in the queue.")
        vc.stop()
        return

    if cmd == "pause":
        if not vc:
            await ch.send("I'm not in a voice channel.")
            return
        if vc.is_paused():
            vc.resume()
            bot.paused = False
            await ch.send("Resumed :arrow_forward:")
        elif vc.is_playing():
            vc.pause()
            bot.paused = True
            await ch.send("Paused :pause_button:")
        else:
            await ch.send("Nothing is playing right now.")
        return

    if cmd == "stop":
        if bot.jam_task:
            bot.jam_task.cancel()
            bot.jam_task = None
        bot.jam_paused = False
        bot.paused = False
        song_queue.clear()
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await ch.send("Stopped :stop_button:")
        else:
            await ch.send("Nothing to stop.")
        return

    if cmd == "clear":
        if not song_queue and not bot.jam_paused:
            await ch.send("The queue is already empty.")
            return
        song_queue.clear()
        bot.jam_paused = False
        await ch.send("Queue cleared :wastebasket:")
        return

    if cmd == "exit":
        if bot.jam_task:
            bot.jam_task.cancel()
            bot.jam_task = None
        bot.jam_paused = False
        bot.paused = False
        song_queue.clear()
        if vc:
            await vc.disconnect()
            await ch.send("Bye!")
        else:
            await ch.send("I'm not in any voice channel.")
        return

    if cmd in ("py", "yt", "jam"):
        if not args and cmd != "jam":
            pfx = guild_prefixes.get(message.guild.id, "")
            await ch.send(f"Usage: `{pfx}{cmd} <song>`")
            return
        if not message.author.voice:
            await ch.send("You need to be in a voice channel!")
            return
        voice_ch = message.author.voice.channel
        if vc:
            await vc.move_to(voice_ch)
        else:
            vc = await voice_ch.connect()

        if cmd == "jam":
            song_queue.clear()
            bot.jam_paused = False
            track = await asyncio.to_thread(current_track)
            if track:
                url = await asyncio.to_thread(fetch_audio_url, f"{track['artist']} - {track['name']}")
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
                await ch.send(f"Now playing: **{track['name']}** — {track['artist']}\nSyncing with your Spotify JAM :headphones:")
            else:
                await ch.send("Nothing playing on Spotify right now. I'll start when something does :headphones:")
            if bot.jam_task:
                bot.jam_task.cancel()
            bot.jam_task = asyncio.create_task(sync_loop(vc, track['id'] if track else None, ch))
            return

        if cmd == "py":
            if bot.jam_task and not bot.jam_task.done():
                track_info = await asyncio.to_thread(search_spotify_track, args)
                if not track_info:
                    pfx = guild_prefixes.get(message.guild.id, "")
                    await ch.send(f"Couldn't find `{args}` on Spotify. Try `{pfx}yt` to search YouTube instead.")
                    return
                try:
                    await asyncio.to_thread(sp.add_to_queue, track_info['uri'])
                except Exception as e:
                    await ch.send(f"Failed to queue on Spotify: `{e}`")
                    return
                await ch.send(f"Added to Spotify queue: **{track_info['name']}** — {track_info['artist']} :notes:")
                return
            if vc.is_playing():
                song_queue.append(args)
                await ch.send(f"Added to queue (#{len(song_queue)}): **{args}** :notes:")
                return
            try:
                url = await asyncio.to_thread(fetch_audio_url, args)
            except Exception:
                await ch.send(f"Nothing found for `{args}`.")
                return
            vc.play(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, ch), bot.loop)
            )
            await ch.send(f"Now playing: **{args}** :notes:")
            return

        if cmd == "yt":
            if bot.jam_task and not bot.jam_task.done():
                song_queue.append(args)
                bot.jam_paused = True
                await ch.send(f"Added to YouTube JAM queue (#{len(song_queue)}): **{args}** :notes:")
                return
            if vc.is_playing():
                song_queue.append(args)
                await ch.send(f"Added to queue (#{len(song_queue)}): **{args}** :notes:")
                return
            try:
                url = await asyncio.to_thread(fetch_audio_url, args)
            except Exception:
                await ch.send(f"Nothing found for `{args}`.")
                return
            vc.play(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, ch), bot.loop)
            )
            await ch.send(f"Now playing: **{args}** :notes:")

# ---------------------------------------------------------------------------

bot.run(os.getenv("DISCORD_TOKEN"))
