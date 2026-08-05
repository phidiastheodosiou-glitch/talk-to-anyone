#!/usr/bin/env bash
# Report what the original repo has that this fork doesn't — and whether merging
# it would conflict. Deliberately STOPS before merging.
#
# A fork does not track upstream automatically, and this fork has rewritten the
# files upstream is most likely to touch (skills/coach/SKILL.md, README.md), so
# an unattended merge is a good way to silently corrupt the skill. This script
# does the remembering; you keep the deciding.
#
# Usage:  ./scripts/sync-upstream.sh
#
# Exit codes: 0 = up to date, 10 = updates available and merge is clean,
#             11 = updates available but merge conflicts, 2 = setup problem.

set -uo pipefail

UPSTREAM_URL="https://github.com/coltonjosephdean-rgb/talk-to-anyone.git"
UPSTREAM_BRANCH="main"

cd "$(dirname "$0")/.." || { echo "ERROR: cannot find repo root" >&2; exit 2; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "ERROR: not a git repository" >&2; exit 2; }

if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "Adding 'upstream' remote -> $UPSTREAM_URL"
  git remote add upstream "$UPSTREAM_URL" || exit 2
fi

echo "Fetching upstream..."
git fetch --quiet upstream "$UPSTREAM_BRANCH" || {
  echo "ERROR: could not reach upstream. Offline, or the repo moved?" >&2; exit 2; }

CURRENT="$(git rev-parse --abbrev-ref HEAD)"
BEHIND="$(git rev-list --count HEAD..upstream/$UPSTREAM_BRANCH)"
AHEAD="$(git rev-list --count upstream/$UPSTREAM_BRANCH..HEAD)"

echo
echo "Branch '$CURRENT': $AHEAD commit(s) ahead of upstream, $BEHIND behind."

if [ "$BEHIND" -eq 0 ]; then
  echo "Already up to date with upstream. Nothing to do."
  exit 0
fi

echo
echo "=== What upstream added ==="
git --no-pager log --oneline --no-decorate "HEAD..upstream/$UPSTREAM_BRANCH" | sed 's/^/  /'

echo
echo "=== Files upstream touched ==="
git --no-pager diff --stat "HEAD...upstream/$UPSTREAM_BRANCH" | sed 's/^/  /'

# Real conflict check: merge-tree computes the merge in memory, so the working
# tree is never touched and nothing needs unwinding if it goes badly.
echo
echo "=== Would merging conflict? ==="
CONFLICTS=""
if git merge-tree --write-tree --name-only HEAD "upstream/$UPSTREAM_BRANCH" >/tmp/.sync_mt 2>/dev/null; then
  echo "  No conflicts — the merge would apply cleanly."
  STATUS=10
else
  # Output is: tree oid, then conflicted paths, then a blank line, then
  # human-readable commentary ("Auto-merging ...", "CONFLICT (content) ...").
  # Take only the path block — everything after line 1 up to the first blank.
  CONFLICTS="$(tail -n +2 /tmp/.sync_mt | awk 'NF==0{exit} {print}')"
  if [ -n "$CONFLICTS" ]; then
    echo "  Conflicts in:"
    echo "$CONFLICTS" | sed 's/^/    /'
    echo
    echo "  These are files this fork also changed. Review each one and keep the"
    echo "  fork's additions — the books, podcast and provenance work lives in"
    echo "  skills/coach/SKILL.md and README.md."
  else
    echo "  Merge reported a problem but listed no paths; inspect manually."
  fi
  STATUS=11
fi
rm -f /tmp/.sync_mt

cat <<EOF

=== Next steps (nothing has been changed yet) ===
  Review the commits above, then if you want them:

    git merge upstream/$UPSTREAM_BRANCH
    # resolve any conflicts, then:
    git push origin $CURRENT
    claude plugin marketplace update talk-to-anyone

  To back out mid-merge:  git merge --abort
EOF
exit $STATUS
