# Marketplace ETA Intelligence System

## Purpose

Build a simplified food-delivery marketplace system that predicts delivery
duration at order confirmation, later adds late-delivery risk, and follows
predictions through delayed outcomes, monitoring, retraining, and rollback.

This is a learning-first project. Daman must understand the code, decisions,
and operational behavior. Understanding and control matter more than speed
or feature count. The project brief describes the destination, not permission
to build everything.

The agreed scope is a synthetic Toronto-inspired downtown market with multiple
zones, weather, traffic, calendar effects, promised deadlines, cancellations,
and multi-order deliveries. See `README.md` and `docs/order-schema.md` for the
current design. Implement this scope gradually; do not silently remove it.
Use synthetic zone labels and sampled distances, not maps or detailed routing.
Additional batched orders must stay near the first order's pickup and drop-off;
keep the exact proximity cutoff explicit when implementing that rule.

The implementation now includes the direct synthetic-data generator, label-aware
chronological splits, three baselines, offline LightGBM evaluation, and a local
full-data refit/save/load workflow. Daman approved the 160-tree refit on all observed
delivered labels; see `docs/handoff.md` for the verified artifact and next decision.
Generated model artifacts stay local and Git-ignored. No API or deployment has
been approved as part of this step.
Sample features and calculate outcomes with a simple formula. Workload and
courier counts are sampled context, not reconstructed state.
Do not reintroduce marketplace objects, queues, dispatch, event clocks, intermediate
stages, or route histories. Batching is only a sampled nearby-detour effect.
Live replay and delayed outcome handling are separate, later steps.

## Working agreement: one small step at a time

1. **Propose:** State one small objective, why it matters, which files would
   change, and how we will check it. Surface decisions before making them.
2. **Wait:** Get Daman's approval before implementation. A direct request for
   a clearly bounded change approves that change only. A question, review,
   or discussion is not permission to edit. Relevant read-only inspection is fine.
3. **Implement:** Complete only the agreed step, including its focused checks.
   Keep the diff small enough to review together. If scope grows, stop and ask.
4. **Explain:** Walk through what changed, each new function's purpose, the
   inputs and outputs, and non-obvious choices. Use a tiny example when useful.
   Explain the reasoning, not merely a list of files.
5. **Verify:** Report the exact checks run and their results. Distinguish what
   works, what is untested, and what remains uncertain.
6. **Publish:** Commit and push the verified, agreed changes after every step.
   Daman has authorized this recurring Git workflow. Review the diff and stage
   only intended files; exclude secrets, local data, and generated artifacts.
   Use a descriptive commit message, never force-push, and verify that GitHub
   has the local commit. If publishing fails, report it rather than claiming
   the step is fully complete.
7. **Stop:** Propose the next small step, but do not start it. Wait for Daman
   to confirm he is comfortable with the current step and ready to continue.

- A release is many steps, not one implementation request.
- If Daman is unsure, pause new work and explain the current piece differently.
  Do not assume understanding from silence or impose quizzes.
- Be direct and concise. Assume strong Python, statistics, and business skills;
  explain unfamiliar serving and operational ML concepts without jargon.
- Do not delegate to subagents or run work in the background unless requested.
- Dependency installation, long-running jobs, services, cloud resources, and
  application deployment must be explicitly part of an approved step. Explain
  costs and side effects before proceeding. Publishing code is not permission
  to deploy an application or provision infrastructure.
- Preserve existing work. Do not make unrelated fixes or silently expand scope.

## Code style

- Write simple, clean, pithy Python. Prefer readable code over clever brevity.
- Use clear names, short focused functions, and explicit inputs and outputs.
  Add type hints where they clarify interfaces.
- Start with ordinary functions and data structures. Add classes or abstractions
  only when a concrete need makes them simpler than the alternative.
- Reuse existing utilities and appropriate library functionality. Explain and
  agree on new dependencies; keep the environment project-local.
- No speculative frameworks, generic plugin systems, unused configuration,
  premature optimization, or scaffolding for future releases.
- Comments explain why. Avoid narrating obvious code or adding boilerplate
  docstrings. No stray debug prints, swallowed errors, or silent fallback behavior.
- Add focused tests with the behavior they protect; do not postpone all tests
  to a later release. Keep exploratory notebooks out of the required runtime.
- Grow the file structure as responsibilities appear. Do not create the entire
  architecture in advance.
- Keep Python source under `code/`, grouped by responsibility (currently
  `code/simulator/`), tests under `tests/`, and design notes under `docs/`.
  `code/` is not a Python package; use `PYTHONPATH=code` when running tests.

## Modeling and simulation guardrails

- Define the product and target before modeling. The primary target is
  `delivery_duration_minutes`, measured from order confirmation to delivery.
- Define the promised deadline separately from the model prediction before
  implementing late-delivery risk. Do not invent a probability from a point ETA.
- Start with a tiny, inspectable, seeded simulation. Add heterogeneity,
  nonlinearities, interactions, noise, and regime changes in explainable steps;
  scale the dataset only when useful. Make simulator assumptions explicit.
- Use only information available at prediction time. Actual preparation,
  courier-wait, and travel durations are outcomes, not inference features.
- Split chronologically. Fit preprocessing and historical aggregates using
  eligible past data only. Respect when delayed labels become available.
  Do not tune on the final test set.
- Establish mean, distance/hour, and linear baselines before a boosted-tree
  model. Select LightGBM or XGBoost when that step arrives, not both by default.
- Use MAE as the primary metric; inspect tail errors, bias, and relevant
  segments. Agree on business thresholds instead of treating example values
  in the brief as requirements or measured results.
- Keep training and serving feature definitions consistent. Validate inputs
  and labels with small, explicit checks.
- In the live stage, call the actual prediction API, log model versions, and
  join predictions to outcomes only after delivery. Separate simulated time
  from wall-clock latency.
- Distinguish input drift from observed performance degradation. Evaluate a
  challenger on an untouched, chronologically later evaluation window before
  promotion; successful training alone is not a promotion rule.
- Label all data and results as simulated. Never present illustrative metrics
  as measurements or simulation performance as evidence of real-world accuracy.

## Roadmap, not current authorization

Start with **Phase 0: a short product spec** covering the user, prediction
moment, target, available inputs, promised deadline, metrics, and non-goals.

- **v0.1:** Inspectable simulator, chronological dataset, baselines, ETA model.
- **v0.2:** Reproducible command-line training, validation, and broader tests.
- **v0.3:** FastAPI `/predict` and `/health`, request validation, Docker.
- **v0.4:** Cloud deployment, PostgreSQL, prediction logging, live simulation
  through the API, and delayed outcomes.
- **v0.5:** Operational and model monitoring, deliberate regime shifts, drift
  detection, and explicit alerts.
- **v1.0:** Retraining, versioned models, challenger evaluation, promotion,
  rollback, and CI/CD.

Each release must work before moving on. Agree on each step within it.
Keep the interface minimal; add a monitoring dashboard when there is something
real to monitor. Incentives and causal experiments are a separate later phase.

Do not introduce Kubernetes, Kafka, Spark, Airflow, Terraform, Redis, feature
stores, microservices, neural networks, or LLMs without a concrete need and
explicit agreement. FastAPI, PostgreSQL, Docker, and a model registry are future
components, not prerequisites for the first simulator.

## Completion standard

A step is complete only when its agreed behavior is checked, its changes and
limitations are explained, the commit is pushed and verified on GitHub, and
Daman has had the opportunity to review it.
Passing tests does not by itself authorize the next step. Keep documentation
truthful about what exists today versus what is planned.
