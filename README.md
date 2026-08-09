# spotify-library-manager

A small local web app with a homepage listing Spotify library-management
functions. Currently available:

- **Liked Songs Sync** - keeps your Liked Songs in sync with the union of
  two playlists: reads both playlists, reads your current Liked Songs, adds
  what's missing, and removes liked tracks that are no longer in either
  playlist.
- **Duplicate Finder** - pick two or more playlists and find tracks that
  appear in more than one of them, with the date each copy was added, so
  you can select which playlist(s) to remove each duplicate from.
- **Track Search** - search Spotify's catalog and preview any track with
  the embedded player.
- **Playlist Filter** - pick one or more playlists and a condition (release
  year, popularity, explicit, artist name, or track name), and add every
  matching track to another playlist - an existing one or a brand new one.

## Setup

1. Register a Spotify app at https://developer.spotify.com/dashboard:
    - Click **Create app**.
    - Redirect URI: `http://127.0.0.1:8888/callback` (must match exactly).
    - APIs used: **Web API**.
    - Save, then copy the **Client ID** and **Client Secret** from Settings.

2. Install dependencies:

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

3. Copy `.env.example` to `.env` and fill in `SPOTIFY_CLIENT_ID` and
   `SPOTIFY_CLIENT_SECRET`.

4. Run the app:
    ```bash
    python app.py
    ```
    Open http://127.0.0.1:8888/, log in if prompted, then click into
    **Liked Songs Sync** and pick your two source playlists from the
    dropdowns (your choice is remembered in the browser - use
    **Edit playlists** to change it later). Click **Refresh**. It shows a
    preview of what will be added/removed - click **Apply** to actually
    update Liked Songs.
