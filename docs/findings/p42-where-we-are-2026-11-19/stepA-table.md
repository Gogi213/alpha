TK-624 STEP A. Window 01.07–13.11.2026 (136 d), stake 500 $, Σ = gross − fees (no funding), by entry day; totals equal accepted reports (L_base +7.06/621, S_base +259.14/431, S_mk1 +248.05/372).
Sharpe = mean/std of daily P&L incl. zero-trade days × √365 (per month = that month's days only; short months are noisy). Max DD = peak→trough of cumulative daily $ inside the period (start = 0).

**L_base (long, taker)**
| period | Σ $ | trades | mean $/trade | Sharpe | max DD $ |
|---|---|---|---|---|---|
| July | -128.59 | 130 | -0.989 | -4.29 | 128.59 |
| August | +124.86 | 131 | +0.953 | +5.55 | 52.11 |
| September | +6.80 | 152 | +0.045 | +0.18 | 102.51 |
| October | -95.47 | 143 | -0.668 | -2.84 | 179.09 |
| Nov 1–13 | +99.46 | 65 | +1.530 | +11.22 | 29.91 |
| **TOTAL 136 d** | +7.06 | 621 | +0.011 | +0.05 | 228.75 |

**S_base (short, taker)**
| period | Σ $ | trades | mean $/trade | Sharpe | max DD $ |
|---|---|---|---|---|---|
| July | +71.11 | 96 | +0.741 | +3.43 | 42.59 |
| August | +77.46 | 108 | +0.717 | +6.79 | 22.88 |
| September | -22.88 | 92 | -0.249 | -1.83 | 40.88 |
| October | +110.61 | 95 | +1.164 | +8.14 | 26.83 |
| Nov 1–13 | +22.85 | 40 | +0.571 | +4.76 | 9.53 |
| **TOTAL 136 d** | +259.14 | 431 | +0.601 | +3.99 | 46.39 |

**S_mk1 (short, maker limit)**
| period | Σ $ | trades | mean $/trade | Sharpe | max DD $ |
|---|---|---|---|---|---|
| July | +65.26 | 81 | +0.806 | +3.62 | 36.88 |
| August | +66.87 | 93 | +0.719 | +6.91 | 19.56 |
| September | -4.30 | 78 | -0.055 | -0.48 | 26.34 |
| October | +85.79 | 83 | +1.034 | +6.65 | 23.78 |
| Nov 1–13 | +34.43 | 37 | +0.931 | +7.10 | 6.13 |
| **TOTAL 136 d** | +248.05 | 372 | +0.667 | +4.42 | 39.27 |

**Raw signal, 60-min markout, $ per signal at 500 $ (no exits, no costs)** — source TK-617 §6 (`docs/findings/p42-long-autopsy-2026-11-19/autopsy.md:432`); round-trip taker cost = 0.42 $ (fees 0.32 + slippage 0.10).
| month | long: signals | $/signal | share >0 | short: signals | $/signal | share >0 |
|---|---|---|---|---|---|---|
| July | 132 | -1.39 | 44% | 104 | +3.38 | 54% |
| August | 131 | +0.42 | 44% | 110 | +1.29 | 49% |
| September | 155 | +2.78 | 44% | 94 | +0.06 | 48% |
| October | 144 | +1.24 | 44% | 96 | +2.51 | 50% |
| Nov 1–13 | 78 | -2.37 | 40% | 41 | -0.47 | 46% |
| **ALL** | 640 | +0.47 | 43% | 445 | +1.61 | 50% |

After cost (all months): long +0.05 $/signal, short +1.20 $/signal.

Files: srv /data/p42-tk624/table.json (daily series for equity curves), code tools/p42/tk624_table.py (reads TK-599 out/<win>/{c53a116125b39=L_base, c10fbba7be522=S_base}, TK-616 root out/<win>/c934346e168dd=S_mk1). STEP B starts now.
