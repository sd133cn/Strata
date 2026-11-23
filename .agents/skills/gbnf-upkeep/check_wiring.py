#!/usr/bin/env python3
"""Check that this branch's constrained-decoding wiring survived a merge from upstream.

Every merge re-applies upstream over this branch's edits. Where both sides added code the
result is usually right, but where upstream REFACTORED a region this branch had patched, the
merge can drop the patch silently: no conflict marker, tests that do not cover it still pass.
That happened once to the metal path in src/core/verify.cpp (#3862), which is why the check
for it is region-based and not a plain count.

Counts match WHOLE identifiers, not substrings: a rename that leaves the old name inside a
longer one (`strata_grammar` -> `strata_grammarX`) still counts zero, which a substring count
would miss.

Exit 0 when every hook is in place, 1 naming what is missing.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# (path, symbol, at least this many whole occurrences, what it does)
REQUIRED = [
    ("src/program/generate.cpp", "GrammarRing", 1, "the per-row mask ring the sampler reads"),
    ("src/program/generate.cpp", "grammar_ring", 4, "its upload and per-slot rows"),
    ("src/program/generate.cpp", "req_grammar", 5, "a request naming a registered grammar"),
    ("src/program/generate.cpp", "set_row_grammar_masks", 1, "the draft window's chained masks"),
    ("src/program/generate.cpp", "gseed", 2, "the continuation pass replayed through the grammar"),
    ("src/program/generate.cpp", "grammar_registry", 1, "the registry that parses and caches grammars"),
    ("include/strata/core/verify.hpp", "row_gmask_", 1, "the window's per-row mask pointers"),
    ("include/strata/core/verify.hpp", "set_row_grammar_masks", 2, "its setter and the chained-stage relay"),
    ("src/core/verify.cpp", "row_gmask_", 1, "sample_rows applying the row mask"),
    ("serve/server.py", "ensure_grammar", 2, "registering a grammar with the engine"),
    ("serve/server.py", "grammar_q", 2, "routing the engine's GRAMMAR replies"),
    ("serve/server.py", "gseed_keys", 2, "the gseed= wire key"),
    ("serve/server.py", "grammar_api", 2, "the grammar wire format and canary"),
    ("CMakeLists.txt", "strata_grammar", 2, "the host-only grammar library target"),
    ("include/strata/kernels/sampler.hpp", "grammar_mask", 1, "the mask the sampler kernel applies"),
    ("src/kernels/cuda/sampler.cu", "grammar_mask", 2, "the CUDA sampler applying it"),
]

# Files that must exist at all: the feature's own additions, which upstream does not carry.
REQUIRED_FILES = [
    "src/grammar/grammar.cpp",
    "src/grammar/grammar_mask.cpp",
    "src/grammar/grammar_registry.cpp",
    "src/grammar/grammar_vocab.cpp",
    "include/strata/grammar/grammar.hpp",
    "include/strata/grammar/grammar_mask.hpp",
    "include/strata/grammar/grammar_registry.hpp",
    "serve/grammar.py",
    "serve/grammar_tools.py",
    "serve/grammar_schema.py",
    "tools/grammar_strict_test.cpp",
]

# Regions where a dropped patch leaves no conflict marker.  Each entry is
# (path, start marker, end marker, symbol, what it protects).
REGIONS = [
    ("src/core/verify.cpp", "window_run_slots(metal_", "#endif", "gmask",
     "the metal window's row sampling: its own loop, outside the CUDA conflict hunk"),
]


def count_word(text: str, symbol: str) -> int:
    return len(re.findall(r"\b" + re.escape(symbol) + r"\b", text))


def main() -> int:
    problems = []

    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            problems.append(f"missing file: {rel}   (this branch's own addition, upstream does not carry it)")

    for rel, symbol, least, what in REQUIRED:
        p = ROOT / rel
        if not p.is_file():
            problems.append(f"missing file: {rel}")
            continue
        n = count_word(p.read_text(encoding="utf-8", errors="replace"), symbol)
        if n < least:
            problems.append(f"{rel}: '{symbol}' appears {n} time(s), expected at least {least}   -- {what}")

    for rel, start, end, symbol, what in REGIONS:
        p = ROOT / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        i = text.find(start)
        if i < 0:
            problems.append(f"{rel}: region start '{start}' not found   -- {what}")
            continue
        j = text.find(end, i)
        region = text[i:j if j >= 0 else len(text)]
        if count_word(region, symbol) == 0:
            problems.append(f"{rel}: region '{start}' .. '{end}' has no '{symbol}'   -- {what}")

    if problems:
        print("WIRING RED: a merge dropped part of the constrained-decoding wiring")
        print()
        for line in problems:
            print("  " + line)
        print()
        print("Restore each one from the branch's own history, e.g.")
        print("  git log -p --all -S '<symbol>' -- <file>")
        print("then rebuild and run this check again.")
        return 1

    total = len(REQUIRED) + len(REQUIRED_FILES) + len(REGIONS)
    print(f"WIRING GREEN: all {total} hooks in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())
