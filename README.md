# botking

A Discord music bot that mirrors your Spotify playback into a voice channel in real time, with YouTube fallback queuing.

## How it works

The bot does **not** stream audio from Spotify directly — Spotify's API does not expose audio streams (they are DRM-protected). Instead, the bot:

1. Reads what is currently playing on your Spotify via the API
2. Searches for that track on YouTube using `yt-dlp`
3. Streams the YouTube audio into the Discord voice channel via FFmpeg

## Commands

| Command | Description |
|---|---|
| `/jam` | Join your voice channel and start mirroring your Spotify playback in real time |
| `/py <song>` | In JAM mode: add a song to the **Spotify queue** (no desync). Outside JAM: play or queue via YouTube |
| `/yt <song>` | Queue a YouTube track. Works inside a JAM (accepts desync — Spotify sync resumes when done) |
| `/skip` | Skip the current track. In JAM mode with no queue, hands control back to Spotify |
| `/salir` | Disconnect the bot from the voice channel |
| `botking play <song>` | Text command to immediately play a song (stops any active JAM) |
| `!sync` | Re-sync slash commands in the current server (owner use) |

### `/py` vs `/yt` inside a JAM

- **`/py`** searches Spotify and calls `add_to_queue` on your account. Spotify handles the order natively, so when the queued track finishes your JAM continues exactly where it left off — no desync.
- **`/yt`** searches YouTube and plays via the bot's internal queue. When the YouTube track ends the bot resumes Spotify sync, but Spotify may have advanced while the YouTube track was playing.

## Setup

### 1. Spotify credentials

Go to [developer.spotify.com](https://developer.spotify.com), create an app, and copy your **Client ID** and **Client Secret**.

These credentials identify **your app**, not your Spotify account. Anyone self-hosting this bot needs to create their own app — do not share yours, as Spotify's rate limits and app bans apply per Client ID.

Add `http://127.0.0.1:8888/callback` as a Redirect URI in your app settings.

### 2. Discord bot token

Go to [discord.com/developers/applications](https://discord.com/developers/applications), create a bot, and copy its token. Enable the **Message Content Intent** under the Bot settings.

### 3. FFmpeg

Install FFmpeg and make sure it is available in your PATH, or set `FFMPEG_PATH` in your `.env` to the full executable path.

### 4. Install dependencies

```bash
pip install discord.py yt-dlp spotipy python-dotenv
```

### 5. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
DISCORD_TOKEN=...
FFMPEG_PATH=ffmpeg
```

### 6. Authenticate with Spotify

Run this once to complete the OAuth flow and save your token locally:

```bash
python spotify_auth.py
```

A browser window will open. Log in and authorize the app. The token is saved to `.spotify_cache` and refreshed automatically from then on.

### 7. Run the bot

```bash
python botking.py
```
