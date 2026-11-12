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
| `restcheck.py` | completeness/fidelity against the exchange's own REST history (Binance aggTrades from files; Bybit recent-trade from a capture) |
| `keycmp.py` | key sets of two independent receivers (Python vs Rust) on the same streams, exchange-time window |
| `lanecmp.py` | from `ALPHA_COLLECT_CAPTURE=1` captures: coverage per line, skew between lines, what the old rule «key ≤ last» loses vs identity dedup |

`ALPHA_COLLECT_CAPTURE=1` (env of the collector process) writes every received frame of every session to `<root>/capture-<shard>.tsv` — diagnostics only (≈ 10 MB/min/shard at Bybit rates, ≈ 100 MB/min for all Binance shards).
