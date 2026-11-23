---
name: gbnf-upkeep
description: Use when this branch must take upstream commits without losing constrained decoding, or when a GBNF/tool-schema reply looks wrong (garbled characters, stuck output, a tool call that will not close). Merges upstream onto a scratch branch, proves the grammar wiring survived, and asks a tool-carrying request — a shape that actually runs the mask.
---

# Keeping constrained decoding alive across upstream merges

This branch adds GBNF-constrained decoding to a codebase that does not have it. Every merge
from upstream re-applies their work over this branch's edits, and the danger is not the
conflict markers — git shows those. The danger is a **silent drop**: upstream refactored a
region this branch had patched, the merge keeps their version, and nothing turns red. It has
happened once here (the metal window loop in `src/core/verify.cpp`), and the code compiled
and every test passed.

Two facts decide whether any check below is meaningful:

- **A bare chat request proves nothing.** It never consults the mask, so it answers "fine" no
  matter what the mask does — which is how a byte-fallback defect here survived 45 clean runs.
- **A merge is green when you have *counted* the wiring, not when `git status` is clean.**

## Step 1 — merge on a scratch branch

```
python .agents/skills/gbnf-upkeep/sync_upstream.py --upstream v0.2.13
```

Exit **0**: nothing to merge, done. Exit **2**: merged clean, go to step 2. Exit **3**: it
printed each conflicting file with its block count — resolve them, then go to step 2.

The merge lands on `sync/upstream-<tag>`; the working branch is untouched until the checks
pass, so a bad merge costs one `git branch -D`.

Resolving, by shape of the conflict:

- **Both sides added code at the same place.** Keep both. This is the common case and it is
  not a judgement call.
- **Upstream refactored what this branch patched inline.** Take upstream's structure, then
  re-insert this branch's behaviour inside it. Example: upstream moved per-row sampling into
  `slot_row_draw()`, whose `return 0` means "plain greedy, keep the window's argmax"; this
  branch had set `sp.grammar_mask` inline. The result fills in the pieces upstream's early
  return skips, and does not skip when a mask will be applied — a mask rejects the argmax
  token itself.
- **Two continuation keys on one wire line.** Both ride it, in a fixed order
  (`{gseed}{pen_from}`); they answer different questions.

Merging a thousand commits at once stacks conflicts past the point of telling which change
caused which, so merge one release tag at a time.

## Step 2 — count the wiring

```
python .agents/skills/gbnf-upkeep/check_wiring.py
```

**WIRING GREEN** ends the step. Red names the file, the symbol and what it was for. Restore
the missing piece from this branch's own history (`git log -p --all -S '<symbol>' -- <file>`)
— never by reverting the file to the pre-merge version, which throws away upstream's work in
it.

The check is region-based where a refactor can drop a patch without a marker, and count-based
everywhere else. When upstream restructures a region this branch touches, the new region
belongs in `check_wiring.py`'s `REGIONS` list before the next merge.

## Step 3 — build and run the C++ suites

Build from a shell that has the Visual Studio environment; without it `cl.exe` cannot find
`<cstdint>` and the error looks like a broken include path. On this machine:

```bat
call "D:\VisualStudio\VC\Auxiliary\Build\vcvars64.bat"
set TMP=F:\Strata-dev\.tmp-build
set TEMP=F:\Strata-dev\.tmp-build
cd /d F:\Strata-dev\build
"D:\VisualStudio\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" ^
    --build . --target strata grammar_test grammar_registry_test grammar_strict_test ^
    grammar_decode_test -j 2
```

`-j 2` and `TMP`/`TEMP` on a roomy drive: nvcc needs several GB per job and its scratch files
land on `C:` by default, where a full disk reads as `catastrophic error: out of memory` — a
build failure that looks like code and is not. A build interrupted mid-flight can leave a
locked `.obj`; delete that one file and rerun rather than rebuilding everything.

Then run all four. Done when each prints its passed line:

| binary | passed line |
|---|---|
| `grammar_test` | `All grammar tests passed.` |
| `grammar_registry_test` | `All grammar registry tests passed.` |
| `grammar_strict_test` | `All strict grammar tests passed.` |
| `grammar_decode_test` | `grammar decode: … all legal` + `grammar chain: …` |

The wider net is `./.venv/Scripts/python.exe -m pytest serve/ -q` (~12 min, ~1500 tests). Worth
it on any merge that touched `serve/server.py`.

## Step 4 — ask with tools declared

**Two build trees exist and only one is served.** `strata-swift-iq3_s.json`'s `exe` names
`build-release\strata.exe`; `build\strata.exe` is a second tree that the C++ tests come from.
Building one and asking the other measures the old code — check the running process's own
command line and the file's timestamp before believing any result.

Windows will not let a link overwrite a running `.exe`, so stop the server and its engine (the
`python.exe` running `serve/server.py`, and `strata.exe`, and `strata-vision.exe`) before
building `build-release`, then start it again with `run-swift-iq3_s.bat` and wait for
`/api/health` to say `"loaded": true`.

Then:

```
python .agents/skills/gbnf-upkeep/tool_ask.py --runs 3
```

It first asks a **liveness probe**, then the pelican question twice per round — once with
`tools`, once with the same request plus `strata_grammar: false` — and prints a U+FFFD count
for each arm.

The liveness probe is the part that makes the rest mean anything. A mask that never ran and a
mask that ran cleanly both count zero U+FFFD. The probe puts `maxLength: 6` on a tool argument
whose prompt demands sixty characters: under the grammar the argument is cut at six, on the
prompt path the model writes the long sentence. **LIVENESS GREEN** means the counts below
measured the mask; anything else means they measured the prompt path and the run is
inconclusive, not green.

**GREEN** ends the step. **RED with a difference between the arms** names the mask: the
constrained arm corrupts and the unconstrained one does not. **RED in both** is the model or
the client, not this feature.

The fault is intermittent, so one clean round proves little; that is what `--runs` is for. A
byte-fallback vocabulary splits a character across tokens (this model writes some CJK
characters and every emoji that way), and that is where the mask's partial-character state
matters — the default prompt contains one on purpose.

Evidence worth writing down: the two counts, the liveness note and its length, the model, the
engine binary's path and its timestamp.

## Step 5 — land it

```
git checkout feature/gbnf-constrained-decoding
git merge --ff-only sync/upstream-<tag>
git branch -D sync/upstream-<tag>
```

Done when the feature branch is at the scratch branch's commit and
`git rev-list --count HEAD..origin/main` is 0.

Commit message: what upstream range came in, each conflict and how it was resolved, the
checks that passed with their counts, and — separately — what was **not** run. HIP and SYCL
are not built in this checkout; a Metal branch edit is not compiled at all. Say so rather than
letting a green CUDA build imply coverage it does not have.

## Reference

**Where the wiring lives.** When a check is red, or a merge needs a symbol's purpose:

| file | what this branch added there |
|---|---|
| `src/grammar/` | the ported grammar: parser, stacks, reject fold, mask builder, registry |
| `include/strata/grammar/` | its headers; `MaskVocab` pre-decodes the vocabulary, `GrammarMaskCache` keys rows by `(stacks, partial_utf8)` |
| `src/program/generate.cpp` | `GrammarRing`, `req_grammar`, `gseed`, the batch rows' chained masks |
| `include/strata/core/verify.hpp`, `src/core/verify.cpp` | `row_gmask_` / `gmask`: the draft window's per-row masks, one loop per backend |
| `src/kernels/cuda/sampler.cu`, `sycl/…/sampler.dp.cpp` | the kernel that applies the mask |
| `serve/grammar.py`, `grammar_tools.py`, `grammar_schema.py` | the wire format, the tool-call grammar, the JSON-schema converter |
| `serve/server.py` | `ensure_grammar`, `grammar_q`, `gseed_keys`, the three request routes |

**Two invariants the mask holds, and the tests that hold them.**

`served semantics match llama.cpp's apply_impl` — a token survives when at least one stack
accepts its whole piece, EOG only at a terminal state, control tokens always banned.

`the grammar's partial-character state is part of the mask's state` — a byte-fallback piece
that starts with a UTF-8 continuation byte may ride only when the grammar is mid-character,
and the cache key carries `partial_utf8`. Dropping either half lets the model open a character
it can never finish; the reply then shows U+FFFD where the character should be.
`grammar_test`'s `test_mask_partial_utf8` pins both halves, `grammar_decode_test` the chain.

**Diagnosis, when a reply is wrong under `tools` but fine without.** Compare the two arms
first (step 4), then walk down: is the grammar live at all (the `maxLength` probe, or a bare
`grammar` of `root ::= "ZZZ-CONSTRAINED-OK"` — the reply must be exactly that text); does the
mask allow the token the model needs (`STRATA_GRAMMAR_DEBUG=1` traces every accepted token);
is the corruption in bytes or in display (a token stream that decodes cleanly means the client
is splitting a character). Only after those, suspect the mask's state.

A tool argument that ignores its own schema is the shape to recognise: `maxLength` not cutting
the value, an `enum` not restricting it, a tool call that will not close. Those are the mask
not applying, and they lead back to step 2's wiring list rather than to the model.
