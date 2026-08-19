#!/usr/bin/env python3
"""Fetch YouTube transcripts for a person so Claude can build their persona.

Given a channel URL/handle (or explicit video URLs), this script:
  1. Lists the channel's most popular videos (yt-dlp, no API key needed)
  2. Downloads each video's captions (manual subs preferred, auto-captions as fallback)
  3. Writes clean plain-text transcripts + a videos.json metadata index

Usage:
  python3 fetch_youtube.py --channel https://www.youtube.com/@AlexHormozi --max-videos 15 --out ./persona-workdir
  python3 fetch_youtube.py --search "Warren Buffett interview" --max-videos 8 --out ./persona-workdir
  python3 fetch_youtube.py --videos URL1 URL2 ... --out ./persona-workdir

--search finds long-form videos OF a person across all of YouTube (interviews,
podcasts, keynotes) — for people who have no channel of their own.

Requires: yt-dlp  (pip install yt-dlp   OR   brew install yt-dlp)
Optional fallback: youtube-transcript-api (pip install youtube-transcript-api)

Exit codes: 0 = at least one transcript fetched, 1 = none fetched, 2 = bad args/missing deps.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SLEEP_BETWEEN_VIDEOS_SECONDS = 2
SUBTITLE_LANG_PREFERENCE = ["en", "en-US", "en-GB", "en-orig"]
MAX_TITLE_SLUG_LENGTH = 60


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def find_yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if path:
        return path
    fail(
        "yt-dlp is not installed. Install it with one of:\n"
        "  brew install yt-dlp\n"
        "  pip3 install --user yt-dlp\n"
        "  pipx install yt-dlp"
    )
    return ""  # unreachable


def run_json_lines(cmd: list[str]) -> list[dict]:
    """Run a command that emits one JSON object per line; return parsed objects."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def normalize_channel_url(channel: str) -> str:
    """Accept @handle, handle, or full URL; return a /videos tab URL."""
    channel = channel.strip()
    if channel.startswith("http"):
        base = channel.rstrip("/")
    elif channel.startswith("@"):
        base = f"https://www.youtube.com/{channel}"
    else:
        base = f"https://www.youtube.com/@{channel}"
    for tab in ("/videos", "/shorts", "/streams", "/featured"):
        if base.endswith(tab):
            base = base[: -len(tab)]
            break
    return base


def list_popular_videos(yt_dlp: str, channel: str, max_videos: int) -> list[dict]:
    """Return metadata for the channel's most popular videos.

    Tries the 'popular' sorted videos tab first; falls back to the recent-videos
    tab if YouTube doesn't serve the sorted variant.
    """
    base = normalize_channel_url(channel)
    candidates = [
        f"{base}/videos?view=0&sort=p&flow=grid",  # sorted by popularity
        f"{base}/videos",  # recent uploads fallback
    ]
    for url in candidates:
        print(f"Listing videos from: {url}")
        entries = run_json_lines(
            [
                yt_dlp,
                "--flat-playlist",
                "--dump-json",
                *(["--playlist-end", str(max_videos * 2)] if max_videos else []),
                url,
            ]
        )
        videos = []
        for entry in entries:
            video_id = entry.get("id")
            video_url = entry.get("url") or (
                f"https://www.youtube.com/watch?v={video_id}" if video_id else None
            )
            if not video_id or not video_url:
                continue
            # Skip shorts: they carry little coaching substance per fetch
            if "/shorts/" in str(video_url):
                continue
            duration = entry.get("duration") or 0
            if duration and duration < 120:
                continue
            videos.append(
                {
                    "id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": entry.get("title") or video_id,
                    "duration_seconds": duration,
                    "view_count": entry.get("view_count"),
                }
            )
            if max_videos and len(videos) >= max_videos:
                break
        if videos:
            return videos
    return []


def search_videos(yt_dlp: str, query: str, max_videos: int, min_duration: int = 480) -> list[dict]:
    """Return videos matching a YouTube search, dropping anything under min_duration.

    The default floor keeps search mode on substantial spoken content (interviews,
    podcasts, keynotes) rather than clips. Lower it for corpora that legitimately live
    in short uploads — scene clips for a fictional character, for instance.
    """
    print(f"Searching YouTube: {query}")
    entries = run_json_lines(
        [
            yt_dlp,
            "--flat-playlist",
            "--dump-json",
            f"ytsearch{(max_videos * 3) if max_videos else 500}:{query}",  # overshoot; shorts filtered below
        ]
    )
    videos = []
    for entry in entries:
        video_id = entry.get("id")
        if not video_id:
            continue
        duration = entry.get("duration") or 0
        # Search mode wants substantial spoken content, not clips
        if duration and duration < min_duration:
            continue
        videos.append(
            {
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": entry.get("title") or video_id,
                "duration_seconds": duration,
                "view_count": entry.get("view_count"),
                "channel": entry.get("channel") or entry.get("uploader"),
            }
        )
        if max_videos and len(videos) >= max_videos:
            break
    return videos


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:MAX_TITLE_SLUG_LENGTH] or "video"


def clean_vtt(vtt_text: str) -> str:
    """Convert WebVTT captions to deduplicated plain text."""
    lines = []
    for raw_line in vtt_text.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
            or "-->" in line
            or re.fullmatch(r"\d+", line)
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line)  # strip inline timing/style tags
        line = line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
        line = line.replace("&quot;", '"').replace("&gt;", ">").replace("&lt;", "<")
        if line:
            lines.append(line)
    # Auto-captions repeat each line as the rolling window advances; dedupe neighbors
    deduped = []
    for line in lines:
        if deduped and (line == deduped[-1] or deduped[-1].endswith(line)):
            continue
        if deduped and line.startswith(deduped[-1]):
            deduped[-1] = line
            continue
        deduped.append(line)
    return "\n".join(deduped)


def fetch_captions_via_yt_dlp(yt_dlp: str, video_url: str, workdir: Path) -> str | None:
    """Download subs for one video with yt-dlp; return cleaned transcript text."""
    for existing in workdir.glob("sub.*"):
        existing.unlink()
    subprocess.run(
        [
            yt_dlp,
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(SUBTITLE_LANG_PREFERENCE),
            "--sub-format",
            "vtt",
            "-o",
            str(workdir / "sub"),
            video_url,
        ],
        capture_output=True,
        text=True,
    )
    vtt_files = sorted(workdir.glob("sub.*.vtt"))
    if not vtt_files:
        return None
    # Prefer manual captions ordering by our language preference
    text = clean_vtt(vtt_files[0].read_text(encoding="utf-8", errors="replace"))
    for vtt in workdir.glob("sub.*"):
        vtt.unlink()
    return text if text.strip() else None


def fetch_captions_via_transcript_api(video_id: str) -> str | None:
    """Fallback: youtube-transcript-api, if installed."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=SUBTITLE_LANG_PREFERENCE)
        text = "\n".join(snippet.text.strip() for snippet in fetched if snippet.text.strip())
        return text if text.strip() else None
    except Exception as error:  # noqa: BLE001 — any fetch failure just means "no fallback"
        print(f"  transcript-api fallback failed: {error}", file=sys.stderr)
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", help="Channel URL or @handle (e.g. https://www.youtube.com/@AlexHormozi)")
    parser.add_argument("--search", help="YouTube search query for long-form videos OF the person (e.g. \"Warren Buffett interview\") — for people with no channel of their own")
    parser.add_argument("--videos", nargs="*", default=[], help="Explicit video URLs to fetch instead of / in addition to the channel's popular videos")
    parser.add_argument("--max-videos", type=int, default=12,
                        help="How many videos to pull per source (default 12; use 0 for ALL — "
                             "expect ~1-2 hours on a large channel, and see --refetch)")
    parser.add_argument("--min-duration", type=int, default=480,
                        help="Drop --search results shorter than this many seconds (default 480). "
                             "Lower it when the material really is short — e.g. 120 for film scene "
                             "clips when building a fictional character")
    parser.add_argument("--refetch", action="store_true",
                        help="Re-download transcripts that are already on disk (default: skip them, "
                             "so re-running only picks up new uploads and an interrupted run resumes)")
    parser.add_argument("--out", required=True, help="Output directory (transcripts/ and videos.json are written here)")
    args = parser.parse_args()
    if not args.channel and not args.videos and not args.search:
        parser.error("provide --channel, --search, and/or --videos")
    if args.max_videos < 0:
        parser.error("--max-videos must be >= 0 (0 means no limit)")
    if args.min_duration < 0:
        parser.error("--min-duration must be >= 0")
    return args


def existing_video_ids(transcripts_dir: Path) -> set[str]:
    """Video IDs already on disk. Filenames end '-<11-char id>.txt'."""
    ids = set()
    for path in transcripts_dir.glob("*.txt"):
        match = re.search(r"-([A-Za-z0-9_-]{11})$", path.stem)
        if match:
            ids.add(match.group(1))
    return ids


def main() -> None:
    args = parse_args()
    yt_dlp = find_yt_dlp()

    out_dir = Path(args.out).expanduser().resolve()
    transcripts_dir = out_dir / "transcripts"
    workdir = out_dir / ".work"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    videos: list[dict] = []
    if args.channel:
        videos = list_popular_videos(yt_dlp, args.channel, args.max_videos)
        if not videos:
            print("WARNING: could not list channel videos (region block or layout change?)", file=sys.stderr)
    if args.search:
        seen_ids = {v["id"] for v in videos}
        videos.extend(v for v in search_videos(yt_dlp, args.search, args.max_videos, args.min_duration) if v["id"] not in seen_ids)
    for url in args.videos:
        video_id_match = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
        if not video_id_match:
            print(f"WARNING: skipping unrecognized video URL: {url}", file=sys.stderr)
            continue
        videos.append({"id": video_id_match.group(1), "url": url, "title": None, "duration_seconds": None, "view_count": None})

    if not videos:
        fail("no videos to fetch", 1)

    # Skip what's already on disk. On a 500-video channel this is what makes the
    # fetch resumable and makes a re-run cost only the new uploads.
    known = set() if args.refetch else existing_video_ids(transcripts_dir)
    if known:
        before = len(videos)
        videos = [v for v in videos if v["id"] not in known]
        print(f"{len(known)} already on disk; {before - len(videos)} skipped, {len(videos)} to fetch")
    if not videos:
        print("Nothing new to fetch.")
        return

    fetched = []
    for index, video in enumerate(videos, 1):
        title = video.get("title") or video["id"]
        print(f"[{index}/{len(videos)}] {title}")
        text = fetch_captions_via_yt_dlp(yt_dlp, video["url"], workdir)
        source = "yt-dlp"
        if not text:
            text = fetch_captions_via_transcript_api(video["id"])
            source = "youtube-transcript-api"
        if not text:
            print("  no captions available, skipping", file=sys.stderr)
            continue
        filename = f"{slugify(title)}-{video['id']}.txt"
        transcript_path = transcripts_dir / filename
        header = f"# {title}\n# {video['url']}\n\n"
        transcript_path.write_text(header + text + "\n", encoding="utf-8")
        fetched.append({**video, "title": title, "transcript_file": f"transcripts/{filename}", "caption_source": source, "words": len(text.split())})
        print(f"  saved {transcript_path.name} ({len(text.split())} words via {source})")
        if index < len(videos):
            time.sleep(SLEEP_BETWEEN_VIDEOS_SECONDS)

    # Merge, never clobber: an incremental run must not erase earlier entries.
    index_path = out_dir / "videos.json"
    merged = {}
    if index_path.exists():
        try:
            for entry in json.loads(index_path.read_text(encoding="utf-8")):
                path = entry.get("transcript_file")
                if path and (out_dir / path).exists():
                    merged[entry.get("id") or path] = entry
        except (json.JSONDecodeError, TypeError):
            print("WARNING: videos.json unreadable, rewriting it", file=sys.stderr)
    for entry in fetched:
        merged[entry["id"]] = entry
    index_path.write_text(json.dumps(list(merged.values()), indent=2) + "\n", encoding="utf-8")
    workdir.rmdir()

    print(f"\nDone: {len(fetched)}/{len(videos)} transcripts -> {transcripts_dir}")
    if not fetched:
        sys.exit(1)


if __name__ == "__main__":
    main()
