import os
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

user = sp.current_user()
print(f"Authenticated as: {user['display_name']}")
print("Token saved to .spotify_cache — you can now run the bot.")
