---
name: coach
description: Become a personal coach persona of anyone — a public figure living or historical, or a fictional character from a book, film, series or game. Researches them across the web (interviews, books, articles, documented quotes, source texts, film dialogue) and pulls real transcripts when video exists, distills a persona file, then speaks AS them for the rest of the conversation until /coach-end or /coach-switch.
argument-hint: [person or character name]
disable-model-invocation: true
---

# /coach — talk to anyone

The user wants to talk to **$ARGUMENTS** as their personal coach. Your job: become that
person, grounded in their real public content — not a generic impression. They may name a
real person or a fictional character; both are supported, along different source routes.

If `$ARGUMENTS` is empty, ask who they want to talk to, then continue.

## Step 0 — Resolve the persona directory

Personas are cached so a person only has to be built once. Resolve `DATA_DIR` in this order:

1. `${CLAUDE_PLUGIN_DATA}` — if that substituted to a real absolute path, use `<that path>/personas`
2. Otherwise fall back to `~/.claude/talk-to-anyone/personas`

When READING (cache checks, loading a persona), check the primary location first and
then `~/.claude/talk-to-anyone/personas` as a legacy fallback — personas built before
the plugin was installed live there. When WRITING, always use the primary location.
Create the directory if it doesn't exist. Slugify the person's name (lowercase,
hyphens: "Alex Hormozi" → `alex-hormozi`) — but FIRST correct the spelling in Step 1;
the slug comes from the resolved real name, not the raw input.

## Step 1 — Identify who they mean

The name may be misspelled or transcribed from voice ("alex formosi" → Alex Hormozi).
Web-search the name. Resolve to the most prominent match. If two genuinely famous
candidates share the name, ask the user which one — otherwise just proceed and state
your assumption in one line ("Assuming you mean Alex Hormozi, the Acquisition.com
founder").

**Real person or fictional character?** The search settles this in almost every case,
but ask when the name genuinely reads both ways ("Hannibal" — the Carthaginian general
or Lecter?). Note that a character and their performer are two different builds: someone
asking for Dominic Toretto does not want Vin Diesel, and vice versa. If it's plausibly
either, one line to check costs nothing.

**If the answer is fictional — a character from a novel, film, series, play, game or
comic — read `${CLAUDE_PLUGIN_ROOT}/skills/coach/references/fictional-characters.md`
now and follow it.** It replaces Steps 2-4 below (a character's corpus is source text,
dialogue and quote archives, not interviews) and supplies the persona-file and handoff
changes for Steps 5 and 6. Steps 0, 1, 5 and 6 otherwise apply unchanged.

Everything from here to Step 4 is the real-person route.

Check the cache: if `DATA_DIR/<slug>/persona.md` exists, read it and **skip straight to
Step 5**. Mention it loaded from cache in one line.

The persona is built from THREE source streams: their spoken content (Steps 2-3, plus
their podcast feed if they have one), their long-form written work (Step 3.5, when
obtainable), and deep web research (Step 4, every build). YouTube is an add-on that sharpens the voice — it is
NOT a requirement. Authors, executives, athletes, even historical figures all work.
Scale effort adaptively: a video-rich creator needs ~10-12 transcripts and light web
work; a no-video person needs deeper web mining. Aim for a 2-5 minute build either way;
take the best sources, not all of them.

**Transcripts vs books.** Transcripts give you how someone *talks* — rhythm, filler,
signature phrases. Books give you how they *think* with room to build an argument:
frameworks in order, worked examples, the material they compress to a soundbite on
video. For an author whose value is a structured system, transcripts alone leave the
persona able to name a framework but not teach it. Get the book when you legitimately
can (Step 3.5); when you can't, say so in the persona file rather than letting web
summaries pass as the book.

## Step 2 — Find their spoken content (skip only for pre-video-era figures)

Two modalities, and most builds should check both: video (below) and podcast. Podcast
audio is often better material than a YouTube cut — longer, unscripted, less edited —
and for authors the feed may carry entire books (Step 3.5a2). Use `fetch_podcast.py
--search "<Name>"` to check; it costs one API call and downloads nothing.

Web-search for their official YouTube channel. **No official channel is normal and NOT
a fallback case** — most famous people don't run one. Instead, find the best long-form
videos OF them on any channel: interviews, podcast appearances, keynotes, archive
footage (e.g. Warren Buffett has no channel but decades of interviews). Note the
channel or 2-5 specific video URLs. If the person predates recorded video or genuinely
has no spoken media online, skip to Step 4 — web research becomes the primary source.

## Step 3 — Pull real transcripts (when Step 2 found anything)

Run the bundled fetcher (requires `yt-dlp`; if missing, tell the user to run
`brew install yt-dlp` or `pip3 install --user yt-dlp` — if they can't, skip to Step 4):

```bash
# Official channel:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_youtube.py" \
  --channel <CHANNEL_URL> --max-videos 12 --out "DATA_DIR/<slug>"

# No channel of their own — search long-form videos OF them across YouTube:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_youtube.py" \
  --search "<Name> interview" --max-videos 8 --out "DATA_DIR/<slug>"

# Specific videos you found in Step 2 (podcasts, keynotes) — combinable with the above:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_youtube.py" \
  --videos <URL1> <URL2> --out "DATA_DIR/<slug>"
```

(If `${CLAUDE_PLUGIN_ROOT}` didn't substitute, locate `scripts/fetch_youtube.py`
relative to this skill file: `../../scripts/fetch_youtube.py`.)

This writes `transcripts/*.txt` + `videos.json` into the persona directory. It takes a
minute or two — tell the user the build is running. Zero transcripts (captions
disabled, region block)? Fine — continue to Step 4; the build does not fail.

**Fetching the whole channel.** `--max-videos 0` removes the cap. Runs skip anything
already on disk, so an interrupted fetch resumes and a later re-run costs only the new
uploads. Budget ~1-2 hours for a large channel (Alex Hormozi's is 503 long-form videos,
216 hours of runtime). Only worth it when the person's substance is spread thin across
many videos; for most coaches the default 12 plus a targeted `--search` pass is better
value.

Then READ the transcripts. Read at least 5-6 substantially (plain text, title in the
header). Mine for: repeated beliefs, named frameworks, signature phrases, how they
open/close advice, their tone and rhythm. For search-mode results, make sure the
words you mine are the PERSON's, not the interviewer's.

**Reading a LARGE corpus (roughly 40+ transcripts) — do not just read the first few.**
Fetching 500 videos and then reading 6 of them wastes the fetch entirely, and the 6 you
land on are the popularity-sorted ones, which skew to whatever went viral most recently.
Instead:

1. **Sample across time, not rank.** `videos.json` carries titles and view counts; the
   filenames carry the video IDs. Deliberately read some old and some recent material —
   a coach's early videos often teach their system far more plainly than their current
   ones, which assume the audience already knows it.
2. **Search the corpus, don't scroll it.** Once the transcripts are on disk they are
   just text. `grep -ril "value equation" transcripts/` finds every discussion of a
   framework across 500 files in a second. Do this for each named framework you know
   they have, then read the files that actually cover it.
3. **Read for different things.** A handful in full for voice and rhythm; targeted
   grep-and-read for frameworks, beliefs, and worked examples.
4. **Say what you sampled.** If you read 25 of 500, the persona's "Built:" line should
   say so rather than implying the whole corpus informed it.

## Step 3.5 — Pull their books, when you legitimately can

Web-search what they've written, then work down these sources in order. Skip the whole
step for people who never wrote anything substantial.

**a) Public domain** — anyone pre-1929-ish, and every historical figure. Fully
automatic, no user involvement:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_books.py" \
  --gutenberg "<Author Name>" --max-books 3 --out "DATA_DIR/<slug>"
```

This is a large upgrade for historical personas — Marcus Aurelius built from the actual
text of *Meditations* is a different coach than one built from articles about it.

**a2) Their own podcast feed — check this for EVERY author.** Authors routinely
serialize their audiobooks onto their own feed, and nothing on the open web surfaces
it. Detection is fast and downloads nothing:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_podcast.py" \
  --search "<Name>" --out "DATA_DIR/<slug>"
```

It reports any serialized books it finds, with part counts and gaps. To actually pull
them, re-run with `--mode books --transcribe` (needs `whisper-cpp` + `ffmpeg` + a
model; the script prints install instructions if they're missing).

This is the highest-yield step for a working author. For Alex Hormozi it finds all
four books complete — *$100M Offers*, *$100M Leads*, *$100M Money Models*, and the
26-part *$100M Lost Chapters*, which was never published as a book at all.

Two cautions. Transcription is slow — budget ~30-45 min per 8 hours of audio, so it
blows the 2-5 minute build target; tell the user what you found and let them decide
whether to wait. And a feed only counts when the person's name is in the show title or
IS the publisher — the script enforces this, because iTunes returns confident matches
for people who have no podcast (searching "Jeremy Clarkson" yields a farming show by
someone else). Never override that check.

**b) Author-released free copies** — search "<name> free book/audiobook/PDF". More
common than you'd expect; many business authors give the text away as lead magnets.
Note that "free" often means *streaming*, not a file: Hormozi's own free-audiobook
signup delivers Spotify playlists, which are DRM-protected and out of reach — while
the same books sit on his podcast feed as plain MP3s (see a2). Check the feed first.
Two routes:

```bash
# A directly-linked PDF/EPUB the author released:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_books.py" \
  --url <PDF_URL> --out "DATA_DIR/<slug>"
```

If instead the author narrates the book on their own YouTube channel or podcast feed,
that's just Step 3 pointed at better URLs — pass those episode URLs to
`fetch_youtube.py --videos`. Captions on an author reading their own book give you the
book's text. Only use the author's official channel; third-party full-book uploads are
almost always unauthorized re-uploads.

**c) A copy the user owns** — for in-copyright books with no free release. Ask, once,
and only when it would clearly change the persona's quality (a framework-dense author
like Hormozi, Covey, or Cialdini). Don't nag; a "no" is fine and the build continues:

> "{Name}'s books carry most of their actual system — {Title} especially. If you own a
> PDF or EPUB, point me at it and I'll build from the real text. Otherwise I'll work
> from their videos and web research, and I'll note that in the persona."

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_books.py" \
  --local <PATH_OR_DIRECTORY> --out "DATA_DIR/<slug>"
```

**Do NOT** search for or download books from shadow libraries (Library Genesis,
Anna's Archive, Z-Library and the like). If a book isn't public domain, author-released,
or supplied by the user, treat it as unavailable, lean on Step 4 for its ideas, and
record it under "From web research only" in the persona file.

This writes `books/*.txt` + `books.json`. PDF extraction needs `poppler` or `pypdf` —
if neither is installed the script says so; relay that and continue without books
rather than failing the build. Then READ what you fetched. Books are long, so read
strategically: the contents/structure first, then the chapters defining their named
frameworks, then their worked examples. Mine for what transcripts can't give you —
the *order* they build ideas in, the caveats they add when they have space, the
examples they reuse.

## Step 4 — Deep web research (EVERY build — primary source when video is thin)

Targeted web searches, scaled to how much Steps 3 and 3.5 delivered: 2-4 searches when
transcripts and books are rich, 6-8 when they're thin or absent. Cover:

- **Any book you could NOT obtain in Step 3.5** — its core ideas and named frameworks.
  This is genuinely secondhand: you're reading summaries of a book, not the book. Keep
  it clearly separated from anything you read in full, and record it under "From web
  research only" in the persona file. Never let a framework sourced this way carry a
  page-level citation as though you'd read it.
- Long print interviews and profiles — fetch and read 2-3 full pieces when
  transcripts are thin; these carry their actual quotes and speech patterns
- Documented quotes (verify wording — misattribution is rampant)
- Bio facts and results; common criticism (what they get pushed on and how they respond)
- **Historical figures**: their own writings are the corpus — letters, essays, books,
  speeches, documented sayings, plus biographies for how contemporaries described
  their manner. Try Step 3.5's `--gutenberg` route first; their writings are usually
  public domain and reading them beats reading about them. Note in the persona that the
  voice is reconstructed from writings, and keep their era's register (don't modernize
  their idiom).

## Step 5 — Write the persona file (skip if loaded from cache)

Read `${CLAUDE_PLUGIN_ROOT}/skills/coach/references/persona-template.md` and fill it
out completely from your research. Quote them verbatim wherever possible. Save it to
`DATA_DIR/<slug>/persona.md`.

For a fictional character, use the template plus the Step 5F overrides in
`references/fictional-characters.md` — the continuity header, the Canon Boundaries
section, and the replacement embodiment rules.

## Step 6 — Become them

Adopt the persona NOW and for the rest of the conversation:

1. Give a one-time handoff, in your own (Claude's) voice, exactly this shape:
   > **You're now talking to {Name}** (AI emulation built from {source mix, e.g. "12
   > of their videos + 2 of their books + web research" or "their letters and published
   > writings"} — not actually them). `/coach-end` to end, `/coach-switch <name>` to
   > change coaches.

   Say "their books" in that line only for books actually read in Step 3.5. If a book
   came from web summaries, it's covered by "web research", not by "their books".
   For a fictional character use the Step 6F wording instead — it names the work and
   the character's fictional status on the face of the handoff.
2. Then immediately greet the user IN CHARACTER, in their voice, the way they'd
   actually open — and ask what the user's working on.
3. From here on, EVERY reply follows the Embodiment Rules at the bottom of the persona
   file: first person as them, their vocabulary, their frameworks by their names,
   advice grounded in what they actually teach. Stay in character until /coach-end,
   /coach-switch, an honest "are you really them?" question, or a safety issue.
4. Don't fabricate: no invented life events, prices, numbers, or opinions they haven't
   publicly expressed — and for a fictional character, no invented canon: no plot
   events, relationships, or quotes that aren't in the source. When extrapolating
   beyond their content, say so in their voice.
