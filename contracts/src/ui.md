# src/ui — contract

The thing judges actually look at. A map of Germany's charge points, a day of it played back, and
the pooling curve moving as sites join. Served by `src/service`; **no build step**.

Read `contracts/CONVENTIONS.md` first.

## Shape

```
src/ui/templates/*.html      # Jinja2, rendered by src/service
src/ui/static/*.js|css       # hand-written ES modules, no bundler
```

Third-party front-end libraries come from a CDN with an integrity hash, pinned by version. No
`node_modules`, no npm in CI, nothing to install on a demo laptop. If a library must be vendored,
commit the exact file under `static/vendor/` and note the version and source URL in a comment.

## Screens (in the order the demo uses them — see `DEMO.md`)
1. **Map** — every site from `/api/sites`, coloured by `firm_kw`. Zoom to Munich, zoom out to the
   country. Must stay interactive with tens of thousands of points (aggregate/bin at low zoom).
2. **One day** — `/api/scenario/{id}/timeseries`: baseline vs optimised load, the thermal envelope
   as a band, price underneath, the sold reduction floor shaded. Press play → SSE playback.
3. **The call** — a button that POSTs a reduction event, then shows promised vs delivered and the
   vans still finishing on time.
4. **Pooling** — `/api/scenario/{id}/pooling`: safe promise per site rising as sites are added,
   shortfall rate staying flat. One slider.
5. **Ledger** — totals, and every assumption behind them from `/api/assumptions`. Nothing on screen
   without a traceable source; `warnings[]` is rendered, not swallowed.

## Guarantees
- Loads and renders with the API reachable and *nothing else* — no analytics, no fonts, no calls to
  anything but the API and the pinned CDN. A demo laptop on conference wifi is the target.
- Degrades honestly: a failed fetch shows the error text from the API, never an empty chart that
  looks like zero. Zero and unknown must never look the same.
- Readable from three metres on a projector: axis labels, units on every number, no reliance on
  colour alone.
- Keyboard-driven happy path (space = play/pause, `d` = dispatch) so the demo does not depend on a
  trackpad in front of judges.

## Explicitly not this lane's job
Computing anything. If a number is not in the API response, the fix is a `src/service` issue —
never a calculation in JavaScript.
