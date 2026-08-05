# Talk to Anyone

![Talk to Anyone — Claude Code plugin](assets/social-preview.png)

**Turn any person on earth into your personal coach, inside Claude Code.** Type `/coach` and a name. It researches them — their real interviews, books, transcripts, writings — and then you're talking to them.

```
/coach alex hormozi     → business coaching from Alex Hormozi
/coach andrew huberman  → health protocols from Andrew Huberman
/coach warren buffett   → no YouTube channel needed — interviews + shareholder letters
/coach marcus aurelius  → no video at all — built from his actual writings
```

When you name a person, the plugin researches them across the web — interviews,
books, articles, documented quotes — and, when they have video content anywhere on
YouTube (their own channel or interviews on other people's), pulls **real transcripts
of them speaking** (no API keys). Everything gets distilled into a persona file: their
voice, beliefs, named frameworks, signature phrases, and how they actually give
advice. Then Claude *becomes* them for the rest of the conversation — answers come in
their voice, grounded in what they actually teach. Works for anyone with a public
footprint, living or historical.

Personas are cached locally, so the first `/coach alex hormozi` takes a couple of
minutes and every one after loads instantly.

## Install

```
/plugin marketplace add coltonjosephdean-rgb/talk-to-anyone
/plugin install talk-to-anyone@talk-to-anyone
```

**Optional (recommended):** [yt-dlp](https://github.com/yt-dlp/yt-dlp) for transcript
pulling — `brew install yt-dlp` or `pip3 install --user yt-dlp`. Without it, personas
build from web research alone; with it, people with video content get their real
spoken voice mined from transcripts.

**Optional (for books):** a PDF extractor, only if you want to feed in PDF books —
`brew install poppler` (recommended; no Python packaging) or `pip3 install --user pypdf`.
EPUB, plain text and Project Gutenberg need nothing at all. On Homebrew Python,
`pip3 --user` is often blocked by PEP 668 — poppler sidesteps that.

## Commands

| Command | What it does |
| --- | --- |
| `/coach <name>` | Build (or load) the persona and start talking to them |
| `/coach-switch <name>` | Swap coaches mid-conversation |
| `/coach-end` | Back to normal Claude |
| `/coach-list` | Show every coach saved on this machine |
| `/coach-refresh <name>` | Rebuild a persona from fresh research |

If the short names collide with another plugin, use the namespaced form:
`/talk-to-anyone:coach <name>`.

## How it works

1. **Identify** — web-searches the name (handles misspellings: "alex formosi" →
   Alex Hormozi) and maps their public footprint.
2. **Pull spoken content** — when any exists: their own channel's popular videos, or
   long-form interviews/podcasts/keynotes OF them on any channel (`--search` mode).
   `scripts/fetch_youtube.py` downloads captions with yt-dlp (manual subs preferred,
   auto-captions fallback) and cleans them into plain-text transcripts.
3. **Pull their books** — when legitimately obtainable. `scripts/fetch_books.py` reads
   public-domain works straight from Project Gutenberg, an author's own free release
   from a URL, or a copy you already own on disk (PDF / EPUB / TXT). Transcripts give
   you how someone *talks*; books give you how they *think* with room to build an
   argument — frameworks in order, worked examples, the caveats that don't survive a
   soundbite. See [Sourcing books](#sourcing-books).
4. **Deep web research** — every build: named frameworks, print interviews and profiles,
   verified quotes, bio, common criticism, plus the core ideas of any book that
   couldn't be obtained in step 3. For people with little or no video this is the
   primary source; for historical figures the corpus is their own writings.
5. **Distill** — Claude merges the streams into a structured persona file: identity,
   voice & delivery, core beliefs (with verbatim quotes), named frameworks with their
   actual steps, coaching style, signature quotes, and embodiment rules.
6. **Embody** — Claude speaks as them until you end or switch. Advice is grounded in
   their real content; when it extrapolates beyond it, it says so in their voice.

Personas live in `${CLAUDE_PLUGIN_DATA}/personas/<slug>/` (survives plugin updates),
falling back to `~/.claude/talk-to-anyone/personas/`. Each contains `persona.md`,
`videos.json`, the raw `transcripts/`, and — when books were pulled — `books.json`
plus the extracted `books/`.

## Sourcing books

The persona file distinguishes books **read in full** from works known only through
web summaries, so you can always tell which one you're talking to. Three ways in:

| Source | How | Needs you |
| --- | --- | --- |
| **Public domain** | `--gutenberg "<author>"` searches Project Gutenberg and pulls full texts | Nothing — automatic |
| **Author-released** | `--url <pdf-url>` for a free copy the author published themselves | The URL |
| **Your own copy** | `--local <file-or-dir>` extracts PDF / EPUB / TXT from disk | The file |
| **Their podcast feed** | `fetch_podcast.py --mode books --transcribe` finds audiobooks serialized as episodes | Nothing — automatic |

**Podcast feeds are the highest-yield source for working authors.** Authors routinely
serialize their audiobooks as podcast episodes, where no web search will find them.
`fetch_podcast.py --search "<Name>"` detects them in one API call without downloading
anything. For Alex Hormozi it finds all four books complete, including the 26-part
*$100M Lost Chapters* that was never published as a book. Detection is free;
transcription is opt-in via `--transcribe` because a serialized book can be 20+ hours.

Feed attribution is deliberately strict: the person's name must be the publisher or
appear in the show title. iTunes returns confident results for people with no podcast
at all, and inheriting a stranger's voice is worse than finding nothing.

**Not supported, deliberately:** searching shadow libraries (Library Genesis, Anna's
Archive, Z-Library). If a book isn't public domain, author-released, or supplied by
you, the build treats it as unavailable, falls back to web research for its ideas, and
labels it as such in the persona file rather than passing a summary off as the book.

## What it feels like

Real output from a built persona, not a mockup — `/coach alex hormozi`, first message:

> What's up, man. Rock and roll — let's do this.
>
> So here's how this usually goes: you tell me what you're working on, and then I'm
> gonna ask you a bunch of questions, because I always fix things from back to front —
> the thing you sell, how you sell it, who you sell it to, and how they find out about
> you. Right? And most of the time the constraint isn't the one people think it is.
>
> So — what do you do, how much money do you make, and what's the problem right now?

That opener isn't scripted. It's his actual diagnostic pattern, mined from 12 of his
videos.

## Example personas (built by this exact pipeline)

| Persona | Path exercised | Sources |
| --- | --- | --- |
| [Alex Hormozi](examples/alex-hormozi/persona.md) | Own YouTube channel | 12 videos, ~59k transcript words |
| [Warren Buffett](examples/warren-buffett/persona.md) | **No channel of his own** | 6 interviews from other channels (~69k words) + his shareholder letters |
| [Marcus Aurelius](examples/marcus-aurelius/persona.md) | **No video ever existed** | Meditations full text, every quote cited by book.section |

## How this repo was built

One overnight run of Claude Code. I described the idea, went to bed, and woke up to
this working plugin — researched, written, tested against real channels, and pushed.
The persona quality bar (verbatim quotes only, refuse unsourceable ones) came from the
same run: the Buffett build rejected the famous "20 years to build a reputation" quote
because it couldn't source it, and used his 1991 Senate testimony line instead.

**If this made you want to try it, [star the repo](https://github.com/coltonjosephdean-rgb/talk-to-anyone/stargazers) — it's how other people find it.**

## Honest limits

- **It's an emulation, not the person.** Built only from public content; it will say so
  if you ask. It won't invent personal facts or private opinions.
- **Not professional advice.** A Huberman persona repeating his public protocols is not
  your doctor; a Hormozi persona is not your fiduciary.
- Voice fidelity scales with source quality: video-rich people sound sharpest,
  web-only builds lean on written quotes, and historical figures are reconstructions
  from their writings in their era's register.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `yt-dlp is not installed` | `brew install yt-dlp` (or `pip3 install --user yt-dlp`), then `/coach-refresh <name>` |
| Zero transcripts fetched | Channel may have captions disabled or region-blocked; persona builds from web research instead |
| `no working PDF extractor` | `brew install poppler` (or `pip3 install --user pypdf`), then re-run |
| PDF yields almost no text | It's a scan with no text layer — needs OCR first, or use the EPUB instead |
| Persona shows 0 books for an author | Expected unless you supplied one — `/coach-refresh <name>` with a copy to hand |
| Wrong person picked | `/coach-refresh` with a more specific name ("the podcaster", "the founder of X") |
| Want the entire channel | `fetch_youtube.py --channel <url> --max-videos 0` — resumes if interrupted, and later runs fetch only new uploads |
| Commands not showing | `/plugin` → verify talk-to-anyone is installed + enabled, then restart Claude Code |

## Repo layout

```
.claude-plugin/
  plugin.json          # plugin manifest
  marketplace.json     # this repo doubles as its own marketplace
skills/
  coach/               # main skill + persona template
  coach-switch/  coach-end/  coach-list/  coach-refresh/
scripts/
  fetch_youtube.py     # channel/search/URLs → long-form videos → clean transcripts
                       #   --max-videos 0 fetches the whole channel; runs resume
  fetch_books.py       # Gutenberg/URL/local files → PDF·EPUB·TXT → clean book text
  fetch_podcast.py     # iTunes lookup → RSS → serialized-audiobook detection → whisper
examples/
  alex-hormozi/        # real persona built by this pipeline
```
