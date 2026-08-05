#!/usr/bin/env python3
"""Find a person's podcast and pull it as persona material.

Podcast RSS is the modality the other two fetchers miss. It matters for two
different reasons:

  1. VOICE — long-form unscripted talking, often better than their YouTube cut
  2. BOOKS — authors routinely serialize their own audiobooks onto their own
     feed ("Part 1", "Chapter 3", "... Audiobook"). That is an author-released
     full-text source hiding in plain sight, and it is how $100M Offers, Leads,
     Money Models and the unpublished Lost Chapters were found for the Hormozi
     persona: four complete books, none of which any web search surfaces.

Detection is cheap and always runs. Transcription is expensive and is opt-in,
because a serialized audiobook can be 20+ hours of audio — see --transcribe.

Usage:
  # What has this person got? (fast, no downloads — always do this first)
  python3 fetch_podcast.py --search "Alex Hormozi" --out ./persona-workdir

  # Pull their books off the feed (slow: downloads + transcribes)
  python3 fetch_podcast.py --search "Alex Hormozi" --mode books --transcribe --out ./persona-workdir

  # Pull recent long-form episodes for voice, capped
  python3 fetch_podcast.py --feed <RSS_URL> --mode voice --max-episodes 6 --transcribe --out ./persona-workdir

Feed lookup uses the iTunes Search API (public, no key). Episode audio is
fetched from the enclosure URLs the feed publishes — the same thing any podcast
client does.

Requires: nothing to detect (stdlib only).
--transcribe additionally needs:  brew install whisper-cpp ffmpeg
  plus a model, e.g.
  curl -L -o ~/.local/share/whisper/ggml-small.en.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin

Exit codes: 0 = found something, 1 = nothing found, 2 = bad args/missing deps.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ITUNES_SEARCH = "https://itunes.apple.com/search"
HTTP_TIMEOUT_SECONDS = 90
USER_AGENT = "talk-to-anyone/1.0 (podcast client; +https://github.com/coltonjosephdean-rgb/talk-to-anyone)"
DEFAULT_MODEL = Path.home() / ".local/share/whisper/ggml-small.en.bin"
MAX_TITLE_SLUG_LENGTH = 60
MIN_AUDIO_BYTES = 100_000

# A serialized book announces itself in the episode title. Ordered: first match wins.
PART_PATTERNS = [
    re.compile(r"\b(?:part|pt\.?)\s*:?\s*(\d+)", re.I),
    re.compile(r"\bchapter\s*:?\s*(\d+)", re.I),
    re.compile(r"^\s*(\d+)\s*[.)]\s+"),
    re.compile(r"\bep(?:isode)?\s*(\d+)\b", re.I),
]
BOOKISH = re.compile(r"audiobooks?|\baudio\s?books?\b|\bbooks?\b|\bchapters?\b", re.I)
OPENER = re.compile(r"\bwelcome to\b|\bintroduction\b|\bforeword\b|\bpreface\b", re.I)

# How strongly a title segment signals "this names the book" vs "this names the
# chapter". A chapter title can itself contain the word "Chapter", so segments
# must be ranked, not just length-compared.
SEGMENT_SIGNALS = [(re.compile(r"audio\s?books?", re.I), 3),
                   (re.compile(r"\bbooks?\b", re.I), 2),
                   (re.compile(r"\bchapters?\b", re.I), 1)]


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:MAX_TITLE_SLUG_LENGTH] or "episode"


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


# ----------------------------------------------------------------- feed lookup


def _normalize(text: str) -> str:
    """Lowercase and strip accents so 'Brené Brown' matches 'Brene Brown'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# Unrelated shows stuff famous names into their publisher field for search rank:
# "Hosted by Heather Chauvin | Insights inspired by Mel Robbins, Brené Brown".
# A bare substring test reads that as the person's own show.
STUFFING = re.compile(r"inspired by|similar to|fans of|in the style of|for listeners of|"
                      r"better than|if you (?:like|love)", re.I)


def _attribution(wanted: str, collection: str, artist: str) -> str | None:
    """Decide whether a search hit is really this person's show.

    Being wrong here is expensive: an unrelated feed silently becomes the
    coach's voice. So the name has to sit where a real attribution puts it —
    at the front of the publisher, or inside the show's own title — not buried
    in a keyword-stuffed list of influences.
    """
    artist_norm, collection_norm = _normalize(artist), _normalize(collection)
    if STUFFING.search(artist_norm) or STUFFING.search(collection_norm):
        return None
    # Their own show: publisher IS them (allow a trailing tagline).
    if artist_norm == wanted or artist_norm.startswith(wanted):
        return "publisher"
    # Named in the show title — covers co-hosted and network-published shows,
    # e.g. "Unlocking Us with Brené Brown" published by Vox Media.
    if wanted in collection_norm:
        return "title"
    # Name buried mid-publisher with no title support is not evidence.
    return None


def find_feeds(name: str, limit: int = 8) -> list[dict]:
    """Resolve a person's name to candidate podcast feeds via the iTunes API.

    iTunes always returns *something* — searching "Jeremy Clarkson" (who has no
    podcast) confidently returns The Farmers Guardian Podcast. Taking the top
    hit on faith would attribute a stranger's voice to the coach, which is worse
    than finding nothing. So a feed only counts as theirs when their name
    actually appears in the show title or the publisher.
    """
    query = urllib.parse.urlencode({"term": name, "entity": "podcast", "limit": limit})
    print(f"Searching podcasts for: {name}")
    try:
        payload = json.loads(http_get(f"{ITUNES_SEARCH}?{query}").decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
        print(f"WARNING: podcast search failed: {error}", file=sys.stderr)
        return []

    wanted = _normalize(name)
    feeds = []
    for result in payload.get("results", []):
        if not result.get("feedUrl"):
            continue
        collection, artist = result.get("collectionName") or "", result.get("artistName") or ""
        confidence = _attribution(wanted, collection, artist)
        if confidence is None:
            continue
        feeds.append(
            {
                "name": collection,
                "artist": artist,
                "feed": result["feedUrl"],
                "episodes": result.get("trackCount"),
                "confidence": confidence,
            }
        )
    feeds.sort(key=lambda f: (f["confidence"] != "publisher", -(f["episodes"] or 0)))
    return feeds


def parse_feed(feed_url: str) -> list[dict]:
    """Return every episode in the feed that has downloadable audio."""
    print(f"Fetching feed: {feed_url}")
    try:
        root = ET.fromstring(http_get(feed_url))
    except (urllib.error.URLError, ET.ParseError, TimeoutError) as error:
        print(f"WARNING: could not read feed: {error}", file=sys.stderr)
        return []
    ns = {"itunes": "http://www.itunes.apple.com/dtds/podcast-1.0.dtd"}
    episodes = []
    for item in root.findall(".//item"):
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            continue
        episodes.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "url": enclosure.get("url"),
                "published": item.findtext("pubDate"),
                "duration": item.findtext("itunes:duration", namespaces=ns),
            }
        )
    return episodes


# ------------------------------------------------------------ book detection


def _part_number(title: str) -> int | None:
    for pattern in PART_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1))
    return 1 if OPENER.search(title) else None


def _series_key(title: str) -> str:
    """Reduce an episode title to the identity of the book it belongs to.

    The hard case is that most feeds put the CHAPTER name in the title:
        "10. Cost To Acquire A Customer CAC | $100M Lost Chapters Audiobook"
        "Part 11: Advertising in Real Life | $100M Leads Book"
    Keying on the whole title gives every episode its own series and detects
    nothing. The book identity lives in the pipe-delimited segment carrying the
    book-ish word, so prefer that segment when one exists; otherwise fall back
    to the whole title minus its part marker (covers "$100M Offers Audiobook
    Part 5", which has no chapter name).

    "audiobook"/"book"/"chapter" are then dropped from the key itself — they are
    format markers, not identity, and keeping them splits one book across two
    series when a feed is inconsistent ("... Audiobook" vs "... Book").
    """
    key = re.sub(r"\|\s*ep\.?\s*\d+\s*$", " ", title, flags=re.I)  # trailing "| Ep 581"

    segments = [s.strip() for s in key.split("|") if s.strip()]
    if len(segments) > 1:
        def score(indexed: tuple[int, str]) -> tuple[int, int]:
            position, segment = indexed
            best = max((weight for pattern, weight in SEGMENT_SIGNALS if pattern.search(segment)),
                       default=0)
            return (best, position)  # ties break toward the later segment
        position, segment = max(enumerate(segments), key=score)
        if score((position, segment))[0] > 0:
            key = segment

    for pattern in PART_PATTERNS:
        key = pattern.sub(" ", key)
    key = OPENER.sub(" ", key)
    key = re.sub(r"\b(?:audio\s?books?|audiobooks?|books?|chapters?)\b", " ", key, flags=re.I)
    key = re.sub(r"[|:\-–—()]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip(" .-–—|").lower()
    return key


def detect_books(episodes: list[dict], min_parts: int = 4) -> list[dict]:
    """Group episodes into serialized books, dropping duplicate re-releases.

    Two passes. The first is strict: a title must look book-ish AND carry a part
    number. The second relaxes the book-ish test for episodes whose series key
    already matches a book found in pass one — that recovers openers like
    "Part 1: Welcome to $100M Money Models", which name no book format at all
    and would otherwise silently cost you chapter one of every book.
    """
    series = defaultdict(dict)
    for episode in episodes:
        title = episode["title"]
        if not BOOKISH.search(title):
            continue
        part = _part_number(title)
        if part is None:
            continue
        key = _series_key(title)
        if not key:
            continue
        series[key].setdefault(part, episode | {"part": part})

    known = {key for key, parts in series.items() if len(parts) >= min_parts}
    for episode in episodes:
        title = episode["title"]
        if BOOKISH.search(title):
            continue  # already had its chance in pass one
        part = _part_number(title)
        if part is None:
            continue
        key = _series_key(title)
        if key in known:
            series[key].setdefault(part, episode | {"part": part})

    books = []
    for key, parts in series.items():
        if len(parts) < min_parts:
            continue
        numbers = sorted(parts)
        gaps = [n for n in range(min(numbers), max(numbers) + 1) if n not in parts]
        books.append(
            {
                "series": key,
                "parts": [parts[n] for n in numbers],
                "count": len(numbers),
                "range": [min(numbers), max(numbers)],
                "gaps": gaps,
            }
        )
    return sorted(books, key=lambda b: -b["count"])


def pick_voice_episodes(episodes: list[dict], books: list[dict], limit: int) -> list[dict]:
    """Longest non-book episodes — unscripted talking, not book narration."""
    book_urls = {p["url"] for b in books for p in b["parts"]}

    def seconds(episode: dict) -> int:
        raw = (episode.get("duration") or "").strip()
        if raw.isdigit():
            return int(raw)
        parts = [int(p) for p in raw.split(":") if p.isdigit()]
        total = 0
        for value in parts:
            total = total * 60 + value
        return total

    candidates = [e for e in episodes if e["url"] not in book_urls]
    candidates.sort(key=seconds, reverse=True)
    return candidates[:limit]


# ------------------------------------------------------------- transcription


def check_transcribe_deps(model: Path) -> str:
    whisper = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if not whisper:
        fail(
            "--transcribe needs whisper.cpp. Install it with:\n"
            "  brew install whisper-cpp ffmpeg\n"
            "then download a model, e.g.:\n"
            "  mkdir -p ~/.local/share/whisper && curl -L -o ~/.local/share/whisper/ggml-small.en.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
        )
    if not shutil.which("ffmpeg"):
        fail("--transcribe needs ffmpeg (brew install ffmpeg)")
    if not model.exists():
        fail(f"whisper model not found at {model} — pass --model, or download one (see --help)")
    return whisper


def transcribe(whisper: str, model: Path, url: str, workdir: Path) -> str | None:
    """Download one episode and return its transcript text."""
    mp3, wav, stem = workdir / "ep.mp3", workdir / "ep.wav", workdir / "ep"
    for stale in (mp3, wav, workdir / "ep.txt"):
        stale.unlink(missing_ok=True)
    try:
        mp3.write_bytes(http_get(url))
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"    download failed: {error}", file=sys.stderr)
        return None
    if mp3.stat().st_size < MIN_AUDIO_BYTES:
        print("    audio too small, skipping", file=sys.stderr)
        return None
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        capture_output=True,
    )
    subprocess.run(
        [whisper, "-m", str(model), "-f", str(wav), "--output-txt",
         "--output-file", str(stem), "-np", "-nt"],
        capture_output=True,
    )
    out = workdir / "ep.txt"
    text = out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""
    for stale in (mp3, wav, out):
        stale.unlink(missing_ok=True)
    return text or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--search", help='Person or show name to look up (e.g. "Alex Hormozi")')
    parser.add_argument("--feed", help="Explicit RSS feed URL (skips lookup)")
    parser.add_argument("--mode", choices=["detect", "books", "voice", "both"], default="detect",
                        help="detect = report only, no downloads (default); books/voice/both = pull material")
    parser.add_argument("--transcribe", action="store_true",
                        help="Actually transcribe audio. Without this, only the manifest is written.")
    parser.add_argument("--max-episodes", type=int, default=6, help="Cap for voice episodes (default 6)")
    parser.add_argument("--max-book-parts", type=int, default=60, help="Safety cap per book (default 60)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="whisper.cpp model path")
    parser.add_argument("--out", required=True, help="Persona directory")
    args = parser.parse_args()
    if not args.search and not args.feed:
        parser.error("provide --search or --feed")
    return args


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    workdir = out_dir / ".work-podcast"
    out_dir.mkdir(parents=True, exist_ok=True)

    feed_url = args.feed
    if not feed_url:
        feeds = find_feeds(args.search)
        if not feeds:
            fail(f"no podcast attributable to {args.search!r} — most people don't have one, "
             "and an unattributable feed is worse than none; this is not an error", 1)
        for i, f in enumerate(feeds):
            marker = "->" if i == 0 else "  "
            print(f"  {marker} [{f['confidence']:>9}] {f['name']} by {f['artist']} ({f['episodes']} episodes)")
        feed_url = feeds[0]["feed"]
        print(f"Using: {feeds[0]['name']}")

    episodes = parse_feed(feed_url)
    if not episodes:
        fail("feed had no downloadable episodes", 1)
    print(f"{len(episodes)} episodes in feed")

    books = detect_books(episodes)
    print(f"\n=== serialized books detected: {len(books)} ===")
    for book in books:
        gaps = f"  GAPS at {book['gaps']}" if book["gaps"] else "  (complete)"
        print(f"  {book['count']:>3} parts  [{book['range'][0]}-{book['range'][1]}]  {book['series'][:52]}{gaps}")
    if not books:
        print("  (none — this feed is interview/monologue content, not serialized books)")

    manifest = {
        "feed": feed_url,
        "episode_count": len(episodes),
        "books": [{k: v for k, v in b.items() if k != "parts"} | {"parts": len(b["parts"])} for b in books],
    }
    (out_dir / "podcast.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if args.mode == "detect":
        total = sum(b["count"] for b in books)
        if total:
            print(f"\n{total} book parts available. Re-run with --mode books --transcribe to pull them.")
            print("Expect roughly 30-45 min of transcription per 8 hours of audio on Apple silicon.")
        return

    if not args.transcribe:
        print("\n--mode set but --transcribe not passed: wrote manifest only, no audio fetched.")
        return

    whisper = check_transcribe_deps(args.model)
    workdir.mkdir(parents=True, exist_ok=True)
    books_dir, pod_dir = out_dir / "books", out_dir / "podcast"

    if args.mode in ("books", "both"):
        books_dir.mkdir(exist_ok=True)
        for book in books:
            parts = book["parts"][: args.max_book_parts]
            if len(book["parts"]) > len(parts):
                print(f"NOTE: capping {book['series'][:40]} at {len(parts)}/{len(book['parts'])} parts "
                      f"(raise --max-book-parts to include the rest)")
            print(f"\n--- {book['series'][:60]} ({len(parts)} parts) ---")
            chunks = []
            for i, part in enumerate(parts, 1):
                print(f"  [{i}/{len(parts)}] {part['title'][:60]}")
                text = transcribe(whisper, args.model, part["url"], workdir)
                if text:
                    chunks.append(f"\n\n## {part['title']}\n\n{text}")
                    print(f"      {len(text.split()):,} words")
            if not chunks:
                continue
            body = "".join(chunks).strip()
            dest = books_dir / f"{slugify(book['series'])}.txt"
            dest.write_text(
                f"# {book['series']}\n# Source: podcast feed {feed_url}\n"
                f"# {len(chunks)} episodes, transcribed with whisper.cpp\n"
                f"# Rights: author-released audio on their public podcast feed\n\n{body}\n",
                encoding="utf-8",
            )
            print(f"  -> {dest.name} ({len(body.split()):,} words)")

    if args.mode in ("voice", "both"):
        pod_dir.mkdir(exist_ok=True)
        picks = pick_voice_episodes(episodes, books, args.max_episodes)
        print(f"\n--- {len(picks)} voice episodes ---")
        for i, episode in enumerate(picks, 1):
            print(f"  [{i}/{len(picks)}] {episode['title'][:60]}")
            text = transcribe(whisper, args.model, episode["url"], workdir)
            if not text:
                continue
            (pod_dir / f"{slugify(episode['title'])}.txt").write_text(
                f"# {episode['title']}\n# {feed_url}\n\n{text}\n", encoding="utf-8"
            )
            print(f"      {len(text.split()):,} words")

    shutil.rmtree(workdir, ignore_errors=True)
    print("\nDone.")


if __name__ == "__main__":
    main()
