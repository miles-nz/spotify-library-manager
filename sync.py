from __future__ import annotations

import logging

from spotipy import Spotify

from cancellation import CancelCheck, check_cancelled

BATCH_SIZE = 50

logger = logging.getLogger(__name__)


def get_playlist_track_uris(
    sp: Spotify,
    playlist_id: str,
    playlist_name: str,
    cancel_check: CancelCheck | None = None,
) -> set[str]:
    uris: set[str] = set()
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(uri,is_local)),next",
        additional_types=["track"],
    )
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri") and not track.get("is_local"):
                uris.add(track["uri"])
        logger.info("playlist '%s': %d tracks fetched so far", playlist_name, len(uris))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return uris


def get_liked_track_uris(
    sp: Spotify, cancel_check: CancelCheck | None = None
) -> set[str]:
    uris: set[str] = set()
    results = sp.current_user_saved_tracks(limit=BATCH_SIZE)
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri"):
                uris.add(track["uri"])
        logger.info("liked songs: %d tracks fetched so far", len(uris))
        check_cancelled(cancel_check)
        results = sp.next(results) if results.get("next") else None
    return uris


def compute_diff(
    playlist_uris: set[str], liked_uris: set[str]
) -> tuple[set[str], set[str]]:
    to_add = playlist_uris - liked_uris
    to_remove = liked_uris - playlist_uris
    logger.info("diff computed: %d to add, %d to remove", len(to_add), len(to_remove))
    return to_add, to_remove


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def apply_diff(sp: Spotify, to_add: set[str], to_remove: set[str]) -> None:
    add_batches = list(_chunks(list(to_add), BATCH_SIZE))
    for i, batch in enumerate(add_batches, start=1):
        logger.info("adding batch %d/%d (%d tracks)", i, len(add_batches), len(batch))
        sp.current_user_saved_tracks_add(tracks=batch)

    remove_batches = list(_chunks(list(to_remove), BATCH_SIZE))
    for i, batch in enumerate(remove_batches, start=1):
        logger.info(
            "removing batch %d/%d (%d tracks)", i, len(remove_batches), len(batch)
        )
        sp.current_user_saved_tracks_delete(tracks=batch)


def get_target_diff(
    sp: Spotify,
    playlist_id_1: str,
    playlist_id_2: str,
    cancel_check: CancelCheck | None = None,
):
    name_1 = sp.playlist(playlist_id_1, fields="name")["name"]
    logger.info("fetching playlist '%s'", name_1)
    playlist_uris = get_playlist_track_uris(sp, playlist_id_1, name_1, cancel_check)
    check_cancelled(cancel_check)

    name_2 = sp.playlist(playlist_id_2, fields="name")["name"]
    logger.info("fetching playlist '%s'", name_2)
    playlist_uris |= get_playlist_track_uris(sp, playlist_id_2, name_2, cancel_check)
    check_cancelled(cancel_check)

    logger.info("fetching liked songs")
    liked_uris = get_liked_track_uris(sp, cancel_check)

    return compute_diff(playlist_uris, liked_uris)
