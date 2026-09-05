One file per lane: `contracts/<lane>.md` — the interface that lane exposes to others (functions, CLI, file formats, HTTP routes).
Design-owned. Any lane may read, none may write. A contract change PR comments on every open claim in the affected lanes.
