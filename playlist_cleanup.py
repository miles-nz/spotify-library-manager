from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from spotipy import Spotify

from cancellation import CancelCheck, check_cancelled
import playlist_filter

REMOVE_BATCH_SIZE = 100

logger = logging.getLogger(__name__)

FIELD_OPERATORS = {
    "added_date": ["within_last", "older_than"],
    **playlist_filter.FIELD_OPERATORS,
}

_UNIT_DAYS = {"days": 1, "weeks": 7, "months": 30}


def _cutoff_datetime(value: str, unit: str) -> datetime:
    days = int(value) * _UNIT_DAYS[unit]
    return datetime.now(timezone.utc) - timedelta(days=days)


def _parse_added_at(added_at: str | None) -> datetime | None:
    if not added_at:
        return None
    try:
        return datetime.fromisoformat(added_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def matches_criterion(
    track: dict, field: str, operator: str, value: str, value2: str | None
) -> bool:
    if field == "added_date":
        added_dt = _parse_added_at(track.get("added_at"))
        if added_dt is None:
            return False
        cutoff = _cutoff_datetime(value, value2)
        if operator == "within_last":
            return added_dt >= cutoff
        if operator == "older_than":
            return added_dt < cutoff
        return False
    return playlist_filter.matches_criterion(track, field, operator, value, value2)


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
