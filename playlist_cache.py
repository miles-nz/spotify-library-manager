from __future__ import annotations

import logging

from spotipy import Spotify

from cancellation import CancelCheck, check_cancelled

logger = logging.getLogger(__name__)

# Superset of every field any cascade step needs, so a playlist is only ever
# paged through once no matter how many steps use it.
_FIELDS = (
    "items(added_at,track(uri,name,artists,explicit,popularity,is_local,"
    "album(name,release_date))),next"
)


def _parse_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def _fetch_playlist_tracks(
    sp: Spotify,
    playlist_id: str,
    playlist_name: str,
    cancel_check: CancelCheck | None,
) -> list[dict]:
    tracks: list[dict] = []
    results = sp.playlist_items(playlist_id, fields=_FIELDS, additional_types=["track"])
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri") and not track.get("is_local"):
                tracks.append(
                    {
                        "uri": track["uri"],
                        "name": track["name"],
                        "artists": ", ".join(a["name"] for a in track["artists"]),
                        "album": (track.get("album") or {}).get("name") or "",
                        "explicit": bool(track.get("explicit")),
                        "popularity": track.get("popularity"),
                        "release_year": _parse_year(
                            (track.get("album") or {}).get("release_date")
                        ),
                        "added_at": item.get("added_at"),
                    }
                )
        logger.info("playlist '%s': %d tracks fetched so far", playlist_name, len(tracks))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return tracks


def _fetch_liked_track_uris(sp: Spotify, cancel_check: CancelCheck | None) -> set[str]:
    uris: set[str] = set()
    results = sp.current_user_saved_tracks(limit=50)
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri"):
                uris.add(track["uri"])
        logger.info("liked songs: %d tracks fetched so far", len(uris))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return uris


class PlaylistCache:
    """Holds already-fetched playlist tracks (and optionally Liked Songs) so
    a cascade's steps can share one fetch per playlist instead of each step
    re-paging through the same tracks.

    Track dicts: {"uri", "name", "artists", "album", "explicit",
    "popularity", "release_year", "added_at"} - a superset covering every
    field any of the five functions look at.
    """

    def __init__(self) -> None:
        self._playlists: dict[str, dict] = {}
        self._liked_songs: set[str] | None = None

    def ensure_playlists(
        self, sp: Spotify, playlist_ids, cancel_check: CancelCheck | None = None
    ) -> None:
        for playlist_id in playlist_ids:
            if playlist_id in self._playlists:
                continue
            name = sp.playlist(playlist_id, fields="name")["name"]
            logger.info("fetching playlist '%s'", name)
            tracks = _fetch_playlist_tracks(sp, playlist_id, name, cancel_check)
            self._playlists[playlist_id] = {"name": name, "tracks": tracks}
            check_cancelled(cancel_check)

    def ensure_liked_songs(self, sp: Spotify, cancel_check: CancelCheck | None = None) -> None:
        if self._liked_songs is None:
            logger.info("fetching liked songs")
            self._liked_songs = _fetch_liked_track_uris(sp, cancel_check)

    def playlist(self, playlist_id: str) -> dict:
        entry = self._playlists[playlist_id]
        return {"id": playlist_id, "name": entry["name"], "tracks": entry["tracks"]}

    def playlists(self, playlist_ids) -> list[dict]:
        return [self.playlist(playlist_id) for playlist_id in playlist_ids]

    def liked_song_uris(self) -> set[str]:
        return set(self._liked_songs or set())

    def register_new_playlist(self, playlist_id: str, name: str) -> None:
        self._playlists[playlist_id] = {"name": name, "tracks": []}

    def remove_tracks(self, playlist_id: str, uris) -> None:
        uris = set(uris)
        entry = self._playlists.get(playlist_id)
        if entry:
            entry["tracks"] = [t for t in entry["tracks"] if t["uri"] not in uris]

    def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        entry = self._playlists.setdefault(playlist_id, {"name": "", "tracks": []})
        existing = {t["uri"] for t in entry["tracks"]}
        for track in tracks:
            if track["uri"] not in existing:
                entry["tracks"].append(track)
                existing.add(track["uri"])

    def update_liked_songs(self, to_add, to_remove) -> None:
        if self._liked_songs is None:
            return
        self._liked_songs = (self._liked_songs - set(to_remove)) | set(to_add)
