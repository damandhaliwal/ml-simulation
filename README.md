# Marketplace ETA Intelligence System

A learning-first project for predicting food-delivery duration and following
predictions through actual outcomes and the model's operational lifecycle.

**Status:** Product definition only. No simulator, trained model, API, or
deployment exists yet. All marketplace data will be simulated; results will
describe that simulation, not real delivery performance.

## Product spec

### Who this serves and why

A customer needs a useful delivery estimate when an order is confirmed.
Underestimation creates disappointment; systematic overestimation may discourage
orders. A marketplace operator needs to know when estimates become unreliable.

### Prediction contract

- **When:** Once, at order confirmation, before preparation and delivery finish.
- **Target:** `delivery_duration_minutes` = minutes from confirmation to delivery.
- **Initial output:** A predicted duration in minutes, not an arrival timestamp.
- **Later output:** Late-delivery probability, model version, prediction ID,
  and measured prediction latency.
- **Label availability:** The actual duration becomes available only when the
  delivery finishes, even if the simulator knows the outcome internally earlier.

Candidate inputs include restaurant, location, distance, confirmation time,
weather, restaurant backlog, basket details, traffic, and courier supply/demand.
We will choose a minimal schema together before coding. Every feature must be
available at confirmation; historical summaries may use only information already
observed. Actual preparation, courier-wait, and travel times are outcomes, not inputs.

### ETA error is different from lateness

An order confirmed at 18:00 and delivered at 18:38 has a duration of 38 minutes.
A prediction of 35 minutes has an absolute error of 3 minutes and a signed error
of -3 minutes. If the promised deadline was 18:40, the delivery was not late.

Later, define `late_delivery` as delivery after the deadline promised at
confirmation. Preserve that original promise. The promise-setting policy and
the severe-lateness threshold are still undecided. A point ETA alone does not
provide a late-delivery probability.

### How we will evaluate it

- **Primary:** MAE, the mean absolute prediction error in minutes.
- **Secondary:** RMSE, median and 90th-percentile absolute error, and bias
  defined as mean(predicted - actual). Negative bias means underprediction.
- **Operational relevance:** Share of orders with absolute error below 5 and
  10 minutes, plus the underprediction rate. Add severe-lateness and classifier
  metrics when their definitions and the risk model are agreed.
- **Validation:** Train on earlier orders, validate on later orders, and reserve
  the latest test period. Respect delayed label availability and never tune on
  the final test set. Compare with simple baselines and inspect relevant segments.

The 5- and 10-minute bands are reporting measures, not launch targets. No model
accuracy, latency, or promotion threshold has been chosen or achieved yet.

### Scope and first milestone

Start offline: a small, seeded, inspectable dataset of completed deliveries,
simple baselines, and a chronological model comparison. Grow complexity only
when we can explain what it adds.

Eventually, serve predictions through an API, store predictions and delayed
outcomes, monitor errors and drift, and evaluate new models before promotion
with rollback available. The staged roadmap is in [AGENTS.md](AGENTS.md).

Not now: cloud infrastructure, dashboards, automatic retraining, dispatch
optimization, courier incentives, or causal experiments. No full architecture
scaffolding and no large dataset generation at this stage.

### Next decision

Agree on the smallest useful order schema and a transparent first simulator.
No implementation begins until that step is approved.
