---
name: coach-list
description: List all coach personas already built and cached on this machine.
disable-model-invocation: true
---

# /coach-list — saved coaches

Show the user every persona already built on this machine.

1. Resolve the personas directories the same way as /coach Step 0:
   `${CLAUDE_PLUGIN_DATA}/personas` if that substituted to a real path, AND the legacy
   location `~/.claude/talk-to-anyone/personas`. Check both, merge the results
   (primary wins on duplicate slugs).
2. List their subdirectories. For each one that contains a `persona.md`, pull the name
   and the "Built:" line from the top of the file, count files in `transcripts/`, and
   count files in `books/` (absent on personas built before books support — show 0).
3. Print a short table: coach name, built date, number of source videos, number of
   books read in full. A persona showing 0 books is not broken — most people haven't
   written any — but for an author it's the signal that `/coach-refresh <name>` with a
   book to hand would improve them. End with:
   `/coach <name>` loads instantly · `/coach-refresh <name>` rebuilds from fresh research.
4. If the directory is empty or missing, say no coaches are saved yet and that
   `/coach <name>` builds the first one.
