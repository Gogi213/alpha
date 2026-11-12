#!/usr/bin/env bash
# G-022 turbo: PGO build of the collector on srv (В-32: the production binary is a PGO build). Run as a background unit on srv.
#   build-pgo.sh <source tree on srv, e.g. /opt/rbuild/pgo-src> <output binary> [<alpha release binary used only for the synthetic source>]
# Steps: instrumented build -> workload (synthetic ladder 2k/10k/50k fps + live Binance 3 min + live Bybit 3 min, all exiting normally so .profraw is written)
#        -> llvm-profdata merge -> build with the profile -> install. The profile is specific to this source tree: rebuild it after any change of the hot path.
set -euo pipefail
SRC="$1"; OUT="$2"; SYN="${3:-}"
B=/opt/rbuild; export RUSTUP_HOME=$B/rustup CARGO_HOME=$B/cargo PATH=$B/cargo/bin:$PATH
PROF=/data/turbo-probe/pgo-prof; GEN=$B/pgo-gen; USE=$B/pgo-use
# rustc 1.93 (LLVM 21) segfaults in libLLVM with -Cprofile-generate on this crate (dmesg: "lto cgu"/"opt cgu"); value profiling is disabled (-disable-vp) and LTO is off in both PGO builds;
# a non-PGO LTO-off build is made for a fair A/B
export CARGO_PROFILE_RELEASE_LTO=off
PD=$(dirname "$(find "$RUSTUP_HOME/toolchains" -name llvm-profdata | head -1)")
rm -rf "$PROF"; mkdir -p "$PROF"
cd "$SRC"
RUSTFLAGS="-Cprofile-generate=$PROF -Cllvm-args=-disable-vp" cargo build --release --target-dir "$GEN" -j 6 2>&1 | tail -2
BIN="$GEN/release/alpha"; SYN="${SYN:-$BIN}"
W=/data/turbo-probe/pgo-work; rm -rf "$W"; mkdir -p "$W"
"$SYN" collect synth --listen 127.0.0.1:1 --symbols 64 --duration-s 1 --write-pool "$W/pool.json" >/dev/null 2>&1 || true
for R in 2000 10000 50000; do
  "$SYN" collect synth --listen 127.0.0.1:9890 --symbols 64 --rate "$R" --duration-s 40 >/dev/null 2>&1 &
  sleep 1
  "$BIN" collect run --exchange binance --plain-ws 127.0.0.1:9890 --pool-file "$W/pool.json" --root "$W/syn$R" --health "$W/h$R.json" --kinds book --streams-per-conn 64 --lanes 1 --duration-s 25 >/dev/null 2>&1
  wait
done
for EX in binance bybit; do
  "$BIN" collect run --exchange "$EX" --root "$W/live-$EX" --health "$W/live-$EX.json" --lanes 2 --duration-s 200 >/dev/null 2>&1 &
done
wait
"$PD/llvm-profdata" merge -o "$PROF/merged.profdata" "$PROF"/*.profraw
RUSTFLAGS="-Cprofile-use=$PROF/merged.profdata" cargo build --release --target-dir "$USE" -j 6 2>&1 | tail -2
install -D "$USE/release/alpha" "$OUT"
RUSTFLAGS="" cargo build --release --target-dir "$B/pgo-base" -j 6 2>&1 | tail -2
install -D "$B/pgo-base/release/alpha" "$OUT-nopgo"
md5sum "$OUT"
