# Challenger evaluation — September 6, 2026

Contender: ETA/risk refits on January–August plus September week 1 (116,390
delivered rows, frozen 160 trees, same settings). Model hashes `722c052f…`
(ETA) and `dc1d5d3f…` (risk). Champion: the January–August refits
(`29447c8e…` / `7ed78992…`). Arena: untouched September week 2, seed 9
(3,374 orders, SHA-256 `88ebb75a…`), replayed live once per contender under
separate run IDs. All data and errors simulated.

## Promotion rule (agreed before either replay)

Promote only if, on September week 2, the challenger has lower MAE **and**
no-worse storm bias **and** no-worse risk Brier than production.

## Results

| Contender | Run | MAE | Bias | P95 | Storm bias | Risk Brier |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Production | EVAL-PROD-SEPW2 | 3.175 | -0.505 | 8.570 | -0.669 | 0.0806 |
| Challenger | EVAL-CHAL-SEPW2 | 3.167 | -0.496 | 8.522 | -0.694 | 0.0804 |

3,272 matched deliveries each; 102 cancellations counted; 0 pending both runs.

## Verdict: rejected

MAE (-0.008) and Brier (-0.0002) favor the challenger by slivers, but storm
bias is worse (-0.694 vs -0.669), failing the second clause. The incumbent
stands — ties and tradeoffs against the known weak slice do not promote.
The margins are thin enough that a wider evaluation window is the legitimate
next move; retuning the frozen challenger against this window is not.

## Rollback drill (same session, explicitly a drill)

With the verdict recorded, the challenger was promoted with note "rollback
drill only", serving restarted on it (health showed `722c052f…`), then rolled
back and serving restarted on production (health `29447c8e…` restored). The
machinery works; the decision stands. Both eval runs admin-deleted after;
residue `0|0|0|0`.
