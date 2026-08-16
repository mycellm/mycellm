#!/usr/bin/env bash
# mycellm — local branch triage snapshot
#
# Read-only report: prints a per-branch ahead/behind/merged table for every
# local branch other than the base branch. Never deletes, pushes, resets, or
# force-updates a ref — pruning is a decision for a human to make and execute
# by hand; see the printed commands at the end of the report.
#
# Definitions (must match .drydock/branch-triage.md):
#   ahead/behind — `git rev-list --left-right --count <base>...<branch>`
#                  ahead  = commits on <branch> that <base> does not have
#                  behind = commits on <base> that <branch> does not have
#   merged       — `git merge-base --is-ancestor <branch> <base>` exits 0,
#                  i.e. the branch tip is already reachable from <base> and
#                  its residual diff vs <base> is empty.
set -euo pipefail

BASE="${1:-main}"

cd "$(git rev-parse --show-toplevel)"

if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
    echo "branch-triage: base branch '$BASE' not found" >&2
    exit 1
fi

printf '%-70s %6s %6s %10s\n' 'Branch' 'Ahead' 'Behind' 'Status'
printf '%-70s %6s %6s %10s\n' "$(printf -- '-%.0s' {1..70})" '------' '------' '----------'

git for-each-ref --format='%(refname:short)' refs/heads |
    grep -v "^${BASE}\$" |
    while IFS= read -r branch; do
        counts="$(git rev-list --left-right --count "${BASE}...${branch}")"
        behind="$(printf '%s' "$counts" | cut -f1)"
        ahead="$(printf '%s' "$counts" | cut -f2)"

        if git merge-base --is-ancestor "$branch" "$BASE" 2>/dev/null; then
            status="merged"
        else
            status="unmerged"
        fi

        printf '%-70s %6s %6s %10s\n' "$branch" "$ahead" "$behind" "$status"
    done

cat <<'EOF'

This is a report only: no branch was deleted, no ref was force-updated, and
nothing was pushed. See .drydock/branch-triage.md for the human-readable
triage (recommendation + one-line justification per branch, grouped into
fully-merged/safe-to-delete, superseded-unmerged/safe-to-delete, and
real-unmerged/keep). Pruning is the human's action; that document spells out
the exact ref-removal commands to run by hand — this script runs none of them.
EOF
