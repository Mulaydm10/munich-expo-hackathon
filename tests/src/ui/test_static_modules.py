"""Source-level assertions over `src/ui/static/*.js`.

READ THIS BEFORE TRUSTING A GREEN TICK HERE.

**These tests do not execute one line of JavaScript.** `ADR-0002` fixes the stack at
Python with no node toolchain, so nothing in CI can run these modules, and adding a
runner is a design decision rather than a lane one (issue #37 raises it; this PR does not
settle it). Everything below is therefore a *grep over source text*. It can show that a
dangerous construct is absent and that a required one is present. It cannot show that the
module parses, that it runs, that the arguments are right, or that the drawing is
correct. A refactor that preserves these strings while breaking the behaviour passes
every test in this file.

They exist because the alternative was nothing. PR #36 shipped 92 tests over this
directory, all of them Jinja render tests, and five defects went in underneath that
number — including an XSS sink and a POST body that no service would accept. Both are
detectable as text. A defect caught by a weak test beats a defect nobody notices, as long
as nobody mistakes the weak test for the strong one; hence this docstring.

The scanners themselves are unit-tested against synthetic strings further down, so a
grep that silently stopped matching cannot pass by finding nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ui import api

MODULES = sorted(api.STATIC_DIR.glob("*.js"))

# Sinks that turn a string into live markup or code. Anything reaching one of these can
# execute in the service's own origin, and every string in these modules ultimately came
# out of an HTTP response. The Jinja half of this lane is autoescaped; there is no
# equivalent protection on this side, so the sinks are banned outright rather than
# audited case by case.
HTML_SINKS = (
    "insertAdjacentHTML",
    "innerHTML",
    "outerHTML",
    "document.write",
    "eval(",
    "new Function(",
)


def source(name: str) -> str:
    return (api.STATIC_DIR / name).read_text()


def _code_only(text: str) -> str:
    """`text` with every full-line `//` comment blanked out.

    A whole-line comment is free to explain a fix using the exact broken snippet it
    replaced (as this file's own comments do, e.g. quoting `kw.toLocaleString()` or
    `ev.offsetX`); a scanner that treated that prose as code would pass or fail for the
    wrong reason. This is NOT a JavaScript parser (ADR-0002 rules that toolchain out) --
    it only drops lines whose first non-whitespace characters are `//`, so a trailing
    `// comment` after real code on the same line is deliberately left alone.
    """
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


def html_sinks_in(text: str) -> list[str]:
    """Every banned sink appearing in `text`. Comments are NOT excluded: a module in
    this directory has no reason to spell one of these names at all, and a scanner that
    tried to skip comments would be a JavaScript parser, which is the toolchain
    ADR-0002 rules out. Describe the fix in a comment, not the construct."""
    return [sink for sink in HTML_SINKS if sink in text]


def test_the_sink_scanner_detects_a_sink() -> None:
    """The scanner must be able to fire, or its silence proves nothing. This is the
    guard against the recurring defect in this project: a check that reports success
    because its failure path was never exercised."""
    assert html_sinks_in('el.innerHTML = `<p>${detail}</p>`;') == ["innerHTML"]
    assert html_sinks_in('btn.insertAdjacentHTML("afterend", x);') == ["insertAdjacentHTML"]
    assert html_sinks_in('p.textContent = `Dispatch failed: ${detail}`;') == []


def test_there_are_modules_to_scan() -> None:
    """If the glob ever returns nothing, every scan below would pass vacuously."""
    assert len(MODULES) >= 5
    assert {p.name for p in MODULES} >= {
        "payload.js", "screen-call.js", "screen-day.js", "screen-map.js",
    }


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_module_interpolates_response_data_into_an_html_sink(module: Path) -> None:
    """#37 item 1, the security finding. `screen-call.js` built its failure message with
    `insertAdjacentHTML` and a template literal holding `error`, `detail`, `how_to_fix`
    and a stringified exception — all API-controlled — so a crafted error body became
    active markup in the service origin. The replacement builds the element and assigns
    every API string through `textContent`, which can only become text.

    SOURCE ASSERTION, NOT EXECUTION: this proves the sink is absent from the file, not
    that the DOM code that replaced it behaves correctly in a browser.
    """
    assert html_sinks_in(module.read_text()) == []


def test_the_failure_path_builds_an_element_and_assigns_text() -> None:
    """The positive half of the same fix: absence of a sink would also be satisfied by
    deleting the error message entirely, which would be silent failure — the thing this
    lane exists to prevent."""
    js = source("screen-call.js")
    assert "createElement" in js
    assert "textContent" in js
    assert "insertAdjacentElement" in js
    # Both paths report: the HTTP failure and the unreachable-API exception.
    assert "showFailure(" in js
    assert '"dispatch-failed"' in js
    assert '"dispatch-unreachable"' in js
    assert "${err}" not in js, "the caught exception went into the same sink as `detail`"


def test_the_dispatch_body_is_a_reduction_event_and_not_an_empty_object() -> None:
    """#37 item 5. The button POSTed the literal body "{}" to a route whose frozen
    contract requires a `ReductionEvent`, so a validating service rejects it and the
    call screen cannot produce promised-vs-delivered at all.

    SOURCE ASSERTION: this proves the module serialises the payload the template
    renders. Nothing here proves the POST succeeds against a real service — `src/service`
    is unmerged (#28), and no test in this lane has ever reached one.
    """
    js = source("screen-call.js")
    assert 'body: "{}"' not in js
    assert "body: JSON.stringify(event)" in js
    assert 'readPayload("reduction-event")' in js
    # And it refuses rather than inventing one when the server sent no event.
    assert '"dispatch-no-event"' in js


def test_the_map_does_not_coerce_an_unknown_capacity_to_zero() -> None:
    """#37 item 3. `typeof s.firm_kw === "number" ? s.firm_kw : 0` put a site whose
    capacity the API never sent into the smallest-capacity bucket: a filled low dot,
    indistinguishable from a site that can genuinely promise nothing.

    SOURCE ASSERTION for the drawing; the *legend* half of this fix is executed by
    `test_the_map_legend_names_unknown_capacity_as_its_own_mark`, and the two are pinned
    to each other by the label check below.
    """
    js = source("screen-map.js")
    assert not re.search(r'firm_kw\s*:\s*0\b', js)
    assert "? s.firm_kw : 0" not in js
    assert "UNKNOWN_MARK" in js and "drawUnknown" in js
    # The unknown branch must not fall through into a bucket.
    assert "if (kw === null) { drawUnknown(x, y); continue; }" in js


def test_the_marks_the_module_draws_are_the_marks_the_legend_names() -> None:
    """The legend is rendered server-side from `api.MAP_UNKNOWN_LABEL`; the canvas text
    is a separate literal in the module. Pinning them together is the only thing keeping
    the executed half and the un-executed half of item 3 describing one page."""
    assert f'"{api.MAP_UNKNOWN_LABEL}"' in source("screen-map.js")


def test_the_map_hit_tests_for_the_hover_figure_the_legend_promises() -> None:
    """#37 item 9. The legend claimed each site's exact `firm_kw` on hover and there was
    no hit-testing at all — a page promising a figure it could not show.

    SOURCE ASSERTION, and a weak one: it shows a pointermove handler exists that resolves
    a site and writes the readout. Whether the hit radius is usable with a trackpad in
    front of judges is not testable here and has NOT been verified in a browser.
    """
    js = source("screen-map.js")
    # Definition AND call site. Asserting only the call let a revert that renamed the
    # function away still pass, which is the same weakness this whole file has to guard
    # against: a check that cannot fail is not evidence.
    assert "function siteAt(px, py) {" in js
    assert "const hit = siteAt(" in js
    assert 'addEventListener("pointermove"' in js
    assert 'addEventListener("pointerleave"' in js
    assert "renderReadout" in js
    # The hovered site's own value, and the unknown case saying so rather than "0 kW".
    assert "firm capacity" in js
    assert "UNKNOWN_TEXT" in js


def test_every_series_renderer_breaks_at_a_gap_through_one_shared_helper() -> None:
    """#37 item 6. `band()` skipped missing rows and joined the survivors into one
    polygon, shading envelope and sold-floor capacity straight across intervals the API
    never sent — while `line()` on the same chart broke correctly, so the two disagreed.
    Both now consume `runs()`, which is the gap logic written once.

    SOURCE ASSERTION: it shows the renderers call the shared helper. It does not draw a
    pixel, and no test here confirms the resulting polygon looks right.
    """
    assert "export function runs(rows, key)" in source("payload.js")
    for name in ("screen-day.js", "screen-call.js"):
        js = source(name)
        assert re.search(r'import \{[^}]*\bruns\b[^}]*\} from "\./payload\.js"', js), name
        assert "runs(rows, key)" in js or "runs(rows, " in js, name

    day = source("screen-day.js")
    band = re.search(r"function band\(key, colour\) \{(.*?)\n  \}", day, re.S)
    assert band is not None
    assert "for (const run of runs(rows, key))" in band.group(1)
    # The single-polygon-over-all-rows shape must be gone from the band.
    assert "ctx.moveTo(x(0), yKw(0))" not in band.group(1)


# --- #40 item 3: the hover readout must not round the value it promises is exact ----

def test_the_hover_readout_does_not_round_the_exact_firm_kw_it_promises() -> None:
    """#40 item 3. `toLocaleString()` with no arguments caps at 3 fraction digits, so
    `12.34567` rendered as `12.346` while the map legend promises the site's EXACT
    `firm_kw`. `String(kw)` is JS's own exact decimal rendering of the stored double --
    no rounding function stands between the wire value and the screen.

    SOURCE ASSERTION, NOT EXECUTION: ADR-0002 rules out a JS runtime in this repo's CI
    (`node --check` exits 0 on a deliberately broken file and proves nothing), so
    nothing here proves a browser actually prints the unrounded string -- only that the
    rounding call is gone and the exact-rendering call is present at the call site that
    used to round.
    """
    js = source("screen-map.js")
    code_lines = _code_only(js)
    assert "toLocaleString()" not in code_lines, "an executable toLocaleString() call remains"
    assert re.search(r"String\(kw\)\s*\}\s*kW firm capacity", js), (
        "renderReadout must interpolate the exact value, not a locale-rounded one"
    )


# --- #40 item 4: hover and drag must convert through one coordinate helper ----------

def test_the_map_converts_pointer_coordinates_through_one_shared_helper() -> None:
    """#40 item 4. `siteAt` compared CSS-pixel `ev.offsetX/offsetY` against sites
    projected into the canvas's 1200x800 intrinsic backing store; the shared responsive
    CSS renders the canvas at `width: 100%`, so the two spaces differ on essentially
    every real screen and hit-testing was displaced. The pan handler divided a raw
    `offsetX` delta by `canvas.width`, mis-scaled by the same factor. Both paths must
    convert through `getBoundingClientRect()` via ONE shared helper, or they can drift
    apart exactly the way this defect did.

    SOURCE ASSERTION, NOT EXECUTION: this shows both call sites route through
    `toCanvasPoint`, not that the resulting pixel math is correct in a real browser.
    """
    payload_src = source("payload.js")
    assert "export function toCanvasPoint(canvas, offsetX, offsetY)" in payload_src
    assert "getBoundingClientRect" in payload_src
    assert "canvas.width / rect.width" in payload_src
    assert "canvas.height / rect.height" in payload_src

    js = source("screen-map.js")
    assert re.search(r'import \{[^}]*\btoCanvasPoint\b[^}]*\} from "\./payload\.js"', js)
    # Both the hover path (siteAt) and the drag path (the pointermove delta) must
    # consume the SAME conversion call -- not two independent ones that can disagree.
    conversions = re.findall(r"toCanvasPoint\(canvas, ev\.offsetX, ev\.offsetY\)", js)
    assert len(conversions) >= 2, "pointerdown and pointermove must both convert"
    assert "siteAt(p.x, p.y)" in js
    # No raw offsetX/offsetY may bypass the helper in executable code: removing every
    # converted call site should remove every mention of ev.offsetX/ev.offsetY from the
    # code (comments describing the fix are exempt).
    code_lines = _code_only(js)
    stripped = re.sub(r"toCanvasPoint\(canvas, ev\.offsetX, ev\.offsetY\)", "", code_lines)
    assert "ev.offsetX" not in stripped and "ev.offsetY" not in stripped, (
        "a raw ev.offsetX/offsetY outside the shared conversion means an unconverted path"
    )


def test_no_module_grew_a_node_dependency() -> None:
    """`contracts/src/ui.md`: hand-written ES modules, no bundler, nothing to install on
    a demo laptop. The tempting fix for this whole file is a JS test runner; that is a
    design decision (`docs/setup.sh` and `requirements-dev.txt` are canary-gated) and
    explicitly out of scope for #37."""
    for module in MODULES:
        js = module.read_text()
        assert "require(" not in js, module.name
        assert not re.search(r'from\s+["\'][^./]', js), module.name  # bare specifier
