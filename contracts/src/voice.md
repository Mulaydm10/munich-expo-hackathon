# src/voice — contract

The depot operator's interface. Someone standing in a yard, phone in a pocket, hands full: they ask
what is happening tonight and get told, and they authorise the grid's request by speaking.

The whole national picture is on the screen; this lane is the one person inside it. It is a thin
layer over `src/service` — it holds no state and computes nothing.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/voice/api.py`

```python
TOOLS: list[ToolSpec]        # the agent's tool schema, generated from the HTTP surface it wraps

def handle_tool_call(name: str, args: dict) -> dict
    """Server-side executor for the agent's tools. Each one maps to exactly one src/service route:
      tonight_summary()            -> GET  /api/scenario/{id}
      why(metric)                  -> GET  /api/assumptions + the scorecard row behind that number
      pending_request()            -> the open ReductionEvent, if any
      approve_reduction(event_id)  -> POST /api/scenario/{id}/dispatch
      vehicle_status(vehicle_id)   -> per-session finish time from the timeseries route
    Read-only tools answer from cache; the one acting tool is `approve_reduction`."""

def brief_text(result: dict) -> str
    """Result JSON -> the two sentences a human actually needs. Spoken, so: no units read out as
    symbols, no six-digit numbers, no jargon. 'You're earning about ninety euros tonight; every van
    is full by six.'"""
```

## Rules
- **Read is free, act is confirmed.** `approve_reduction` requires an explicit spoken confirmation
  turn and is idempotent per `event_id`. No tool may charge money, cancel a bid, or change a
  schedule without that turn.
- **No invented facts, spoken.** Every number in speech comes from the API response. If a field is
  missing the answer is "I don't have that" — an agent that guesses a euro figure out loud is worse
  than one that stays quiet.
- **Secrets from the environment only.** `ELEVENLABS_API_KEY` (and any agent id) are read from env
  or a gitignored `.env`; never committed, never logged, never returned by a route.
- **Degrades to text.** If no key is configured, the lane still runs and `brief_text` still works —
  every test in `tests/src/voice/` runs with no key and no network, against recorded payloads.
- Latency budget: first audible word within 2 s of the question ending. Prefetch
  `tonight_summary` on session start rather than on first ask.

## Explicitly not this lane's job
Any calculation, any persistence, any UI. If the voice layer needs a number the API does not expose,
that is a `src/service` issue.
