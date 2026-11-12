# G-022 / В-148 — turbo collector in Rust (Binance + Bybit): report

Branch `g022-turbo` (merged with `origin/master` 74ba6fcd). Owner, 12.11: «коллектор тоже должен быть турбо, чтобы не было бекпрешшура и подвиссаний»; then «переделываем в раст»,
«не надо отстраивать раст от питона байт в байт, просто делаем раст без задержек», and (В-152) «у меня нет задачи коллектить дату сейчас. у меня есть задача сделать хороший коллектор».
Labels: **measured** = a number from a run on srv with the artifact named; **inference** = follows from measured facts; **guess** = not verified.
All times GMT+4; srv = 5.231.25.224 (8 vCPU / 62 GB). Artifacts: `docs/findings/g022-turbo-collector-2026-11-12/`; scripts: `tools/turbo/`.

## 0. Result in five lines

1. A production-grade Rust collector exists: `alpha collect run --exchange binance|bybit` (`src/collector/`, 7.6 k lines of which 1.5 k are 36 unit/integration tests on local WebSocket sources). Same file format and health/acceptance tooling as the Python core; health schema 2 = schema 1 + backpressure.
2. **Data quality, against the exchange itself** (measured): Binance trades vs `/fapi/v1/aggTrades` REST — 47 816 / 47 816 and 74 967 / 74 967 identical (id, price, qty, first/last id, side), 0 missing, 0 extra, 0 mismatched;
   two independent receivers (Python and Rust) recorded the identical key sets over 170 s (484 743 book, 62 109 trade, 40 527 mark keys, 0 differences). Bybit trades vs `/v5/market/recent-trade`: all 20 278 REST trades present in the captured WS feed, 0 mismatches.
3. **Defect found and fixed (affects Python data too):** at Bybit the two entrances (`stream.bybit.com`, `stream.bybit.kz`) number `orderbook.1` `u` independently (same book state: `u` 79 205 427 on com, 858 576 on kz; same `seq`/`cts`).
   The Python rule «drop a record whose key ≤ last written» therefore threw away one whole line (measured on a 7-min capture of both lines: 429 891 distinct book states, old rule keeps 271 380 = loses 36.9 %). New rule: de-duplicate by exchange identity (book `seq`, trade UUID, …) in a 120 s window — loses nothing that arrived (0 of 429 891), writes no duplicates.
4. **No backpressure, no hangs** (measured, §5): 0 queue overflows, receive-queue depth max 33–74 frames of 32 768, waiting time of a frame before the writer p99 3 ms, kernel Recv-Q max 3–29 KB; CPU 0.5–0.7 cores for all 36–50 connections (Python: 1.5–2.0) and 2.65 % of samples with a non-empty kernel queue (Python 10.5 %).
5. Open for the coordinator: review by Opus, PGO build (tooling present on srv, procedure in §7), cut-over per §7; Bybit-specific items in §8.

## 1. Where the Python collector loses time (step 1, minimum — baseline only)

Python numbers are from the same 3-minute window on the same machine while it was the production collector (13:26–13:29, srv load ≈ 8–10 on 8 vCPU: a 19 GB Python job at 100 % + builds; artifacts `bn1/`, `by1/`):

| Binance, 180 s | Python (prod) | Rust |
|---|---|---|
| CPU of the unit, cores mean / p95 / max (cgroup) | 2.03 / 2.45 / 2.67 | 0.73 / 0.94 / 1.07 |
| worst single shard (stats `cpu_frac` max) at ≈ 1 000 frames/s | 0.36 core (book07) | 0.13 core (all threads of the shard) |
| samples with non-empty kernel receive queue (`ss`, 50 connections × 180 s) | 10.5 % (mean 116 B, max 41 KB) | 2.65 % (mean 16 B, max 29 KB) |
| time with some task waiting for CPU (PSI `cpu.pressure`, some) mean / max | 0.29 / 0.61 | 0.20 / 0.36 |
| book lag rx−E on disk p50 / p99 / max, ms | 117 / 342 / 728 | 115 / 209 / 598 |
| trade lag p50 / p99 / max, ms | 154 / 290 / 635 | 114 / 208 / 262 |
| rx→disk lag (`lag_out`) p50 / p99, ms | 526 / 1 024 | 178 / 219 |

- **`lag_out` 500/1000 ms is the flush period, not a queue** (code + data): Python flushes zstd when the *next record* arrives after `FLUSH_PERIOD_S = 1.0`; rx→disk is uniform over 0…1 s (p50 ≈ 500, p99 ≈ 990; measured 526/1 024). Rust cuts a block every `--flush-ms` (200 ms): 178/219 ms. Size cost of a shorter flush (bench, §2): 200 ms ≈ 0 %, 50 ms +3 %, 10 ms +12 %.
- Python's tail under load (measured earlier the same hour, srv load ≈ 10): `lag_in` p99 17.2 s, `fresh.book.late_1h` 36 245 records, max 13.2 s. **Inference:** one asyncio loop per shard process doing read + parse + dedup + encode + zstd + write at 0.25–0.36 core is starved by CPU contention; the Rust design uses many threads each at 1–3 % of a core (short runs, quick wake-ups). **Guess:** not isolated from other causes (network path); no controlled A/B at equal load was possible after the owner stopped the Python units.
- Python dedup flaw: see §3 (not a speed issue, a completeness issue).

## 2. Architecture and why (step 2)

```
socket ──read(200 ms tick)──► rx thread (one per connection) ──ring 32 768──► writer thread (one per shard) ──blocks(64)──► disk thread (zstd, file, .ok)
        rx stamp, counters,       no parsing, no allocation of        Parser (adapter) → identity dedup → Encoder → block     flush every --flush-ms;
        ping/pong, Recv-Q         records; frame moved as Bytes       raw-copy dedup, fresh, lag histograms                  lag_out measured here
```
- **One OS thread per connection with a blocking socket (sync tungstenite + rustls/ring), no async runtime on the data path.** The rx thread does only: read → `clock_gettime` stamp → atomics → push. Nothing it does can wait on the disk except a full ring (that is the backpressure, counted: `ring.full_events`, `blocked_ns`). Ping/pong, the 15-s path probe, the gap watch and `FIONREAD` (kernel Recv-Q) run on the 200 ms read-timeout ticks.
- **Parsing is in the writer thread**, not the rx thread: a slow parser can never delay reading. Hand-written scanner with an exact key-set check (unknown shape ⇒ the frame is written raw, never partially); Bybit `tickers` (≈ 30 fields) goes through `pick`. Per-frame cost on srv (quiet, 2 M frames, `bench-4.txt`): own scanner 1 231 ns; `serde_json` typed-borrowed 1 182 ns; `serde_json::Value` 3 297 ns; top-level-only 427 ns. Own scanner chosen for the exact-shape/all-or-nothing contract, with parity speed after optimising its nested-object skip (first run 1 436 vs 1 025 ns, srv loaded). Whole writer path (parse+dedup+encode+zstd-1) 1 043 ns/frame ⇒ ≈ 960 k frames/s per core, far above the exchanges' 4–25 k/s per connection.
- **Hand-off:** `std::sync::mpsc::sync_channel` (crossbeam-based) + `Bytes` per frame: 1.5 µs/frame at saturation in the bench (two threads, loaded), ≈ 0 at real rates. A lock-free byte ring would save ≈ 1 µs/frame — not worth the complexity at ≤ 25 k frames/s/connection.
- **zstd on its own thread**, level 1 (level 3 is *worse* on these records: ×5.4 vs ×7.3 and slower — bench). A stalled disk fills the 64-block queue, then the ring, then `push` blocks — visible in `backpressure`, never silent loss.
- **Dedup by identity**, 120 s window (§3). Raw frames with an exchange timestamp are de-duplicated by per-session delivery counts (a copy is a frame another session has already delivered more times than this one); service frames (subscription ack, pong) are all kept.
- **Lines and entrances:** `--lanes 2`; Bybit line 0 = `stream.bybit.com` over IPv4, line 1 = `stream.bybit.kz`; spare entrance of a line = the other line's primary. A line moves to the spare after 3 sessions shorter than 60 s in a row (`connect_fail`) or when its session is replaced for late delivery (`late_delivery`); it **returns to its primary** after 10 min (doubling after each failed return, cap 2 h). A replacement that is not accepted (no connection / path probe failed) is cancelled and the old session keeps the stream (`switch_failed`) — the Python flow cancelled the old session anyway.
- **Path selection as in Python:** RTT of the TCP handshake from `TCP_INFO` (reject > 300 ms before TLS), 15-s lag probe (p50 ≤ 350 ms, p99 ≤ 1.5 s), live watch (p50 > 2 s for two 10-s windows ⇒ degraded ⇒ replacement), pong timeout 10 s ⇒ degraded, 23 h max age with 20 s overlap, gap = diagnostics only (event `gap`, counter, no action).
- **Connection budget** `Budget` with several windows (200 per 300 s now; the 1 s window OKX needs is the same mechanism). **Stop:** one SIGTERM (repeats ignored), sessions stop and write `closed`, the writer drains the ring, then zstd frame closed and `.ok` written — tested (`shard_end_to_end…`: `closed` is the last row of every file) and live (all 25 + 18 files `zstd -t` ok, `.ok` present).
- **Bench-based choices** (`bench-4.txt`): zstd level 1; flush 200 ms; parse = own scanner; hand-off = std channel.

## 3. Data-quality findings and the fix (new)

Method: `ALPHA_COLLECT_CAPTURE=1` writes every frame of every session before de-duplication (`capture-<shard>.tsv`); `tools/turbo/lanecmp.py` computes, per (kind, symbol), exchange identities, coverage by line, skew between lines, and what each rule would write. 7-minute runs, both lines (artifacts `bn2/ by2/ bn3/ by3/`, `lanecmp*.json`).

| | distinct records | old rule «key ≤ last» writes | lost by old rule | new rule (identity, 120 s) writes / lost / duplicates |
|---|---|---|---|---|
| **Bybit book** (`orderbook.1`), run 2 | 429 891 | 271 380 | **158 721 (36.9 %)** | 429 891 / 0 / 0 |
| Bybit book, run 3 | 429 829 | 260 967 | 169 046 (39.3 %) | 429 829 / 0 / 0 |
| Bybit trades, run 3 | 81 656 | 81 741 (**94 duplicates written**) | 9 | 81 656 / 0 / 0 |
| Bybit mark (tickers delta), run 3 | 46 251 | 46 251 | 0 | 46 251 / 0 / 0 |
| Binance book, run 3 | 1 229 293 | 1 229 293 | 0 (run 2: 11 of 1 079 121) | same |
| Binance trades / mark | 123 589 / 396 114 (run 2) | same | 0 | same |

- **Why Bybit differs:** the two entrances give different `u` for the *same* book state and identical `seq` and `cts` (frame pairs inspected: ADAUSDT `u` 79 205 427 vs 858 576, `seq` 77 055 both). Only 35 % of book states arrive on both lines — each gateway pushes its own 10-ms samples — so the second line *adds* 58 % more states than any single line, and the Python rule kept only the line with the numerically larger `u`: **the second line gave no protection against a stall of the first one, and the file carried one line's samples.** Bybit trades: the synthetic key `a = seq·1000 + index` differs between lines for 1.7 % of fills (batching), so the rule both lost fills and wrote duplicates; identity = the fill's UUID.
- **Skew between lines** (same record on both lines, |Δ receive time|): Bybit book p50 15.7 ms, p99 852 ms, max 4.8 s; Bybit trades p99 270 ms; Binance book p50 0.9 ms, p99 95 ms, max 0.8 s. TK-532 saw a stuck line up to 15 s and a degraded entrance up to 120 s ⇒ identity window 120 s (≈ 50 MB per process at Binance rates).
- **Cost of the fix:** records are written in arrival order, so a Bybit book state that arrives on the slower line after a newer state from the faster line is written after it: 10 % of Bybit book rows are «not increasing» in `seq` (`accept_ws.py` flags `book_u_not_increasing` — expected, not loss); Binance 0–11 per million. **Readers sort by the key (or by E/T) — documented in `docs/collector-ws-format.md`.** Bybit `b` records now carry `seq` in the `u` column (connection-local `u` is not comparable between entrances; it stays in the capture). A reorder buffer would restore file order at the price of ≥ 1 s extra rx→disk lag (skew p99 852 ms) — rejected: it contradicts the goal and consumers sort anyway.
- **Exchange-side oracle** (`tools/turbo/restcheck.py`): Binance, 25 most active symbols, 5-min windows — runs 2 and 3: 47 816 and 74 967 aggregate trades in REST, 47 816 and 74 967 in files, **missing 0 / extra 0 / mismatched 0** (price, quantity, first/last id, side). Bybit recent-trade (last 1 000 per symbol, 30 symbols): 20 278 REST trades, all present in the capture (0 missing, 0 mismatched); 157 captured trades are absent from REST (RPI trades and several fills in one millisecond — REST list does not carry them; WS ⊇ REST).
- **Two independent receivers** (`keycmp.py`, exchange-time window ±5 s, Python and Rust on the same streams): Binance book 484 743 = 484 743 keys, trades 62 109 = 62 109, mark 40 527 = 40 527 — 0 differences. (Bybit differs by design: §3 first bullet.)

## 4. Requirements coverage (coordinator's list)

| requirement | status |
|---|---|
| same file format; health page works; backpressure fields | done: format unchanged (`Decoder` round-trip, `accept_ws.py` reads Rust files); health schema 2 ⊇ 1 (`serve.py`/`page.html` accept both, 3 new columns, yellow row on ring overflow) |
| route choice by RTT at connect, replacement without a gap | done: RTT reject > 300 ms, probe, watch, overlap 20 s; tests `replacement_session_overlaps_without_gap_or_duplicates` (u contiguous across two rotations), `failed_return_to_primary_keeps_the_stream` |
| two lines per shard, dedup incl. raw frames | done (identity dedup; raw copies by per-session counts; tests `two_lanes_deliver_one_gapless_stream`, `writer_raw_copies_…`) |
| Bybit lines on different entrances (com IPv4 + kz), automatic spare | done + live: in run 3 line 1 left kz for com on `late_delivery` (230 s) and came back logic added afterwards; `entrance_switches_1h` in health |
| Binance book 32 streams per connection, trade 64, mark 1 | done (25 shards × 2 lines = 50 connections, as Python) |
| connection budget ≤ 200 / 5 min | done; multi-window budget (OKX) |
| fresh (rx−E > 2 s, max lag by kind, 1 h / 24 h), gap diagnostics only | done (per record after dedup; minute buckets) |
| correct zstd close on stop; daily rotation | done; `closed` is written before the file closes; rotation also by clock (`.ok` 5 s after midnight even without a new record) |
| gate «byte for byte» | dropped by the owner; replaced by exchange-side checks (§3) |
| OKX not excluded | design hooks only: multi-window budget; tail-column format extension; key reset on seqId reset and `notice 64008` replacement not implemented (listed §8) |

## 5. Measurements (steps 3–4) — placeholder, filled below
