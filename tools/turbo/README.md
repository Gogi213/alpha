# tools/turbo — probes for the Rust collector (G-022 / В-148)

Run on srv (5.231.25.224) as background systemd units, never in the ssh session (В-147). Copy: `tar -cf - tools/collector tools/turbo | ssh … tar -xf - -C /data/turbo-probe/src`.
Python needs `zstandard` — use `/root/bnws-venv/bin/python`. Everything writes under `/data/turbo-probe/<tag>/`.

| script | what it answers |
|---|---|
| `probe.sh <binance\|bybit> <tag> <alpha> [args]` | 3-minute window on the live exchange: Rust collector in its own directory, `winmon.json` (CPU / Recv-Q / CPU wait of the unit via cgroup + `ss`), `accept-rust.json` (`accept_ws.py`), `zstd -t` of every closed file. If a Python collector is running it is measured in the same window (`accept-python.json`) |
| `winmon.py` | sampler: CPU (cgroup `cpu.stat`), CPU wait (PSI), kernel Recv-Q of sockets per unit |
| `ladder.sh <rust\|python> <tag> <alpha> <rate>…` + `chk_synth.py` | load ladder on ONE connection from the synthetic source (`alpha collect synth`): rows/s achieved, gaps/duplicates in `u`, lag, pauses, ring depth, Recv-Q, CPU; `py_baseline.py` runs the Python core on the same source |
| `summarize.py <tag dir>` | Python-vs-Rust table of a `probe.sh` window |
| `check_files.py <root>` | acceptance of recorded files for the identity-dedup format: `zstd -t`, `.ok` vs rows, `closed` last, duplicates (must be 0), out-of-order (informational), Binance trade id gaps |
| `restcheck.py` | completeness/fidelity against the exchange's own REST history (Binance aggTrades from files; Bybit recent-trade from a capture, or from the files themselves — trade column `i` is the execId). `--spread` picks symbols evenly over the ranking (every shard is represented); `--covered-ms` adds `missing_after_covered` (cold start: trades before every shard had a session are unavoidable) |
| `coldstart.sh <binance\|bybit> <tag> <alpha>` + `coverage.py` | cold-start window: fresh process, empty root, 3 min from the process start; `coverage.json` = time to first connection per shard, seconds a shard had no live session, dial-budget events; `restcheck.json` over the same window. Stop the soak of that exchange first and wait 5 min (the exchange counts dials per IP) |
| `steps.sh <tag> <binary> "<flags>" "<steps>"` (`EXCH=binance\|bybit\|okx\|bingx`, `ROOT`, `TOOLS`, `RESTPASS=defer`) + `steppools.py`, `stepsum.py`, `abwin.py`, `stepstable.py` | step ladder of the full collector, one process at a time: top-N of the pool by 24h volume, 3 windows x 3 min per step, one status line per window (`status.txt`), stops at the first not clean window. `RESTPASS=defer`: collection windows back to back, the REST check is not run between them |
| `restpass.sh <tag> "<steps>"` | REST check of all windows of a deferred-REST ladder, after it; full verdict lines in `status-final.txt`. OKX oracle in `restcheck.py okx`: `/market/history-trades` per trade against the recorded aggregate ranges `f..l` (missing / mismatch / extra), first page by time, the rest by `tradeId`, requests paced at the exchange limit |
| `bxsnap.py <pool.json> <t0> <t1> <out.json.gz>` | BingX only: REST keeps no trade history (last 1000 trades of a symbol), so `steps.sh` (EXCH=bingx) takes a REST snapshot of every pool symbol right after the window end (busiest first, 40 requests/s); `restcheck.py bingx --snap` compares it with the files (missing / mismatch / extra by id) and checks id continuity over the whole pool (BingX ids are contiguous per symbol); `stepsum.py` fails on missing, mismatch and id gaps |
| `keycmp.py` | key sets of two independent receivers (Python vs Rust) on the same streams, exchange-time window |
| `lanecmp.py` | from `ALPHA_COLLECT_CAPTURE=1` captures: coverage per line, skew between lines, what the old rule «key ≤ last» loses vs identity dedup |

`ALPHA_COLLECT_CAPTURE=1` (env of the collector process) writes every received frame of every session to `<root>/capture-<shard>.tsv` — diagnostics only (≈ 10 MB/min/shard at Bybit rates, ≈ 100 MB/min for all Binance shards).
