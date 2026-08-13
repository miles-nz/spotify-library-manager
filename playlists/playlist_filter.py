from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from spotipy import Spotify

from core.cancellation import CancelCheck, check_cancelled

ADD_BATCH_SIZE = 100

# Strips trailing version qualifiers like "(Single Version)", "[Live]", or
# "- Remastered 2011" so title comparisons can tell "same song, different
# version" from "different song".
_BRACKETED_SUFFIX_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+[^-]+$")

logger = logging.getLogger(__name__)

FIELD_OPERATORS = {
    "added_date": ["within_last", "older_than"],
    "release_year": ["is", "before", "after", "between"],
    "popularity": ["at_least", "at_most"],
    "explicit": ["is"],
    "artist": ["contains"],
    "track_name": ["contains"],
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


def _parse_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def get_playlist_tracks(
    sp: Spotify,
    playlist_id: str,
    playlist_name: str,
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id,
        fields=(
            "items(added_at,track(uri,name,artists,explicit,popularity,is_local,"
            "album(release_date))),next"
        ),
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
                        "explicit": bool(track.get("explicit")),
                        "popularity": track.get("popularity"),
                        "release_year": _parse_year((track.get("album") or {}).get("release_date")),
                        "added_at": item.get("added_at"),
                    }
                )
        logger.info("playlist '%s': %d tracks fetched so far", playlist_name, len(tracks))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return tracks


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
    elif field == "release_year":
        year = track["release_year"]
        if year is None:
            return False
        target = int(value)
        if operator == "is":
            return year == target
        if operator == "before":
            return year < target
        if operator == "after":
            return year > target
        if operator == "between":
            target2 = int(value2)
            lo, hi = sorted((target, target2))
            return lo <= year <= hi
    elif field == "popularity":
        popularity = track["popularity"]
        if popularity is None:
            return False
        target = int(value)
        if operator == "at_least":
            return popularity >= target
        if operator == "at_most":
            return popularity <= target
    elif field == "explicit":
        return track["explicit"] == (value == "yes")
    elif field == "artist":
        return value.lower() in track["artists"].lower()
    elif field == "track_name":
        return value.lower() in track["name"].lower()
    return False


def matches_all_criteria(track: dict, criteria: list[dict]) -> bool:
    return all(
        matches_criterion(track, c["field"], c["operator"], c["value"], c.get("value2"))
        for c in criteria
    )


def find_matches_from_tracks(
    source_playlists: list[dict],
    criteria: list[dict],
) -> list[dict]:
    """source_playlists: [{"id": ..., "name": ..., "tracks": [...]}, ...]
    with already-fetched tracks. criteria: [{"field", "operator", "value",
    "value2"}, ...] - a track must match every criterion.

    Returns matching tracks, deduped by uri across source playlists, sorted
    by artist/name: [{"uri", "name", "artists"}]
    """
    seen: dict[str, dict] = {}

    for playlist in source_playlists:
        for track in playlist["tracks"]:
            if track["uri"] in seen:
                continue
            if matches_all_criteria(track, criteria):
                seen[track["uri"]] = {
                    "uri": track["uri"],
                    "name": track["name"],
                    "artists": track["artists"],
                }

    matches = sorted(seen.values(), key=lambda t: (t["artists"].lower(), t["name"].lower()))
    logger.info(
        "found %d matching track(s) across %d source playlist(s)",
        len(matches),
        len(source_playlists),
    )
    return matches


def find_matches(
    sp: Spotify,
    source_playlists: list[dict],
    criteria: list[dict],
    cancel_check: CancelCheck | None = None,
) -> list[dict]:
    """source_playlists: [{"id": ..., "name": ...}, ...]"""
    fetched = []
    for playlist in source_playlists:
        logger.info("fetching playlist '%s'", playlist["name"])
        tracks = get_playlist_tracks(sp, playlist["id"], playlist["name"], cancel_check)
        check_cancelled(cancel_check)
        fetched.append({"id": playlist["id"], "name": playlist["name"], "tracks": tracks})

    return find_matches_from_tracks(fetched, criteria)


def get_playlist_track_uris(
    sp: Spotify, playlist_id: str, cancel_check: CancelCheck | None = None
) -> set[str]:
    uris: set[str] = set()
    results = sp.playlist_items(
        playlist_id, fields="items(track(uri)),next", additional_types=["track"]
    )
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri"):
                uris.add(track["uri"])
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return uris


def exclude_existing(matches: list[dict], existing_uris: set[str]) -> list[dict]:
    return [m for m in matches if m["uri"] not in existing_uris]


def get_playlist_track_details(
    sp: Spotify, playlist_id: str, cancel_check: CancelCheck | None = None
) -> list[dict]:
    """Like get_playlist_track_uris but also fetches name/artists, for the
    "check for similar versions" comparison - one extra field, no extra
    request, since we're already paging through the whole playlist."""
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id, fields="items(track(uri,name,artists)),next", additional_types=["track"]
    )
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri"):
                tracks.append(
                    {
                        "uri": track["uri"],
                        "name": track["name"],
                        "artists": ", ".join(a["name"] for a in track["artists"]),
                    }
                )
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return tracks


def _normalize_title(title: str) -> str:
    cleaned = title.strip().lower()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _BRACKETED_SUFFIX_RE.sub("", cleaned).strip()
    return _DASH_SUFFIX_RE.sub("", cleaned).strip()


def _primary_artist(artists: str) -> str:
    return artists.split(",")[0].strip().lower()


def find_similar_versions(
    matches: list[dict], destination_tracks: list[dict]
) -> dict[str, list[dict]]:
    """For each match, finds tracks already in the destination that share the
    same primary artist and normalized title (version qualifiers like
    "(Single Version)"/"- Remastered 2011" stripped) but a different uri -
    i.e. likely a different version of the same song. Only matches with at
    least one hit are included: {match_uri: [{"uri", "name", "artists"}, ...]}
    """
    by_key: dict[tuple[str, str], list[dict]] = {}
    for track in destination_tracks:
        key = (_primary_artist(track["artists"]), _normalize_title(track["name"]))
        by_key.setdefault(key, []).append(track)

    result: dict[str, list[dict]] = {}
    for match in matches:
        key = (_primary_artist(match["artists"]), _normalize_title(match["name"]))
        candidates = [t for t in by_key.get(key, []) if t["uri"] != match["uri"]]
        if candidates:
            result[match["uri"]] = candidates
    return result


def create_playlist(sp: Spotify, name: str) -> str:
    user_id = sp.current_user()["id"]
    playlist = sp.user_playlist_create(user_id, name, public=False)
    logger.info("created playlist '%s' (%s)", name, playlist["id"])
    return playlist["id"]


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def add_new_tracks_to_playlist(
    sp: Spotify, playlist_id: str, uris: list[str], existing_uris
) -> tuple[int, int]:
    """Adds uris not already in existing_uris. Returns (added, skipped)."""
    to_add = [uri for uri in uris if uri not in existing_uris]
    skipped = len(uris) - len(to_add)

    batches = list(_chunks(to_add, ADD_BATCH_SIZE))
    for i, batch in enumerate(batches, start=1):
        logger.info(
            "adding %d/%d track(s) to playlist %s (batch %d/%d)",
            len(batch),
            len(to_add),
            playlist_id,
            i,
            len(batches),
        )
        sp.playlist_add_items(playlist_id, batch)

    return len(to_add), skipped


def add_tracks_to_playlist(sp: Spotify, playlist_id: str, uris: list[str]) -> tuple[int, int]:
    """Adds uris not already present in the playlist. Returns (added, skipped)."""
    existing = get_playlist_track_uris(sp, playlist_id)
    return add_new_tracks_to_playlist(sp, playlist_id, uris, existing)
