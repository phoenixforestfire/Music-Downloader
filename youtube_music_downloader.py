import sys
import os
import re
import difflib
import json
import time
import urllib.parse
import urllib.request
import unicodedata
import subprocess
from datetime import datetime

from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TIT2,
    TPE1,
    TPE2,
    TALB,
    TDRC,
    TRCK,
    TCON,
    TSRC,
    APIC,
)
from mutagen.mp3 import MP3


# ============================================================
# Configuration
# ============================================================

LIBREWOLF_PROFILE = (
    r"firefox:C:\Users\andyb\AppData\Roaming\librewolf"
    r"\Profiles\etz6ijs4.default-default"
)

SCRIPT_DIRECTORY = os.path.dirname(
    os.path.abspath(__file__)
)

YT_DLP = os.path.join(
    SCRIPT_DIRECTORY,
    "yt-dlp.exe",
)

FFMPEG = os.path.join(
    SCRIPT_DIRECTORY,
    "ffmpeg.exe",
)

DEFAULT_DESTINATION_FOLDER = "Andy Music"

MUSICBRAINZ_USER_AGENT = "AndyMusicDownloader/3.0"
MUSICBRAINZ_DELAY = 1.0


# ============================================================
# Report
# ============================================================

REPORT_CATEGORIES = [
    "successful",
    "completed_with_warnings",
    "duplicates",
    "audio_errors",
    "metadata_errors",
    "missing_metadata",
    "artwork_errors",
    "artwork_unavailable",
    "metadata_differences",
    "url_errors",
    "unavailable",
    "playlist_errors",
    "filename_conflicts",
    "network_api_errors",
    "file_errors",
    "interrupted",
]


REPORT_LABELS = {
    "successful": "Successful",
    "completed_with_warnings": "Completed with warnings",
    "duplicates": "Duplicates skipped",
    "audio_errors": "Audio errors",
    "metadata_errors": "Metadata errors",
    "missing_metadata": "Missing metadata",
    "artwork_errors": "Artwork errors",
    "artwork_unavailable": "Artwork unavailable",
    "metadata_differences": "Metadata differences",
    "url_errors": "URL errors",
    "unavailable": "Unavailable/private/deleted",
    "playlist_errors": "Playlist errors",
    "filename_conflicts": "Filename conflicts",
    "network_api_errors": "Network/API errors",
    "file_errors": "File/permission errors",
    "interrupted": "Interrupted/cancelled",
}


class DownloadReport:
    def __init__(self, folder):
        self.folder = folder

        self.items = {
            category: []
            for category in REPORT_CATEGORIES
        }

        self.metadata_difference_count = 0

        self.start_time = datetime.now()

    def add(
        self,
        category,
        filename,
        reason="",
    ):
        if category not in self.items:
            return

        entry = {
            "filename": str(
                filename or "Unknown"
            ),
            "reason": str(
                reason or ""
            ),
        }

        self.items[category].append(entry)

    def add_metadata_difference(
        self,
        filename,
        differences,
    ):
        if not differences:
            return

        self.metadata_difference_count += 1

        self.items[
            "metadata_differences"
        ].append(
            {
                "filename": filename,
                "reason": "",
                "differences": differences,
            }
        )

    def count(self, category):
        return len(
            self.items.get(
                category,
                [],
            )
        )

    def _write_detail_section(
        self,
        file,
        category,
    ):
        entries = self.items.get(
            category,
            [],
        )

        if not entries:
            return

        file.write(
            f"{REPORT_LABELS[category]}:\n"
        )

        for entry in entries:
            filename = entry["filename"]
            reason = entry.get(
                "reason",
                "",
            )

            if category == "metadata_differences":
                file.write(
                    f"  {filename}\n"
                )

                for difference in entry.get(
                    "differences",
                    [],
                ):
                    file.write(
                        f"    {difference['label']}:\n"
                    )

                    file.write(
                        f"      YouTube Music: "
                        f"{difference['youtube']}\n"
                    )

                    file.write(
                        f"      MusicBrainz:   "
                        f"{difference['musicbrainz']}\n"
                    )

                file.write(
                    "\n"
                )

            else:
                if reason:
                    file.write(
                        f"  {filename} — "
                        f"{reason}\n"
                    )
                else:
                    file.write(
                        f"  {filename}\n"
                    )

        file.write("\n")

    def save(self):
        report_file = os.path.join(
            self.folder,
            "download_report.txt",
        )

        try:
            with open(
                report_file,
                "a",
                encoding="utf-8",
            ) as file:

                file.write("\n")
                file.write(
                    "============================================================\n"
                )
                file.write(
                    "YouTube Music Downloader Report\n"
                )
                file.write(
                    f"Run: "
                    f"{self.start_time.strftime('%B %d, %Y %I:%M:%S %p')}\n"
                )
                file.write(
                    "============================================================\n"
                )
                file.write("\n")

                # Only categories that actually occurred
                for category in REPORT_CATEGORIES:
                    self._write_detail_section(
                        file,
                        category,
                    )

                file.write(
                    "============================================================\n"
                )
                file.write(
                    "Summary\n"
                )
                file.write(
                    "============================================================\n"
                )
                file.write("\n")

                for category in REPORT_CATEGORIES:
                    file.write(
                        f"{REPORT_LABELS[category] + ':':30}"
                        f"{self.count(category)}\n"
                    )

                file.write("\n")
                file.write(
                    "============================================================\n"
                )
                file.write("\n")

            return True

        except Exception as error:
            print()
            print(
                "WARNING: Could not write download report."
            )
            print(error)
            return False


# ============================================================
# General helpers
# ============================================================

def safe_filename(text):
    text = str(text or "Unknown")

    text = (
        text
        .replace("/", "-")
        .replace("\\", "-")
    )

    text = re.sub(
        r'[<>:"|?*]',
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).rstrip(" .")

    return text or "Unknown"


def normalize_text(text):
    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKC",
        str(text),
    ).lower()

    text = (
        text
        .replace("’", "'")
        .replace("‘", "'")
        .replace("&", "and")
    )

    text = re.sub(
        r"[/\\_]",
        " ",
        text,
    )

    # Keep Unicode letters and numbers so Japanese and other non-Latin
    # album/track names can still be matched. Python's \w is Unicode-aware.
    text = re.sub(
        r"[^\w\s]",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def same_text(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    return bool(
        a
        and b
        and a == b
    )


def loose_text(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    return bool(
        a
        and b
        and (
            a == b
            or a in b
            or b in a
        )
    )


def first_year(value):
    match = re.search(
        r"\d{4}",
        str(value or ""),
    )

    return (
        match.group(0)
        if match
        else ""
    )


def artist_credit_to_string(credit):
    parts = []

    for item in credit or []:
        name = (
            item.get("name")
            or item.get(
                "artist",
                {},
            ).get(
                "name",
                "",
            )
        )

        if name:
            parts.append(name)

        parts.append(
            item.get(
                "joinphrase",
                "",
            )
        )

    return "".join(parts).strip()


# ============================================================
# Title parsing
# ============================================================

def clean_title(title):
    title = str(
        title or ""
    ).strip()

    title = re.sub(
        r"\s*[\(\[\{]\s*"
        r"(official\s+video|official\s+audio|official|"
        r"music\s+video|audio|lyrics?|lyric\s+video|"
        r"visualizer|4k|hd)"
        r"\s*[\)\]\}]\s*$",
        "",
        title,
        flags=re.I,
    )

    title = re.sub(
        r"\s*[-|]\s*"
        r"(official\s+video|official\s+audio|official|"
        r"music\s+video|audio|lyrics?|lyric\s+video|"
        r"visualizer|4k|hd)"
        r"\s*$",
        "",
        title,
        flags=re.I,
    )

    return title.strip(" -|")


def split_artist_song(title):
    title = clean_title(title)

    if " - " in title:
        artist, song = title.split(
            " - ",
            1,
        )

        if artist.strip() and song.strip():
            return (
                artist.strip(),
                song.strip(),
            )

    return "", title


# ============================================================
# ID3 helpers
# ============================================================

def id3_text(tags, frame):
    value = tags.get(frame)

    if value is None:
        return ""

    try:
        if getattr(
            value,
            "text",
            None,
        ):
            return str(
                value.text[0]
            ).strip()

        return str(
            value
        ).strip()

    except Exception:
        return ""


# ============================================================
# Destination folder
# ============================================================

def get_destination_folder():
    print()
    print(
        "============================================================"
    )
    print(
        "Destination Folder"
    )
    print(
        "============================================================"
    )
    print()

    name = input(
        f"Folder name [{DEFAULT_DESTINATION_FOLDER}]: "
    ).strip()

    if not name:
        name = DEFAULT_DESTINATION_FOLDER

    name = safe_filename(
        name
        .replace("/", "_")
        .replace("\\", "_")
    )

    folder = os.path.join(
        SCRIPT_DIRECTORY,
        name,
    )

    try:
        os.makedirs(
            folder,
            exist_ok=True,
        )

    except OSError as error:
        print()
        print(
            "ERROR: Could not create destination folder."
        )
        print(error)
        return None

    print()
    print(
        f"Destination: {folder}"
    )

    try:
        os.startfile(folder)
    except Exception:
        pass

    return folder


# ============================================================
# Web requests
# ============================================================

def request_json(url):
    """Request JSON from MusicBrainz with retries for temporary failures."""
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": MUSICBRAINZ_USER_AGENT,
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                data = json.load(response)

            time.sleep(MUSICBRAINZ_DELAY)
            return data

        except urllib.error.HTTPError as error:
            retryable = error.code in {429, 500, 502, 503, 504}
            if retryable and attempt < max_attempts:
                retry_after = error.headers.get("Retry-After", "")
                try:
                    wait = max(2.0, float(retry_after))
                except (TypeError, ValueError):
                    wait = 3.0 * attempt

                print(
                    f"WARNING: MusicBrainz HTTP {error.code}; "
                    f"retrying in {wait:.1f} seconds "
                    f"(attempt {attempt + 1}/{max_attempts})..."
                )
                time.sleep(wait)
                continue

            print(f"WARNING: Web request failed: {error}")
            time.sleep(MUSICBRAINZ_DELAY)
            return None

        except Exception as error:
            if attempt < max_attempts:
                wait = 2.0 * attempt
                print(
                    f"WARNING: Web request failed: {error}; "
                    f"retrying in {wait:.1f} seconds "
                    f"(attempt {attempt + 1}/{max_attempts})..."
                )
                time.sleep(wait)
                continue

            print(f"WARNING: Web request failed: {error}")
            time.sleep(MUSICBRAINZ_DELAY)
            return None

    return None

def download_binary(url):
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    MUSICBRAINZ_USER_AGENT,
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            return (
                response.read(),
                "success",
            )

    except urllib.error.HTTPError as error:
        if error.code == 404:
            return (
                None,
                "not_found",
            )

        return (
            None,
            f"http_{error.code}",
        )

    except Exception as error:
        return (
            None,
            str(error),
        )


# ============================================================
# yt-dlp
# ============================================================

def ytdlp_args():
    return [
        "--cookies-from-browser",
        LIBREWOLF_PROFILE,
        "--no-warnings",
    ]


def run_ytdlp(
    args,
    capture=False,
):
    return subprocess.run(
        [YT_DLP] + args,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def classify_ytdlp_error(stderr):
    text = str(
        stderr or ""
    ).lower()

    unavailable_words = [
        "video unavailable",
        "private video",
        "has been removed",
        "video is unavailable",
        "this video is private",
        "members-only",
        "sign in to confirm",
    ]

    for word in unavailable_words:
        if word in text:
            return "unavailable"

    if (
        "invalid url" in text
        or "unsupported url" in text
    ):
        return "url"

    if (
        "network" in text
        or "connection" in text
        or "timed out" in text
        or "timeout" in text
    ):
        return "network"

    return "other"


def get_youtube_info(
    url,
    quiet=False,
):
    if not quiet:
        print()
        print(
            "Reading YouTube / YouTube Music metadata..."
        )
        print()

    try:
        result = run_ytdlp(
            ytdlp_args()
            + [
                "--dump-single-json",
                "--skip-download",
                url,
            ],
            True,
        )

    except FileNotFoundError:
        print(
            "ERROR: yt-dlp.exe was not found."
        )
        return None, "file"

    if result.returncode != 0:
        print(
            "ERROR: Could not read YouTube metadata."
        )

        if result.stderr:
            print(
                result.stderr.strip()
            )

        return (
            None,
            classify_ytdlp_error(
                result.stderr
            ),
        )

    for line in result.stdout.splitlines():
        try:
            data = json.loads(line)

            if isinstance(data, dict):
                return data, "success"

        except json.JSONDecodeError:
            pass

    print(
        "ERROR: yt-dlp returned no usable JSON metadata."
    )

    return None, "other"


# ============================================================
# YouTube metadata
# ============================================================

def youtube_metadata(info):
    info = info or {}

    raw_title = str(
        info.get("track")
        or info.get("title")
        or ""
    ).strip()

    parsed_artist, parsed_song = (
        split_artist_song(raw_title)
    )

    artist = str(
        info.get("artist")
        or info.get("creator")
        or parsed_artist
        or info.get("channel")
        or info.get("uploader")
        or ""
    ).strip()

    if parsed_artist and parsed_song:
        title = parsed_song
    else:
        title = clean_title(
            raw_title
        )

    album = str(
        info.get("album")
        or ""
    ).strip()

    album_artist = str(
        info.get("album_artist")
        or artist
        or ""
    ).strip()

    track_number = str(
        info.get("track_number")
        or ""
    ).strip()

    year = str(
        info.get("release_year")
        or first_year(
            info.get("release_date")
        )
        or ""
    ).strip()

    isrc = str(
        info.get("isrc")
        or ""
    ).strip()

    return {
        "artist": artist,
        "title": title,
        "album": album,
        "album_artist": album_artist,
        "track": track_number,
        "year": year,
        "isrc": isrc,
        "genre": "",
        "release_id": "",
        "recording_id": "",
        "channel": str(
            info.get("channel")
            or ""
        ).strip(),
        "uploader": str(
            info.get("uploader")
            or ""
        ).strip(),
    }


def print_youtube_metadata(meta):
    print(
        "YouTube / yt-dlp metadata:"
    )

    for label, key in [
        ("Artist", "artist"),
        ("Track", "title"),
        ("Album", "album"),
        ("Album artist", "album_artist"),
        ("Track number", "track"),
        ("Year", "year"),
        ("ISRC", "isrc"),
        ("Channel", "channel"),
    ]:
        print(
            f"  {label + ':':14}"
            f"{meta.get(key) or '(none)'}"
        )

    print()


# ============================================================
# Playlist handling
# ============================================================

def is_playlist_url(url):
    try:
        return bool(
            urllib.parse.parse_qs(
                urllib.parse.urlparse(
                    url
                ).query
            ).get("list")
        )

    except Exception:
        return False


def get_playlist_entries(url):
    print()
    print(
        "Getting playlist information..."
    )
    print()

    try:
        result = run_ytdlp(
            ytdlp_args()
            + [
                "--flat-playlist",
                "--dump-single-json",
                "--skip-download",
                url,
            ],
            True,
        )

    except FileNotFoundError:
        print(
            "ERROR: yt-dlp.exe was not found."
        )
        return [], "file"

    if result.returncode != 0:
        print(
            "ERROR: Could not read the playlist."
        )

        if result.stderr:
            print(
                result.stderr.strip()
            )

        return (
            [],
            classify_ytdlp_error(
                result.stderr
            ),
        )

    data = None

    for line in result.stdout.splitlines():
        try:
            candidate = json.loads(line)

            if isinstance(candidate, dict):
                data = candidate
                break

        except json.JSONDecodeError:
            pass

    if not data:
        return [], "other"

    entries = []

    for entry in data.get(
        "entries",
        [],
    ) or []:

        if not entry:
            continue

        if not entry.get("id"):
            continue

        entries.append(
            {
                "id": entry["id"],
                "url": (
                    entry.get("webpage_url")
                    or entry.get("original_url")
                    or (
                        "https://www.youtube.com/watch?v="
                        + entry["id"]
                    )
                ),
            }
        )

    return entries, "success"


# ============================================================
# MusicBrainz
# ============================================================

def mb_search(
    entity,
    query,
    limit=100,
):
    url = (
        "https://musicbrainz.org/ws/2/"
        f"{entity}/?"
        f"query={urllib.parse.quote(query)}"
        f"&fmt=json"
        f"&limit={limit}"
    )

    if entity == "recording":
        url += (
            "&inc=releases+artist-credits+isrcs"
        )

    elif entity == "release":
        url += (
            "&inc=artist-credits+release-groups"
        )

    return request_json(url)


def release_candidates(recording):
    result = []

    for release in recording.get(
        "releases",
        [],
    ) or []:

        group = release.get(
            "release-group",
            {},
        ) or {}

        if not release.get("id"):
            continue

        result.append(
            {
                "release_id": release["id"],
                "release_group_id": group.get(
                    "id",
                    "",
                ),
                "album": release.get(
                    "title",
                    "",
                ),
                "primary_type": group.get(
                    "primary-type",
                    "",
                ) or "",
                "secondary_types": group.get(
                    "secondary-types",
                    [],
                ) or [],
                "date": (
                    release.get(
                        "date",
                        "",
                    )
                    or group.get(
                        "first-release-date",
                        "",
                    )
                ),
                "status": release.get(
                    "status",
                    "",
                ),
                "track_count": release.get(
                    "track-count",
                    0,
                ),
            }
        )

    return result


def generic_compilation(title):
    title = normalize_text(title)

    phrases = [
        "100 romantic songs",
        "100 greatest",
        "100 hits",
        "100 songs",
        "greatest hits",
        "greatest songs",
        "greatest",
        "best of",
        "the best of",
        "best songs",
        "best hits",
        "essential",
        "essentials",
        "ultimate",
        "collection",
        "anthology",
        "favorites",
        "favourites",
        "top 100",
        "top hits",
        "hitlist",
        "hits",
        "romantic songs",
        "romantic hits",
        "love songs",
        "wedding songs",
        "party songs",
        "party hits",
        "summer songs",
        "summer hits",
        "road trip",
        "throwback",
        "throwbacks",
        "now thats what i call",
        "various artists",
    ]

    return any(
        phrase in title
        for phrase in phrases
    )


def candidate_score(
    recording,
    release,
    meta,
):
    score = 0

    r_artist = (
        artist_credit_to_string(
            recording.get(
                "artist-credit",
                [],
            )
        )
        or "Unknown Artist"
    )

    r_title = recording.get(
        "title",
        "",
    )

    album = release.get(
        "album",
        "",
    )

    secondary = {
        normalize_text(x)
        for x in release.get(
            "secondary_types",
            [],
        )
    }

    primary = normalize_text(
        release.get(
            "primary_type",
            "",
        )
    )

    if same_text(
        meta.get("artist"),
        r_artist,
    ):
        score += 1000
    elif loose_text(
        meta.get("artist"),
        r_artist,
    ):
        score += 300
    else:
        score -= 1000

    if same_text(
        meta.get("title"),
        r_title,
    ):
        score += 1000
    elif loose_text(
        meta.get("title"),
        r_title,
    ):
        score += 300
    else:
        score -= 1000

    if meta.get("album"):
        if same_text(
            meta["album"],
            album,
        ):
            score += 5000
        elif loose_text(
            meta["album"],
            album,
        ):
            score += 1500
        else:
            score -= 500

    score += {
        "album": 500,
        "ep": 150,
        "single": 100,
    }.get(
        primary,
        0,
    )

    if (
        primary == "album"
        and not secondary
    ):
        score += 500

    if generic_compilation(album):
        score -= 2500

    if "compilation" in secondary:
        score -= 2500

    if "live" in secondary:
        score -= 1000

    if "remix" in secondary:
        score -= 1000

    if "demo" in secondary:
        score -= 700

    if "soundtrack" in secondary:
        score -= 700

    if normalize_text(
        release.get("status")
    ) == "official":
        score += 150

    requested_year = first_year(
        meta.get("year")
    )

    release_year = first_year(
        release.get("date")
    )

    if requested_year and release_year:
        if requested_year == release_year:
            score += 4000
        else:
            score -= 4000

    try:
        count = int(
            release.get(
                "track_count",
                0,
            )
            or 0
        )

        if (
            primary == "album"
            and count >= 8
        ):
            score += 150

        if (
            primary == "album"
            and count == 1
        ):
            score -= 300

    except (
        TypeError,
        ValueError,
    ):
        pass

    return score


def search_recordings(meta):
    artist = meta.get(
        "artist",
        "",
    )

    title = meta.get(
        "title",
        "",
    )

    album = meta.get(
        "album",
        "",
    )

    isrc = meta.get(
        "isrc",
        "",
    )

    searches = []

    if isrc:
        searches.append(
            (
                f'isrc:"{isrc}"',
                "ISRC",
            )
        )

    if artist and title and album:
        searches.append(
            (
                f'recording:"{title}" '
                f'AND artist:"{artist}" '
                f'AND release:"{album}"',
                "artist + track + album",
            )
        )

    if artist and title:
        searches.append(
            (
                f'recording:"{title}" '
                f'AND artist:"{artist}"',
                "artist + track",
            )
        )

    if title:
        searches.append(
            (
                f'recording:"{title}"',
                "track only",
            )
        )

    seen = set()
    results = []

    for query, label in searches:
        print(
            f"MusicBrainz backup search "
            f"({label}): {query}"
        )

        data = mb_search(
            "recording",
            query,
        )

        if not data:
            continue

        for recording in data.get(
            "recordings",
            [],
        ) or []:

            rid = recording.get(
                "id",
                "",
            )

            if (
                rid
                and rid not in seen
            ):
                seen.add(rid)
                results.append(
                    recording
                )

        # Do not stop after the first successful search. MusicBrainz can
        # return a poor recording match even when a better match is found
        # by another query. Collect all candidates and let the scorer rank
        # them instead.

    return results


def artist_affinity(a, b):
    """Return a score showing how closely two artist-credit strings relate."""
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm or not b_norm:
        return 0

    if a_norm == b_norm:
        return 5000

    if a_norm in b_norm or b_norm in a_norm:
        return 2500

    # YouTube and MusicBrainz often format multi-artist credits differently.
    # Compare individual artist names as well as the complete credit string.
    split_pattern = r'\s*(?:,|;|&|/|\band\b|\bfeat\.?\b|\bft\.?\b)\s*'
    a_parts = [normalize_text(x) for x in re.split(split_pattern, str(a), flags=re.I) if normalize_text(x)]
    b_parts = [normalize_text(x) for x in re.split(split_pattern, str(b), flags=re.I) if normalize_text(x)]

    if any(x == y for x in a_parts for y in b_parts):
        return 3000

    if any((x in y or y in x) for x in a_parts for y in b_parts if x and y):
        return 1800

    return -5000


def artwork_album_variants(album):
    """Return useful MusicBrainz search variants for an album title."""
    album = str(album or "").strip()
    if not album:
        return []

    variants = [album]

    # Digital stores frequently add edition labels that MusicBrainz stores
    # separately or omits, e.g. "Cinematics (Expanded Edition)".
    stripped = re.sub(
        r"\s*[\[(][^\[\]()]+[\])]\s*$",
        "",
        album,
    ).strip()

    if stripped and stripped not in variants:
        variants.append(stripped)

    # Also try a punctuation-light form for titles where MusicBrainz uses a
    # slightly different punctuation/spacing convention.
    simplified = re.sub(
        r"[^\w\s]",
        " ",
        unicodedata.normalize("NFKC", album),
    )
    simplified = re.sub(r"\s+", " ", simplified).strip()

    if simplified and simplified not in variants:
        variants.append(simplified)

    return variants


def artwork_album_match(wanted, candidate):
    """Return True when two album names are close enough for artwork use."""
    wanted_n = normalize_text(wanted)
    candidate_n = normalize_text(candidate)

    if not wanted_n or not candidate_n:
        return False

    if wanted_n == candidate_n:
        return True

    if wanted_n in candidate_n or candidate_n in wanted_n:
        return True

    # Protect against small spelling/transliteration differences such as
    # "Finare" vs "Finale" while still requiring a very close title.
    ratio = difflib.SequenceMatcher(
        None,
        wanted_n,
        candidate_n,
    ).ratio()

    return ratio >= 0.88


def search_releases_for_artwork(meta):
    """Find plausible album releases for artwork only.

    YouTube metadata remains authoritative. These release records are used
    only to find Cover Art Archive images. Artist matching is a ranking
    factor, not a hard filter, because MusicBrainz and YouTube often format
    multi-artist credits differently.
    """
    artist = meta.get("artist", "")
    album = meta.get("album", "")

    if not album:
        return []

    album_variants = artwork_album_variants(album)
    searches = []

    seen_queries = set()

    for variant in album_variants:
        if artist:
            query = f'release:"{variant}" AND artist:"{artist}"'
            if query not in seen_queries:
                searches.append((query, "artist + album"))
                seen_queries.add(query)

        query = f'release:"{variant}"'
        if query not in seen_queries:
            searches.append((query, "album only"))
            seen_queries.add(query)

    # A short descriptive portion can help when the YouTube title contains a
    # store-specific suffix or a small typo. This is deliberately limited so
    # unrelated releases with a generic word like "Album" are not collected.
    words = normalize_text(album).split()
    if len(words) >= 3:
        core = " ".join(words[: min(5, len(words))])
        query = f'release:"{core}"'
        if query not in seen_queries:
            searches.append((query, "album core"))
            seen_queries.add(query)

    seen = set()
    results = []

    for query, label in searches:
        print(
            f"MusicBrainz artwork search ({label}): {query}"
        )

        data = mb_search(
            "release",
            query,
        )

        if not data:
            continue

        for release in data.get("releases", []) or []:
            release_id = release.get("id", "")

            if not release_id or release_id in seen:
                continue

            release_title = release.get("title", "")

            if not any(
                artwork_album_match(variant, release_title)
                for variant in album_variants
            ):
                continue

            release_artist = artist_credit_to_string(
                release.get("artist-credit", [])
            )
            group = release.get("release-group", {}) or {}

            seen.add(release_id)
            results.append(
                {
                    "release_id": release_id,
                    "album": release_title,
                    "artist": release_artist,
                    "primary_type": group.get("primary-type", "") or "",
                    "secondary_types": group.get("secondary-types", []) or [],
                    "date": (
                        release.get("date", "")
                        or group.get("first-release-date", "")
                    ),
                    "status": release.get("status", ""),
                }
            )

    return results

def artwork_release_score(release, meta):
    """Score an album release specifically for artwork use."""
    score = 0

    artist = release.get("artist", "")
    album = release.get("album", "")
    primary = normalize_text(release.get("primary_type", ""))
    secondary = {
        normalize_text(x)
        for x in release.get("secondary_types", [])
    }

    score += artist_affinity(meta.get("artist", ""), artist)

    if same_text(meta.get("album"), album):
        score += 7000
    elif loose_text(meta.get("album"), album):
        score += 1500
    else:
        score -= 12000

    requested_year = first_year(meta.get("year"))
    release_year = first_year(release.get("date"))

    if requested_year and release_year:
        if requested_year == release_year:
            score += 3000
        else:
            score -= 500

    if primary == "album":
        score += 1200
    elif primary == "ep":
        score += 200
    elif primary == "single":
        score -= 500

    if not secondary:
        score += 500

    if "compilation" in secondary:
        score -= 5000
    if "live" in secondary:
        score -= 3000
    if "remix" in secondary:
        score -= 3500
    if "demo" in secondary:
        score -= 2500
    if "soundtrack" in secondary:
        # A soundtrack is not automatically wrong. Keep it usable when the
        # YouTube album itself is a soundtrack.
        if normalize_text(meta.get("album")) != normalize_text(release.get("album")):
            score -= 2000

    if normalize_text(release.get("status")) == "official":
        score += 500

    return score

def recording_artwork_score(candidate, meta):
    """Score a release for artwork using the matched recording as the bridge.

    The recording title is deliberately weighted more heavily than the
    YouTube album name. This handles singles, alternate album names,
    transliterations, edition labels, and soundtrack releases where the
    YouTube album does not match the MusicBrainz release title.
    """
    score = 0

    recording_title = candidate.get("title", "")
    recording_artist = candidate.get("artist", "")
    release = candidate.get("release", {}) or {}

    release_album = release.get("album", "")
    primary = normalize_text(release.get("primary_type", ""))
    secondary = {
        normalize_text(x)
        for x in release.get("secondary_types", [])
    }

    wanted_title = meta.get("title", "")
    wanted_artist = meta.get("artist", "")
    wanted_album = meta.get("album", "")

    if same_text(wanted_title, recording_title):
        score += 12000
    elif loose_text(wanted_title, recording_title):
        score += 8000
    else:
        ratio = difflib.SequenceMatcher(
            None,
            normalize_text(wanted_title),
            normalize_text(recording_title),
        ).ratio()
        if ratio >= 0.90:
            score += 6000
        elif ratio >= 0.82:
            score += 3000
        else:
            score -= 5000

    score += artist_affinity(
        wanted_artist,
        recording_artist,
    )

    if wanted_album:
        if same_text(wanted_album, release_album):
            score += 1500
        elif loose_text(wanted_album, release_album):
            score += 700
        elif artwork_album_match(wanted_album, release_album):
            score += 400
        else:
            # An album mismatch is only a small penalty because the recording
            # itself is the important bridge to the release.
            score -= 500

    if primary == "album":
        score += 800
    elif primary == "ep":
        score += 200
    elif primary == "single":
        score += 100

    if not secondary:
        score += 300

    if "compilation" in secondary:
        score -= 3500
    if "live" in secondary:
        score -= 2500
    if "remix" in secondary:
        score -= 3000
    if "demo" in secondary:
        score -= 2000

    # A soundtrack is perfectly acceptable when the matched recording is
    # actually on that soundtrack. Do not penalize it just for being a
    # soundtrack.
    if "soundtrack" in secondary:
        score += 300

    requested_year = first_year(meta.get("year"))
    release_year = first_year(release.get("date"))

    if requested_year and release_year:
        if requested_year == release_year:
            score += 1000
        else:
            score -= 300

    if normalize_text(release.get("status")) == "official":
        score += 400

    return score


def recording_artwork_candidates(choices, meta, limit=15):
    """Return release IDs ranked from recording matches for artwork."""
    ranked = []
    seen = set()

    for candidate in choices:
        release_id = candidate.get("release_id", "")
        if not release_id or release_id in seen:
            continue

        score = recording_artwork_score(candidate, meta)

        if score <= 0:
            continue

        seen.add(release_id)
        ranked.append((score, candidate))

    ranked.sort(
        key=lambda item: (
            -item[0],
            normalize_text(
                item[1].get("album", "")
            ),
        )
    )

    return [
        candidate.get("release_id", "")
        for score, candidate in ranked[:limit]
        if candidate.get("release_id", "")
    ]


def choose_musicbrainz_release(
    recordings,
    meta,
):
    choices = []

    requested_isrc = normalize_text(
        meta.get("isrc")
    )

    for recording in recordings:
        recording_id = recording.get(
            "id",
            "",
        )

        if not recording_id:
            continue

        recording_isrcs = {
            normalize_text(x)
            for x in recording.get(
                "isrcs",
                [],
            ) or []
        }

        for release in release_candidates(
            recording
        ):
            choices.append(
                {
                    "recording_id": recording_id,
                    "release_id": release[
                        "release_id"
                    ],
                    "artist": (
                        artist_credit_to_string(
                            recording.get(
                                "artist-credit",
                                [],
                            )
                        )
                        or "Unknown Artist"
                    ),
                    "title": recording.get(
                        "title",
                        "",
                    ),
                    "album": release.get(
                        "album",
                        "",
                    ),
                    "year": first_year(
                        release.get(
                            "date",
                            "",
                        )
                    ),
                    "recording": recording,
                    "release": release,
                    "isrc_match": bool(
                        requested_isrc
                        and requested_isrc
                        in recording_isrcs
                    ),
                    "score": candidate_score(
                        recording,
                        release,
                        meta,
                    ),
                }
            )

    # IMPORTANT: Keep recording-derived artwork candidates BEFORE applying
    # the stricter metadata validation. A recording can be the correct song
    # while its release title differs from YouTube's album title.
    recording_artwork_ids = recording_artwork_candidates(
        choices,
        meta,
        limit=15,
    )

    if not choices:
        # There may still be useful album releases for artwork even when
        # MusicBrainz did not return a recording match.
        artwork_releases = search_releases_for_artwork(meta)
        artwork_releases.sort(
            key=lambda x: -artwork_release_score(x, meta)
        )

        artwork_ids = [
            x["release_id"]
            for x in artwork_releases
            if artwork_release_score(x, meta) > 0
        ][:10]

        if artwork_ids:
            return {
                "release_id": "",
                "recording_id": "",
                "artist": "",
                "title": "",
                "album": "",
                "year": "",
                "recording": {},
                "release": {},
                "isrc_match": False,
                "score": 0,
                "artwork_release_ids": artwork_ids,
            }

        return None

    # Reject clearly unrelated recording matches before choosing the
    # MusicBrainz metadata candidate. This does NOT remove those recordings
    # from the artwork candidates calculated above.
    valid_choices = []

    for candidate in choices:
        artist_match = (
            not meta.get("artist")
            or same_text(
                meta.get("artist"),
                candidate.get("artist")
            )
            or loose_text(
                meta.get("artist"),
                candidate.get("artist")
            )
        )

        album_match = (
            not meta.get("album")
            or same_text(
                meta.get("album"),
                candidate.get("album")
            )
            or loose_text(
                meta.get("album"),
                candidate.get("album")
            )
        )

        title_match = (
            not meta.get("title")
            or same_text(
                meta.get("title"),
                candidate.get("title")
            )
            or loose_text(
                meta.get("title"),
                candidate.get("title")
            )
        )

        if artist_match and album_match and title_match:
            valid_choices.append(candidate)

    if valid_choices:
        choices = valid_choices

    elif meta.get("artist") and meta.get("album"):
        # Do not use an unrelated MusicBrainz recording to fill metadata.
        # However, recording-derived artwork candidates are still valid
        # because the track itself can identify the correct soundtrack/release.
        artwork_releases = search_releases_for_artwork(meta)
        artwork_releases.sort(
            key=lambda x: -artwork_release_score(x, meta)
        )

        artwork_release_ids = []

        for release_id in recording_artwork_ids:
            if release_id not in artwork_release_ids:
                artwork_release_ids.append(release_id)

        for release in artwork_releases:
            release_id = release.get(
                "release_id",
                "",
            )

            if (
                release_id
                and release_id not in artwork_release_ids
                and artwork_release_score(release, meta) > 0
            ):
                artwork_release_ids.append(release_id)

            if len(artwork_release_ids) >= 15:
                break

        if artwork_release_ids:
            return {
                "release_id": "",
                "recording_id": "",
                "artist": "",
                "title": "",
                "album": "",
                "year": "",
                "recording": {},
                "release": {},
                "isrc_match": False,
                "score": 0,
                "artwork_release_ids": artwork_release_ids,
            }

        return None

    isrc_choices = [
        x
        for x in choices
        if x["isrc_match"]
    ]

    if isrc_choices:
        isrc_choices.sort(
            key=lambda x: (
                -x["score"],
                x["album"].lower(),
            )
        )
        ordered_choices = isrc_choices
    else:
        choices.sort(
            key=lambda x: (
                -x["score"],
                x["album"].lower(),
            )
        )
        ordered_choices = choices

    # The first choice remains the authoritative MusicBrainz candidate for
    # filling missing metadata. The remaining matches are artwork fallbacks.
    best = ordered_choices[0]

    artwork_release_ids = []

    # Prefer releases attached to an exact/strong recording match.
    for release_id in recording_artwork_ids:
        if (
            release_id
            and release_id not in artwork_release_ids
        ):
            artwork_release_ids.append(
                release_id
            )

        if len(artwork_release_ids) >= 15:
            break

    # Then use direct album-release searches as a secondary artwork source.
    artwork_releases = search_releases_for_artwork(meta)
    artwork_releases.sort(
        key=lambda x: -artwork_release_score(x, meta)
    )

    for release in artwork_releases:
        release_id = release.get(
            "release_id",
            "",
        )

        if (
            release_id
            and release_id not in artwork_release_ids
            and artwork_release_score(release, meta) > 0
        ):
            artwork_release_ids.append(
                release_id
            )

        if len(artwork_release_ids) >= 15:
            break

    best["artwork_release_ids"] = artwork_release_ids

    return best

def get_release_details(
    release_id,
):
    if not release_id:
        return None

    url = (
        "https://musicbrainz.org/ws/2/"
        "release/"
        + urllib.parse.quote(
            release_id
        )
        + "?fmt=json"
        "&inc=artists+artist-credits+"
        "recordings+release-groups+"
        "genres+tags"
    )

    return request_json(url)


def find_track_in_release(
    release,
    artist,
    title,
):
    if not release:
        return None

    wanted = normalize_text(
        title
    )

    candidates = []

    release_artist = artist_credit_to_string(
        release.get(
            "artist-credit",
            [],
        )
    )

    year = first_year(
        release.get(
            "date"
        )
        or release.get(
            "release-group",
            {},
        ).get(
            "first-release-date",
            "",
        )
    )

    release_genres = [
        x.get(
            "name",
            "",
        )
        for x in release.get(
            "genres",
            [],
        )
        if x.get("name")
    ]

    for medium in release.get(
        "media",
        [],
    ) or []:

        for track in medium.get(
            "tracks",
            [],
        ) or []:

            recording = track.get(
                "recording",
                {},
            )

            if normalize_text(
                recording.get(
                    "title"
                )
            ) != wanted:
                continue

            recording_artist = (
                artist_credit_to_string(
                    recording.get(
                        "artist-credit",
                        [],
                    )
                )
                or release_artist
            )

            genres = [
                x.get(
                    "name",
                    "",
                )
                for x in recording.get(
                    "genres",
                    [],
                )
                if x.get("name")
            ]

            if not genres:
                genres = release_genres

            candidates.append(
                {
                    "artist": recording_artist,
                    "album_artist": (
                        release_artist
                        or recording_artist
                    ),
                    "album": release.get(
                        "title",
                        "",
                    ),
                    "title": recording.get(
                        "title",
                        "",
                    ),
                    "release_id": release.get(
                        "id",
                        "",
                    ),
                    "recording_id": recording.get(
                        "id",
                        "",
                    ),
                    "track": str(
                        track.get(
                            "position"
                        )
                        or track.get(
                            "number"
                        )
                        or ""
                    ),
                    "year": year,
                    "genre": (
                        genres[0]
                        if genres
                        else ""
                    ),
                }
            )

    if not candidates:
        return None

    for item in candidates:
        if loose_text(
            artist,
            item["artist"],
        ):
            return item

    return candidates[0]


# ============================================================
# MusicBrainz backup metadata
# ============================================================

def compare_metadata(
    youtube,
    musicbrainz,
):
    differences = []

    for field, label in [
        ("artist", "Artist"),
        ("title", "Track"),
        ("album", "Album"),
        ("album_artist", "Album artist"),
        ("year", "Year"),
        ("track", "Track number"),
        ("isrc", "ISRC"),
    ]:
        yt_value = youtube.get(
            field,
            "",
        )

        mb_value = musicbrainz.get(
            field,
            "",
        )

        if (
            yt_value
            and mb_value
            and not same_text(
                yt_value,
                mb_value,
            )
        ):
            differences.append(
                {
                    "label": label,
                    "youtube": str(
                        yt_value
                    ),
                    "musicbrainz": str(
                        mb_value
                    ),
                }
            )

    return differences


def get_musicbrainz_backup(meta):
    """
    MusicBrainz is NOT the primary metadata source.

    It:
      1. Searches MusicBrainz using YouTube metadata.
      2. Finds a likely release.
      3. Gets detailed release information.
      4. Compares MusicBrainz against YouTube.
      5. Uses MusicBrainz only to fill missing fields.
      6. Provides a release ID for artwork.
    """

    differences = []

    recordings = search_recordings(
        meta
    )

    if not recordings:
        # Recording search can fail because of MusicBrainz naming/artist-credit
        # differences. Still search the exact album for artwork candidates.
        artwork_releases = search_releases_for_artwork(meta)
        artwork_releases.sort(
            key=lambda x: -artwork_release_score(x, meta)
        )

        meta["artwork_release_ids"] = [
            x["release_id"]
            for x in artwork_releases
            if artwork_release_score(x, meta) > 0
        ][:10]
        return meta, differences

    choice = choose_musicbrainz_release(
        recordings,
        meta,
    )

    if not choice:
        return meta, differences

    # MusicBrainz's best release remains the release used for
    # missing metadata. These additional release IDs are ONLY
    # used if the best release has no Cover Art Archive artwork.
    meta["artwork_release_ids"] = (
        choice.get(
            "artwork_release_ids",
            [],
        )
        or [choice.get("release_id", "")]
    )

    if not choice.get("release_id"):
        return meta, differences

    release = get_release_details(
        choice["release_id"]
    )

    if not release:
        return meta, differences

    mb_track = find_track_in_release(
        release,
        choice["artist"],
        choice["title"],
    )

    if not mb_track:
        mb_track = {
            "artist": choice["artist"],
            "album_artist": choice["artist"],
            "album": choice["album"],
            "title": choice["title"],
            "release_id": choice[
                "release_id"
            ],
            "recording_id": choice[
                "recording_id"
            ],
            "track": "",
            "year": choice["year"],
            "genre": "",
        }

    # Compare BEFORE filling missing fields.
    differences = compare_metadata(
        meta,
        mb_track,
    )

    # YouTube remains authoritative.
    if not meta.get("artist"):
        meta["artist"] = mb_track.get(
            "artist",
            "",
        )

    if not meta.get("title"):
        meta["title"] = mb_track.get(
            "title",
            "",
        )

    if not meta.get("album"):
        meta["album"] = mb_track.get(
            "album",
            "",
        )

    if not meta.get("album_artist"):
        meta["album_artist"] = (
            mb_track.get(
                "album_artist"
            )
            or meta.get(
                "artist",
                "",
            )
        )

    if not meta.get("track"):
        meta["track"] = mb_track.get(
            "track",
            "",
        )

    if not meta.get("year"):
        meta["year"] = mb_track.get(
            "year",
            "",
        )

    if not meta.get("genre"):
        meta["genre"] = mb_track.get(
            "genre",
            "",
        )

    if not meta.get("isrc"):
        recording = choice.get(
            "recording",
            {},
        )

        isrcs = recording.get(
            "isrcs",
            [],
        ) or []

        if isrcs:
            meta["isrc"] = isrcs[0]

    meta["release_id"] = (
        mb_track.get(
            "release_id"
        )
        or choice.get(
            "release_id",
            "",
        )
    )

    meta["recording_id"] = (
        mb_track.get(
            "recording_id"
        )
        or choice.get(
            "recording_id",
            "",
        )
    )

    return meta, differences


# ============================================================
# Metadata resolution
# ============================================================

def resolve_metadata(youtube):
    final = dict(youtube)

    print()
    print(
        "Metadata source"
    )
    print(
        "---------------"
    )

    print(
        "Primary source: YouTube / YouTube Music"
    )

    missing = []

    for field, label in [
        ("artist", "Artist"),
        ("title", "Track"),
        ("album", "Album"),
        ("album_artist", "Album artist"),
        ("track", "Track number"),
        ("year", "Year"),
        ("isrc", "ISRC"),
    ]:
        if not final.get(field):
            missing.append(label)

    if missing:
        print(
            "Missing fields: "
            + ", ".join(missing)
        )

        print(
            "Using MusicBrainz as backup..."
        )

    else:
        print(
            "YouTube metadata is complete."
        )

        print(
            "MusicBrainz will only be used "
            "for artwork/release identification."
        )

    final, differences = (
        get_musicbrainz_backup(
            final
        )
    )

    print()

    return (
        final,
        differences,
        missing,
    )


def print_resolution(
    youtube,
    final,
    differences,
):
    print()
    print(
        "Final metadata"
    )
    print(
        "--------------"
    )

    print(
        f"Artist:       "
        f"{final.get('artist') or '(none)'}"
    )

    print(
        f"Album:        "
        f"{final.get('album') or '(none)'}"
    )

    print(
        f"Song:         "
        f"{final.get('title') or '(none)'}"
    )

    print(
        f"Album artist: "
        f"{final.get('album_artist') or '(none)'}"
    )

    print(
        f"Year:         "
        f"{final.get('year') or '(none)'}"
    )

    print(
        f"Track:        "
        f"{final.get('track') or '(none)'}"
    )

    print(
        f"Genre:        "
        f"{final.get('genre') or '(none)'}"
    )

    print(
        f"ISRC:         "
        f"{final.get('isrc') or '(none)'}"
    )

    if final.get("release_id"):
        print(
            f"MusicBrainz release: "
            f"{final['release_id']}"
        )

    print()

    if differences:
        print(
            "Note: MusicBrainz returned different "
            "information for some fields."
        )

        print(
            "YouTube / YouTube Music was kept as "
            "the preferred source."
        )

        for difference in differences:
            print(
                f"  {difference['label']}: "
                f"YouTube='{difference['youtube']}' "
                f"MusicBrainz='{difference['musicbrainz']}'"
            )

        print()


# ============================================================
# Duplicate detection
# ============================================================

def find_existing_song(
    folder,
    meta,
):
    wanted = (
        normalize_text(
            meta.get("title")
        ),
        normalize_text(
            meta.get("artist")
        ),
        normalize_text(
            meta.get("album")
        ),
    )

    if not all(wanted):
        return None

    try:
        names = os.listdir(
            folder
        )

    except OSError:
        return None

    for name in names:
        if not name.lower().endswith(
            ".mp3"
        ):
            continue

        path = os.path.join(
            folder,
            name,
        )

        try:
            audio = MP3(path)

            if not audio.tags:
                continue

            got = (
                normalize_text(
                    id3_text(
                        audio.tags,
                        "TIT2",
                    )
                ),
                normalize_text(
                    id3_text(
                        audio.tags,
                        "TPE1",
                    )
                ),
                normalize_text(
                    id3_text(
                        audio.tags,
                        "TALB",
                    )
                ),
            )

            if got == wanted:
                return path

        except Exception:
            pass

    return None


# ============================================================
# Album artwork
# ============================================================

def download_cover_art(
    release_ids,
):
    if isinstance(
        release_ids,
        str,
    ):
        release_ids = (
            [release_ids]
            if release_ids
            else []
        )

    release_ids = [
        release_id
        for release_id in (
            release_ids
            or []
        )
        if release_id
    ]

    if not release_ids:
        print(
            "No suitable MusicBrainz release was found "
            "for artwork."
        )
        return None, "unavailable"

    print()
    print(
        "Searching Cover Art Archive..."
    )

    saw_error = False

    for index, release_id in enumerate(
        release_ids,
        start=1,
    ):
        if index == 1:
            print(
                f"Trying primary MusicBrainz release: "
                f"{release_id}"
            )
        else:
            print(
                f"Trying alternate MusicBrainz release "
                f"{index}: {release_id}"
            )

        url = (
            "https://coverartarchive.org/release/"
            + urllib.parse.quote(
                release_id
            )
            + "/front-500"
        )

        data = None
        status = "error"

        for attempt in range(1, 4):
            data, status = download_binary(url)

            if data:
                break

            if status in {"http_429", "http_500", "http_502", "http_503", "http_504"}:
                if attempt < 3:
                    wait = 2.0 * attempt
                    print(
                        f"  Cover Art Archive temporary error "
                        f"({status}); retrying in {wait:.1f} seconds..."
                    )
                    time.sleep(wait)
                    continue

            break

        if not data:
            if status == "not_found":
                print(
                    "  No artwork found for this release."
                )
            else:
                saw_error = True
                print(
                    "  Cover Art Archive request failed."
                )
            continue

        artwork_file = os.path.join(
            SCRIPT_DIRECTORY,
            "_temporary_album_art.jpg",
        )

        try:
            with open(
                artwork_file,
                "wb",
            ) as file:
                file.write(data)

            if index == 1:
                print(
                    "Artwork downloaded."
                )
            else:
                print(
                    "Artwork downloaded from an "
                    "alternate MusicBrainz release."
                )

            return artwork_file, "success"

        except OSError as error:
            print(
                "ERROR: Could not save album artwork."
            )
            print(error)
            return None, "file"

    if saw_error:
        print(
            "ERROR: No usable artwork was found after "
            "trying the matching MusicBrainz releases."
        )
        return None, "error"

    print(
        "No artwork found in the matching "
        "MusicBrainz releases."
    )
    return None, "unavailable"

def embed_metadata(
    mp3_file,
    meta,
    artwork_file,
):
    try:
        try:
            tags = ID3(
                mp3_file
            )

        except ID3NoHeaderError:
            tags = ID3()

        for frame in [
            "TIT2",
            "TPE1",
            "TPE2",
            "TALB",
            "TDRC",
            "TRCK",
            "TCON",
            "TSRC",
            "APIC",
        ]:
            tags.delall(
                frame
            )

        tags.add(
            TIT2(
                encoding=3,
                text=meta["title"],
            )
        )

        tags.add(
            TPE1(
                encoding=3,
                text=meta["artist"],
            )
        )

        tags.add(
            TPE2(
                encoding=3,
                text=(
                    meta.get(
                        "album_artist"
                    )
                    or meta["artist"]
                ),
            )
        )

        tags.add(
            TALB(
                encoding=3,
                text=meta["album"],
            )
        )

        if meta.get("year"):
            tags.add(
                TDRC(
                    encoding=3,
                    text=str(
                        meta["year"]
                    ),
                )
            )

        if meta.get("track"):
            tags.add(
                TRCK(
                    encoding=3,
                    text=str(
                        meta["track"]
                    ),
                )
            )

        if meta.get("genre"):
            tags.add(
                TCON(
                    encoding=3,
                    text=meta["genre"],
                )
            )

        if meta.get("isrc"):
            tags.add(
                TSRC(
                    encoding=3,
                    text=meta["isrc"],
                )
            )

        if (
            artwork_file
            and os.path.isfile(
                artwork_file
            )
        ):
            with open(
                artwork_file,
                "rb",
            ) as file:
                image_data = file.read()

            tags.add(
                APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=image_data,
                )
            )

        tags.save(
            mp3_file,
            v2_version=3,
        )

        return True, ""

    except Exception as error:
        print(
            "ERROR: Could not embed metadata:"
        )

        print(error)

        return False, str(error)


# ============================================================
# Download
# ============================================================

def download_track(
    url,
    meta,
    folder,
):
    filename = (
        safe_filename(
            meta["title"]
        )
        + ".mp3"
    )

    output = os.path.join(
        folder,
        filename,
    )

    if os.path.isfile(
        output
    ):
        print(
            "WARNING: A file with this "
            "song title already exists."
        )

        print(
            "The existing file did not match "
            "the requested metadata."
        )

        print(
            "Skipping to avoid overwriting it."
        )

        return None, "filename_conflict"

    print()
    print(
        "Downloading MP3..."
    )
    print()

    try:
        result = run_ytdlp(
            ytdlp_args()
            + [
                "-x",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "0",
                "--ffmpeg-location",
                FFMPEG,
                "-o",
                output,
                url,
            ]
        )

    except FileNotFoundError:
        print(
            "ERROR: yt-dlp.exe was not found."
        )

        return None, "file"

    if (
        result.returncode != 0
        or not os.path.isfile(
            output
        )
    ):
        print(
            "ERROR: yt-dlp failed or "
            "the MP3 was not created."
        )

        error_type = classify_ytdlp_error(
            result.stderr
        )

        return None, error_type

    return output, "success"


# ============================================================
# Process one song
# ============================================================

def process_item(
    url,
    number,
    total,
    folder,
    report,
    cached_info=None,
):
    print()
    print(
        "============================================================"
    )

    print(
        f"Item: {number} of {total}"
    )

    print(
        "============================================================"
    )

    print()

    info = cached_info

    if info is None:
        info, info_status = (
            get_youtube_info(url)
        )

        if not info:
            if info_status == "unavailable":
                report.add(
                    "unavailable",
                    url,
                    "Video is unavailable, private, or deleted",
                )

            elif info_status == "url":
                report.add(
                    "url_errors",
                    url,
                    "Invalid or unsupported URL",
                )

            elif info_status == "network":
                report.add(
                    "network_api_errors",
                    url,
                    "Network error while reading YouTube metadata",
                )

            elif info_status == "file":
                report.add(
                    "file_errors",
                    url,
                    "yt-dlp.exe could not be found",
                )

            else:
                report.add(
                    "metadata_errors",
                    url,
                    "Could not read usable YouTube metadata",
                )

            return False

    youtube = youtube_metadata(
        info
    )

    print_youtube_metadata(
        youtube
    )

    # --------------------------------------------------------
    # Check for missing metadata
    # --------------------------------------------------------

    missing_before = []

    for field, label in [
        ("artist", "Artist"),
        ("title", "Track"),
        ("album", "Album"),
        ("album_artist", "Album artist"),
        ("track", "Track number"),
        ("year", "Year"),
        ("isrc", "ISRC"),
    ]:
        if not youtube.get(field):
            missing_before.append(label)

    # --------------------------------------------------------
    # Early duplicate check
    # --------------------------------------------------------

    if (
        youtube.get("artist")
        and youtube.get("title")
        and youtube.get("album")
    ):
        provisional = {
            "artist": youtube["artist"],
            "title": youtube["title"],
            "album": youtube["album"],
        }

        existing = find_existing_song(
            folder,
            provisional,
        )

        if existing:
            print(
                "Song already exists."
            )

            print(
                "Skipping MusicBrainz, download, "
                "artwork, and tagging."
            )

            print(
                f"Existing file: "
                f"{os.path.basename(existing)}"
            )

            report.add(
                "duplicates",
                os.path.basename(existing),
                f"Already exists with matching "
                f"Artist / Album / Song",
            )

            return True

    # --------------------------------------------------------
    # Metadata resolution
    # --------------------------------------------------------

    final, differences, missing = (
        resolve_metadata(
            youtube
        )
    )

    filename = (
        safe_filename(
            final.get("title")
            or youtube.get("title")
            or "Unknown"
        )
        + ".mp3"
    )

    if differences:
        report.add_metadata_difference(
            filename,
            differences,
        )

    # Missing fields that remain after MusicBrainz backup
    still_missing = []

    for field, label in [
        ("artist", "Artist"),
        ("title", "Track"),
        ("album", "Album"),
        ("album_artist", "Album artist"),
        ("track", "Track number"),
        ("year", "Year"),
        ("isrc", "ISRC"),
    ]:
        if not final.get(field):
            still_missing.append(label)

    if still_missing:
        report.add(
            "missing_metadata",
            filename,
            "Missing: "
            + ", ".join(
                still_missing
            ),
        )

    if not final.get("artist"):
        print(
            "ERROR: Could not determine the artist."
        )

        report.add(
            "metadata_errors",
            filename,
            "Could not determine artist",
        )

        return False

    if not final.get("title"):
        print(
            "ERROR: Could not determine the song title."
        )

        report.add(
            "metadata_errors",
            filename,
            "Could not determine song title",
        )

        return False

    if not final.get("album"):
        print(
            "ERROR: Could not determine the album."
        )

        report.add(
            "metadata_errors",
            filename,
            "Could not determine album",
        )

        return False

    print_resolution(
        youtube,
        final,
        differences,
    )

    # --------------------------------------------------------
    # Check duplicate again after MusicBrainz
    # --------------------------------------------------------

    existing = find_existing_song(
        folder,
        final,
    )

    if existing:
        print(
            "Song already exists."
        )

        print(
            "Skipping audio download and artwork."
        )

        print(
            f"Existing file: "
            f"{os.path.basename(existing)}"
        )

        report.add(
            "duplicates",
            os.path.basename(existing),
            "Already exists with matching "
            "Artist / Album / Song",
        )

        return True

    # --------------------------------------------------------
    # Check filename conflict before download
    # --------------------------------------------------------

    expected_path = os.path.join(
        folder,
        filename,
    )

    if os.path.isfile(
        expected_path
    ):
        print(
            "ERROR: The filename already exists, "
            "but its metadata does not match."
        )

        report.add(
            "filename_conflicts",
            filename,
            "File exists but does not match requested metadata",
        )

        return False

    # --------------------------------------------------------
    # Download MP3
    # --------------------------------------------------------

    mp3, download_status = (
        download_track(
            url,
            final,
            folder,
        )
    )

    if not mp3:
        if download_status == "filename_conflict":
            report.add(
                "filename_conflicts",
                filename,
                "Filename already exists",
            )

        elif download_status == "unavailable":
            report.add(
                "unavailable",
                filename,
                "Video became unavailable during download",
            )

        elif download_status == "url":
            report.add(
                "url_errors",
                filename,
                "Invalid or unsupported URL",
            )

        elif download_status == "network":
            report.add(
                "network_api_errors",
                filename,
                "Network error during audio download",
            )

        elif download_status == "file":
            report.add(
                "file_errors",
                filename,
                "yt-dlp.exe or required file was unavailable",
            )

        else:
            report.add(
                "audio_errors",
                filename,
                "yt-dlp/FFmpeg failed to create the MP3",
            )

        return False

    # --------------------------------------------------------
    # Download artwork
    # --------------------------------------------------------

    artwork, artwork_status = (
        download_cover_art(
            final.get(
                "artwork_release_ids",
                [],
            )
        )
    )

    warning = False

    if artwork_status == "unavailable":
        report.add(
            "artwork_unavailable",
            os.path.basename(mp3),
            "No album artwork was found",
        )
        warning = True

    elif artwork_status == "error":
        report.add(
            "artwork_errors",
            os.path.basename(mp3),
            "Cover Art Archive request failed",
        )
        warning = True

    elif artwork_status == "file":
        report.add(
            "file_errors",
            os.path.basename(mp3),
            "Could not save album artwork",
        )
        warning = True

    # --------------------------------------------------------
    # Embed metadata
    # --------------------------------------------------------

    tagging_success, tagging_error = (
        embed_metadata(
            mp3,
            final,
            artwork,
        )
    )

    # --------------------------------------------------------
    # Delete temporary artwork
    # --------------------------------------------------------

    if (
        artwork
        and os.path.isfile(
            artwork
        )
    ):
        try:
            os.remove(
                artwork
            )

        except OSError:
            pass

    if not tagging_success:
        report.add(
            "metadata_errors",
            os.path.basename(mp3),
            "Could not embed ID3 metadata"
            + (
                f": {tagging_error}"
                if tagging_error
                else ""
            ),
        )

        return False

    # --------------------------------------------------------
    # Finished
    # --------------------------------------------------------

    print()
    print(
        "Finished:"
    )

    print(
        f"  Artist:       "
        f"{final['artist']}"
    )

    print(
        f"  Album:        "
        f"{final['album']}"
    )

    print(
        f"  Song:         "
        f"{final['title']}"
    )

    print(
        f"  Album Artist: "
        f"{final.get('album_artist', '')}"
    )

    print(
        f"  Year:         "
        f"{final.get('year', '')}"
    )

    print(
        f"  Track:        "
        f"{final.get('track', '')}"
    )

    print(
        f"  File:         "
        f"{os.path.basename(mp3)}"
    )

    # --------------------------------------------------------
    # Report success/warning
    # --------------------------------------------------------

    if warning:
        report.add(
            "completed_with_warnings",
            os.path.basename(mp3),
            "Audio and metadata completed, "
            "but album artwork was unavailable or failed",
        )

    else:
        report.add(
            "successful",
            os.path.basename(mp3),
        )

    return True


# ============================================================
# Main
# ============================================================

def main():
    print()
    print(
        "============================================================"
    )
    print(
        "YouTube Music Downloader"
    )
    print(
        "============================================================"
    )

    # ========================================================
    # URL FIRST
    # ========================================================

    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()

    else:
        url = input(
            "Paste YouTube / YouTube Music URL: "
        ).strip()

    if not url:
        print()
        print(
            "No URL supplied."
        )

        return 1

    # ========================================================
    # Destination folder SECOND
    # ========================================================

    folder = get_destination_folder()

    if not folder:
        return 1

    report = DownloadReport(
        folder
    )

    try:
        # ====================================================
        # Playlist
        # ====================================================

        if is_playlist_url(url):
            entries, playlist_status = (
                get_playlist_entries(url)
            )

            if not entries:
                if playlist_status == "unavailable":
                    report.add(
                        "unavailable",
                        url,
                        "Playlist is unavailable or private",
                    )

                elif playlist_status == "url":
                    report.add(
                        "url_errors",
                        url,
                        "Invalid or unsupported playlist URL",
                    )

                elif playlist_status == "network":
                    report.add(
                        "network_api_errors",
                        url,
                        "Network error while reading playlist",
                    )

                elif playlist_status == "file":
                    report.add(
                        "file_errors",
                        url,
                        "yt-dlp.exe was not found",
                    )

                else:
                    report.add(
                        "playlist_errors",
                        url,
                        "Could not read any accessible playlist items",
                    )

                report.save()

                return 1

            print()
            print(
                f"Playlist contains "
                f"{len(entries)} items."
            )

            print()

            items = []

            for i, entry in enumerate(
                entries,
                1,
            ):
                print(
                    f"Reading playlist metadata "
                    f"{i}/{len(entries)}..."
                )

                info, info_status = (
                    get_youtube_info(
                        entry["url"],
                        quiet=True,
                    )
                )

                if info:
                    items.append(
                        {
                            "url": entry["url"],
                            "info": info,
                        }
                    )

                else:
                    if info_status == "unavailable":
                        report.add(
                            "unavailable",
                            entry["url"],
                            "Playlist item is unavailable, "
                            "private, or deleted",
                        )

                    elif info_status == "url":
                        report.add(
                            "url_errors",
                            entry["url"],
                            "Invalid playlist item URL",
                        )

                    elif info_status == "network":
                        report.add(
                            "network_api_errors",
                            entry["url"],
                            "Network error reading playlist item",
                        )

                    else:
                        report.add(
                            "metadata_errors",
                            entry["url"],
                            "Could not read YouTube metadata",
                        )

            if not items:
                report.add(
                    "playlist_errors",
                    url,
                    "No playlist items had usable metadata",
                )

                report.save()

                return 1

            for i, item in enumerate(
                items,
                1,
            ):
                process_item(
                    item["url"],
                    i,
                    len(items),
                    folder,
                    report,
                    item["info"],
                )

                # Save after every item so the report
                # survives an unexpected interruption.
                report.save()

            print()
            print(
                "Playlist processing finished."
            )

            report.save()

            return (
                0
                if (
                    report.count("successful")
                    + report.count(
                        "completed_with_warnings"
                    )
                    + report.count(
                        "duplicates"
                    )
                ) > 0
                else 1
            )

        # ====================================================
        # Single song
        # ====================================================

        process_item(
            url,
            1,
            1,
            folder,
            report,
        )

        report.save()

        return (
            0
            if (
                report.count("successful")
                + report.count(
                    "completed_with_warnings"
                )
                + report.count(
                    "duplicates"
                )
            ) > 0
            else 1
        )

    except KeyboardInterrupt:
        print()
        print(
            "Download cancelled by user."
        )

        report.add(
            "interrupted",
            url,
            "Downloader was cancelled",
        )

        report.save()

        return 1

    except Exception as error:
        print()
        print(
            "ERROR: Unexpected downloader error:"
        )
        print(error)

        report.add(
            "file_errors",
            url,
            f"Unexpected error: {error}",
        )

        report.save()

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )