from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from spotipy import Spotify

from background_job import BackgroundJob
from spotify_client import make_oauth, get_authenticated_client
from sync import apply_diff, get_target_diff
import duplicates as duplicates_module
import playlist_filter as playlist_filter_module

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("app")

# Above this many combined add/remove tracks, skip fetching display names
# up front (that's one extra API request per 50 tracks) and just show
# counts; names can be loaded on demand via /diff/names.
NAME_LOOKUP_THRESHOLD = 200

app = Flask(__name__)

_sync_job = BackgroundJob(["sync", "app"])
_dup_job = BackgroundJob(["duplicates", "app"])
_filter_job = BackgroundJob(["playlist_filter", "app"])


def _credentials_configured() -> bool:
    return bool(
        os.environ.get("SPOTIFY_CLIENT_ID")
        and os.environ.get("SPOTIFY_CLIENT_SECRET")
        and os.environ.get("SPOTIFY_REDIRECT_URI")
    )


def _track_labels(sp: Spotify, uris: set[str]) -> list[str]:
    ids = [uri.split(":")[-1] for uri in uris]
    labels = []
    for i in range(0, len(ids), 50):
        batch = sp.tracks(ids[i : i + 50])["tracks"]
        for track in batch:
            if not track:
                continue
            artists = ", ".join(a["name"] for a in track["artists"])
            labels.append(f"{artists} – {track['name']}")
        logger.info("looked up %d/%d track names", min(i + 50, len(ids)), len(ids))
    return sorted(labels)


def _run_sync_diff(playlist_id_1: str, playlist_id_2: str):
    def target(cancel_check):
        sp = get_authenticated_client()
        return get_target_diff(sp, playlist_id_1, playlist_id_2, cancel_check=cancel_check)

    return target


def _run_duplicate_scan(playlist_ids: list[str]):
    def target(cancel_check):
        sp = get_authenticated_client()
        playlists = [
            {"id": pid, "name": sp.playlist(pid, fields="name")["name"]}
            for pid in playlist_ids
        ]
        return duplicates_module.find_duplicates(sp, playlists, cancel_check=cancel_check)

    return target


def _run_playlist_filter_scan(
    playlist_ids: list[str],
    field: str,
    operator: str,
    value: str,
    value2: str | None,
    destination: dict,
):
    def target(cancel_check):
        sp = get_authenticated_client()
        playlists = [
            {"id": pid, "name": sp.playlist(pid, fields="name")["name"]}
            for pid in playlist_ids
        ]
        matches = playlist_filter_module.find_matches(
            sp, playlists, field, operator, value, value2, cancel_check=cancel_check
        )

        already_in_destination = 0
        similar_versions: dict = {}
        if destination["destination_mode"] == "existing":
            logger.info(
                "fetching destination playlist '%s'", destination["destination_playlist_name"]
            )
            destination_tracks = playlist_filter_module.get_playlist_track_details(
                sp, destination["destination_playlist_id"], cancel_check=cancel_check
            )
            existing_uris = {t["uri"] for t in destination_tracks}
            before_count = len(matches)
            matches = playlist_filter_module.exclude_existing(matches, existing_uris)
            already_in_destination = before_count - len(matches)
            similar_versions = playlist_filter_module.find_similar_versions(
                matches, destination_tracks
            )

        return {
            "matches": matches,
            "already_in_destination": already_in_destination,
            "similar_versions": similar_versions,
            **destination,
        }

    return target


@app.route("/")
def home():
    if not _credentials_configured():
        return render_template("home.html", needs_credentials=True)

    sp = get_authenticated_client()
    return render_template(
        "home.html",
        needs_credentials=False,
        logged_in=sp is not None,
    )


@app.route("/api/playlists")
def api_playlists():
    sp = get_authenticated_client()
    if sp is None:
        return jsonify({"error": "not_authenticated"}), 401

    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for playlist in results["items"]:
            images = playlist.get("images") or []
            playlists.append(
                {
                    "id": playlist["id"],
                    "name": playlist["name"],
                    "image_url": images[0]["url"] if images else None,
                }
            )
        results = sp.next(results) if results.get("next") else None

    return jsonify(playlists)


@app.route("/search")
def search_page():
    if not _credentials_configured():
        return render_template("search.html", needs_credentials=True)

    sp = get_authenticated_client()
    return render_template(
        "search.html",
        needs_credentials=False,
        logged_in=sp is not None,
    )


@app.route("/api/search")
def api_search():
    sp = get_authenticated_client()
    if sp is None:
        return jsonify({"error": "not_authenticated"}), 401

    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    results = sp.search(q=query, type="track", limit=10)
    tracks = []
    for track in results["tracks"]["items"]:
        images = track["album"].get("images") or []
        tracks.append(
            {
                "uri": track["uri"],
                "name": track["name"],
                "artists": ", ".join(a["name"] for a in track["artists"]),
                "image_url": images[0]["url"] if images else None,
            }
        )

    return jsonify(tracks)


@app.route("/login")
def login():
    oauth = make_oauth(open_browser=False)
    return redirect(oauth.get_authorize_url())


@app.route("/callback")
def callback():
    oauth = make_oauth(open_browser=False)
    code = request.args.get("code")
    oauth.get_access_token(code, as_dict=False)
    return redirect(url_for("home"))


# --- Liked Songs Sync -------------------------------------------------


@app.route("/liked-songs-sync")
def liked_songs_sync():
    if not _credentials_configured():
        return render_template("liked_songs_sync.html", needs_credentials=True)

    sp = get_authenticated_client()
    return render_template(
        "liked_songs_sync.html",
        needs_credentials=False,
        logged_in=sp is not None,
    )


@app.route("/liked-songs-sync/diff")
def diff():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    playlist_id_1 = request.args.get("playlist_id_1")
    playlist_id_2 = request.args.get("playlist_id_2")
    if not playlist_id_1 or not playlist_id_2:
        return redirect(url_for("liked_songs_sync"))

    _sync_job.start(_run_sync_diff(playlist_id_1, playlist_id_2))

    return render_template(
        "progress.html",
        status_url=url_for("diff_status"),
        cancel_url=url_for("diff_cancel"),
        result_url=url_for("diff_result"),
        back_url=url_for("liked_songs_sync"),
        heading="Refreshing…",
        description="Reading your playlists and Liked Songs. This can take a few minutes for large libraries.",
    )


@app.route("/liked-songs-sync/diff/status")
def diff_status():
    return jsonify(_sync_job.status())


@app.route("/liked-songs-sync/diff/cancel", methods=["POST"])
def diff_cancel():
    _sync_job.cancel()
    return jsonify({"ok": True})


@app.route("/liked-songs-sync/diff/result")
def diff_result():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    if _sync_job.result is None:
        return redirect(url_for("diff"))
    to_add, to_remove = _sync_job.result

    if len(to_add) + len(to_remove) > NAME_LOOKUP_THRESHOLD:
        return render_template(
            "diff.html",
            add_count=len(to_add),
            remove_count=len(to_remove),
            names_loaded=False,
        )

    return render_template(
        "diff.html",
        add_count=len(to_add),
        remove_count=len(to_remove),
        add_labels=_track_labels(sp, to_add),
        remove_labels=_track_labels(sp, to_remove),
        names_loaded=True,
    )


@app.route("/liked-songs-sync/diff/names")
def diff_names():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    if _sync_job.result is None:
        return redirect(url_for("diff"))
    to_add, to_remove = _sync_job.result

    return render_template(
        "diff.html",
        add_count=len(to_add),
        remove_count=len(to_remove),
        add_labels=_track_labels(sp, to_add),
        remove_labels=_track_labels(sp, to_remove),
        names_loaded=True,
    )


@app.route("/liked-songs-sync/apply", methods=["POST"])
def apply():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    to_add, to_remove = _sync_job.result or (set(), set())
    apply_diff(sp, to_add, to_remove)
    _sync_job.result = None

    return render_template("result.html", added=len(to_add), removed=len(to_remove))


# --- Duplicate Finder ---------------------------------------------------


@app.route("/duplicates")
def duplicates_picker():
    if not _credentials_configured():
        return render_template("duplicates.html", needs_credentials=True)

    sp = get_authenticated_client()
    return render_template(
        "duplicates.html",
        needs_credentials=False,
        logged_in=sp is not None,
    )


@app.route("/duplicates/scan")
def duplicates_scan():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    playlist_ids = request.args.getlist("playlist_id")
    if len(playlist_ids) < 2:
        return redirect(url_for("duplicates_picker"))

    _dup_job.start(_run_duplicate_scan(playlist_ids))

    return render_template(
        "progress.html",
        status_url=url_for("duplicates_scan_status"),
        cancel_url=url_for("duplicates_scan_cancel"),
        result_url=url_for("duplicates_scan_result"),
        back_url=url_for("duplicates_picker"),
        heading="Scanning for duplicates…",
        description="Reading your playlists to find tracks that appear in more than one. This can take a few minutes for large libraries.",
    )


@app.route("/duplicates/scan/status")
def duplicates_scan_status():
    return jsonify(_dup_job.status())


@app.route("/duplicates/scan/cancel", methods=["POST"])
def duplicates_scan_cancel():
    _dup_job.cancel()
    return jsonify({"ok": True})


@app.route("/duplicates/scan/result")
def duplicates_scan_result():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    if _dup_job.result is None:
        return redirect(url_for("duplicates_picker"))

    return render_template("duplicates_result.html", duplicates=_dup_job.result)


@app.route("/duplicates/remove", methods=["POST"])
def duplicates_remove():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    removals = []
    for item in request.form.getlist("remove"):
        playlist_id, _, uri = item.partition("::")
        if playlist_id and uri:
            removals.append({"playlist_id": playlist_id, "uri": uri})

    duplicates_module.remove_from_playlists(sp, removals)
    _dup_job.result = None

    return render_template("duplicates_removed.html", removed_count=len(removals))


# --- Playlist Filter -----------------------------------------------------


@app.route("/playlist-filter")
def playlist_filter_picker():
    if not _credentials_configured():
        return render_template("playlist_filter.html", needs_credentials=True)

    sp = get_authenticated_client()
    return render_template(
        "playlist_filter.html",
        needs_credentials=False,
        logged_in=sp is not None,
    )


@app.route("/playlist-filter/scan")
def playlist_filter_scan():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    playlist_ids = request.args.getlist("playlist_id")
    field = request.args.get("field")
    operator = request.args.get("operator")
    value = request.args.get("value")
    value2 = request.args.get("value2")
    destination_mode = request.args.get("destination_mode")
    destination_playlist_id = request.args.get("destination_playlist_id")
    destination_playlist_name = request.args.get("destination_playlist_name")
    destination_name = request.args.get("destination_name")

    if not playlist_ids or not field or not operator or not value:
        return redirect(url_for("playlist_filter_picker"))
    if destination_mode == "existing" and not destination_playlist_id:
        return redirect(url_for("playlist_filter_picker"))
    if destination_mode == "new" and not destination_name:
        return redirect(url_for("playlist_filter_picker"))

    destination = {
        "destination_mode": destination_mode,
        "destination_playlist_id": destination_playlist_id,
        "destination_playlist_name": destination_playlist_name,
        "destination_name": destination_name,
    }
    _filter_job.start(
        _run_playlist_filter_scan(playlist_ids, field, operator, value, value2, destination)
    )

    return render_template(
        "progress.html",
        status_url=url_for("playlist_filter_scan_status"),
        cancel_url=url_for("playlist_filter_scan_cancel"),
        result_url=url_for("playlist_filter_scan_result"),
        back_url=url_for("playlist_filter_picker"),
        heading="Scanning playlists…",
        description="Reading your playlists to find matching tracks. This can take a few minutes for large libraries.",
    )


@app.route("/playlist-filter/scan/status")
def playlist_filter_scan_status():
    return jsonify(_filter_job.status())


@app.route("/playlist-filter/scan/cancel", methods=["POST"])
def playlist_filter_scan_cancel():
    _filter_job.cancel()
    return jsonify({"ok": True})


@app.route("/playlist-filter/scan/result")
def playlist_filter_scan_result():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    result = _filter_job.result
    if result is None:
        return redirect(url_for("playlist_filter_picker"))

    destination_label = (
        result["destination_name"]
        if result["destination_mode"] == "new"
        else result["destination_playlist_name"]
    )

    return render_template(
        "playlist_filter_result.html",
        matches=result["matches"],
        destination_label=destination_label,
        destination_mode=result["destination_mode"],
        already_in_destination=result["already_in_destination"],
        similar_versions=result.get("similar_versions", {}),
    )


@app.route("/playlist-filter/apply", methods=["POST"])
def playlist_filter_apply():
    if not _credentials_configured():
        return redirect(url_for("home"))

    sp = get_authenticated_client()
    if sp is None:
        return redirect(url_for("login"))

    result = _filter_job.result
    if result is None:
        return redirect(url_for("playlist_filter_picker"))

    match_uris = {m["uri"] for m in result["matches"]}
    selected_uris = [uri for uri in request.form.getlist("track") if uri in match_uris]

    if result["destination_mode"] == "new":
        playlist_id = playlist_filter_module.create_playlist(sp, result["destination_name"])
        playlist_name = result["destination_name"]
    else:
        playlist_id = result["destination_playlist_id"]
        playlist_name = result["destination_playlist_name"]

    added, skipped = playlist_filter_module.add_tracks_to_playlist(sp, playlist_id, selected_uris)
    _filter_job.result = None

    return render_template(
        "playlist_filter_done.html", added=added, skipped=skipped, playlist_name=playlist_name
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8888, debug=True, threaded=True)
