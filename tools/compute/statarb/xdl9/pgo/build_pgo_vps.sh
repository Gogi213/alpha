#!/usr/bin/env bash
# TK-493 pgo (В-32): сборка ядра stat-arb для Steam Deck на VPS Софии — рецепт tools/compute/build-pgo.sh
# (x86-64-v3 + fat LTO + 1 codegen-unit + panic=abort), но без calc-специфики: обучение идёт на Deck (train.sh), здесь только сборки.
#   build_pgo_vps.sh plain       v3 + LTO без PGO (контроль: отделяет эффект флагов сборки от PGO)
#   build_pgo_vps.sh gen         инструментированный бинарь (-C profile-generate) → W/bin/..-gen
#   build_pgo_vps.sh use         PGO-бинарь (-C profile-use=$W/merged.profdata) → W/bin/..-pgo
#   build_pgo_vps.sh merge       llvm-profdata merge W/profraw/*.profraw → W/merged.profdata
# env: W (рабочий каталог, по умолчанию /opt/alpha-compute/pgo-xdl9), SRC (дерево исходников, по умолчанию $W/src),
#      NAME (имя бинаря, по умолчанию statarb-xdl9-4ef45cc8), ALPHA_COMMIT (вшивается в --version, как у обычной сборки).
# Замок /opt/alpha-compute/.build.lock берётся на время сборки (очередь vps-check не нарушаем); target — свой, удаляется после сборки.
set -uo pipefail
STEP=${1:?plain|gen|use|merge}
W=${W:-/opt/alpha-compute/pgo-xdl9}; SRC=${SRC:-$W/src}; NAME=${NAME:-statarb-xdl9-4ef45cc8}
export PATH=$HOME/.cargo/bin:$PATH
SYSROOT=$(rustc --print sysroot); PROFDATA=$SYSROOT/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata
export CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort
[ -n "${ALPHA_COMMIT:-}" ] && export ALPHA_COMMIT
V3='-C target-cpu=x86-64-v3'
mkdir -p "$W/bin"
build() {  # <метка> <rustflags>
  local avail; avail=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
  [ "$avail" -lt 6 ] && { echo "build_pgo_vps: свободно ${avail}G < 6G — отказ"; return 75; }
  rm -f "$W/bin/$NAME-$1"
  echo "== $1: flags=[$2] avail=${avail}G $(date +%T)"
  ( cd "$SRC" && flock -w 7200 /opt/alpha-compute/.build.lock env RUSTFLAGS="$2" nice -n 5 cargo build --release --locked -j 4 --bin alpha --target-dir "$W/tgt-$1" ) > "$W/build-$1.log" 2>&1
  local rc=$?
  tail -4 "$W/build-$1.log"
  [ $rc -eq 0 ] && install -D "$W/tgt-$1/release/alpha" "$W/bin/$NAME-$1" && sha256sum "$W/bin/$NAME-$1" | tee -a "$W/binaries.sha256"
  rm -rf "$W/tgt-$1"
  echo "== $1: rc=$rc $(date +%T) avail=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)G"
  return $rc
}
case "$STEP" in
  plain) build plainv3 "$V3" ;;
  # rustc 1.93.1 / LLVM 21.1.8: fat LTO + profile-generate + v3 падает SIGSEGV в LoopVectorize (VPIRPhi::execute, 06.11) —
  # у инструментированной сборки векторизатор циклов выключен (инструментация идёт ДО него: профиль и CFG-хеши те же; PGO-сборка векторизует как обычно)
  gen)   rm -rf "$W/profraw"; mkdir -p "$W/profraw"; build gen "$V3 -C profile-generate=$W/profraw ${GEN_EXTRA--C no-vectorize-loops}" ;;
  merge) "$PROFDATA" merge -o "$W/merged.profdata" "$W"/profraw/*.profraw && ls -l "$W/merged.profdata" && md5sum "$W/merged.profdata" | tee -a "$W/binaries.sha256" ;;
  use)   [ -s "$W/merged.profdata" ] || { echo "нет $W/merged.profdata"; exit 2; }
         build pgo "$V3 -C profile-use=$W/merged.profdata -C llvm-args=-pgo-warn-missing-function"
         echo "предупреждений о функциях без профиля: $(grep -c 'no profile data available' "$W/build-pgo.log")" ;;
  *) echo "plain|gen|use|merge"; exit 2 ;;
esac
