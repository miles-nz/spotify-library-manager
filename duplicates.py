from __future__ import annotations

import logging

from spotipy import Spotify

from cancellation import CancelCheck, check_cancelled

REMOVE_BATCH_SIZE = 100

logger = logging.getLogger(__name__)


def get_playlist_tracks(
    sp: Spotify,
    playlist_id: str,
    playlist_name: str,
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id,
        fields="items(added_at,track(uri,name,artists,is_local)),next",
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
                        "added_at": item.get("added_at"),
                    }
                )
        logger.info("playlist '%s': %d tracks fetched so far", playlist_name, len(tracks))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return tracks


def find_duplicates_from_tracks(playlists: list[dict]) -> list[dict]:
    """playlists: [{"id": ..., "name": ..., "tracks": [...]}, ...] with
    already-fetched tracks (each needing "uri", "name", "artists",
    "added_at").

    Returns duplicate groups, sorted by artist/name:
    [{"uri", "name", "artists",
      "occurrences": [{"playlist_id", "playlist_name", "added_at"}, ...]}]
    """
    occurrences_by_uri: dict[str, list[dict]] = {}
    track_meta: dict[str, tuple[str, str]] = {}

    for playlist in playlists:
        for track in playlist["tracks"]:
            occurrences_by_uri.setdefault(track["uri"], []).append(
                {
                    "playlist_id": playlist["id"],
                    "playlist_name": playlist["name"],
                    "added_at": track["added_at"],
                }
            )
            track_meta[track["uri"]] = (track["name"], track["artists"])

    duplicates = []
    for uri, occurrences in occurrences_by_uri.items():
        if len(occurrences) > 1:
            name, artists = track_meta[uri]
            duplicates.append(
                {
                    "uri": uri,
                    "name": name,
                    "artists": artists,
                    "occurrences": sorted(occurrences, key=lambda o: o["added_at"] or ""),
                }
            )

    duplicates.sort(key=lambda d: (d["artists"].lower(), d["name"].lower()))
    logger.info(
        "found %d duplicate track(s) across %d playlists", len(duplicates), len(playlists)
    )
    return duplicates


def find_duplicates(
    sp: Spotify, playlists: list[dict], cancel_check: CancelCheck | None = None
) -> list[dict]:
    """playlists: [{"id": ..., "name": ...}, ...]"""
    fetched = []
    for playlist in playlists:
        logger.info("fetching playlist '%s'", playlist["name"])
        tracks = get_playlist_tracks(sp, playlist["id"], playlist["name"], cancel_check)
        check_cancelled(cancel_check)
        fetched.append({"id": playlist["id"], "name": playlist["name"], "tracks": tracks})

    return find_duplicates_from_tracks(fetched)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def remove_from_playlists(sp: Spotify, removals: list[dict]) -> None:
    """removals: [{"playlist_id": ..., "uri": ...}, ...]"""
    by_playlist: dict[str, list[str]] = {}
    for removal in removals:
        by_playlist.setdefault(removal["playlist_id"], []).append(removal["uri"])

    for playlist_id, uris in by_playlist.items():
        batches = list(_chunks(uris, REMOVE_BATCH_SIZE))
        for i, batch in enumerate(batches, start=1):
            logger.info(
                "removing %d/%d track(s) from playlist %s (batch %d/%d)",
                len(batch),
                len(uris),
                playlist_id,
                i,
                len(batches),
            )
            sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
