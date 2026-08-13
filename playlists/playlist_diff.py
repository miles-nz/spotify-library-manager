from __future__ import annotations

import logging

from spotipy import Spotify

from core.cancellation import CancelCheck, check_cancelled
import playlists.playlist_filter as playlist_filter

logger = logging.getLogger(__name__)


def get_playlist_tracks(
    sp: Spotify,
    playlist_id: str,
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    """Returns [{"uri", "name", "artists", "album"}, ...] for a playlist,
    skipping local files."""
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(uri,name,artists,album(name),is_local)),next",
        additional_types=["track"],
    )
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
                    }
                )
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return tracks


def _signature(track: dict) -> tuple[str, str, str]:
    """Identifies "the same song" regardless of uri (e.g. a remaster or a
    different regional release): artist + album + title, case/whitespace
    insensitive."""
    return (
        track["artists"].strip().lower(),
        track["album"].strip().lower(),
        track["name"].strip().lower(),
    )


def find_missing_from_tracks(
    source_playlists: list[dict], target_playlists: list[dict]
) -> list[dict]:
    """source_playlists / target_playlists: [{"id": ..., "name": ...,
    "tracks": [...]}, ...] with already-fetched tracks.

    Returns tracks present in the source playlists but absent from every
    target playlist - by uri, or by matching artist/album/title (so a
    different version of a track already in the target isn't treated as
    missing) - deduped by uri, sorted by artist/name:
    [{"uri", "name", "artists", "album"}]
    """
    seen: dict[str, dict] = {}
    for playlist in source_playlists:
        for track in playlist["tracks"]:
            seen.setdefault(track["uri"], track)

    target_uris: set[str] = set()
    target_signatures: set[tuple[str, str, str]] = set()
    for playlist in target_playlists:
        for track in playlist["tracks"]:
            target_uris.add(track["uri"])
            target_signatures.add(_signature(track))

    missing = [
        t
        for uri, t in seen.items()
        if uri not in target_uris and _signature(t) not in target_signatures
    ]
    missing.sort(key=lambda t: (t["artists"].lower(), t["name"].lower()))
    logger.info(
        "found %d track(s) in source playlist(s) missing from target playlist(s)",
        len(missing),
    )
    return missing


def find_missing(
    sp: Spotify,
    source_playlists: list[dict],
    target_playlists: list[dict],
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    """source_playlists / target_playlists: [{"id": ..., "name": ...}, ...]"""

    def fetch(playlists):
        fetched = []
        for playlist in playlists:
            logger.info("fetching playlist '%s'", playlist["name"])
            tracks = get_playlist_tracks(sp, playlist["id"], cancel_check)
            check_cancelled(cancel_check)
            fetched.append({"id": playlist["id"], "name": playlist["name"], "tracks": tracks})
        return fetched

    return find_missing_from_tracks(fetch(source_playlists), fetch(target_playlists))


def add_to_playlists(sp: Spotify, additions: list[dict]) -> dict[str, int]:
    """additions: [{"playlist_id", "uri"}, ...]

    Adds each uri to its playlist (skipping ones already present). Returns
    {playlist_id: added_count}, omitting playlists with nothing added.
    """
    by_playlist: dict[str, list[str]] = {}
    for addition in additions:
        by_playlist.setdefault(addition["playlist_id"], []).append(addition["uri"])

    added_counts: dict[str, int] = {}
    for playlist_id, uris in by_playlist.items():
        added, _skipped = playlist_filter.add_tracks_to_playlist(sp, playlist_id, uris)
        if added:
            added_counts[playlist_id] = added
    return added_counts
