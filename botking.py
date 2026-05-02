from discord import app_commands, Intents, Client, Interaction
import discord
import random
import asyncio
from collections import deque
import os
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="user-read-currently-playing user-read-playback-state user-modify-playback-state",
    cache_path=".spotify_cache"
))

FFMPEG_EXE = r"C:\Users\7mori\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"
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

# -----------------------------------------------------------------------

class Bot(Client):
    def __init__(self, *, intents: Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.jam_task: asyncio.Task | None = None
        self.jam_paused: bool = False  # True mientras suena algo de la cola en medio de un JAM

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
        await message.channel.send(f"Comandos sincronizados en **{message.guild.name}** ✓")
        return

    content = message.content.lower()
    if content.startswith("botking pone "):
        query = message.content[len("botking pone "):].strip()
        if not query:
            return

        if not message.author.voice:
            await message.channel.send("Tenés que estar en un voice channel!")
            return

        channel = message.author.voice.channel
        vc = message.guild.voice_client
        if vc:
            await vc.move_to(channel)
        else:
            vc = await channel.connect()

        await message.channel.send(f"Buscando **{query}**...")
        try:
            url = await asyncio.to_thread(fetch_audio_url, query)
        except Exception as e:
            await message.channel.send(f"No encontré nada para `{query}`.")
            return

        if vc.is_playing():
            vc.stop()
        if bot.jam_task:
            bot.jam_task.cancel()
            bot.jam_task = None
        vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
        await message.channel.send(f"Reproduciendo: **{query}** :notes:")

@bot.event
async def on_ready():
    print(f"Conectado como: {bot.user}", flush=True)
    cmds = [c.name for c in bot.tree.get_commands()]
    print(f"Comandos registrados globalmente: {cmds}", flush=True)

@bot.tree.command()
async def listo(interaction: Interaction):
    await interaction.response.send_message("Listo bbto!")

@bot.tree.command()
async def ruleta(interaction: Interaction):
    lado1 = 'CT :black_large_square:'
    lado2 = 'TT :yellow_square:'
    medio = ':game_die: DADOS :game_die:'
    resultados = random.choices([lado1, lado2, medio], [48, 48, 4], k=1)
    resultado = resultados[0]
    if resultado == medio:
        await interaction.response.send_message('> Aposta a **'+resultado+'**\n> eto no e coca papiii\n> GL bbto!! :fingers_crossed::skin-tone-2:')
    else:
        await interaction.response.send_message('> Aposta a **'+resultado+'**\n> GL bbto!!')

# -----------------------------------------------------------------------
# Helpers de voz/Spotify

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

def current_track():
    playback = sp.current_playback()
    if not playback or not playback.get('item'):
        return None
    item = playback['item']
    return {
        'id':     item['id'],
        'name':   item['name'],
        'artist': item['artists'][0]['name'],
    }

INACTIVITY_TIMEOUT = 5 * 60  # segundos sin música antes de desconectarse

async def sync_loop(vc: discord.VoiceClient, initial_id: str, channel: discord.TextChannel):
    last_id = initial_id
    idle_since = None
    while True:
        await asyncio.sleep(5)
        try:
            if not vc.is_connected():
                break
            # Cola JAM pendiente: si el track actual terminó, arrancamos el siguiente
            if bot.jam_paused:
                if not vc.is_playing():
                    if song_queue:
                        asyncio.create_task(play_next_jam(vc, channel))
                    else:
                        bot.jam_paused = False  # cola vacía, volvemos al sync normal
                continue
            track = await asyncio.to_thread(current_track)
            if not track and not vc.is_playing():
                if idle_since is None:
                    idle_since = asyncio.get_event_loop().time()
                elif asyncio.get_event_loop().time() - idle_since >= INACTIVITY_TIMEOUT:
                    print("[jam] Inactividad — desconectando.", flush=True)
                    await vc.disconnect()
                    break
                last_id = None
                continue
            idle_since = None
            if not track or track['id'] == last_id:
                continue
            last_id = track['id']
            print(f"[jam] Cambiando a: {track['name']} — {track['artist']}", flush=True)
            url = await asyncio.to_thread(fetch_audio_url, f"{track['artist']} - {track['name']}")
            if vc.is_playing():
                vc.stop()
            vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
            await channel.send(f":notes: Ahora reproduciendo: **{track['name']}** — {track['artist']}")
        except Exception as e:
            print(f"[jam] error en sync: {e}", flush=True)

# -----------------------------------------------------------------------
# Cola de reproducción

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
        await channel.send(f":notes: Ahora reproduciendo: **{query}**")
        print(f"[queue] Reproduciendo: {query}", flush=True)
    except Exception as e:
        print(f"[queue] error: {e}", flush=True)
        await play_next(vc, channel)

async def play_next_jam(vc: discord.VoiceClient, channel: discord.TextChannel):
    """Reproduce el siguiente tema de la cola en modo JAM; si se vacía, devuelve el control al sync de Spotify."""
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
        await channel.send(f":notes: Ahora reproduciendo: **{query}**")
        print(f"[jam-queue] Reproduciendo: {query}", flush=True)
    except Exception as e:
        print(f"[jam-queue] error: {e}", flush=True)
        await play_next_jam(vc, channel)

# -----------------------------------------------------------------------
# Comandos de voz

@bot.tree.command(description="Reproduce una canción o la encola si ya hay algo sonando")
@app_commands.describe(tema="Nombre del tema o artista a reproducir")
async def py(interaction: Interaction, tema: str):
    if not interaction.user.voice:
        await interaction.response.send_message("Tenés que estar en un voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    # Modo JAM activo: encolar directamente en Spotify
    if bot.jam_task and not bot.jam_task.done():
        track_info = await asyncio.to_thread(search_spotify_track, tema)
        if not track_info:
            await interaction.followup.send(f"No encontré `{tema}` en Spotify. Probá con `/yt` para buscar en YouTube.")
            return
        try:
            await asyncio.to_thread(sp.add_to_queue, track_info['uri'])
        except Exception as e:
            await interaction.followup.send(f"No pude encolar en Spotify: `{e}`")
            return
        await interaction.followup.send(f"Agregado a la cola de Spotify: **{track_info['name']}** — {track_info['artist']} :notes:")
        return

    # Modo normal (sin JAM)
    if vc.is_playing():
        song_queue.append(tema)
        await interaction.followup.send(f"Agregado a la cola (#{len(song_queue)}): **{tema}** :notes:")
        return

    try:
        url = await asyncio.to_thread(fetch_audio_url, tema)
    except Exception:
        await interaction.followup.send(f"No encontré nada para `{tema}`.")
        return

    vc.play(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
        after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, interaction.channel), bot.loop)
    )
    await interaction.followup.send(f"Reproduciendo: **{tema}** :notes:")

@bot.tree.command(description="Encola un tema de YouTube (dentro de un JAM acepta el desync)")
@app_commands.describe(tema="Nombre del tema o artista a buscar en YouTube")
async def yt(interaction: Interaction, tema: str):
    if not interaction.user.voice:
        await interaction.response.send_message("Tenés que estar en un voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    if bot.jam_task and not bot.jam_task.done():
        song_queue.append(tema)
        bot.jam_paused = True
        await interaction.followup.send(f"Agregado a la cola YT en JAM (#{len(song_queue)}): **{tema}** :notes:")
        return

    if vc.is_playing():
        song_queue.append(tema)
        await interaction.followup.send(f"Agregado a la cola (#{len(song_queue)}): **{tema}** :notes:")
        return

    try:
        url = await asyncio.to_thread(fetch_audio_url, tema)
    except Exception:
        await interaction.followup.send(f"No encontré nada para `{tema}`.")
        return

    vc.play(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS),
        after=lambda e: asyncio.run_coroutine_threadsafe(play_next(vc, interaction.channel), bot.loop)
    )
    await interaction.followup.send(f"Reproduciendo: **{tema}** :notes:")

@bot.tree.command(description="Salta al siguiente tema en la cola")
async def skip(interaction: Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("No hay nada reproduciendo.", ephemeral=True)
        return
    in_jam = bot.jam_task and not bot.jam_task.done()
    if song_queue:
        await interaction.response.send_message(f"Saltando... siguiente: **{song_queue[0]}** :track_next:")
    elif in_jam:
        await interaction.response.send_message("Saltando... Spotify retoma el control :headphones:")
    else:
        await interaction.response.send_message("Saltando... no hay más temas en la cola.")
    vc.stop()

@bot.tree.command(description="Conecta al voice y reproduce tu Spotify Jam en tiempo real")
async def jam(interaction: Interaction):
    song_queue.clear()
    bot.jam_paused = False
    if not interaction.user.voice:
        await interaction.response.send_message("Tenés que estar en un voice channel!", ephemeral=True)
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
        await interaction.followup.send(f"Reproduciendo: **{track['name']}** — {track['artist']}\nSincronizando con el Jam :headphones:")
    else:
        await interaction.followup.send("Nada sonando en Spotify ahora. Te aviso cuando empiece algo :headphones:")

    if bot.jam_task:
        bot.jam_task.cancel()
    bot.jam_task = asyncio.create_task(sync_loop(vc, track['id'] if track else None, interaction.channel))

@bot.tree.command(description="Desconecta el bot del voice channel")
async def salir(interaction: Interaction):
    if bot.jam_task:
        bot.jam_task.cancel()
        bot.jam_task = None
    bot.jam_paused = False
    song_queue.clear()
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Chau!")
    else:
        await interaction.response.send_message("No estoy en ningún voice.", ephemeral=True)

# -----------------------------------------------------------------------

bot.run(os.getenv("DISCORD_TOKEN"))
