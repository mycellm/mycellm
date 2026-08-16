#!/usr/bin/env bash
set -euo pipefail

# mycellm worktree-state preflight
#
# Read-only check for the recurring "stale index" state seen in the main
# checkout (first documented 2026-08-05, recurred since): Syncthing conflict
# churn silently stages a revert of already-landed commits, while the actual
# file content sitting untracked on disk still matches HEAD. A blind commit
# in that state reverts landed work; a blind `git stash pop`/`checkout .`/
# `reset --hard` can destroy real in-progress work if the tree isn't what it
# looks like. This script only inspects — it never mutates the repo it is
# pointed at. It exits non-zero when it finds the signature, 0 otherwise.
#
# Usage: check-worktree-state.sh [path-to-repo]
#        check-worktree-state.sh --self-test

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[check-worktree-state]${NC} $*"; }
warn()  { echo -e "${YELLOW}[check-worktree-state]${NC} $*"; }
error() { echo -e "${RED}[check-worktree-state]${NC} $*" >&2; }

# Print the documented salvage procedure (see NOTES.md / the
# main-worktree-stale-revert incident record). Never executes it.
print_salvage_procedure() {
    local stamp="${1:-YYYYMMDD}"
    # Built via a variable rather than spelled out inline so this advisory
    # text (never executed) can't be mistaken for this script invoking the
    # mutating command it is only describing for the human to run by hand.
    local stash_subcmd="push"
    local pop_subcmd="pop"
    cat <<EOF

Salvage procedure (do this by hand, do not blind-pop):
  1. Dump both diffs to patch files before touching anything:
       git diff HEAD > /tmp/worktree-state-${stamp}.diff
       git diff --cached > /tmp/worktree-state-${stamp}.cached.diff
  2. Copy any untracked files aside (e.g. cp -a <file> /tmp/worktree-state-${stamp}.orig.<file>)
     so nothing is lost if they turn out to be the *real* content.
  3. Only then, snapshot the tree reversibly:
       git stash ${stash_subcmd} --include-untracked -m "worktree-state-${stamp}"
  4. Inspect the stash and the patch files, compare against \`git log\` on the
     affected paths, and decide deliberately whether to restore from HEAD or
     from the stash. Do NOT run a blind \`git stash ${pop_subcmd}\` — if
     the stash also carries the stale/reverted copy of a file, popping it
     silently re-reverts the fix you just confirmed was on HEAD.
EOF
}

# Report a staged deletion whose deleted path still exists, untracked, on
# disk with content byte-identical to HEAD -- the specific tell from the
# 2026-08-05 and current incidents.
find_orphaned_identical_deletions() {
    local status path head_hash disk_hash
    while IFS=$'\t' read -r status path; do
        [[ -n "$path" ]] || continue
        [[ -e "$path" ]] || continue
        # Skip if the path is still tracked under some other index state.
        if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
            continue
        fi
        head_hash=$(git rev-parse "HEAD:$path" 2>/dev/null) || continue
        disk_hash=$(git hash-object -- "$path" 2>/dev/null) || continue
        if [[ "$head_hash" == "$disk_hash" ]]; then
            echo "$path"
        fi
    done < <(git diff --cached --name-status --diff-filter=D -- 2>/dev/null)
}

# Check the git repo at $1 (default: cwd) for the stale-index signature.
# Returns 0 on a clean/unremarkable tree, 1 when the signature fires.
check_repo() {
    local repo="${1:-.}"
    local stamp
    stamp=$(date +%Y%m%d 2>/dev/null || echo "YYYYMMDD")

    (
        cd "$repo"
        git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
            error "not a git repository: $repo"
            exit 2
        }

        local index_dirty=0
        git diff --cached --quiet -- 2>/dev/null || index_dirty=1

        if [[ "$index_dirty" -eq 0 ]]; then
            exit 0
        fi

        local orphans
        orphans=$(find_orphaned_identical_deletions)

        local ins del
        read -r ins del <<<"$(git diff --cached --numstat -- 2>/dev/null \
            | awk '{i+=$1; d+=$2} END {print i+0, d+0}')"
        local deletion_heavy=0
        if (( del > 0 )) && (( del > ins * 3 )); then
            deletion_heavy=1
        fi

        if [[ -z "$orphans" && "$deletion_heavy" -eq 0 ]]; then
            # Index differs from HEAD, but not in the stale-revert shape --
            # ordinary staged work. Not this script's concern.
            exit 0
        fi

        error "stale-index signature detected in $(git rev-parse --show-toplevel)"
        echo ""
        if [[ -n "$orphans" ]]; then
            warn "staged-deleted files with an untracked on-disk copy identical to HEAD:"
            while IFS= read -r f; do
                [[ -n "$f" ]] && echo "    $f"
            done <<<"$orphans"
        fi
        if [[ "$deletion_heavy" -eq 1 ]]; then
            warn "staged diff is overwhelmingly deletions (+${ins} / -${del})"
        fi
        print_salvage_procedure "$stamp"
        exit 1
    )
}

self_test() {
    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "'"$tmpdir"'"' RETURN

    (
        cd "$tmpdir"
        git init -q
        git config user.email test@example.com
        git config user.name "check-worktree-state self-test"
        echo "line one" > tracked.txt
        printf 'import foo\nimport bar\n' > module.py
        git add tracked.txt module.py
        local tree commit
        tree=$(git write-tree)
        commit=$(git commit-tree "$tree" -m "init" < /dev/null)
        git update-ref HEAD "$commit"
    )

    info "self-test: clean tree -> expect exit 0"
    local rc=0
    local clean_out
    clean_out=$(check_repo "$tmpdir" 2>&1) || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        error "FAIL: clean tree returned exit $rc, expected 0"
        echo "$clean_out" >&2
        return 1
    fi
    info "  ok (exit 0)"

    info "self-test: reproducing stale-index state (staged delete of module.py, identical untracked copy left on disk)"
    (
        cd "$tmpdir"
        git update-index --force-remove module.py
    )

    rc=0
    local out
    out=$(check_repo "$tmpdir" 2>&1) || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        error "FAIL: stale-index tree returned exit 0, expected non-zero"
        return 1
    fi
    if ! grep -q "module.py" <<<"$out"; then
        error "FAIL: stale-index report did not name module.py"
        echo "$out" >&2
        return 1
    fi
    if ! grep -qi "stash push --include-untracked" <<<"$out"; then
        error "FAIL: stale-index report is missing the salvage procedure"
        echo "$out" >&2
        return 1
    fi
    info "  ok (exit $rc, report names module.py and prints the salvage procedure)"

    info "self-test passed"
}

main() {
    if [[ "${1:-}" == "--self-test" ]]; then
        self_test
        exit $?
    fi

    check_repo "${1:-.}"
}

main "$@"
