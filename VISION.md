> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# VISION.md — the thesis

Filled in 2026-09-01, resolving `Q-0001`. Challenge: *Mobility & Automotive: EV Charging Load
Predictor* (see `COMPETITION.md` — that file, not this one, owns event facts).

## What we're building
**FlexGrid** — a system that treats parked, plugged-in electric vehicles as dispatchable grid
capacity, and can say honestly how much of that capacity is safe to sell.

Three parts, in order of how much of the value each carries:

1. **An honest forecast.** Charging demand per site is predicted as a *distribution*, not a number.
   The number that matters is the pessimistic end: the load that will be there even on a bad night.
   That is the only figure a promise to a grid operator can be based on.
2. **A schedule that keeps two promises at once.** Every vehicle is full by its departure deadline,
   and enough charging is deliberately still running during the hours we sold, so the promised
   reduction can actually be delivered. Inside the site's real physical envelope — transformer
   hot-spot temperature and per-phase limits, not the number on the nameplate.
3. **Pooling, at national scale.** One site must promise timidly because its own bad night is
   likely. Hundreds of sites do not all have a bad night simultaneously, so the pool's worst case is
   far better than the sum of individual worst cases. The safe promise *per site* rises as the
   portfolio grows. We simulate this on Germany's real, public registry of charge points — every
   site, real coordinates, driven by real historical prices, weather and grid load.

The question the system answers, which the challenge brief does not ask but Europe does: *how many
gas peaker plants does Germany not need to build, if the cars already plugged in were coordinated?*

## For whom
- **Primary user (the one in the demo):** the operator of a commercial depot — a delivery or
  logistics firm with 20–50 electric vans on its own yard, returning in the evening and leaving at
  dawn. They pay a large electricity bill, have no idea their charging is a sellable asset, and have
  hands full of parcels — hence the voice layer, not a fourth dashboard to check.
- **Buyer:** whoever aggregates those depots (a virtual-power-plant operator, an energy retailer, a
  fleet-charging provider). The per-site earnings are small; the portfolio's are not.
- **Beneficiary:** the transmission system operator, and everyone downstream of grid stability —
  flexibility that already physically exists gets used instead of built.

## Why it isn't already solved
- **Smart charging today optimises for price only.** Commercial charge-management software shifts
  load to cheap hours. It does not produce a *guaranteeable* reduction, because it forecasts a point
  estimate: you cannot size a contractual promise from an average without eventually breaking it,
  and balancing-market penalties are deliberately harsh enough that one failure erases a month of
  earnings.
- **Aggregators exist, but not for this asset class.** Virtual power plants (Next Kraftwerke and
  peers) aggregate generation, storage and industrial load. Depot charging is a large, growing,
  genuinely flexible load that is largely unaggregated — its flexibility is real and idle.
- **Capacity is treated as a fixed nameplate number.** Real limits are thermal and per-phase: a
  cold transformer tolerates well above nameplate briefly, a warm one less, and one overloaded phase
  trips a site whose total looks fine. Ignoring this both loses free headroom and causes outages
  that dashboards do not predict.
- **The pooling benefit is misunderstood as a middleman's margin.** It is not: it is reliability
  manufactured out of statistical independence, the same mathematics as insurance. Nobody quantifies
  it for EV charging at national scale — and it is quantifiable from public data, which is exactly
  what makes it a demo rather than a claim.

Where this project is *not* original: shifting charging to cheap hours, and the phrase "virtual
power plant". Both will appear in other submissions. The distinguishing claim is narrower and
checkable: bidding sized off a calibrated lower quantile, a thermally-derived envelope, and a
measured diversification curve — built, not asserted.

## What "working" looks like at demo time
A judge watches, in under four minutes:

1. A map of Germany with **every real charge point from the public registry** on it. Zoom to one
   Munich depot.
2. One real historical night plays back: uncontrolled charging versus ours, the day-ahead price
   underneath, the thermal envelope as a band above, the sold reduction floor shaded.
3. The grid operator's call arrives mid-window. Chargers pause; promised versus delivered is shown;
   every van still reaches full before 06:00. The uncontrolled baseline, side by side, either breaks
   its promise or overloads the transformer.
4. The pooling slider: sites are added to the portfolio and the safe promise **per site** climbs
   while the shortfall rate stays flat. Then the national total — megawatts of flexibility, euros,
   tonnes of CO₂, gas plants displaced — with each figure traceable to a public source.
5. The depot operator asks, out loud, what is happening tonight, and is told in two sentences.

Every number on screen traces to a public dataset or to a named assumption the UI can display. The
whole run is reproducible from a date and a seed.

## Explicit non-goals
- **Not a real market participant.** No live bidding, no prequalification, no TSO integration; the
  auction and settlement are simulated against published prices and rules.
- **Not V2G.** Discharging vehicles back into the grid is out of scope: it is a different physical
  and contractual problem. We only modulate charging.
- **Not a charge-point control integration.** No OCPP against real hardware; the dispatch acts on
  the simulator.
- **Not a forecasting-accuracy leaderboard.** Calibration and the euros it enables are the metric;
  chasing the lowest point error is explicitly not the goal.
- **Not federated learning in v1.** Training without centralising operator data is the right answer
  for the real product and is written up as future work, not built before the deadline.
- **No pretty-but-empty UI.** Anything on screen that is not backed by a computed, sourced number
  gets removed instead of faked.
