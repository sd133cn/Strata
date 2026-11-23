#!/usr/bin/env python3
"""Merge upstream into this branch on a scratch branch, so the working branch stays runnable.

Never merges onto the feature branch itself: the merge lands on sync/upstream-<tag>, and the
feature branch only fast-forwards to it once the checks are green.  A merge that goes wrong is
then `git branch -D` away from being forgotten, with the feature branch untouched.

Releases, not the tip: --tag (default origin/main) is merged one at a time in the caller's
hands, because a thousand commits merged at once stack their conflicts past the point of
telling which change caused which.

Exit codes:
  0  up to date, nothing to do
  2  merge is clean and committed on the scratch branch: run the checks, then fast-forward
  3  conflicts to resolve by hand (the files and their block counts are printed)
  1  the merge could not start (dirty tree, unknown ref, git failure)
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def git(*args, check=False):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("git %s failed:\n%s%s" % (" ".join(args), p.stdout, p.stderr))
    return p


def branch():
    return git("branch", "--show-current").stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--upstream", default="origin/main", help="what to merge (a release tag is better than the tip)")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()

    if not a.no_fetch:
        print("fetching %s ..." % a.upstream.split("/")[0])
        f = git("fetch", a.upstream.split("/")[0])
        if f.returncode != 0:
            print("fetch failed (continuing with what is already local):")
            print(f.stderr.strip()[:400])

    behind = git("rev-list", "--count", "HEAD..%s" % a.upstream).stdout.strip()
    if behind in ("", "0"):
        print("UP TO DATE: nothing to merge from %s" % a.upstream)
        return 0
    print("behind %s: %s commits" % (a.upstream, behind))

    origin = branch()
    scratch = "sync/upstream-" + re.sub(r"[^A-Za-z0-9._-]+", "-", a.upstream.rsplit("/", 1)[-1])
    if git("rev-parse", "--verify", scratch).returncode == 0:
        print("NOTE: %s already exists; reusing it" % scratch)
    else:
        git("checkout", "-b", scratch, origin, check=True)
        print("created %s from %s" % (scratch, origin))

    m = git("merge", "--no-ff", "--no-commit", a.upstream)
    unmerged = [l for l in git("diff", "--name-only", "--diff-filter=U").stdout.splitlines() if l.strip()]

    if unmerged:
        print()
        print("CONFLICTS in %d file(s):" % len(unmerged))
        for f in unmerged:
            p = ROOT / f
            n = 0
            if p.is_file():
                n = p.read_text(encoding="utf-8", errors="replace").count("<<<<<<<")
            print("  %2d block(s)  %s" % (n, f))
        print()
        print("Resolve each by hand (the merge is on %s; %s is untouched)." % (scratch, origin))
        print("Never `--ours` a whole file: that silently keeps this branch's version of")
        print("upstream's own work.  Both sides' additions usually belong in the result.")
        print("Then: git add <files> && git commit, and run check_wiring.py before trusting it.")
        return 3

    if m.returncode != 0:
        both = (m.stdout + m.stderr).strip()
        if "would be overwritten" in both:
            # git's own guard: only local edits to a file the merge touches block it.  A generated
            # file such as strata-*.json.bak changes on every run and must not stop a sync.
            print("REFUSED: local edits to a file this merge touches; commit or stash them first")
            print(both[:2000])
            return 1
        print("MERGE FAILED:")
        print(both[:2000])
        git("merge", "--abort")
        return 1

    msg = "同步上游 %s\n\n合并由 .agents/skills/gbnf-upkeep/sync_upstream.py 在独立分支上完成。\n" % a.upstream
    c = git("commit", "-m", msg)
    if c.returncode != 0:
        print("COMMIT FAILED:")
        print((c.stdout + c.stderr).strip()[:2000])
        return 1
    print()
    print("MERGED CLEAN on %s" % scratch)
    print("Next: the checks in SKILL.md's step 3, then")
    print("  git checkout %s && git merge --ff-only %s && git branch -D %s" % (origin, scratch, scratch))
    return 2


if __name__ == "__main__":
    sys.exit(main())
