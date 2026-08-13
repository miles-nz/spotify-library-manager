from __future__ import annotations

import logging

from spotipy import Spotify

from core.cancellation import CancelCheck, check_cancelled
import playlists.playlist_filter as playlist_filter

REMOVE_BATCH_SIZE = 100

logger = logging.getLogger(__name__)

FIELD_OPERATORS = playlist_filter.FIELD_OPERATORS

matches_criterion = playlist_filter.matches_criterion


def find_removals_from_tracks(
    playlist_name: str,
    tracks: list[dict],
    field: str,
    operator: str,
    value: str,
    value2: str | None,
) -> list[dict]:
    """tracks: already-fetched tracks for the playlist.

    Returns tracks in the playlist that do NOT match the keep-criterion,
    sorted by artist/name: [{"uri", "name", "artists", "added_at"}, ...]
    """
    removals = [
        {
            "uri": t["uri"],
            "name": t["name"],
            "artists": t["artists"],
            "added_at": t["added_at"],
        }
        for t in tracks
        if not matches_criterion(t, field, operator, value, value2)
    ]
    removals.sort(key=lambda t: (t["artists"].lower(), t["name"].lower()))
    logger.info(
        "playlist '%s': %d of %d track(s) will be removed",
        playlist_name,
        len(removals),
        len(tracks),
    )
    return removals


def find_removals(
    sp: Spotify,
    playlist_id: str,
    playlist_name: str,
    field: str,
    operator: str,
    value: str,
    value2: str | None,
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    tracks = playlist_filter.get_playlist_tracks(sp, playlist_id, playlist_name, cancel_check)
    check_cancelled(cancel_check)
    return find_removals_from_tracks(playlist_name, tracks, field, operator, value, value2)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def remove_tracks(sp: Spotify, playlist_id: str, uris: list[str]) -> None:
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
