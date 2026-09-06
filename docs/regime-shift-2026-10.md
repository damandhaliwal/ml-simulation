# Deliberate regime shift — October 2026

Status: design. No generator, data, or monitor changes exist yet.

## What shifts

Storm travel gets 20% worse: the storm weather multiplier rises from 1.5 to
1.8, plus its precipitation term unchanged. Everything else — arrival rates,
zones, distances, promises, the ETA and risk models — stays frozen. October
data is generated with the same code and a new seed; only this coefficient
differs.

## Why this shift

Storms are already the weakest slice (September held-out: MAE 3.789, bias
-1.261, P95 11.969). Strengthening the exact weakness maximizes the lesson
per row: a small, legible change with predictable casualties.

## Predicted effects

- Storm delivery durations rise ~15–20%; storm ETA bias goes more negative and
  storm log-loss worsens. Overall MAE/log-loss degrade in proportion to the
  ~5% storm share — visible but not catastrophic.
- Late rate rises concentrated in storms, so the risk model's storm
  calibration drifts while clear-weather calibration holds.
- Input features barely move (same storm frequency, same distances): this is
  primarily **observed-performance degradation, not input drift**. Any monitor
  that fires on inputs alone should stay quiet; monitors on outcomes and
  storm-conditioned errors must fire. That split is the point of the exercise.

## Mechanism (for the implementation step)

A `weather_multipliers` override on the generator, defaulting to the current
table. October generation passes `{"storm": 1.8}`; all earlier months keep the
defaults, so no existing dataset is regenerated and no history rewrites itself.

## Out of scope

No model changes in response to October data (that is the retraining phase's
job). No new weather types, zones, or promise rules.
