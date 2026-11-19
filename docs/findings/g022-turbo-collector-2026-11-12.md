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

- **`lag_out` 500/1000 ms is the flush period, not a queue** (code + data): Python flushes zstd when the *next record* arrives after `FLUSH_PERIOD_S = 1.0`; rx→disk is uniform over 0…1 s (p50 ≈ 500, p99 ≈ 990; measured 526/1 024). Rust cuts a block every `--flush-ms` (default **100 ms** in code, `runner.rs`/`ShardOpts::new`; the 178/219 ms above were measured at 200 ms). Size cost of a shorter flush (bench, §2): 200 ms ≈ 0 %, 50 ms +3 %, 10 ms +12 %.
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
- **Bench-based choices** (`bench-4.txt`): zstd level 1; flush 200 ms in the bench (production default since: 100 ms); parse = own scanner; hand-off = std channel.

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

### 3a. Second finding (Python too): Binance `!markPrice@arr@1s` was stored raw for most symbols

Capture of `mark00` (run 3): each second two arrays arrive — 237 elements (all `st`=1) and **747 elements of which 30 are COIN-M contracts (`BTCUSD_PERP`, `st`=2)**. The rule «parse the whole array or write it raw»
(Python and my first Rust version) turned the 747-element array into one raw frame per second: **structured `m` records existed for 237 symbols only; the marks of ≈ 700 symbols (most of the pool) were in raw JSON blobs** (data present, but not in the record format — measured: 237 symbols with `m` rows in `bn3`, 340 raw rows/340 s).
Fix: arrays are parsed **per element** — valid USDⓈ-M elements become `m` records, the remaining elements are written as one `r` row in a frame of the same shape (nothing lost, nothing duplicated, copies from the other line removed). Same treatment for Bybit trade batches (a block/RPI fill no longer turns the whole batch raw; the fill's position in the batch — hence its key `a` — is unchanged). A malformed array is still raw as a whole.

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

## 5. Measurements (steps 3–4)

All on srv. «Quiet» = no other collectors/benchmarks running, load average ≈ 1.5 (Python units were stopped 14:05).

### 5.1 Live windows (3 min) — Rust only, soak units, binary 6 (`w1-*`; first 3 min after a restart, so they include warm-up)

| | Binance (50 connections, 511 symbols) | Bybit (36 connections, 413 symbols) |
|---|---|---|
| records in 180 s (b / t / m) | 745 514 / 73 145 / **172 674** | 176 094 / 41 678 / 25 196 |
| lag rx−E on disk p50 / p99 / max, ms (book) | 115 / 379 / 828 | 108 / 160 / 357 |
| lag trade p50 / p99 / max, ms | 114 / 165 / 466 | 107 / 120 / 210 |
| lag mark p50 / p99 / max, ms | 194 / 231 / 236 | 108 / 120 / 210 |
| rx→disk lag (`lag_out`) p50 / p99, ms | 99 / 110 | 101 / 106 |
| records late > 2 s (health `fresh`, 1 h) | 0 | 0 |
| unit CPU, cores mean / p95 / max | 0.84 / 1.17 / 1.72 | 0.57 / 0.79 / 0.96 |
| kernel Recv-Q non-empty samples; max | 2.2 %; 41 KB | 1.6 %; 4.2 KB |
| receive-queue depth max (cap 32 768); overflows; blocked | 236; 0; 0 ms | 79; 0; 0 ms |
| frame wait in queue before the writer p99 | 2.5 ms | 2.1 ms |
| reader busy outside `read` max | 6.8 ms | 18.1 ms |
| disk block write max | 14.9 ms | 16.3 ms |
| events | 4 path rejects by RTT, 1 ping timeout → 1 path switch | none |
| sequence checks (`accept_ws.py`: u/a/E not increasing, trade id gaps) | 0 | 0 |
| REST oracle (25 symbols) | 36 826 / 36 826 trades identical | — |

Mark records: 172 674 in 180 s ≈ 960/s (all USDⓈ-M symbols); the Python/first-Rust layout gave 238/s (§3a).

### 5.2 Earlier 3-min windows next to the running Python collector (same srv, loaded; binary 1; `bn1/`, `by1/`, tables in `*/table.md`)
Binance — CPU 2.03 (Python) vs 0.73 cores (Rust); book lag p99 342 vs 209 ms; Recv-Q non-empty 10.5 % vs 2.65 %; `lag_out` p50/p99 526/1 024 vs 178/219 ms. Bybit — CPU 1.46 vs 0.50; book lag p99 108 vs 92 ms; `lag_out` 514/1 100 vs 199/207 ms; Recv-Q non-empty 7.1 % vs 2.0 %.
(That Rust build lost Bybit mark records — all `tickers` frames with more than 16 fields went raw; found by this very comparison, fixed with `scan::pick`, regression test `bybit_tickers_with_many_fields_still_parse`.)

### 5.3 Load ladder — ONE connection, synthetic Binance book frames (190 B, 64 symbols, plain ws), quiet srv, binary 6 (`r6/`)
| frames/s on the connection | achieved rows/s | gaps / dups in `u` | lag rx−E p50 / p99 / max, ms | collector CPU, cores | CPU µs/frame | ring depth max | overflows | wait in queue p99, ms |
|---|---|---|---|---|---|---|---|---|
| 10 000 | 10 000 | 0 / 0 | 0 / 1 / 12 | 0.26 | 25.5 | 970 | 0 | 2.8 |
| 50 000 | 50 000 | 0 / 0 | 0 / 2 / 35 | 0.46 | 9.3 | 7 030 | 0 | 20 |
| 100 000 | 99 999 | 0 / 0 | 0 / 2 / 22 | 0.72 | 7.2 | 28 756 | 0 | 128 |
| 150 000 | 150 001 | 0 / 0 | 0 / 623 / 867 | 1.05 | 7.0 | 32 770 (full) | 13 814 | 141 |
| 200 000 | 196 152 | 0 / 0 | 1 / 702 / 767 | 1.34 | 6.8 | full | 12 190 | 162 |
| 300 000 | 137 319 | 0 / 0 | 2 / 544 / 609 | 1.51 | 11.0 | full | 30 085 | 274 |
**Ceiling of one shard ≈ 100–110 k frames/s** (the writer thread, 100 % of one core; `perf`: JSON scan ≈ 25 %, identity set ≈ 8 %, encoder ≈ 4 %). Above it the ring fills and the reader blocks — backpressure is visible and counted, `u` stays gapless. Exchanges deliver 0.8–5 k frames/s per Binance connection and up to 10–25 k for Bybit deep views (TK-533): **margin ×4…×100**. The Python core measured 0.81 core at 10.4 k msg/s (TK-531) and CPU-bound deep views (TK-533); Rust at 10 k = 0.26 core all-in.
At low rates CPU per frame is dominated by wake-ups, not work: 100 frames/s cost 0.027 core (270 µs/frame), 2 k → 0.22 core, 120 k → 1.0 core (8.5 µs/frame) — batching happens by itself as the rate rises (**measured**, `idle1/`). Real Binance feed: 6.7 k frames/s over 50 connections = 0.73–0.84 core.

### 5.4 Micro-benchmarks (`alpha collect bench`, quiet srv, 2 M frames; `bench-4.txt`)
Parse: own scanner 1 231 ns, `serde_json` typed 1 182 ns, `serde_json::Value` 3 297 ns; writer path (parse + identity + encode + zstd-1) 1 043 ns/frame (≈ 960 k frames/s/core); zstd level 1 ×7.3 vs level 3 ×5.4 on these rows; block size cost (200 ms ≈ 0 %, 50 ms +3 %, 10 ms +12 %).

### 5.5 Replacement and rotation on the live exchange (`rot1/`)
Binance, 560 s, every connection replaced every 180 s (`--max-age-s 180`; real value 23 h), 2 lines: 109 `rotated`, 15 `path_switch`, 28 `switch_failed` (replacement not accepted ⇒ old session kept), 25/25 files closed (`zstd -t`, `.ok`, `closed` last). **REST: 101 638 / 101 638 trades identical across three rotations**; 0 duplicates, 0 trade-id gaps. (This run was deliberately abusive — 100 connections, churn, budget waits — and ran while another build/PGO job loaded srv, so its lag figures are not representative: book rows late > 2 s were 0.47 %.)

### 5.6 Stop / restart (live)
`soak.sh start` restarts the soak with one SIGTERM: 25 + 18 files closed in 1.3 s, every file `zstd -t` ok with `.ok`; `check_files.py` on closed segments: 0 bad files, 0 duplicates, 0 trade-id gaps (`soak-*/check1.json`).

### 5.7 Steady-state windows on the soak, binary 8 (`w2-*`, 16:14–16:17) — and an upstream stall

| | Binance | Bybit |
|---|---|---|
| lag book p50 / p99 / max, ms | 123 / **2 476 / 23 044** | 107 / 118 / 151 |
| lag trade p50 / p99 / max, ms | 126 / 326 / 833 | 107 / 118 / 136 |
| lag mark p50 / p99 / max, ms | 199 / 260 / 271 | 107 / 118 / 178 |
| unit CPU mean / p95 / max, cores; CPU wait | 0.80 / 1.12 / 1.60; 0.11 | 0.62 / 0.88 / 1.12; 0.085 |
| Recv-Q non-empty; max | 1.4 %; 41 KB | 1.7 %; 3 KB |
| ring depth max; overflows; wait in queue p99 | 123; 0; 2.1 ms | 25; 0; 2.3 ms |
| `lag_out` p50 / p99 | 99 / 108 ms | 99 / 106 ms |
| records late > 2 s | **5 136 (book only)** | 0 |
| events | 29 `timeout` (all with live data), 4 `path_switch`, 17 RTT rejects | none |
| REST oracle (25 symbols) | 46 108 / 46 108 trades identical | — |

**The Binance book lateness is upstream, not local** (**measured**, `stats-*.jsonl`, `events-*.jsonl` of the window): from second 130 of the window three of 25 book shards (`book00`, `book01`, `book14`) show lag p99 growing linearly 4.5 → 35 s while p50 stays 120–190 ms (most updates on time, the updates behind the stalled streams arrive late); their message rate dips to 1.4–2 k/10 s and then bursts to 8–9 k/10 s; **both lines of each shard stall at the same moment** (ping timeouts on both sessions) while the host side is idle: kernel Recv-Q ≤ 66 KB, ring depth ≤ 240, reader busy ≤ 20 ms, TCP `retrans` 0–1, `rtt` 310–340 ms (normal 234–300), `last_data_recv_ms` 0–208 (data flowing). Trade and mark shards were unaffected in the same minute. This is the failure class TK-531 recorded at 23:12 on 11.11 («лаг канала книги растёт при всплеске, на всех соединениях сразу»), not a collector defect; every row is still recorded (REST check above, sequence checks 0).
Consequence for the design: two lines to the **same** host do not protect against it (Bybit's two entrances do carry different gateways). Not done: (a) activity-weighted shard layout (Python-integrate `--weights`; 3 hot shards would be spread), (b) a second Binance entrance for the book channel (legacy `/stream` vs `/public/stream`) — candidates for the next step, each needs a measurement.
A second finding of the same window: **pong delays up to 9.4 s while data flows** (`ping_ms` max 9 403). Binary 8 changed the rule to «no pong **and** no frames for 10 s ⇒ degraded» (`timeout` events carry `data_alive`); **superseded in binary 13 (§5.10): back to the Python rule — no pong within 10 s ⇒ `timeout` + `ping_degraded` + seamless replacement** (the `data_alive` field stays as a diagnostic).

### 5.8 Incident 11:50–12:36 UTC on the soak: route degradation + connection-budget starvation (fixed in binary 9/10)

Coordinator's check at 12:36 saw Binance 7 connections / 341 ping timeouts / 157 k late records per hour and Bybit 5 connections. **Layouts were never changed** — the soak runs the defaults all the time (Binance A: 16 book × 32 + 8 trade × 64 + 1 mark = 25 shards × 2 lines = 50 connections; Bybit: 18 shards × ≤ 72 topics × 2 lines = 36 connections; `journalctl` lines «shards 25: book00=32 …», «shards 18: book00=72 …» of the running units). What the logs show (**measured**, `events-*.jsonl` of the soak):
- **The host→Binance route degraded**: TCP-handshake RTT of every new connection, 10-min buckets (p10 / p50 / p90, ms): 11:30 234 / 236 / 308 → 12:00 235 / 352 / 372 → 12:20 263 / 369 / 383 → 12:30 260 / 370 / 381 (TK-531 baseline 234–293 clean). ICMP to Binance is filtered, so RTT is the handshake. Binance also reset connections (`Connection reset by peer`, no HTTP 429); **Bybit returned `502 Bad Gateway` on 152 WebSocket handshakes on both entrances from 12:11:50 and reset connections** — an exchange/edge-side incident on the same hour; at 12:36 it still answered 502 to the burst of 36 simultaneous handshakes of a restart and accepted them 2 s later.
- **Own defect that made it worse:** the absolute RTT filter (reject > 300 ms) rejected nearly every new dial in the degraded route (256 `path_rejected_rtt` in 10 min), each session burned up to 12 dials, the 200/5 min budget was exhausted (81 `connect_wait`), replacements and reconnects could not connect ⇒ the connection count fell to 7. First fix (binary 9/10): a cutoff relative to the peers, `max(300 ms, 1.15 × median RTT of the last 64 dials)` — **withdrawn in binary 12/13** (coordinator, TK-527: it accepted the slow route — handshake p50 370 ms, lag 1–14 s, RTO backoff, §5.9). Now: absolute 300 ms as in Python, rejections capped by a budget reserve (§5.10).
- **Burst of handshakes:** a connection-rate rule was added (`--connect-rate`, 10/s per process): Bybit at the 12:36 restart got 502 for 35 of 36 first handshakes — **but that minute was inside the exchange's own 502 incident (above), so the measurement is confounded; the rule was removed in §5.12 (as in Python) and the clean cold start measured without it.**
- Not caused by the layout: the late records of the stalled book shards appeared on **both lines of the same shard at the same time** with idle host queues (§5.7); the Python baseline «0 late/h» was measured in the calm period (RTT p50 236 ms) — no Python run exists for the degraded period, so a Python-vs-Rust comparison under the same route state is **not possible** (inference: Python would see the same route).

### 5.9 Red health items at 12:47 UTC — mark lateness and ping timeouts (Binance soak, binary 10 = alpha-7bfa8255; window `w3-*`, 12:45:38–12:48:39 UTC)

Health at 12:47 (Binance, process age 4 min): mark late 10 731 (max 46.2 s), book 1 293 (8.8 s), trade 69, raw 11 (41.8 s); 13 ping timeouts, 5 reconnects, 6 gaps; ping p50 370 ms (Python 305). Verdict per item (**measured** unless marked):

1. **Mark lateness is not an artifact of `E` or of the envelope — it is whole frames delivered late by the network.** Test: the recorded mark rows of the latest file (`soak-binance/data/2026-11-12/mark00-*.tsv.zst`, 592 935 rows = 622 arrays × 954 symbols): 16 698 rows are late > 2 s and **every one of the 954 symbols has the same 18 late rows of the same 18 arrays** (an `E`/per-symbol artifact would hit single symbols). Receive times of those arrays (BTCUSDT as the sample): receive silences of 3–17 s, then a catch-up burst of arrays with the old `E` (e.g. `dns` 17 412 ms at rx +496.9 s). During every silence the host side was idle: `stats-mark00.jsonl` `recvq_max` 0, reader `busy_max_us` < 1 ms, ring depth ≤ 2; `events-mark00.jsonl`: `gap`, `timeout` with TCP info `retransmits 4, backoff 4, rto_ms 7 488, last_data_recv_ms 10–11 s` — **our host retransmitting into a stalled path (RTO backoff)**, then `ping_degraded` → `path_switch`. The 46 s maximum is two such stalls on both lines back to back. The raw rows (185 in the window) are the COIN-M `st=2` elements of the same array (`BTCUSD_PERP …`), written as raw by design — the 11 late raw rows belong to the same late arrays. Raw frames were not captured on the soak (`ALPHA_COLLECT_CAPTURE` off); the proof uses the per-record receive times, which the reader thread takes at socket read. Not reconciled: `accept_ws` `disk_stream` of the same window shows mark lag max 1.03 s while the health counters (process lifetime) show 46 s — the counters include the first 90 s after the restart, the window the following 3 min.
2. **Ping timeouts: network, not ping/pong handling or timer starvation.** (a) The pong is read by the same reader thread that reads data: reader `busy_max_us` 21 ms (window), `handoff_max_us` 21 ms; kernel Recv-Q non-empty 1.9 % of samples, max 36 KB; CPU wait (PSI some-fraction) mean 0.11 — nothing that delays a pong by seconds. (b) Same binary, same host, same minute: **Bybit ping p50 228 / p99 276 / max 340 ms, 0 timeouts** in 1 289 pings; Binance ping n = 1 790, p50 368 / p99 914 / max 4 281 ms, 6 timeouts, all on two shards in two bursts (`mark00` 12:45:40, :51, :57; `book01` 12:48:10, :20, :31 — three consecutive 10 s timeouts = one stall of ≈ 25 s each). (c) Ping RTT p50 368 ms **is** the route RTT of the moment: handshake RTT of new dials at 12:30 was p50 370 ms (§5.8) versus 236 ms in the calm period; the Python baseline (305 ms) was measured in between, so p50 305 vs 370 is two states of the same route, not a client difference (**inference**: no Python run exists in the degraded state). (d) Rule behaviour in that build (binary 10): a timeout counted as degraded only after 10 s without frames (`ping_degraded` 1 in the window); replaced in binary 13 by the Python rule (§5.10).
3. **Cause and fix.** The stalls were on connections accepted on the slow route (handshake p50 370 ms): the relative cutoff of binary 9/10 let them in — a regression against TK-527 (two routes chosen by source port: fast ≤ ~293 ms clean, slow ≥ ~347 ms lag 1–14 s with retransmits; Python cutoff absolute 300 ms). The evidence above (a)–(d) shows no ping/pong/timer defect; the fix is route selection, §5.10 (this section's numbers are from binary 10, before the fix).

**3-min window numbers of binary 10 (before the fix; soak, `w3-*`, started 12:43:46 UTC, window begins 2 min after the restart, so it includes warm-up):**

| | Binance | Bybit |
|---|---|---|
| connections (shards × lines) | 51 (25 × 2 + one replacement in flight) | 36 (18 × 2) |
| rows b / t / m on disk | 583 200 / 65 013 / 173 391 | 163 235 / 36 909 / 23 623 |
| lag book p50 / p99 / max, ms | 193 / 575 / 863 | 107 / 117 / 168 |
| lag trade p50 / p99 / max, ms | 194 / 386 / 696 | 107 / 117 / 134 |
| lag mark p50 / p99 / max, ms | 258 / 671 / 1 030 | 107 / 117 / 146 |
| max pause on disk b / t / m, ms | 172 / 74 / 1 090 | 26 / 219 / 125 |
| unit CPU mean / p95 / max, cores; CPU wait | 0.85 / 1.07 / 1.19; 0.11 | 0.60 / 0.80 / 0.99; 0.08 |
| Recv-Q non-empty; max | 1.9 %; 36 KB | 1.3 %; 1 KB |
| ring depth max; full events; sink write max | 298; 0; 22 ms | 25; 0; 2.9 ms |
| `lag_out` p50 / p99 | 101 / 110 ms | 101 / 107 ms |
| sequence checks (book u, trade a, mark E not increasing; trade a gaps) | 0 / 0 / 0; 0 | 0 / 0 / 0; 0 |
| REST oracle trades, 25 symbols | **38 227 / 38 227 identical, 0 missing, 0 mismatch** | — |
| events | 6 ping timeouts, 1 ping_degraded, 1 reset, 1 path_switch, 3 dials | 0 |
| ping p50 / p99 / max, ms (n) | 368 / 914 / 4 281 (1 790) | 228 / 276 / 340 (1 289) |
| dedup: rows written / duplicates dropped | 1 214 846 / 1 085 567 | 354 493 / 354 177 |
| sink blocks dropped / errors | 0 / 0 | 0 / 0 |

### 5.10 Alignment with the Python path logic (g022-integrate @ 3f8e1ce6: `wscore/path.py dial.py session.py budget.py shard.py entrances.py`, `binance_ws.py`, `bybit_ws.py`) — build 14

Order of 17:10 GMT+4: port the proven Python behaviour; every difference reverted unless measured better. Line-by-line diff of the Rust code against those files:

**Reverted to Python (build 13/14):**

| # | Python | Rust before | Now |
|---|---|---|---|
| 1 | handshake-RTT cutoff **absolute 300 ms** (`dial.CONNECT_RTT_MAX_MS`, TK-527); a rejected path = new dial = new source port | `max(300, 1.15 × median of the last 64 dials)` — accepted the slow route (§5.9) | absolute 300 ms |
| 2 | budget 200 / 300 s, the cutoff does not depend on it; no place ⇒ the dial waits (`acquire`) | my addition: reserve (2 × connections) after which rejections stop and slow paths are accepted | reserve removed from the default (`--reject-reserve`, default 0 = Python); see the measurement below |
| 3 | no pong within 10 s ⇒ `timeout` + `ping_degraded` + seamless replacement (`Session.pinger`) | only if also no frames for 10 s | Python rule (`data_alive` stays in the event as a diagnostic) |
| 4 | path-watch lag sample = every 64th frame from the first | every 32nd + at least one per 250 ms | every 64th |
| 5 | Bybit entrances: line 0 `com@v4`, spare `com@v6`; line 1 `kz`, spare `bybitgeorgia.ge` | line 0 com/kz, line 1 kz/com (crossed spares) | as Python |

**Not equal to Python and kept (each has a reason, none is a measured improvement of path choice):**

| # | Python | Rust | Why kept |
|---|---|---|---|
| 6 | no connection-rate rule | ~~`--connect-rate` 10 dials/s per process~~ **removed in §5.12** | the only measurement was inside the Bybit 502 incident (12:11:50 UTC); clean cold start in §5.12 |
| 7 | replacement not accepted within 120 s: `switch_timeout`, the old session is still closed after the overlap | ~~`switch_failed`: old session kept~~ **removed in §5.12**: `switch_timeout` as Python (test `switch_timeout_closes_the_old_session_and_the_replacement_carries_on`) | — |
| 8 | no return to the primary entrance | ~~return after 10 min~~ **removed in §5.12** | — |
| 9 | probe window judged on a frame; `fails` counter per session; first resolved address only; ping 5 s after the previous pong | judged on a 200 ms tick; counter per line; all addresses in order; ping 5 s after the previous ping | no effect on path choice |

**Same constants (checked in the code):** `PROBE_S` 15, skip 4, p50 350, p99 1 500, min samples 5, `PROBE_MAX_REJECTS` 12, `LAG_LIMIT` 2 000 ms × 2 windows, skip 2 windows, ping 5 s / timeout 10 s, `STATS_PERIOD` 10 s, `GRACE` 5 s, gap 3 s, `FAIL_LIMIT` 3 short sessions (< 60 s), max age 23 h, overlap 20 s, switch wait 120 s, subscription pause 0.2 s; Binance layout A (book 32, trade 64, mark 1 per connection ⇒ 25 shards × 2 lines = 50 connections), Bybit ≤ 80 topics per connection (18 shards × 2 lines = 36). **Lane independence:** every line has its own session thread, its own dial (new ephemeral source port), its own RTT check and its own reject counter; the lines are staggered by `k / lanes × max_age`.

**Measurements of this step (soak on srv, Binance; all numbers from `events-*.jsonl`, `w4-*`):**
- *Build 13 (cutoff absolute, reserve 2 × connections), calm minutes 13:19:39–13:23:20 UTC:* 50 connections accepted, handshake RTT p10 / p50 / p90 / max **236 / 237 / 283 / 293 ms — none above 300**; 50 paths rejected, RTT 346–376 ms (p50 365, one 1 401). The two classes of TK-527 reproduce exactly (fast ≤ 293, slow ≥ 346) and the cutoff separates them. No timeouts in these minutes.
- *Same build, from ≈ 13:23:20 the route to Binance degraded as a whole* (independent of the collector: `curl` handshakes to `fstream.binance.com` from srv at 13:28 — 0.27–0.39 s, one 1.4 s; the same `curl` to Bybit 13–35 ms; Bybit soak: 0 events, lag p99 120 ms in the same window): 153 ping timeouts with `ping_degraded` and 61 `path_switch` in 4 min; the replacement storm and the startup had used ≥ 100 dials of the 200, so the reserve **switched the rejections off** and 164 of the next 200 connections were accepted on the slow path (handshake p50 377 ms, max 7.5 s); 28 of 50 connections alive at 13:28. Window `w4-binance` 13:27:24–13:30:25: book lag p50 / p99 / max 188 / 66 794 / 76 906 ms, trade 185 / 10 072 / 14 956, mark 217 / 31 034 / 31 093; ping p50 349 / p99 3 301 ms; 4 timeouts; **REST oracle 45 654 / 45 654 trades, 0 missing** (late, not lost). **Conclusion:** the reserve is a defect against the Python behaviour (rejections must not depend on the budget); removed from the default (build 14). The cutoff itself works only while fast paths exist (18–50 % of dials in these minutes).
- *Build 14 (Python behaviour: absolute 300 ms, no reserve; soak restarted 13:38:32 UTC, route still slow), 13:38:32–13:44 UTC:* 75 connections accepted, handshake RTT p10 / p50 / p90 / max **235 / 238 / 293 / 297 ms — none above 300, no slow path accepted**; 214 paths rejected (RTT p10 / p50 / max 312 / 352 / 1 382 ms) — only **26 %** of the dials found a fast path (50 % in the calm minutes). The dial rate is bounded by the 200 / 5 min budget (216 `connect_wait`), so the ramp is slow: **13 of 50 connections alive at the end of the window, the mark shard had none** (0 mark rows in the window; the pool was not fully covered).
- *Confirmation window `w5-binance`, 13:40:21–13:43:22 UTC (build 14; CPU of a test build ran on srv at the same time, collector CPU 0.21 core):* book lag p50 / p99 / max **120 / 12 005 / 14 374 ms**, trade 217 / 3 257 / 4 475 ms, mark — (no connection); ping p50 324 / p99 5 394 / max 8 868 ms (375 pings); events 2 `timeout` + `ping_degraded`, 6 `switch_failed`; sequence checks 0 / 0 / 0, trade ids 0 gaps; health `fresh` counters over 5 min of life: book 37 405 late (max 14.4 s), mark 5 961 (11.6 s), trade 7 904 (10.6 s); **REST oracle 40 229 / 40 229 trades, 0 missing** (25 symbols). Bybit, same window: 36 / 36 connections, lag p50 / p99 / max 107 / 119 / 204 ms, 0 events, 0 late records.
- **What this says (measured; the interpretation is inference):** the cutoff selects fast paths exactly as in TK-527 (accepted RTT ≤ 297 ms in both builds, nothing slow accepted in build 14), but while the route to Binance is slow as a whole (74 % of handshakes 311–376 ms, `curl` from srv 0.27–0.39 s) the Python logic cannot keep all 50 connections inside 200 dials / 5 min, and the connections that exist still stall (book lag p99 12 s) — slow-route stalls also hit paths that were fast at the handshake (ping p99 5.4 s). The Python baseline «0 late» was taken in the calm route state (50 % fast handshakes). Not tried, not claimed (outside «no new experiments»): a larger dial budget (exchange limit 300 / 5 min), separate budgets for rejected handshakes (a rejected TCP connect without TLS), spreading the source ports/addresses.

### 5.11 Route classes, Python-vs-Rust dial comparison, Rust alone (build 16) — 14:00–14:30 UTC

**Handshake RTT per destination IP × source port** (`tools/turbo/iprtt.py`, TCP only, 13:57–14:03 UTC, soak stopped; artifacts `ip1/srv.json`, `ip1/vps.json`):
- **srv → `fstream.binance.com`:** 192 connects, 16 destination IPs × 6 fixed source ports × 2 passes. Two modes: fast **241 ms** (95 samples, 49 %) and slow **372 ms** (93, 48 %), 4 above 400 ms. **Every IP shows both modes** (fast share 25–75 % with n = 12 each — consistent with one common ≈ 50 %); no IP is consistently fast or slow ⇒ **preferring or pinning destination IPs has nothing to gain** (not implemented). Source port: fast share 25–62 % per port (n = 32); weak. Class is partly fixed by the 5-tuple: the same (IP, port) in pass 2: fast → fast 36 of 40 (90 %), slow → slow 37 of 56 (66 %); consecutive samples are almost independent (P(fast | previous fast) 0.57). The fast share moves in time (first 20 s of the probe 0 %, then ≈ 50 %; the soak's own dials: 50 % at 13:19–13:23, 18 % at 13:23–13:27, 12–26 % at 13:38–13:44, 49 % at 13:57, 20 % over 14:05–14:15, 61 % at 14:24–14:27).
- **IPv6:** `fstream.binance.com` has no AAAA record (srv resolves only v4-mapped addresses) — nothing to test.
- **VPS Sofia 13.140.29.171, same script:** 384 connects, 32 IPs: **100 % ≤ 300 ms, p50 254 / p90 255 ms, one outlier 1 272 ms; single class** (same (IP, port) repeats its class 191 of 192). Reported as a measurement of the path, no change proposed.
- **Dial budget:** Binance default raised to **280 / 5 min** (`--connect-budget`, exchange limit 300 per IP; rejected dials are counted); Bybit stays 200.

**Python vs Rust at the dial (code diff, `dial.py` / `session.py` vs `net.rs` / `session.rs`):** RTT = kernel `tcpi_rtt` of the connected socket right after TCP connect (`TCP_INFO` offset 68), before TLS and WS upgrade, cutoff 300 ms — **identical**. Source port = kernel ephemeral port, no bind — identical. DNS = `getaddrinfo` at every dial, first returned address (Rust tries the next one only if connect fails) — identical in effect, every dial can land on another of the rotating IPs. No other dial-level difference found.

**Same-interval comparison** (14:05:25–14:09:27 UTC, one route state; my Python `binance_ws.py @ 3f8e1ce6` and the Rust soak ran side by side with 140 dials / 5 min each, then Python was stopped on the owner's order; the coordinator's Python alone, `/data/py-check`, is the reference: 60 accepted / 61 rejected dials, 0 late > 2 s, lag p50 146 / p99 816 ms, ping p50 311 / max 1 563 ms):
- Dials: Rust 25 accepted / 35 rejected (42 % fast); reference Python 60 / 61 (50 %); my Python, whole run to 14:15, 62 / 216 (22 %) against Rust 69 / 273 (20 %) — **the fast share depends on the minute, not on the collector**; the earlier «Rust 26 % vs Python 50 %» compared different minutes.
- Tails (same interval, both concurrent): book lag p50 / p99 / max Rust 127 / 2 310 / 19 509 ms vs my Python 144 / 715 / 21 559 ms; trade 125 / 1 050 / 3 425 vs 131 / 362 / 728 ms; ping p99 4 732 vs 1 169 ms. Rust had 9 `timeout`, 12 `path_switch`, 48 `switch_failed` (whole run) against 6 / 7 / 0 for Python — **the one structural difference left: Rust kept the stalled session and retried the replacement every 30 s (`switch_failed`), Python closes it after the overlap and keeps the new session retrying.** Not proven to be the cause of the tail.
- Change made: for replacements caused by a bad path or by age the Python behaviour is restored (`switch_timeout` event, old session closed after the overlap, no 30 s re-spawns); the keep-the-old-session rule stays only for the optional return to the primary entrance (Bybit; Binance has one entrance).

**Bybit entrances** (confirmation): `tools/collector/bybit_ws.py` at `3f8e1ce6` **and at the head of `g022-integrate`** has `entrances = [["stream.bybit.com@v4", "stream.bybit.com@v6"], ["stream.bybit.kz", "stream.bybitgeorgia.ge"]]`; the report of that branch (§4.2) chose «line 0 = `com@v4`, line 1 = `kz`». Primary entrances = v4 + kz (as asked); the spares (`com@v6`, `ge`) are the adapter config of that branch and only act after 3 short sessions.

**Rust alone, build 16 (alpha-07049750; Python off, no other Binance probe; soak started 14:21:03 UTC; window `w7-binance` 14:23:51–14:26:52 UTC; a collector-test build ran on srv at the same time):**

| | Rust, Binance |
|---|---|
| connections alive | 50 / 50 **at the end of the window; the window opened during the ramp** (see below) |
| lag book p50 / p99 / max | 123 / 419 / 825 ms |
| lag trade p50 / p99 / max | 120 / 332 / 640 ms |
| lag mark p50 / p99 / max | 205 / 335 / 1 494 ms |
| records late > 2 s (health counters, process life 14:21–14:27) | book 112 (max 2 123 ms), mark 0, raw 0, trade 0 |
| ping p50 / p99 / max | 295 / 476 / 769 ms |
| events in the window | 37 connected, 24 `path_rejected_rtt`, 0 timeouts, 0 path switches, 4 gaps |
| accepted handshake RTT in the window (n = 37) | p10 / p50 / p90 / max **236 / 238 / 253 / 256 ms**; 24 rejected (p50 347 ms) ⇒ 61 % fast in this minute |
| accepted handshake RTT, since start 14:21 (n = 74) | p10 / p50 / p90 / max **236 / 239 / 280 / 296 ms**; 304 rejected (p50 374 ms) ⇒ 20 % fast; none slow accepted |
| unit CPU mean / p95 / max, cores | 0.69 / 1.50 / 1.79 |
| sequence checks | trade `a` 0 gaps; book `u` not increasing 10 (two lines, arrival order) |
| **REST oracle, 25 symbols** | **58 470 of 69 365 trades, 10 895 missing (15.7 %), 0 extra, 0 mismatch** — all missing in 5 symbols (IDOLUSDT 6 232, KGENUSDT 1 937, BNBUSDT 1 560, JCTUSDT 967, BULLAUSDT 199) |

**Correction (found when the REST oracle finished): this window is NOT a steady state.** The window opened 168 s after the process start, but six shards (`trade01`, `trade02`, `trade03`, `trade05`, `trade06`, `book03`) got their first connection only at +306 s, i.e. 138 s into the window — the connection ramp is bound by the dial budget (280 / 5 min) at a fast share of 20–61 %. The recorded rows are on time (lag numbers above), but **the data of those shards before +306 s does not exist: 10 895 of 69 365 trades missing, exactly the symbols of the late shards**. Lag and `late` counters cannot show a missing connection; the REST oracle did. Consequence: from a cold start the full pool is not recorded for the first ≈ 5 min in this route state (the earlier 13:19 start got 50 connections in 4 min at a 50 % fast share). A steady-state window follows (`w8-binance`, same soak, ramp finished). One window is one sample, and the route was in a better phase (61 % fast) than before; the code changes cannot be credited for it. Over the whole first 8.5 min: 13 timeouts, 10 path switches, i.e. stalls still occur and are masked by the second line.

**Steady-state window `w8-binance`, 14:31:56–14:34:56 UTC (same soak, build 16, ramp finished, 50 / 50 connections, Rust alone) — the route was slow again:** dials in the window **18 accepted / 231 rejected (7 % fast)**; 11 of the 18 accepted had RTT > 300 ms (accepted by the rule «after 12 consecutive rejects the path is taken as is», `PROBE_MAX_REJECTS`, identical in Python — at a 7 % fast share 12 rejects in a row are likely). Book lag p50 / p99 / max **267 / 14 485 / 17 076 ms**, trade 230 / 629 / 1 123, mark 369 / 5 991 / 6 482; ping p50 415 / p99 7 659 / max 9 997 ms; records late > 2 s (counters since 14:21): book 177 274, mark 22 659, trade 0; 23 `timeout` + `ping_degraded`, 11 `path_switch`, 14 `switch_timeout`; unit CPU mean 0.86 core. REST oracle for this window was still running at the time of the report. **Reading (inference, no concurrent Python run exists — forbidden by the owner):** the outcome follows the route's fast share, not the collector: Rust was clean at 61 % fast (`w7`, lag p99 419 ms) and late at 7 % fast (`w8`, book p99 14.5 s); the Python reference (`/data/py-check`) was clean at 50 % fast. With the Python path logic restored, Rust has no remaining dial-level difference; what is left is that a slow-route minute defeats the absolute-cutoff scheme itself (fast paths too rare to find within 12 tries and 280 dials / 5 min).

### 5.12 Закрытие списка RETURN проверяющего (`docs/research/reviews/g022-turbo-review-2026-11-12.md`) — сборки a82b5a51 / d7182825

Текст этого раздела — по-русски (по заданию); остальной файл остался как написан. Время GMT+4, окна — UTC в скобках. Ветка `g022-turbo`, правки поверх 9780557e. Метки: **измерено** — число из прогона на srv с названным артефактом; **вывод** — следует из измеренного.

**Что сделано по пунктам (код — `src/collector/**`, тесты — `tests.rs`, коллектор-тесты 46 из 46):**

| # | пункт RETURN | что сделано | проверка |
|---|---|---|---|
| 1 | покрытие пула | `Budget::acquire(stops, uncovered)`: подключение шарда без единой сессии (`connected == 0`) берёт место раньше второй линии и замен покрытых шардов, пока такие ждут (`budget.rs`). Холодный старт: шард без сессии принимает путь **сразу, даже медленный** (RTT > 300 мс) без окна проверки — данные пишутся с первой секунды, событие `path_slow_accepted` (`why: uncovered`); сессия помечена `slow`, линия открывает замену (обычная проверка пути, бесшовное перекрытие) → `path_upgrade`; не принята за `switch_wait` → `upgrade_timeout`, старая сессия **остаётся** (разрыв хуже медленного пути), повтор через 60 с | тесты `budget_serves_an_uncovered_shard_before_a_covered_one`, `uncovered_shard_accepts_a_slow_path_at_once_and_upgrades_later`; холодные старты ниже |
| 2 | непроверенные отличия | **сняты**: возврат на основной вход (`entrance_return`, `restore_primary`, `spare_since`, `--return-after`), `switch_failed` с сохранением старой сессии, `--connect-rate`. Замена при испорченном пути — как в Python (`switch_timeout`: старая закрывается после перекрытия) | чистый холодный старт Bybit без `--connect-rate` (36 одновременных рукопожатий): 0 ошибок, 0 ответов 502 |
| 3 | id сделки Bybit | в строку `t` — хвостовой столбец `i` (UUID `execId`); у Binance столбца нет, строка кончается на `x` (файлы Binance побайтно прежние) | тесты `codec_roundtrip_exact`, `bybit_trades_batch_keys_and_raw_cases`; REST-проверка Bybit теперь идёт по самим файлам (`restcheck.py` берёт `i`), 3 478 сделок REST сопоставлены по `i` |
| 4 | бюджет при передаче | `--budget-file` (одно число, перечитывается раз в 2 с): сумма лимитов двух процессов на одном IP ≤ 300 / 5 мин — 140 + 140 на перекрытие, 280 новому после остановки старого; порядок записан в §6.4 и `docs/COMMANDS.md` | тест `budget_limit_can_be_lowered_and_raised_at_runtime`; живой прогон `/data/turbo-probe/v2-budget-file` (процесс на Binance, файл 100 → 60 → мусор «abc» → 150): журнал «connect budget 100 -> 60», «60 -> 150», мусор проигнорирован |
| 5 | тест `switch_timeout` | замена не принимает путь за `switch_wait` ⇒ `switch_timeout`, старая закрыта после перекрытия (`rotated`), замена несёт поток, `switch_failed` нет, `u` подряд | `switch_timeout_closes_the_old_session_and_the_replacement_carries_on` |
| 6 | сутки и `.ok` | (а) после закрытия суток по часам приём поднимается до полуночи (`rx_clamped`) — запоздавшая запись ушедших суток идёт в файл новых суток, закрытый файл не открывается; (б) сбой записи: тот же кодировщик zstd дописывает блок с места сбоя, файл не переоткрывается, `rows` не сбрасывается, оборванного кадра нет; блок отбрасывается только если из него кодировщик не принял ни байта | `writer_late_previous_day_record_does_not_reopen_the_closed_day`, `sink_write_failure_keeps_one_valid_frame_and_the_row_count` (4 отказа и записи по 7 байт: один целый кадр, 240 строк, `.ok` = 240) |
| 7 | мелочи | `handoff_max_us` — отдельный замер `push` в очередь (раньше то же число, что `busy_max_us`); `tcp_info`: ответ ядра короче 104 байт ⇒ `None` (а не RTT 0 = «быстрый путь»); шапка `net.rs`: три `unsafe`; flush по умолчанию — 100 мс, отчёт поправлен (§1, §2); линты: ветка давала 833 (одно срабатывание `bybit.rs:365` «indexing may panic» в коде ветки — исправлено), теперь см. §9 | `vps-check lint` 832 из 836; `vps-check clippy` (`-D warnings`) чисто на d7182825; `handoff_max_us` на новом soak (15:26–15:40 UTC, строки `stats-*`): Binance max 54 232 мкс при `busy_max_us` 67 386, Bybit 51 634 при 58 188 — числа разные (в 114 из 1 500 и 110 из 1 080 строк равны, когда `push` — вся занятость кадра); `ring_full` 0 |

**Холодный старт (измерено; артефакты `/data/turbo-probe/v2-cold-bn1`, `-bn2`, `-bb1`, `-bb2`; `tools/turbo/coldstart.sh` + `coverage.py` + `restcheck.py`):** свежий процесс, пустой корень, окно 3 мин от старта процесса (не от установившегося режима); по два старта на биржу, #1 — сборка 3b59b91a (код a82b5a51, 19:12), #2 — сборка 9f79c4d8 (код d7182825, 19:19), оба на обеих биржах одновременно; перед #1 soak биржи остановлен за 5+ мин (лимит биржи считается на IP), Python не запускался.

| | Binance #1 | Binance #2 | Bybit #1 | Bybit #2 |
|---|---|---|---|---|
| шардов × линий | 25 × 2 | 25 × 2 | 18 × 2 | 18 × 2 |
| первое соединение шарда, с от старта: мин / медиана / макс | 0,8 / 0,8 / 1,2 | 0,7 / 0,8 / 1,1 | 0,7 / 0,7 / 0,8 | 0,3 / 0,7 / 0,8 |
| шардов с соединением к 2 с | 25 из 25 | 25 из 25 | 18 из 18 | 18 из 18 |
| шард-секунд без живой сессии после первого соединения | **0,0** | **0,0** | **0,0** | **0,0** |
| первые сессии, принятые правилом «шард без соединения» с медленным RTT | 25 (349–376 мс) | 23 (338–368 мс) | 3 (505–528 мс) | 0 |
| `path_upgrade`: замена на проверенный путь, с от старта (в числе окно проверки 15 с и перекрытие 20 с) | 25 из 25, 37,3–57,5 (мед. 38,4) | 23 из 23, 37,2–41,9 (мед. 37,4) | 3 из 3, 36,7–37,2 | — |
| подключений за 3 мин: принято / отбраковано по RTT / ошибок | 80 / 33 / 5 (сброс соединения биржей) | 74 / 25 / 1 | 39 / 1 / 0 | 36 / 0 / 0 |
| `connect_wait`, `gap`, `switch_timeout`, `upgrade_timeout`, ответов 502 | 0, 0, 0, 0, 0 | 0, 0, 0, 0, 0 | 0, 0, 0, 0, 0 | 0, 0, 0, 0, 0 |
| `timeout` + `ping_degraded` | 19 | 16 | 0 | 0 |
| REST-оракул (25 символов равномерно по рангу), окно от старта процесса до +180 с: REST / файлы | 31 877 / 31 748 | 35 385 / 35 310 | 3 478 / 3 482 | 3 462 / 3 450 |
| пропущено до момента «все шарды с соединением + 3 с» (≤ 4,2 с от старта) | 129 | 75 | 4 | 12 |
| **пропущено после этого момента** / лишних / расхождений | **0** / 0 / 0 | **0** / 0 / 0 | **0** / 8 | **0** / 0 / 0 |
| `zstd -t` закрытых файлов | 25 из 25 | 25 из 25 | 18 из 18 | 18 из 18 |

При более строгой границе (старт + 2 с, шарды соединились к 0,7–1,1 с) Binance #2: после неё REST 35 208 сделок, пропущено 0 (`restcheck-2s.json`). Пропуски «до момента» — сделки, прошедшие до первого соединения своего шарда (первое соединение 0,3–1,2 с): холодный старт без них невозможен. Лишние у Bybit #1 — 8 записей одного символа (ARIAUSDT), все с временем, равным началу охвата REST (`span_ms[0]`: 1 794 496 504 038): `recent-trade` отдаёт последние 1 000 сделок и обрезает самую старую миллисекунду посередине — граничный эффект REST, не артефакт файла (**измерено**: время всех показанных лишних = левая граница охвата; то же в steady-окне, 4 записи). Все ответы Bybit без `502`: 36 одновременных рукопожатий (как в сломавшем замер 12:36 запуске) прошли без `--connect-rate`.

Сравнение с прежним: окно `w7` (сборка 16) при старте — 9 шардов из 25 без соединения 305–311 с, потеряно 10 895 из 69 365 сделок (**измерено**, проверка); теперь все 25 шардов с соединением к 1,2 с (оба старта: ≤ 1,2 с) и 0 потерянных сделок после первых 4,2 с (129 и 75 до него — до первого соединения шарда). **Вывод:** приоритет шарду без соединения и приём медленного пути на холодном старте убирают разрыв; цена — первые 37–57 с часть шардов идёт по медленному пути (запись с его лагом), затем бесшовно заменяется.

**Установившийся режим на soak новой сборки 9f79c4d8 (измерено, `/data/turbo-probe/v2-steady`, `tools/turbo/steady.sh`; soak с 19:26, окно 20:00:00–20:03:00 GMT+4, то есть на 34-й минуте):** health 20:04 — Binance 25 шардов, **минимум 2 соединения на шард**, всего 50; Bybit 18 шардов, минимум 2, всего 36. Шард-секунд без живой сессии за 38 мин работы soak (с 19:26): Binance **0,0**, Bybit **0,0** (`coverage-*.json`, модель сессий по событиям). REST-оракул Binance, 25 символов: REST 23 072 / файлы 23 072, **пропущено 0, лишних 0, расхождений 0**; Bybit, 25 символов: REST 3 824 / файлы 3 828, пропущено 0, расхождений 0, лишних 4 (граничная миллисекунда охвата REST, см. выше). События soak Binance за 38 мин: 120 подключений, 21 `path_slow_accepted` → 21 `path_upgrade` (холодный старт), 109 `path_rejected_rtt`, 34 `timeout` + `ping_degraded`, 38 `path_switch`, 9 ошибок соединения, `connect_wait` 0, `gap` 0, `upgrade_timeout` 0; Bybit: 36 подключений, ни одного другого события.

**Чего это не доказывает:** оба старта — при медленном маршруте до Binance (первые RTT 338–376 мс: 25 и 23 первых сессии приняты как медленные) и быстром до Bybit; на быстром маршруте до Binance первые сессии не нужно будет заменять — не проверялось; поведение при многоминутной деградации маршрута, когда быстрых путей мало (7 % в `w8`), этими окнами не проверено — для него остаётся общее ограничение схемы «абсолютный порог 300 мс + 12 отбраковок + бюджет 280».

### 5.13 Политика замены путей Binance: Rust против Python, лестница, A/B (ветка `bb/g-022-rust-collector-v3-thr_xehgamu7yy`, основа — a32ef34f)

Текст раздела — по-русски (по заданию). Время GMT+4, окна — UTC в скобках. Метки: **измерено** — число из названного файла на srv (`/data/turbo-probe/<метка>/`, копии в `docs/findings/g022-turbo-collector-2026-11-12/r13/`); **вывод** — следует из измеренного или из кода. Задание владельца (13.11): «начинай тесты на расте»; правило окон — «мы тестим по 3 минуты потому что у нас проблемы ДАЖЕ на 3х минутах»; лестница — «я думаю что тебе надо сделать такую же лесенку как делали когда ставили питон. лесенку»; параллельность — «а нахуй делать 18 подключений в момент времени?».

**Результат §5.13 (измерено / вывод):**

1. **На чистом маршруте Rust-коллектор чист на всём пуле, прежняя и новая версия одинаково.** Весь пул (512 символов, 25 шардов × 2 линии), три окна по 3 мин от старта процесса, один процесс за раз: новая версия (`--never-slow --lazy`) — книга p50 116 мс, p99 129 / 137 / 132 мс, поздних 0, обрывов 0, покрытие mark 100 %, REST после covered пропущено 0 в трёх окнах; прежняя версия (без флагов) — p99 124 / 124 / 123 мс, те же нули; `base1` (прежняя, одно окно) — p99 129 мс. Во всех окнах **все подключения быстрые (50 из 50, RTT ≤ 300 мс; в `base1` 235–288 мс)**: замен пути, улучшений и отложенных замен в окнах нет — флаги ничего не делали.
2. **Различия Rust и Python в выборе пути (таблица 5.13.2):** по коду их два — холодный старт (Rust принимает первый путь без проверки RTT и лага, Python проверяет) и улучшение медленной сессии `path_upgrade` (только Rust); приём медленного пути «как есть» после 12 отбраковок, закрытие старой сессии по `switch_timeout` и немедленная замена каждой линии у обоих одинаковы — их и меняют флаги. Константы сторожа лага, пинга, подписки и отбраковки по RTT совпадают; остальные различия — в метрике лага, дедупликации и модели исполнения.
3. **Лестница одного соединения (метод TK-531): предела приёма у Rust нет** — до 522 потоков книги лаг p99 119–217 мс против 144–1 469 мс у Python, CPU 11–15 % против 59–82 % ядра; но на книге `top` у Rust бывают окна, где соединение после подписки замирает или сбрасывается (3 из 24 окон + 3 из 3 в прогоне с 18 параллельными), причина не установлена.
4. **Три флага реализованы и покрыты тестами** (`--never-slow`, `--cold-probe` — тесты; `--lazy` — без отдельного теста), коммиты и сборка в 5.13.4; на плохом маршруте не проверялись: окон с малой долей быстрых путей не было, параллельный A/B владелец отклонил.
5. **Вердикт по заранее заданному правилу** (новая лучше, если покрытие ≥ прежней и p99 книги ниже в ≥ 4 из 5 окон): правило не подтверждается — покрытие равное (100 %), p99 новой ниже в 0 из 3 окон (на 5–14 мс выше при совпадающих событиях: флаги не срабатывали, различие — шум окон и сборок, не флагов; вывод, не проверено). **Какое поведение Rust делает его хуже Python на Binance, этими замерами не найдено:** при быстром маршруте хуже нет; при медленном — не измерено. Прежняя оценка (§5.12, «Чего это не доказывает») остаётся: поведение при многоминутной деградации маршрута не проверено.

**5.13.1 Базовое окно (измерено, `base1`)** — сборка `alpha-9f79c4d8` (та же, что в soak), свежий процесс на Binance (512 символов, 25 × 2 линии), бюджет 280, окно 3 мин от старта процесса: 02:03:23–02:06:23 (22:03:23–22:06:23 UTC). Один и тот же набор скриптов для всех окон: `tools/turbo/abrun.sh` → `abwin.py` (события, лаг на диске, покрытие) и `restcheck.py` (REST-оракул, 25 символов равномерно по рангу). Маршрут в эту минуту чистый: **50 подключений из 50 быстрые** (RTT рукопожатия ≤ 300 мс; 235–288, медиана 237 мс), принято 50, отбраковано 0, замен пути 0, `timeout` 0. Лаг книги на диске p50 **117** / p99 **129** / max 316 мс; поздних (> 2 с) **0** из 517 675 записей. Покрытие «символ × секунда» (ячейка — символ и секунда `E`, где есть хотя бы одна запись книги): у одного процесса нет опорного множества для сравнения, поэтому в базовом окне — доля ячеек сделок, в которых в ту же секунду есть и запись книги: 97,3 % из 21 790 ячеек (и при лаге ≤ 2 с — те же 97,3 %). REST-оракул Binance: REST 5 625 / файлы 5 608; пропущено до подключения последнего шарда (первое соединение шарда 0,7–1,0 с, +3 с) 17, **после — 0**, лишних 0, расхождений 0; `zstd -t` 25 из 25. **Вывод:** на чистом маршруте Rust-коллектор чист по лагу, покрытию и REST; политики замены на таком окне не различить — нужны окна с малой долей быстрых путей (доля плавает от 4–7 до 50–61 % по минутам, `binance-route-problem-2026-11-13.md`).

**5.13.2 Различия Rust и Python в выборе пути (вывод из кода: Python `tools/collector/wscore/{shard,session,dial,path,budget}.py` @ 3f8e1ce6, Rust `src/collector/{shard,session,path,budget,net}.rs` @ a32ef34f)**

| # | поведение | Python 3f8e1ce6 | Rust a32ef34f | флаг в этой ветке |
|---|---|---|---|---|
| 1 | холодный старт: шард без живой сессии | каждое подключение проверяется: RTT рукопожатия ≤ 300 мс (до 12 отбраковок), затем окно лага 15 с (p50 ≤ 350, p99 ≤ 1 500 мс); первые данные не раньше ≈ 15–20 с после старта — на старте дыра покрытия (`session.py:105-121`, `shard.py:52-69`) | `lone`: принимает сразу, **без проверки RTT и лага**, даже медленный (`path_slow_accepted`), сессия помечена `slow` и позже заменяется (`path_upgrade`, 37–57 с) (`session.rs:143-166`) | `--cold-probe` (как Python) |
| 2 | замена испорченного пути после 12 отбраковок подряд (RTT или лаг) | принимает любой путь, сторож лага остаётся | то же (`PROBE_MAX_REJECTS = 12`, `session_forever`) | `--never-slow`: замена продолжает отбраковывать (после каждых 12 — пауза 10 с) |
| 3 | замена не принята за 120 с | `switch_timeout`, старая сессия закрывается после перекрытия 20 с, замена продолжает | то же (`switch_timeout`, `shard.rs`) | `--never-slow`: для испорченного пути ждёт быстрый путь без срока, старая сессия остаётся |
| 4 | улучшение пути сессии, принятой медленной | нет | есть: замена каждые 60 с, пока не найден быстрый путь; не принята за 120 с — `upgrade_timeout`, старая остаётся | `--never-slow` делает и эту замену строгой |
| 5 | когда менять линию | сразу, как сессия `degraded` (p50 лага > 2 с два окна 10 с подряд, первые два окна не считаются; нет pong 10 с); состояние партнёра по шарду не смотрится | то же | `--lazy`: пока партнёр по шарду здоров, линия не заменяется (возрастная пересменка не откладывается) |
| 6 | сторож лага на ходу | каждый 64-й кадр сессии, пороги 2 000 мс / 2 окна | то же (константы совпадают, `path.rs`) | — |
| 7 | пинг | 5 с / 10 с, нет pong — `ping_degraded` и замена, не обрыв | то же | — |
| 8 | бюджет подключений | 200 за 300 с на процесс (общая память), ожидание шагами ≤ 5 с | 280 за 300 с, один процесс; шард без соединения берёт место раньше остальных; `reserve = 0` — отбраковка от бюджета не зависит, как в Python | — (`--connect-budget`) |
| 9 | пауза после отбраковки, переподключение при сбое | 0,5 с; backoff 1→30 с, 3 коротких сессии подряд — смена входа (у Binance один вход) | то же | — |
| 10 | подписка | пачки по 100, пауза 0,2 с | то же | — |
| 11 | модель исполнения | процесс на шард, asyncio (25 процессов, 1 ядро на процесс) | один процесс, поток на сессию, кольцевая очередь, писатель и диск — потоки шарда; CPU 0,73 против 2,03 ядра (§1) | — |
| 12 | метрика лага | `lag_in` каждого 64-го кадра **до** дедупликации; «поздних» — первая копия после дедупликации | каждая записанная запись **после** дедупликации | — |
| 13 | дедупликация | ключ ≤ последнего записанного по символу — отбрасывает пачку старых записей после паузы | множество идентичностей 120–240 с — пишет такую пачку как позднюю | — |

Размер вклада различий 1–5 в плохую минуту маршрута этим разбором не измеряется: для различий 2–5 он замерен в A/B (§5.13.5), для 1 — нет (§5.13.6).

**5.13.3 Лестница нагрузки на Rust тем же методом, что TK-531 (измерено: `l1`, `l3`; окна `docs/findings/g022-turbo-collector-2026-11-12/r13/ladder-rust-windows.csv`, Python — `docs/findings/tk531-binance-ladder-2026-11-12/windows.csv`, числа не пересчитывались)**

Метод — `alpha collect ladder` (`src/collector/ladder.rs`) повторяет `tools/collector/ladder.py` один в один: одно соединение, быстрый путь (RTT рукопожатия ≤ 300 мс, до 12 попыток подключения), `/ws` для книги и `/market/ws` для сделок и mark (`@bookTicker`, `@aggTrade`, `@markPrice@1s`), SUBSCRIBE пачками по 50 с паузой 0,25 с, окно 180 с, первые 5 с после подписки не считаются, лаг = приём − `E`, тишина = секунды окна без кадров, обрыв = ошибка чтения или закрытие, CPU = процесс целиком; группы `top` (по `quoteVolume24h`) и `mid` (со 150-й позиции), ступени 10…320 и весь пул 522 (`top`), три окна на ступень. Параллельность как у TK-531: 6 юнитов (вид × группа) одновременно, повторы подряд, пауза 20 с (`tools/turbo/rladder-seq.sh`) — прогон `l3` (160, 320, 522; 22:33–23:05 UTC). Ступени 10–80 взяты из первого прогона `l1` (22:09–22:33 UTC), где по ошибке шли 18 соединений одновременно (повторы параллельно) — в таблице помечены «18 параллельно»; по приказу владельца («а нахуй делать 18 подключений в момент времени?») ступени 160, 320, 522 прогнаны заново в режиме TK-531, а их окна из `l1` в таблицу не входят (справка — в разборе ниже).

Различия с методом TK-531, которые остаются: (1) приём — кодом коллектора (`net::dial`, `net::ws_connect`, tungstenite, rustls), без очереди и писателя; (2) другой рынок: Rust — ночь 02:10–03:05 GMT+4, Python — 11.11 пик 21:37–23:37 UTC, поэтому сообщ/с у Rust ниже (книга `top` 320: 2,3–2,7 против 3,3–3,9 тыс.) — ступени сопоставимы по числу потоков, не по нагрузке; (3) в запросе подписки Rust шлёт компактный JSON, Python — с пробелами.

Правило «Rust хуже» задано до прогона: медиана p99 лага Rust по окнам ступени выше максимума p99 Python, или обрывов у Rust больше, чем у Python.

| вид | группа | потоков | режим Rust | Python: сообщ/с | Rust: сообщ/с | Python: КБ/с | Rust: КБ/с | Python: пик | Rust: пик | обрывов Py / Rust | тишина >5 с Py / Rust | лаг p50 Py / Rust, мс | лаг p99 Py (по окнам) | лаг p99 Rust (по окнам) | CPU % ядра Py / Rust | Rust хуже |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| agg | mid | 10 | 18 параллельно | 3–5 | 3 | 0–1 | 0 | 25–40 | 22 | 0 / 0 | 24.0 / 38 | 115–116 / 116–117 | 128–256 | 121–134 | 0 / 0 | нет |
| agg | mid | 20 | 18 параллельно | - | 7 | - | 1 | - | 57 | 0 / 0 | - / 12 | - / 117 | - | 124–130 | - / 0 | нет |
| agg | mid | 40 | 18 параллельно | - | 14 | - | 2 | - | 59–60 | 0 / 0 | - / 0 | - / 116–117 | - | 122–161 | - / 0 | нет |
| agg | mid | 80 | 18 параллельно | - | 19 | - | 3 | - | 108–109 | 0 / 0 | - / 0 | - / 116 | - | 122–123 | - / 0 | нет |
| agg | mid | 160 | 6 юнитов | - | 28–36 | - | 4–6 | - | 137–145 | 0 / 0 | - / 0 | - / 116 | - | 119–124 | - / 0–1 | нет |
| agg | mid | 320 | 6 юнитов | - | 50–59 | - | 8–9 | - | 253–363 | 0 / 0 | - / 0 | - / 116 | - | 119 | - / 1 | нет |
| agg | top | 10 | 18 параллельно | 94–178 | 46 | 15–29 | 8 | 418–1085 | 330 | 0 / 0 | 0.0 / 0 | 116–118 / 117 | 151–158 | 125–150 | 5–7 / 1 | нет |
| agg | top | 20 | 18 параллельно | 95–458 | 88 | 15–74 | 14 | 315–3486 | 351 | 0 / 0 | 0.0 / 0 | 115–117 / 117 | 123–411 | 126–132 | 5–14 / 1 | нет |
| agg | top | 40 | 18 параллельно | 150–414 | 175 | 24–67 | 28 | 471–968 | 467–530 | 0 / 0 | 0.0 / 0 | 115–116 / 116–117 | 134–150 | 122–123 | 8–16 / 2 | нет |
| agg | top | 80 | 18 параллельно | 176–375 | 152 | 28–60 | 24 | 857–1203 | 890–891 | 0 / 0 | 0.0 / 0 | 115–117 / 116–117 | 142–146 | 123–126 | 10–15 / 2 | нет |
| agg | top | 160 | 6 юнитов | 236–445 | 150–210 | 38–71 | 24–34 | 881–1508 | 490–727 | 0 / 0 | 0.0 / 0 | 115–116 / 116 | 154–360 | 119–120 | 11–18 / 2–3 | нет |
| agg | top | 320 | 6 юнитов | 365–496 | 202–266 | 58–79 | 32–42 | 1150–2224 | 594–1447 | 0 / 0 | 0.0 / 0 | 115–116 / 116–117 | 147–376 | 119–152 | 17–22 / 3 | нет |
| agg | top | 522 | 6 юнитов | 400–532 | 241–450 | 64–85 | 38–72 | 932–2312 | 674–1934 | 0 / 0 | 0.0 / 0 | 115–116 / 116 | 144–310 | 119–120 | 17–23 / 3–5 | нет |
| book | mid | 10 | 18 параллельно | 30–41 | 36 | 5–6 | 6 | 179–205 | 199–200 | 0 / 0 | 0.0 / 0 | 116–122 / 117 | 192–401 | 129–143 | 2 / 1 | нет |
| book | mid | 20 | 18 параллельно | 57–112 | 84 | 9–18 | 14 | 301–511 | 674–706 | 0 / 0 | 0.0 / 0 | 116–117 / 117 | 128–414 | 128–161 | 3–6 / 1 | нет |
| book | mid | 40 | 18 параллельно | 160–280 | 144 | 26–45 | 23 | 666–1478 | 547–550 | 0 / 0 | 0.0 / 0 | 116–117 / 117 | 419–519 | 125–134 | 8–12 / 1–2 | нет |
| book | mid | 80 | 18 параллельно | 217–366 | 206 | 35–59 | 33 | 669–1183 | 1006–1036 | 0 / 0 | 0.0 / 0 | 116 / 116–117 | 129–390 | 129–153 | 12–17 / 2 | нет |
| book | mid | 160 | 6 юнитов | 337–470 | 251–347 | 54–76 | 40–56 | 1232–2091 | 1081–1347 | 0 / 0 | 0.0 / 0 | 116–127 / 116 | 176–387 | 121–173 | 16–20 / 3–4 | нет |
| book | mid | 320 | 6 юнитов | 626–813 | 444–676 | 101–131 | 72–109 | 2975–5074 | 1985–2998 | 0 / 0 | 0.0 / 0 | 116 / 116 | 414–587 | 119–127 | 24–30 / 5–6 | нет |
| book | top | 10 | 18 параллельно | 1238–1618 | 257–1484 | 198–258 | 41–237 | 5576–8858 | 5361–10647 | 0 / 1 | 0.0 / 146 | 119–130 / 117 | 352–514 | 158–206 | 32–34 / 2–8 | обрывы |
| book | top | 20 | 18 параллельно | 1301–1920 | 1944 | 207–306 | 310 | 4828–8730 | 10648–10683 | 0 / 0 | 0.0 / 0 | 116–119 / 117 | 234–537 | 130–141 | 34–41 / 9 | нет |
| book | top | 40 | 18 параллельно | 2496–2868 | 2791 | 398–457 | 446 | 9108–21943 | 8584–8756 | 0 / 0 | 0.0 / 0 | 118–121 / 117 | 624–805 | 139–180 | 49–53 / 11 | нет |
| book | top | 80 | 18 параллельно | 2211–2533 | 2354 | 353–404 | 377 | 6925–8943 | 10670–10706 | 0 / 0 | 0.0 / 0 | 116–164 / 117 | 282–801 | 142–194 | 48–56 / 11 | нет |
| book | top | 160 | 6 юнитов | 2677–3260 | 1833–2148 | 428–521 | 293–344 | 10516–14871 | 6481–15248 | 0 / 0 | 0.0 / 0 | 116–129 / 116–117 | 424–550 | 120–124 | 59–65 / 11–13 | нет |
| book | top | 320 | 6 юнитов | 3298–3915 | 2341–2743 | 528–626 | 375–440 | 13311–19028 | 9622–10069 | 0 / 0 | 0.0 / 0 | 119–173 / 116 | 1017–1469 | 121–122 | 66–69 / 13–14 | нет (окон без данных у Rust: 1) |
| book | top | 522 | 6 юнитов | 5024–5632 | 2661–2868 | 805–902 | 427–460 | 18167–21276 | 8737–12007 | 0 / 1 | 0.0 / 0 | 118–122 / 116–117 | 429–999 | 122 | 81–82 / 14–15 | обрывы (окон без данных у Rust: 1) |
| mark | mid | 10 | 18 параллельно | 10 | 10 | 2 | 2 | 10 | 10 | 0 / 0 | 0.0 / 0 | 180–183 / 184–185 | 201–232 | 204–206 | 0–1 / 0 | нет |
| mark | mid | 20 | 18 параллельно | 20 | 20 | 3 | 3 | 20 | 20 | 0 / 0 | 0.0 / 0 | 181–184 / 184 | 200–263 | 205–209 | 1 / 0 | нет |
| mark | mid | 40 | 18 параллельно | 40 | 40 | 6–7 | 6 | 40–77 | 40 | 0 / 0 | 0.0 / 0 | 180–182 / 184 | 203–219 | 205–207 | 1 / 0 | нет |
| mark | mid | 80 | 18 параллельно | 80 | 80 | 13 | 13 | 80–89 | 80 | 0 / 0 | 0.0 / 0 | 182–183 / 184 | 208–211 | 205–206 | 1 / 0 | нет |
| mark | mid | 160 | 6 юнитов | 160 | 160 | 26 | 26 | 160 | 160–250 | 0 / 0 | 0.0 / 0 | 183–186 / 182–183 | 215–230 | 201–208 | 2 / 0 | нет |
| mark | mid | 320 | 6 юнитов | 320 | 320 | 52 | 52 | 320 | 320–357 | 0 / 0 | 0.0 / 0 | 189–195 / 182–183 | 225–255 | 203–204 | 3 / 1 | нет |
| mark | top | 10 | 18 параллельно | 10 | 10 | 2 | 2 | 10 | 10 | 0 / 0 | 0.0 / 0 | 177–178 / 176–177 | 203–242 | 197–198 | 0 / 0 | нет |
| mark | top | 20 | 18 параллельно | 20 | 20 | 3 | 3 | 20 | 20 | 0 / 0 | 0.0 / 0 | 176–178 / 180 | 200–295 | 202–203 | 1 / 0 | нет |
| mark | top | 40 | 18 параллельно | 40 | 40 | 7 | 7 | 40–80 | 40 | 0 / 0 | 4.0 / 0 | 178–186 / 179–180 | 202–258 | 202–204 | 1 / 0 | нет |
| mark | top | 80 | 18 параллельно | 80 | 80 | 13 | 13 | 80 | 80 | 0 / 0 | 0.0 / 0 | 178–194 / 182–184 | 202–340 | 203–217 | 1–2 / 0 | нет |
| mark | top | 160 | 6 юнитов | 160 | 160 | 26 | 26 | 160–317 | 160–243 | 0 / 0 | 0.0 / 0 | 182–186 / 182–183 | 206–240 | 204–208 | 2 / 0 | нет |
| mark | top | 320 | 6 юнитов | 320 | 320 | 52 | 52 | 320 | 320 | 0 / 0 | 0.0 / 0 | 188–192 / 182–183 | 242–254 | 203–208 | 3 / 1 | нет |
| mark | top | 522 | 6 юнитов | 522 | 522 | 85 | 85 | 522–944 | 522 | 0 / 0 | 0.0 / 0 | 199–221 / 183–184 | 253–368 | 202–205 | 4 / 1 | нет |

**Разбор лестницы (измерено / вывод / догадка):**

- **Измерено, `l3` (режим TK-531), ступени 160–522, 45 окон:** 43 окна чистые — обрывов 0, тишины > 5 с 0, лаг p99 Rust 119–217 мс против 144–1 469 мс у Python на тех же ступенях (книга `top` 160 / 320 / 522: 120–124 / 121–122 / 122 против 424–550 / 1 017–1 469 / 429–999), CPU процесса Rust 11–15 % ядра против 59–82 % у Python на книге `top`. **2 окна без данных, оба — книга `top`, оба — третий повтор:** 320 / r3 (22:57:47–23:00:54 UTC): подписка принята частично (3 ответа из 7), дальше ни одного кадра, TCP `retransmits 8, backoff 8, rto 120 000 мс, unacked 8` (путь замер, обрыва нет); 522 / r3 (23:01:14, окно закончилось через 5 с): сброс соединения (`peer closed connection without sending TLS close_notify`), получено 8 ответов из 11. По заранее заданному правилу «Rust хуже»: книга `top` 522 (обрыв 1 против 0) и 320 (окно без данных, обрывов 0 — правило их не ловит, но это отказ).
- **Измерено, `l1` (18 параллельно), ступени 10–80:** отказ один — книга `top` 10, r1: сброс соединения на 31-й секунде (`Connection reset without closing handshake`, 257 сообщ/с вместо 1 484, тишина 146 с); остальные 11 окон этих ступеней чистые, p99 130–194 мс.
- **Измерено, `l1`, книга `top` 160 — ноль данных в трёх окнах** (22:22:57–22:26:04 UTC, в таблицу не входят): r1 — ответ на первую подписку получен, дальше ни одного кадра, TCP `retransmits 8, backoff 8, rto 120 000 мс, unacked 8`; r2, r3 — сброс через ≈ 2 с после подписки; три разных адреса AWS; соседние юниты тех же минут (книга `mid`, сделки, mark) — без отказов. В `l3` та же ступень — три чистых окна (1 833–2 148 сообщ/с, p99 120–124 мс). Мой `cargo test` на srv шёл в те же минуты (22:21–22:25 UTC).
- **Итого по книге `top`:** Rust — окон 24 (10–80 из `l1`, 160–522 из `l3`), отказов 3 (10 / r1, 320 / r3, 522 / r3) + 3 окна `l1` ступени 160; у остальных видов и групп — 0 отказов из 96 окон. Python (TK-531) — в 21 окне книги `top` 0 отказов, но в ступени 522 книги два из трёх окон 11.11 (23:10, 23:13) были пустыми при принятой подписке — тот же симптом (отчёт TK-531, §1). **Вывод:** при живом соединении у Rust лаг и CPU заметно лучше Python (проверено до 522 потоков книги), а предела «по числу потоков» нет; но на самом нагруженном виде (книга `top`) бывают окна, где соединение после подписки замирает или сбрасывается, и у Rust их заметно чаще, чем у Python (3–6 из 24 против 0 из 21 в сопоставимых ячейках) — различие не объяснено, причину нужно искать в подключении и подписке. **Догадка (не проверена):** зонд не читает сокет, пока шлёт подписку пачками (4–11 пачек × 0,25 с = 1–3 с без чтения при 0,5–1 МБ/с входящих данных книги) — окно приёма закрывается, у Python чтение идёт в цикле событий независимо от отправки; ровно так же устроена подписка в коллекторе (`session::body`: отправка и пауза 200 мс без чтения), но там пачка одна и данных за паузу мало. Проверка — ступени книги `top` 160–522 с чтением во время подписки; не запускалась (приказ владельца 13.11 ~03:10: больше прогонов не начинать до нового задания).

**5.13.4 Изменения (флаги `alpha collect run`; без флагов поведение прежнее — старый и новый режим из одного бинаря, сборка `alpha-ab-1`, md5 2071406628e6…)**

| коммит | флаг | что делает | тест |
|---|---|---|---|
| `ea7d7c49`, `c2670ff6` | `--never-slow` | замена испорченного (или принятого медленным) пути не принимает медленный путь «как есть» после 12 отбраковок: продолжает отбраковывать (после каждых 12 — пауза 10 с, чтобы не съесть бюджет подключений); замена испорченного пути ждёт быстрый путь без срока, **старая сессия остаётся** — разрыв хуже медленного пути; возрастная пересменка (23 ч) прежняя | `never_slow_replacement_keeps_the_old_session_until_a_fast_path_exists`: все пути «медленные», 14+ отбраковок подряд, ни `path_upgrade`, ни `path_switch`, ни `rotated`, ни `switch_timeout`, старая сессия несёт поток |
| `3300a05c` | `--lazy` | линия не заменяется, пока её партнёр по шарду здоров (его сессия открыта, не `degraded`, не медленная); событие `replace_deferred`; когда партнёр тоже испортился или оборвался — замена как обычно; возрастная пересменка не откладывается | отдельного теста нет (проверка — живой прогон A/B, события `replace_deferred`) |
| `6a6a8a20` | `--cold-probe` | шард без соединения проверяет путь, как Python: отбраковка по RTT (до 12 раз) и окно лага до приёма; без флага — приём сразу | `cold_probe_rejects_a_slow_path_before_the_first_data`: 12 отбраковок до первых данных, данные не раньше 5 с |

Коллектор-тесты: 48 из 48 (`bash tools/vps-check.sh . test collector`: srv 22:25 UTC, Sofia `CHECK_HOST=vps` 23:10 UTC); `clippy -D warnings` на Sofia чисто. Прочее в ветке: `collect ladder` (`src/collector/ladder.rs`) — лестница TK-531 на Rust, `tools/turbo/{rladder-seq.sh,ladder_cmp.py}`; окна — `abrun.sh` (базовое окно), `steps.sh` + `abwin.py` + `stepsum.py` + `stepstable.py` (ступени).

**5.13.5 Ступени полного коллектора, один процесс за раз (измерено: `new1`, `old1`; `tools/turbo/steps.sh`)** — приказы владельца 13.11: «просто проводим тестирование по плану. от меньшего к большему как шли», «пока у нас все монеты сразу не встанут». Параллельный прогон старой и новой версии рядом (`ab1`, окно 1, оба процесса одновременно) владелец отклонил: «нет это хуйня мы не тестируем так» — те данные не используются (одно окно, оба чистые, лаг p99 127 / 128 мс, быстрых 50/50).

Схема. Один процесс коллектора за раз (`collect run --lanes 2`, бюджет подключений ≤ 280 с учётом лимита биржи 300 за 5 мин: скрипт вычитает собственные подключения последних 5 мин), набор — N самых ликвидных символов пула по `quoteVolume24h` (N = 10, 20, 40, 80, 160, 320, весь пул 512), 3 окна по 3 мин на ступень, каждое окно — новый процесс (холодный старт, окно считается от старта процесса), пауза 20 с. Новая версия — `alpha-ab-1` с `--never-slow --lazy`; после последней ступени — прежняя версия (`alpha-9f79c4d8`, без флагов) на всём пуле, 3 окна. `soak-binance` остановлен на всё время. Скрипт окна один на все окна: `coverage.py`, `abwin.py`, `restcheck.py`, CPU по cgroup (процент ядра за 165 с), вердикт `stepsum.py`.

Критерий «чисто» (задан до прогона): **0 обрывов** (событие `error` у сессии, у которой было `connected`; отказы самого подключения считаются отдельно — `connect_errors`), **покрытие mark 100 % после «все шарды с соединением + 3 с»** (`!markPrice@arr@1s` даёт каждый символ каждую секунду: разрыв > 1,5 с между записями символа пула считается пропуском), **пропущено сделок после covered 0** (REST-оракул, 25 символов равномерно по рангу), **0 шард-секунд без живой сессии**, все шарды подключились. Покрытие «символ × секунда» по книге абсолютно измерить нельзя (книга — по событиям, REST-оракула у неё нет); справка — доля ячеек сделок с записью книги в ту же секунду (`book_given_trade` в `win.json`, 97 % в чистом окне `base1`).

| набор | символов | окно | шардов | быстрых подкл. | лаг книги p50 / p99 / max, мс | поздних >2 с | обрывов (`error`) | покрытие mark после covered | REST пропущено после covered (из всех сделок REST) | шард-с без сессии | CPU, % ядра | замен пути / улучшений / отложено | вердикт |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| new1 | 10 | 1 | 3 | 100 % (6/6) | 116 / 122 / 219 | 0 | 0 | 100.000 % | 0 из 10793 | 0.0 | 16 | 0 / 0 / 0 | чисто |
| new1 | 10 | 2 | 3 | 100 % (6/6) | 116 / 148 / 308 | 0 | 0 | 100.000 % | 0 из 7643 | 0.0 | 13 | 0 / 0 / 0 | чисто |
| new1 | 10 | 3 | 3 | 100 % (6/6) | 116 / 135 / 349 | 0 | 0 | 100.000 % | 0 из 7836 | 0.0 | 14 | 0 / 0 / 0 | чисто |
| new2 | весь пул | 1 | 25 | 100 % (50/50) | 116 / 129 / 347 | 0 | 0 | 100.000 % | 0 из 6847 | 0.0 | 94 | 0 / 0 / 0 | чисто |
| new2 | весь пул | 2 | 25 | 100 % (50/50) | 116 / 137 / 321 | 0 | 0 | 100.000 % | 0 из 10186 | 0.0 | 96 | 0 / 0 / 0 | чисто |
| new2 | весь пул | 3 | 25 | 100 % (50/50) | 116 / 132 / 378 | 0 | 0 | 100.000 % | 0 из 6888 | 0.0 | 95 | 0 / 0 / 0 | чисто |
| old1 | весь пул | 1 | 25 | 100 % (50/50) | 116 / 124 / 367 | 0 | 0 | 100.000 % | 0 из 4884 | 0.0 | 95 | 0 / 0 / 0 | чисто |
| old1 | весь пул | 2 | 25 | 100 % (50/50) | 116 / 124 / 339 | 0 | 0 | 100.000 % | 0 из 2964 | 0.0 | 84 | 0 / 0 / 0 | чисто |
| old1 | весь пул | 3 | 25 | 100 % (50/50) | 116 / 123 / 343 | 0 | 0 | 100.000 % | 0 из 3515 | 0.0 | 85 | 0 / 0 / 0 | чисто |

Примечания к таблице (измерено): `new1` — ступень 10 символов (3 окна), ступень 20 прервана, ступени 20–320 пропущены по слову владельца через координатора; `new2` — новая версия на всём пуле; `old1` — прежняя версия (`alpha-9f79c4d8`, без флагов) на всём пуле. CPU — процент одного ядра за 165 с окна от 10-й до 175-й секунды, то есть вместе с холодным стартом (в установившемся режиме soak §1: 0,6–0,8 ядра). Различие p99 новой и прежней версии (129–137 против 123–124 мс) — при 100 % быстрых путей и без единого события замены; объяснено как шум (вывод), отдельно не проверялось. Копии: `docs/findings/g022-turbo-collector-2026-11-12/r13/{new1,new2,old1,base1}/` (`sum.json`, `win.json`, `rest.json`, `cov.json`, `status.txt`).

**5.13.6 Отклонения от задания и что не доказано**

- **Параллельный A/B не сделан:** задание требовало два процесса одновременно (половинные бюджеты, ≥ 5 окон); владелец отклонил («нет это хуйня мы не тестируем так») после первого окна `ab1` — оно в выводах не используется. Вместо него — ступени одного процесса (5.13.5). Поэтому флаги **не сравнивались на одних и тех же минутах при плохом маршруте**.
- **Ступени 20–320 пропущены** (слово владельца через координатора), сделаны 10 и весь пул.
- **Лестница:** ступени 10–80 прошли при 18 одновременных соединениях (мой просчёт, не метод TK-531) и помечены; в минуты `l1` (22:15–22:17 и 22:21–22:25 UTC, ступени 20–40 и 80–160) на srv шли мои `cargo test` — это нарушение правила «без сборок во время окон»; ступени 160–522 переделаны в `l3` без сборок на srv. Нагрузка лестницы Rust (ночной рынок) ниже, чем у Python (пик 11.11).
- **Не проверено:** (1) поведение флагов на маршруте с малой долей быстрых путей — ни одного окна с долей < 100 %; (2) `--cold-probe` вживую — только модульный тест; (3) `--lazy` — без модульного теста, только компиляция и логика; (4) отказы книги `top` в лестнице — причина не установлена, гипотеза про чтение сокета во время подписки не проверялась; (5) сутки и ротация 23 ч с новыми флагами.
- **Состояние srv:** `soak-binance` остановлен с 02:03 GMT+4 и не перезапущен (приказ координатора: до конца прогонов); `soak-bybit`, страницы здоровья не тронуты; юнитов проб нет.
- **Гейт:** коллектор-тесты 48 из 48 (srv 22:25 UTC, Sofia 23:10 UTC), `clippy -D warnings` на Sofia чисто после коммита `431d94b7` (бинарь `alpha-ab-1` собран до него: расхождение только в стиле, поведение то же).

## 6. Cut-over plan (production) and what is needed

1. **Review** of branch `g022-turbo` by Opus (coordinator). Gate on the merged tree: `bash tools/vps-check.sh <tree> all` (fmt, clippy `-D warnings`, lint budget, full tests) — last run result in §9.
2. **Binary:** PGO build per В-32 — `tools/turbo/build-pgo.sh` (srv has `llvm-profdata`; rustc 1.93 crashes in thin-LTO with `-Cprofile-generate`, so the PGO pair is built with `LTO=off`; result and A/B in §9). The profile is tied to the source tree: rebuild after any hot-path change. Install as `/opt/alpha-turbo/alpha` (+ md5).
3. **Units:** `tools/systemd/bnws-turbo.service`, `bbws-turbo.service` (same roots and health files as the Python units; `KillMode=mixed`, one SIGTERM). Python units stay disabled; rollback = swap them back.
4. **Hand-over without a hole (corrected in §5.12):** a restart loses a few seconds (measured: 1 279 trade ids over 299 symbols at a soak restart) and may repeat the last state. To avoid the hole start the new process first and stop the old one **only when the health of the new one shows every shard with `connected` ≥ 1** (measured cold start: every shard connected within 1.2 s, sessions accepted on a slow path are replaced within 37–57 s — so with the new code «≈ 60 s» holds, with the old code it took up to 311 s; times in §5.12). Both write their own `<shard>-<run>.tsv.zst`; readers drop duplicates by identity (trade `a` / Bybit `i` column, book `u`/`seq`, mark `E`). **Dial budget:** both processes run with `--budget-file` (one number, re-read every 2 s): 140 + 140 during the overlap, 280 for the new one after the old stops — the sum of the two limits never exceeds the exchange's 300 / 5 min per IP (each process counts its own dials, so together they stay within the sum in any window; unit test `budget_limit_can_be_lowered_and_raised_at_runtime`). Without an overlap (stop, start) there is no budget issue but a hole of the cold-start length.
5. **Acceptance:** 3-min window (`probe_rust.sh` / `accept_ws.py`: 0 events, 0 `*_not_increasing` except Bybit book, 0 trade-id gaps), `check_files.py`, `restcheck.py` against the exchange; hour check `accept_hour.py` (compatible).
6. **Health page:** `tools/collector_health` reads schema 1 and 2 (3 new columns; yellow row on ring overflow). A copy runs on srv `:8089` against the soak (`/data/turbo-probe/health-soak`); production page `:8088` needs the updated `serve.py/page.html` (they are in this branch on top of g022-integrate).
7. **Readers:** book rows of Bybit are in arrival order and carry `seq` in the `u` column; trade rows have new trailing columns `x` and (Bybit only) `i` — the exchange execution id (UUID), which is what lets a reader drop duplicates between the files of two processes and between runs; Binance mark now has structured rows for all USDⓈ-M symbols. Python readers (`wscore.codec.decode`) read these files unchanged (extra column ignored). Anything that assumed «per-symbol keys strictly increase» must sort by key (`docs/collector-ws-format.md`).

## 7. What is NOT verified (honest list)

- **A full day on the exchange:** day rollover at 00:00 UTC and Binance's 24 h disconnect have not happened yet on a live run (the soak started 15:37 and will cross midnight UTC at 04:00 GMT+4 and 23 h at 14:37); rollover by clock and rotation are covered by unit tests (`writer_closes_ended_day_by_clock…`, `writer_rotates_days…`, replacement tests) and the live short-age run (§5.5).
- **Order book fidelity** beyond key/identity: there is no REST history for L1; fidelity of `b` rows (bid/ask strings) is checked only by unit tests with real frames and by the parser's exact-key-set rule.
- **Bybit trade completeness** is against REST `recent-trade` (last 1 000 per symbol) and the capture, not against a full history; Bybit has no equivalent of Binance's `aggTrades` window.
- **TLS handshake cost / CPU at the real feed with PGO** — see §9.
- **Disk-full behavior:** a failed block write is resumed by the same zstd encoder from the point of failure once a second (frame intact, `.ok` count kept; unit test `sink_write_failure_keeps_one_valid_frame_and_the_row_count` with refusals and partial writes); a block is dropped and counted (`sink_blocks_dropped`) only if the encoder accepted none of it after 60 tries; not exercised on a real full disk.
- **Python comparison under equal load** exists only for the 3-min windows at 13:26–13:35 (§1, §5.2); after the owner stopped the Python units no A/B is possible.

## 8. Deviations from the brief

- Step 3 «byte-for-byte gate» dropped by the owner (13:12); replaced by exchange-side checks (§3).
- Architecture variants were compared at micro level only (parser ×3, hand-off, zstd level/flush); an async tokio variant was **not built** (reasons in §2; **inference**, not measured).
- `tools/collector/` (Python) untouched; `tools/collector_health` changed (schema 2) on top of g022-integrate.
- Branch is not based only on `bb/tk-532-…`: fast-forwarded to `g022-integrate` (3f8e1ce6) and merged with `origin/master` (74ba6fcd) at the coordinator's request.
- Report language: this findings file is English per the owner's order; repo reference docs (`collector-ws-format.md`, `COMMANDS.md`) are Russian.

## 9. State at report time

- **Branch** `g022-turbo`, merged with `origin/master` 74ba6fcd; head — see the report message.
- **Gate:** per the owner's order the full local suite is not run; local = `cargo fmt --check` + `bash tools/vps-check.sh <tree> test collector` (**46 collector tests, all passed on srv at d7182825** — §5.12 added: `switch_timeout`, uncovered-shard slow path, budget priority and runtime limit, late record after clock close, write failure; the test of the removed return-to-primary logic is gone) + `vps-check clippy` (`-D warnings`, clean) + `vps-check lint`; GitHub CI (GC + Quality) is the full gate — status in the report message. Earlier full runs of `vps-check all` on the merged tree were green: rc 0, clippy `-D warnings`, lint budget 832 of 836 on the merged tree; the review measured 833 on `g022-turbo` @ 9780557e — the extra hit was `bybit.rs:365` («indexing may panic», in the branch's own code), fixed in d7182825: **lint now 832 of 836** (measured with `vps-check lint` on d7182825; `master` 71aa6654 also 832), 1 246 tests.
- **Soak** (В-147): `soak-binance`, `soak-bybit` (full pools, 2 lines, data `/data/turbo-probe/soak-*/data`, health `…/health.json`, page `http://5.231.25.224:8089/`), restarted on every build with `tools/turbo/soak.sh start <binary>`; first start 15:37, current build alpha-9f79c4d8 (= code d7182825, md5 9f79c4d8f3a2…), restarted 19:26 GMT+4 (15:26:15 UTC) after the cold-start windows of §5.12 (the previous soak, alpha-07049750, ran 18:21–19:06). Day rollover at 04:00 GMT+4 and the Binance 23 h rotation at ≈ 14:37 will land in this data; analyse with `check_files.py`, `restcheck.py`, `accept_ws.py`, health.
- **PGO (В-32): blocked by the toolchain.** `-Cprofile-generate` makes rustc 1.93.1 (LLVM 21) segfault in libLLVM (`opt cgu` / `lto cgu`, same address, dmesg) — reproducible with LTO off, with value profiling off, and **on the unmodified `origin/master` tree** (so not caused by this branch). `tools/turbo/build-pgo.sh` is ready (works once the instrumented build compiles); options for the coordinator: another toolchain for the instrumented build, or accept a non-PGO release (the collector uses 0.6–0.8 core of 8 and ≥ 4× rate margin, so the gain would be CPU only).
