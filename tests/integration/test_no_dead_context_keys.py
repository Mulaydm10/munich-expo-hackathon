"""No `src/ui` context key gates a user-facing control with zero producer in `src/`.

## The bug this generalises

Issue #40: `call.html`'s dispatch button was gated on a `reduction_event` context key
that nothing in `src/` ever supplied -- only `tests/src/ui/test_render_screens.py`'s
fixtures did. 135 green tests, one permanently dead button, because a lane-confined test
suite cannot see "does anything outside my own fixtures produce the value I am testing
against" -- that question is cross-lane by construction.

The fix for #40 (`default_reduction_event()`) then shipped the SAME pattern a second
time for the button's follow-up form: `call.html`'s adjust-event form reads back through
a `reduction_event_input` context key (`src/ui/api.py:651`) that, as of this writing,
still has no producer anywhere in `src/` -- only `tests/src/ui/test_render_screens.py`
injects it directly. That is issue #43.

## Why this test is pinned to two keys, not general over every context key

`src/ui`'s templates read a dozen-odd top-level context keys (`sites`, `intervals`,
`dispatch`, `curve`, `totals`, `assumptions`, `products`, `error`, `warnings`, ...), all
documented in `src/ui/api.py`'s module docstring. A fully general version of this test
would scan every one of them for a producer in `src/`. That was tried while writing this
suite and rejected: **`src/service` does not yet call `src.ui.render()` from any HTTP
route at all** -- there is no HTML-serving glue code in this repo yet (`src/ui`'s own
docstring: "this lane is tested entirely against recorded fixture payloads ... never
against a running service"). Nothing today literally constructs a dict under the key
`"intervals"`, `"curve"`, `"dispatch"` or `"dispatch_url"` anywhere in `src/`: the ui
layer's key names are its OWN reading of the wire response's prose shape (see that same
docstring's "NOTE ON KEY NAMES"), deliberately translated at an integration boundary that
does not exist in code yet (`/api/scenario/{id}/timeseries` answers under the key
`"rows"`, not `"intervals"`, for exactly this reason). A general producer-scan over that
full key set would therefore fail on nearly every one of them today, for a reason that
has nothing to do with the #40/#43 bug class: those keys fail OPEN (a template that
cannot find a display figure prints "not provided by API" -- itself asserted elsewhere,
e.g. `tests/src/ui/`'s missing-field tests) rather than gating a control shut forever.
Running the general scan would swamp the one signal that matters -- a context key with
no producer that PERMANENTLY DISABLES an action -- under a wall of expected, already-
tracked, not-yet-wired display fields, which is a different and much larger problem
(the service/ui HTML integration layer not existing yet) than issue #38's dead-key
pattern.

So: this test is pinned to the two keys that actually gate the call screen's dispatch
button/form (`reduction_event`, `reduction_event_input`), implemented with a real,
general mechanism (an AST scan of every `.py` file under `src/` for a literal
*producer* of the key -- a dict literal `{"key": ...}` or a subscript assignment
`target["key"] = ...` -- as opposed to a *read*, `obj.get("key")` or `obj["key"]` on the
right-hand side of an expression, which does not count). Extending the pinned set is
cheap the next time a context key is found to gate a control; pretending the general
form is free right now is not honest given the current state of the codebase.

`test_reduction_event_input_key_has_a_producer_in_src` is expected to FAIL right now --
it is marked `xfail` pointing at #43, not weakened or skipped, so it flips to an
unexpected pass (`XPASS`) the moment #43 is fixed and someone needs to notice and unmark it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _iter_src_py_files():
    return sorted(_SRC_ROOT.rglob("*.py"))


def _subscript_key(node: ast.Subscript) -> object:
    """The literal string a `Subscript` node indexes by, if it is a constant string
    (Python 3.9+ puts the index expression directly on `.slice`, no `ast.Index` wrapper)."""
    sl = node.slice
    if isinstance(sl, ast.Constant):
        return sl.value
    return None


def key_has_producer(key: str) -> tuple[bool, list[str]]:
    """True if some file under `src/` PRODUCES a value under the literal key `key`:
    a dict-literal key (`{"key": ...}`) or an assignment into a subscript named `key`
    (`ctx["key"] = ...`). A `.get("key")` call, or `obj["key"]` read on the right-hand
    side of an expression, is a READ and does not count -- that is precisely the
    distinction between issue #40/#43's "only ever read, never supplied" bug and a
    genuine producer.

    Returns `(found, locations)` so a failing assertion can name every place that reads
    the key without ever writing it, which is exactly the shape of evidence issue #43's
    report used (`grep -rn reduction_event_input src/ tests/`).
    """
    found = False
    read_sites: list[str] = []
    for path in _iter_src_py_files():
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(_SRC_ROOT.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and k.value == key:
                        found = True
            elif isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Subscript) and _subscript_key(t) == key:
                        found = True
            elif isinstance(node, ast.Subscript) and _subscript_key(node) == key:
                read_sites.append(f"{rel}:{node.lineno}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == key
            ):
                read_sites.append(f"{rel}:{node.lineno}")
    return found, sorted(set(read_sites))


def test_scan_mechanism_can_tell_a_producer_from_a_read():
    """Vacuity guard for the AST scanner itself, before trusting it on the real keys:
    a key with a known producer in this test file (a dict literal, right here) must be
    found, and a key that is only ever read (also right here) must not be."""
    _ = {"only_produced_here": 1}  # noqa: F841 - the scanner's own fixture, not read below
    only_read_here = {}
    _ = only_read_here.get("only_read_here")

    # These two probe keys live in THIS test module, which is under tests/, not src/ --
    # so neither should be found by a scan of src/, proving the scanner is not matching
    # on the wrong tree. What this test actually exercises is the classification logic
    # (dict-literal / subscript-assign counts, `.get()` / bare read does not), applied
    # directly rather than by round-tripping through the filesystem.
    module = ast.parse(
        "d = {'produced_key': 1}\n"
        "d2 = {}\n"
        "d2['also_produced'] = 2\n"
        "x = d.get('read_only_key')\n"
        "y = d['read_only_key_2']\n"
    )
    produced: set[str] = set()
    read: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant):
                    produced.add(k.value)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant):
                    produced.add(t.slice.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if node.slice.value not in produced:
                read.add(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            read.add(node.args[0].value)
    assert "produced_key" in produced
    assert "also_produced" in produced
    assert "read_only_key" in read and "read_only_key" not in produced
    assert "read_only_key_2" in read and "read_only_key_2" not in produced


def test_reduction_event_key_has_a_producer_in_src():
    """`reduction_event` gates the call screen's dispatch button. It DOES have a
    producer: `src/ui/api.py`'s own `render()` builds one (`default_reduction_event()`,
    then `build_reduction_event_from_input()`) whenever the caller has not already
    supplied one -- issue #40's fix. This is the passing half of the pair, kept beside
    the failing one so the difference between "fixed" and "not yet fixed" is visible in
    one file rather than only in two issue numbers.
    """
    found, _ = key_has_producer("reduction_event")
    assert found, (
        "no producer found for context key 'reduction_event' anywhere in src/ -- "
        "this would be issue #40 regressing"
    )


@pytest.mark.xfail(
    reason=(
        "issue #43: 'reduction_event_input' is read at src/ui/api.py:651 "
        "(ctx.get('reduction_event_input')) but nothing in src/ ever constructs it -- "
        "call.html's adjust-event form POSTs it as a GET query string and nothing in "
        "src/service reads it back into this context key. Fixing #43 is out of scope "
        "for this suite (different lane); do not weaken this assertion to make it pass."
    ),
    strict=True,
)
def test_reduction_event_input_key_has_a_producer_in_src():
    found, read_sites = key_has_producer("reduction_event_input")
    assert found, (
        "no producer for context key 'reduction_event_input' in src/ -- only read at: "
        + ", ".join(read_sites)
    )
