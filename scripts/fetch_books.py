#!/usr/bin/env python3
"""Fetch long-form written work for a person so Claude can build their persona.

Transcripts capture how someone talks. Books capture how they think when they have
room to build an argument — named frameworks in order, worked examples, the material
they never compress into a YouTube video. This script pulls that text from three
sources and writes it alongside the transcripts:

  1. --local      Books you already own, as PDF / EPUB / TXT / MD on disk
  2. --gutenberg  Public-domain works, searched and downloaded from Project Gutenberg
  3. --url        A directly-linked PDF or text file (an author's own free release)

Usage:
  python3 fetch_books.py --local ~/Books/100m-offers.pdf --out ./persona-workdir
  python3 fetch_books.py --local ~/Books/hormozi/ --out ./persona-workdir
  python3 fetch_books.py --gutenberg "Marcus Aurelius" --max-books 3 --out ./persona-workdir
  python3 fetch_books.py --url https://example.com/free-book.pdf --out ./persona-workdir

Writes clean plain-text books/*.txt + a books.json provenance index, mirroring the
transcripts/ + videos.json layout that fetch_youtube.py produces.

SOURCING: this script reads books you point it at and public-domain texts. It has no
search-the-whole-internet mode by design — for in-copyright work, supply your own copy
via --local, or the author's own free release via --url.

Requires: nothing for EPUB / TXT / Gutenberg (stdlib only).
PDF needs ONE of:  brew install poppler  (pdftotext)   OR   pip3 install --user pypdf
  On Homebrew Python, pip3 --user is often blocked by PEP 668; poppler avoids that.

Exit codes: 0 = at least one book extracted, 1 = none extracted, 2 = bad args/missing deps.
"""

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

GUTENDEX_API = "https://gutendex.com/books"
HTTP_TIMEOUT_SECONDS = 60
USER_AGENT = "talk-to-anyone/1.0 (persona builder; +https://github.com/coltonjosephdean-rgb/talk-to-anyone)"
MAX_TITLE_SLUG_LENGTH = 60
MIN_USEFUL_WORDS = 500  # below this it's a cover page or a failed extraction, not a book
BOOK_SUFFIXES = (".pdf", ".epub", ".txt", ".md")


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:MAX_TITLE_SLUG_LENGTH] or "book"


def normalize_text(text: str) -> str:
    """Collapse extraction artifacts without touching the author's actual prose."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("﻿", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)  # PDFs love blank lines
    return text.strip()


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


# ---------------------------------------------------------------- EPUB (stdlib only)


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from an XHTML chapter, dropping markup and script/style."""

    SKIP_TAGS = {"script", "style", "head", "title"}
    BREAK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(markup: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(markup)
    except Exception:  # noqa: BLE001 — malformed chapter shouldn't kill the whole book
        pass
    return parser.text()


def extract_epub(path: Path) -> str:
    """An EPUB is a zip of XHTML; read it in spine order when the manifest allows."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        opf_name = next((n for n in names if n.endswith(".opf")), None)
        ordered: list[str] = []
        if opf_name:
            opf = archive.read(opf_name).decode("utf-8", errors="replace")
            manifest = dict(re.findall(r'<item\b[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf))
            manifest.update(
                {i: h for h, i in re.findall(r'<item\b[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf)}
            )
            base = str(Path(opf_name).parent)
            for idref in re.findall(r"<itemref\b[^>]*idref=\"([^\"]+)\"", opf):
                href = manifest.get(idref)
                if not href:
                    continue
                candidate = str(Path(base) / href) if base not in (".", "") else href
                candidate = urllib.parse.unquote(candidate).replace("\\", "/")
                if candidate in names:
                    ordered.append(candidate)
        if not ordered:  # no spine, or hrefs didn't resolve — fall back to file order
            ordered = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]

        chapters = []
        for name in ordered:
            try:
                markup = archive.read(name).decode("utf-8", errors="replace")
            except KeyError:
                continue
            chapter = html_to_text(markup).strip()
            if chapter:
                chapters.append(chapter)
    return normalize_text("\n\n".join(chapters))


# ------------------------------------------------------------------------ PDF


NO_PDF_EXTRACTOR_HELP = (
    "no working PDF extractor is installed. Install one of:\n"
    "  brew install poppler                      (provides pdftotext — no Python packaging)\n"
    "  pip3 install --user pypdf\n"
    "On Homebrew Python, pip3 --user may be blocked by PEP 668 "
    '("externally-managed-environment") — use poppler, a venv, or pipx in that case.'
)


def has_pdf_extractor() -> bool:
    """True if either supported PDF backend is available."""
    if shutil.which("pdftotext"):
        return True
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def extract_pdf(path: Path) -> str:
    """Try pypdf, then poppler's pdftotext.

    Raises RuntimeError (not sys.exit) so a single unreadable PDF only drops that
    book — the rest of the batch still runs. Absence of *any* extractor is caught
    up front in main() instead, since that's a one-time install, not a bad file.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        text = normalize_text("\n\n".join(pages))
        if len(text.split()) >= MIN_USEFUL_WORDS:
            return text
        print("  pypdf got almost no text (scanned PDF?), trying pdftotext", file=sys.stderr)
    except ImportError:
        pass
    except Exception as error:  # noqa: BLE001 — fall through to the other extractor
        print(f"  pypdf failed ({error}), trying pdftotext", file=sys.stderr)

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        result = subprocess.run(
            [pdftotext, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return normalize_text(result.stdout)

    raise RuntimeError(
        f"could not extract text from {path.name} "
        "(if it's a scan with no text layer, it needs OCR first)"
    )


# ------------------------------------------------------------- Project Gutenberg


GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)


def strip_gutenberg_boilerplate(text: str) -> str:
    """Drop the licence header/footer so only the author's words reach the persona."""
    start = GUTENBERG_START.search(text)
    if start:
        text = text[start.end():]
    end = GUTENBERG_END.search(text)
    if end:
        text = text[: end.start()]
    return text.strip()


def search_gutenberg(author: str, max_books: int) -> list[dict]:
    """Return downloadable public-domain works matching an author name."""
    url = f"{GUTENDEX_API}?{urllib.parse.urlencode({'search': author})}"
    print(f"Searching Project Gutenberg: {author}")
    try:
        payload = json.loads(http_get(url).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
        print(f"WARNING: Gutenberg search failed: {error}", file=sys.stderr)
        return []

    books = []
    for entry in payload.get("results", []):
        formats = entry.get("formats", {})
        text_url = next(
            (
                url
                for mime, url in formats.items()
                if mime.startswith("text/plain") and not url.endswith(".zip")
            ),
            None,
        )
        if not text_url:
            continue
        authors = ", ".join(a.get("name", "") for a in entry.get("authors", [])) or "Unknown"
        books.append(
            {
                "title": entry.get("title") or f"gutenberg-{entry.get('id')}",
                "author": authors,
                "source": "gutenberg",
                "origin": text_url,
                "license": "public domain (Project Gutenberg)",
            }
        )
        if len(books) >= max_books:
            break
    return books


# ------------------------------------------------------------------ collection


def collect_local(paths: list[str]) -> list[dict]:
    """Expand file and directory arguments into concrete book entries."""
    collected = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            print(f"WARNING: no such path, skipping: {path}", file=sys.stderr)
            continue
        if path.is_dir():
            files = sorted(f for f in path.rglob("*") if f.suffix.lower() in BOOK_SUFFIXES)
            if not files:
                print(f"WARNING: no {'/'.join(BOOK_SUFFIXES)} files under {path}", file=sys.stderr)
            for found in files:
                collected.append(_local_entry(found))
        elif path.suffix.lower() in BOOK_SUFFIXES:
            collected.append(_local_entry(path))
        else:
            print(f"WARNING: unsupported file type, skipping: {path.name}", file=sys.stderr)
    return collected


def _local_entry(path: Path) -> dict:
    return {
        "title": path.stem.replace("-", " ").replace("_", " ").strip() or path.name,
        "author": None,
        "source": "local",
        "origin": str(path),
        "license": "user-supplied copy",
    }


def load_text(entry: dict) -> str | None:
    """Resolve one book entry to plain text, whatever its source."""
    source, origin = entry["source"], entry["origin"]

    if source == "local":
        path = Path(origin)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return extract_pdf(path)
        if suffix == ".epub":
            return extract_epub(path)
        return normalize_text(path.read_text(encoding="utf-8", errors="replace"))

    try:
        raw = http_get(origin)
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"  download failed: {error}", file=sys.stderr)
        return None

    if source == "gutenberg":
        return normalize_text(strip_gutenberg_boilerplate(raw.decode("utf-8", errors="replace")))

    # --url: sniff PDF by magic bytes rather than trusting the extension
    if raw[:5] == b"%PDF-":
        scratch = Path(entry["_workdir"]) / "download.pdf"
        scratch.write_bytes(raw)
        try:
            return extract_pdf(scratch)
        finally:
            scratch.unlink(missing_ok=True)
    if raw[:2] == b"PK":  # zip container — most likely an EPUB
        scratch = Path(entry["_workdir"]) / "download.epub"
        scratch.write_bytes(raw)
        try:
            return extract_epub(scratch)
        finally:
            scratch.unlink(missing_ok=True)

    decoded = raw.decode("utf-8", errors="replace")
    if "<html" in decoded[:2000].lower():
        return normalize_text(html_to_text(decoded))
    return normalize_text(decoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--local",
        nargs="*",
        default=[],
        help="PDF/EPUB/TXT/MD files or directories you own (directories are searched recursively)",
    )
    parser.add_argument(
        "--gutenberg",
        help='Author name to search on Project Gutenberg (e.g. "Marcus Aurelius") — public domain only',
    )
    parser.add_argument(
        "--url",
        nargs="*",
        default=[],
        help="Direct URLs to a PDF/EPUB/text an author has released for free",
    )
    parser.add_argument(
        "--max-books", type=int, default=5, help="Cap on Gutenberg results to pull (default 5)"
    )
    parser.add_argument(
        "--out", required=True, help="Persona directory (books/ and books.json are written here)"
    )
    args = parser.parse_args()
    if not args.local and not args.gutenberg and not args.url:
        parser.error("provide --local, --gutenberg, and/or --url")
    if args.max_books < 1:
        parser.error("--max-books must be >= 1")
    return args


def main() -> None:
    args = parse_args()

    out_dir = Path(args.out).expanduser().resolve()
    books_dir = out_dir / "books"
    workdir = out_dir / ".work-books"
    books_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = collect_local(args.local)
    if args.gutenberg:
        entries.extend(search_gutenberg(args.gutenberg, args.max_books))
    entries.extend(
        {
            "title": Path(urllib.parse.urlparse(url).path).stem or "download",
            "author": None,
            "source": "url",
            "origin": url,
            "license": "author-released / user-supplied URL",
        }
        for url in args.url
    )

    if not entries:
        shutil.rmtree(workdir, ignore_errors=True)
        fail("no books to fetch", 1)

    # A missing extractor is a one-time install, not a bad file — say so before
    # downloading anything, rather than failing book-by-book halfway through.
    pdfs_queued = any(
        entry["source"] == "local" and entry["origin"].lower().endswith(".pdf") for entry in entries
    )
    if pdfs_queued and not has_pdf_extractor():
        shutil.rmtree(workdir, ignore_errors=True)
        fail(NO_PDF_EXTRACTOR_HELP)

    extracted = []
    for index, entry in enumerate(entries, 1):
        print(f"[{index}/{len(entries)}] {entry['title']}")
        entry["_workdir"] = str(workdir)
        try:
            text = load_text(entry)
        except Exception as error:  # noqa: BLE001 — one bad book shouldn't abort the batch
            print(f"  extraction failed: {error}", file=sys.stderr)
            continue
        finally:
            entry.pop("_workdir", None)

        if not text:
            print("  no text extracted, skipping", file=sys.stderr)
            continue
        words = len(text.split())
        if words < MIN_USEFUL_WORDS:
            print(f"  only {words} words extracted, skipping as incomplete", file=sys.stderr)
            continue

        filename = f"{slugify(entry['title'])}.txt"
        book_path = books_dir / filename
        header = (
            f"# {entry['title']}\n"
            f"# Author: {entry['author'] or 'unknown'}\n"
            f"# Source: {entry['source']} — {entry['origin']}\n"
            f"# Rights: {entry['license']}\n\n"
        )
        book_path.write_text(header + text + "\n", encoding="utf-8")
        extracted.append({**entry, "book_file": f"books/{filename}", "words": words})
        print(f"  saved {book_path.name} ({words:,} words)")

    # Merge rather than overwrite: a persona is typically built by calling this script
    # once per source lane (--gutenberg, then --url, then --local) into the same
    # directory, and each run must not erase the previous run's provenance.
    index_path = out_dir / "books.json"
    merged: dict[str, dict] = {}
    if index_path.exists():
        try:
            for existing in json.loads(index_path.read_text(encoding="utf-8")):
                if (path := existing.get("book_file")) and (out_dir / path).exists():
                    merged[path] = existing
        except (json.JSONDecodeError, TypeError):
            print("WARNING: books.json was unreadable, rewriting it", file=sys.stderr)
    for book in extracted:  # this run wins on re-extraction of the same file
        merged[book["book_file"]] = book
    index_path.write_text(json.dumps(list(merged.values()), indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(workdir, ignore_errors=True)

    total_words = sum(book["words"] for book in extracted)
    print(f"\nDone: {len(extracted)}/{len(entries)} books ({total_words:,} words) -> {books_dir}")
    if not extracted:
        sys.exit(1)


if __name__ == "__main__":
    main()
