#!/usr/bin/env python3
"""Ask the running server a question WITH TOOLS DECLARED and report any U+FFFD.

Why tools: a plain chat request never consults the mask.  The tool-call grammar is the path
that constrains sampling, so it is the only shape that exercises this feature - a bare chat
request answers "everything is fine" no matter what the mask does.  That is exactly how the
byte-fallback defect stayed hidden through 45 clean runs.

Why the liveness probe comes first: a mask that never ran and a mask that ran cleanly both
report zero U+FFFD.  A `maxLength: 6` on a tool argument tells them apart - under the grammar
the argument is cut at six characters even though the prompt asks for sixty, and on the prompt
path the model writes the long sentence.  Without that probe a green result can mean "the
feature was not under test".

Two arms, and the difference between them is the evidence:
  with tools      -> the grammar is registered and every token is masked
  strata_grammar:false -> the same request on the prompt path, no mask at all

A U+FFFD in the first arm and none in the second names the grammar; a U+FFFD in both is the
model or the client, not this feature.

The needle: byte-fallback vocabulary pieces.  A character stored as several tokens (this
model writes one common CJK character as a two-byte lead plus a finishing byte, and an emoji
as three tokens) is where a mask that ignores the partial-character state bans the only token
that could finish it.  The prompt asks for those characters on purpose.

Exit 0 when the mask is live and no arm produced U+FFFD; 1 otherwise.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_PROMPT = "创建一个HTML，内容是SVG绘制一个鹈鹕骑自行车的2D动画"

TOOLS = [
    {"type": "function", "function": {
        "name": "Write",
        "description": "Write a file to disk.",
        "parameters": {"type": "object", "properties": {
            "file_path": {"type": "string", "description": "Absolute path."},
            "content": {"type": "string", "description": "The full file content."}},
            "required": ["file_path", "content"]}}},
    {"type": "function", "function": {
        "name": "Bash",
        "description": "Run a shell command.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "description": {"type": "string"}},
            "required": ["command"]}}},
]

# The liveness probe.  A mask that never ran and a mask that ran cleanly both give zero U+FFFD,
# so the count alone cannot tell them apart.  A maxLength no model would honour by choice can:
# under the grammar the argument is cut at exactly that many characters, on the prompt path the
# model writes the long answer it was asked for.
LIVE_TOOLS = [{"type": "function", "function": {
    "name": "emit",
    "description": "Emit a long descriptive sentence as the note.",
    "parameters": {"type": "object", "properties": {"note": {"type": "string", "maxLength": 6}},
                   "required": ["note"]}}}]
LIVE_PROMPT = ("Call emit. The note must be a long, detailed sentence about pelicans, "
               "at least 60 characters.")
LIVE_MAX = 6


def post(base, key, body):
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def probe_live(base, key, model):
    """True when the tool grammar cut the argument at LIVE_MAX: the mask is really being applied."""
    body = {"model": model, "messages": [{"role": "user", "content": LIVE_PROMPT}],
            "tools": LIVE_TOOLS, "tool_choice": {"type": "function", "function": {"name": "emit"}},
            # room for the thinking this model writes before a call: a reply cut at "length" before
            # the call says nothing about the grammar, it only says the budget was too small
            "max_tokens": 2000, "temperature": 0.7, "enable_thinking": False}
    try:
        d = post(base, key, body)
    except Exception as e:
        print("LIVENESS PROBE FAILED: %s" % e)
        return False
    ch = d["choices"][0]
    calls = ch["message"].get("tool_calls") or []
    if not calls:
        # say WHY: a reply cut by max_tokens before the call is not the same finding as a model
        # that chose prose, and "no call" alone would send the reader to the wrong place
        said = (ch["message"].get("content") or "") or (ch["message"].get("reasoning_content") or "")
        print("LIVENESS PROBE RED: no tool call came back (finish_reason=%r, said %d chars: %r)"
              % (ch.get("finish_reason"), len(said), said[-160:]))
        return False
    try:
        note = json.loads(calls[0]["function"]["arguments"])["note"]
    except Exception:
        print("LIVENESS PROBE RED: arguments did not parse: %r" % calls[0]["function"]["arguments"][:120])
        return False
    print("LIVENESS: note=%r (%d chars; the schema allows %d)" % (note, len(note), LIVE_MAX))
    if len(note) <= LIVE_MAX:
        print("LIVENESS GREEN: the tool grammar is applying (a model asking for 60 characters wrote %d)" % len(note))
        return True
    print("LIVENESS RED: the model wrote %d characters under maxLength %d -- the tool grammar is NOT live,"
          % (len(note), LIVE_MAX))
    print("  so every U+FFFD count below measures the prompt path, not the mask.")
    return False



def ask(base, key, model, prompt, max_tokens, temperature, extra):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": True, "max_tokens": max_tokens, "temperature": temperature, **extra}
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": "Bearer " + key})
    reason, content, finish = [], [], None
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                d = json.loads(payload)
                if d.get("error"):
                    return None, None, "error: " + str(d["error"])[:200]
                ch = (d.get("choices") or [{}])[0]
                delta = ch.get("delta") or {}
                if delta.get("reasoning_content"):
                    reason.append(delta["reasoning_content"])
                if delta.get("content"):
                    content.append(delta["content"])
                finish = ch.get("finish_reason") or finish
    except urllib.error.HTTPError as e:
        return None, None, "HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        return None, None, "cannot reach the server: %s" % e
    return "".join(reason), "".join(content), finish


def report(arm, reason, content, finish):
    if reason is None:
        print("  %-22s %s" % (arm, content))
        return None
    n = reason.count("\ufffd") + content.count("\ufffd")
    print("  %-22s reason=%5d content=%5d finish=%-6s U+FFFD=%d"
          % (arm, len(reason), len(content), finish, n))
    for name, text in (("reasoning", reason), ("content", content)):
        i = text.find("\ufffd")
        shown = 0
        while i >= 0 and shown < 4:
            print("     !!! %s @%d: %r" % (name, i, text[max(0, i - 40):i + 40]))
            i = text.find("\ufffd", i + 1)
            shown += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--key", default="sk-ouzhiyingyyds666!")
    ap.add_argument("--model", default="swift-1.5-iq3_s")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--runs", type=int, default=3, help="rounds of both arms (the fault is intermittent)")
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--skip-liveness", action="store_true",
                    help="do not probe that the mask is live (only when the probe itself is under test)")
    a = ap.parse_args()

    live = True
    if not a.skip_liveness:
        print("liveness probe:")
        live = probe_live(a.base, a.key, a.model)
        print()

    grammar_hits = prompt_hits = 0
    for k in range(a.runs):
        print("run %d/%d" % (k + 1, a.runs))
        r, c, f = ask(a.base, a.key, a.model, a.prompt, a.max_tokens, a.temperature,
                      {"reasoning_effort": "high", "top_p": 0.95, "top_k": 20, "tools": TOOLS})
        n = report("with tools", r, c, f)
        if n is None:
            print("STOP: the request did not run; fix the server first")
            return 1
        grammar_hits += n
        r, c, f = ask(a.base, a.key, a.model, a.prompt, a.max_tokens, a.temperature,
                      {"reasoning_effort": "high", "top_p": 0.95, "top_k": 20, "tools": TOOLS,
                       "strata_grammar": False})
        n = report("strata_grammar:false", r, c, f)
        if n is None:
            print("STOP: the second arm did not run")
            return 1
        prompt_hits += n

    print()
    print("with tools: %d U+FFFD   |   strata_grammar:false: %d U+FFFD" % (grammar_hits, prompt_hits))
    if grammar_hits:
        print("RED: the constrained path corrupts; the mask is the suspect (compare the second arm)")
        return 1
    if prompt_hits:
        print("RED: both arms corrupt, or the unconstrained one alone -- this is the model or the client, not the mask")
        return 1
    if not live:
        print("INCONCLUSIVE: zero U+FFFD, but the mask was never shown to be live -- the counts above")
        print("describe the prompt path.  Fix the liveness probe before reading this as green.")
        return 1
    print("GREEN: no U+FFFD in %d rounds of either arm, and the mask is live" % a.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
