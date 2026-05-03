import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="user-read-currently-playing user-read-playback-state user-modify-playback-state",
    cache_path=".spotify_cache",
    open_browser=False
)

print("Open this URL in your browser:")
print(auth_manager.get_authorize_url())
print()
redirected = input("Paste the full URL you were redirected to: ").strip()
auth_manager.get_access_token(auth_manager.parse_response_code(redirected))

sp = spotipy.Spotify(auth_manager=auth_manager)
user = sp.current_user()
print(f"Authenticated as: {user['display_name']}")
print("Token saved to .spotify_cache — you can now run the bot.")
