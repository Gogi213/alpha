#!/usr/bin/env bash
# Гейт "argv snapshot" (условие Судьи перед выносом дублирующихся флагов bounce-grid/oos-frozen/
# run-grid — RTT, --h3-mode/--h3-usd, --queue-model, наборы --set — в tools/compute/_env.sh и реестр
# имён наборов): заглушка вместо бинарника и питон-помощников — вместо реальной работы она дописывает
# СВОЙ argv (basename $0 + все аргументы, как есть) в $ARGV_LOG и создаёт файлы, которых ждут скрипты,
# затем выходит кодом 0. Прогоняем каждый счётный скрипт в каждом обвязанном режиме до и после правки —
# снимки обязаны совпасть строка в строку (У2/У3, условия Судьи — docs/research/reviews/consistency-plan-2026-09-26.md).
# Прогон 26.09 на VPS (/opt/alpha-compute/argv-gate/): 41ffd07 против У3+У2 — 26 режимов из 26 совпали; side-grid.sh
# не покрыт (жёстко /opt/alpha-compute — в песочницу не берётся).
#
# Только Linux (нужны systemd-run/systemctl-подобные вызовы, flock, /proc); Windows не поддерживается.
#
#   gate-argv.sh snap <каталог-с-tools/compute> <каталог-снимков>
#   gate-argv.sh diff <каталог-снимков-A> <каталог-снимков-B>
#
# GATE_MODES="nightly-oldbase0 oos-default ..." — посчитать только перечисленные режимы (отладка);
# без переменной — все режимы. Список режимов и то, что сознательно не охвачено, — в конце файла и в
# отчёте вызова (заголовок docs/... не создаётся — это скрипт, не документ).
set -uo pipefail

case "$(uname -s 2>/dev/null)" in
  Linux) ;;
  *) echo "gate-argv.sh: только Linux (нужны systemd-run-подобные вызовы и /proc)" >&2; exit 1 ;;
esac

FIXED_NOW="${GATE_FIXED_NOW:-2026-09-26T02:00:00Z}"
SYMS=(BTCUSDT ETHUSDT)
MAIN_DAYS=(2026-09-16 2026-09-24 2026-09-25)
HIST_DAYS=(2026-09-02 2026-09-03)

usage() {
  echo "usage: gate-argv.sh snap <dir-with-tools/compute> <out-dir>" >&2
  echo "       gate-argv.sh diff <out-dir-A> <out-dir-B>" >&2
}

# ---------- заглушка-бинарник ----------------------------------------------------------------

write_stub_binary() {  # $1 путь до исполняемого файла-заглушки
  cat > "$1" <<'STUBEOF'
#!/usr/bin/env bash
# Заглушка alpha (гейт argv-snapshot): логирует свой вызов и создаёт минимум файлов, которых ждут
# вызывающие скрипты, чтобы конвейер шёл дальше по всем ветвям. Реальных данных не читает и не пишет.
log() {
  if [ -n "${ARGV_LOG:-}" ]; then
    # Собрать строку ЦЕЛИКОМ до записи: под DAY_JOBS/xargs -P несколько заглушек пишут в один файл
    # одновременно — раздельные printf чередовались построчно (найдено гейтом на первом прогоне).
    local line; line=$(printf '%s' "$(basename "$0")"; for a in "$@"; do printf ' %q' "$a"; done)
    printf '%s\n' "$line" >> "$ARGV_LOG"
  fi
}
log "$@"
ARGS=("$@")
val_of() {  # $1 — имя флага; ищет его в глобальном ARGS, печатает следующее значение
  local flag="$1" i
  for ((i = 0; i < ${#ARGS[@]}; i++)); do
    if [ "${ARGS[$i]}" = "$flag" ]; then printf '%s' "${ARGS[$((i + 1))]:-}"; return 0; fi
  done
}
sub1="${1:-}"; sub2="${2:-}"
case "$sub1 $sub2" in
  "lob bounce-grid")
    outdir=$(val_of --out-dir); [ -n "$outdir" ] || outdir=.
    mkdir -p "$outdir"
    sets=()
    for ((i = 0; i < ${#ARGS[@]}; i++)); do
      [ "${ARGS[$i]}" = "--set" ] && sets+=("${ARGS[$((i + 1))]%%:*}")
    done
    write_pair() {
      mkdir -p "$1"
      { echo "# stub bounce-grid $(date -u +%FT%TZ 2>/dev/null)"; echo "day,symbol,form,pnl_bps"; } > "$1/rounds.csv"
      { echo "# stub bounce-grid"; echo "form,n_signals,n_rounds"; echo "stubform,1,1"; } > "$1/forms.csv"
    }
    if [ "${#sets[@]}" -eq 0 ]; then write_pair "$outdir"; else for s in "${sets[@]}"; do write_pair "$outdir/$s"; done; fi
    echo "готов: stub $(echo ${#sets[@]:-1})" >&2
    echo "bounce-grid: форм 1 (stub)" >&2
    ;;
  "lob bounce-verdict")
    out=$(val_of --out)
    if [ -n "$out" ]; then mkdir -p "$(dirname "$out")" 2>/dev/null; : > "$out"; fi
    echo "bounce-verdict: ИТОГ=ok · лучшая stubform · кругов 1 · точка=0.0 · нижняя=0.0"
    ;;
  "lob touches")
    out=$(val_of --out); sym=$(val_of --symbol)
    if [ -n "$out" ]; then
      mkdir -p "$(dirname "$out")" 2>/dev/null
      echo "# stub touches" > "$out"
      d=$(dirname "$out")
      if [ -n "$sym" ]; then : > "$d/approaches-$sym.csv"; : > "$d/mids1m-$sym.csv"; fi
    fi
    lo=$(val_of --levels-out); [ -n "$lo" ] && { mkdir -p "$(dirname "$lo")" 2>/dev/null; echo "# stub levels" > "$lo"; }
    ;;
  "lob levels")
    out=$(val_of --out)
    [ -n "$out" ] && { mkdir -p "$(dirname "$out")" 2>/dev/null; echo "# stub levels" > "$out"; }
    ;;
  "lob import-archive")
    root=$(val_of --root); sym=$(val_of --symbol); day=$(val_of --day)
    if [ -n "$root" ] && [ -n "$sym" ] && [ -n "$day" ]; then mkdir -p "$root"; : > "$root/$sym-$day.binlog"; fi
    echo "разрывов u 0"
    echo "import-archive: $sym $day — готово (stub)"
    ;;
  *)
    out=$(val_of --out); outdir=$(val_of --out-dir)
    [ -n "$out" ] && { mkdir -p "$(dirname "$out")" 2>/dev/null; : > "$out"; }
    [ -n "$outdir" ] && mkdir -p "$outdir"
    ;;
esac
exit 0
STUBEOF
  chmod +x "$1"
}

# ---------- PATH-заглушки для внешних команд --------------------------------------------------

build_shims() {  # $1 каталог заглушек
  local d="$1"
  mkdir -p "$d"

  cat > "$d/systemd-run" <<'EOF'
#!/usr/bin/env bash
# Гейт: не создаём настоящие transient-юниты (нет прав/не нужно в песочнице) — разбираем опции и
# выполняем целевую команду тут же (foreground), сохраняя перенаправления StandardOutput/Error.
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    -p)
      case "${2:-}" in
        StandardOutput=append:*) exec >> "${2#StandardOutput=append:}" ;;
        StandardError=append:*) exec 2>> "${2#StandardError=append:}" ;;
      esac
      shift 2; continue ;;
    -*) shift; continue ;;
    *) break ;;
  esac
done
exec "$@"
EOF

  cat > "$d/date" <<EOF
#!/usr/bin/env bash
REAL=/usr/bin/date; [ -x "\$REAL" ] || REAL=/bin/date
has_d=0
for a in "\$@"; do case "\$a" in -d|-d=*|--date|--date=*|-r|-r=*) has_d=1 ;; esac; done
if [ "\$has_d" = 1 ]; then exec "\$REAL" "\$@"; else exec "\$REAL" -u -d "$FIXED_NOW" "\$@"; fi
EOF

  cat > "$d/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

  cat > "$d/nproc" <<'EOF'
#!/usr/bin/env bash
echo 8
EOF

  cat > "$d/netlog" <<'EOF'
#!/usr/bin/env bash
name=$(basename "$0")
if [ -n "${ARGV_LOG:-}" ]; then
  line=$(printf '%s' "net:$name"; for a in "$@"; do printf ' %q' "$a"; done)
  printf '%s\n' "$line" >> "$ARGV_LOG"
fi
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && { : > "$a" 2>/dev/null || true; }
  prev="$a"
done
exit 0
EOF
  ln -sf netlog "$d/curl"
  ln -sf netlog "$d/ssh"
  ln -sf netlog "$d/rsync"

  cat > "$d/gunzip" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null 2>&1
exit 0
EOF

  cat > "$d/python3" <<'EOF'
#!/usr/bin/env bash
if [ -n "${ARGV_LOG:-}" ]; then
  line=$(printf '%s' "py:$(basename "${1:-python3}")"; for a in "$@"; do printf ' %q' "$a"; done)
  printf '%s\n' "$line" >> "$ARGV_LOG"
fi
prev=""
for a in "$@"; do
  case "$prev" in
    --out|--csv|--summary) mkdir -p "$(dirname "$a")" 2>/dev/null; : > "$a" 2>/dev/null || true ;;
    --out-dir) mkdir -p "$a" 2>/dev/null || true ;;
  esac
  prev="$a"
done
# regime.py --day D пишет study/regime/D.csv сама (не через флаг) — без него oos-frozen.sh не
# считает сутки готовыми; воспроизводим по имени скрипта.
case "${1:-}" in
  */regime.py|regime.py)
    day=""; prev=""
    for a in "$@"; do [ "$prev" = "--day" ] && day="$a"; prev="$a"; done
    [ -n "$day" ] && { mkdir -p study/regime 2>/dev/null; : > "study/regime/$day.csv"; }
    ;;
esac
echo "STUB_PY_OUTPUT"
exit 0
EOF
  for f in systemd-run date sleep nproc netlog gunzip python3; do chmod +x "$d/$f"; done
  ln -sf python3 "$d/python"
}

# ---------- построение "золотого" фикстур-дома ------------------------------------------------

populate_root() {  # $1 каталог root, $2.. сутки
  local root="$1"; shift
  mkdir -p "$root"
  {
    echo "symbol,tick_size,qty_step,min_qty"
    for s in "${SYMS[@]}"; do echo "$s,0.1,0.001,0.001"; done
  } > "$root/instruments.csv"
  echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$root/session.json"
  for s in "${SYMS[@]}"; do echo ok > "$root/verify-$s.status"; done
  for d in "$@"; do for s in "${SYMS[@]}"; do : > "$root/$s-$d.binlog"; done; done
}

populate_derived() {  # $1 дом, $2.. сутки — study/touches (.done готов), study/regime, study/root-<день>
  local home="$1"; shift
  for d in "$@"; do
    local t="$home/study/touches/$d"
    mkdir -p "$t"
    printf '%s\n' "${SYMS[@]}" > "$t/symbols.txt"
    for s in "${SYMS[@]}"; do echo "# stub touches" > "$t/touches-$s.csv"; done
    : > "$t/.done"
    mkdir -p "$home/study/regime"
    echo "minute,pool_ret_4h,btc_ret_4h,ret_1h" > "$home/study/regime/$d.csv"
    mkdir -p "$home/study/root-$d"
  done
}

build_seed() {  # $1 каталог исходников (с tools/compute), $2 каталог для золотого дома
  local src="$1" seed="$2"
  rm -rf "$seed"; mkdir -p "$seed"/{bin,root,study/regime,study/touches,study/approaches/D20,study/klines,b5,epochs}
  cp -a "$src/tools/compute/." "$seed/bin/"
  rm -rf "$seed/bin/tests" "$seed/bin/__pycache__" "$seed/bin/p01_frozen"
  chmod +x "$seed/bin"/*.sh 2>/dev/null || true
  write_stub_binary "$seed/bin/alpha"
  local pins
  pins=$(grep -ohE 'alpha-[0-9a-f]{7}' "$seed/bin"/*.sh 2>/dev/null | sort -u)
  for p in $pins; do ln -sf alpha "$seed/bin/$p"; done

  populate_root "$seed/root" "${MAIN_DAYS[@]}"
  populate_derived "$seed" "${MAIN_DAYS[@]}"
  cat > "$seed/study/titration-sets-v1.txt" <<'EOF'
t-bid-age-45:age=2700,side=bid
t-bid-btc1h-q1:age=2700,side=bid,btc1h_max=-21.17
t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55
EOF
  # Обходим titration-points.py у titrate.sh (её stdout не подделать без потери смысла) — метка
  # прогона "gate" замораживает свои наборы этим файлом, как в проде после первой ночи.
  cat > "$seed/study/titration-sets-gate.txt" <<'EOF'
g-btc4h-q1:age=2700,side=bid,btc4h_max=-10
g-btc4h-q4:age=2700,side=bid,btc4h_max=50
EOF
  echo "day,kind,verdict,form,rounds,day2,logged" > "$seed/study/runs-2026-09-19.csv"

  mkdir -p "$seed/epochs/e-archive"
  populate_root "$seed/epochs/e-archive/root" "${HIST_DAYS[@]}"
  populate_derived "$seed/epochs/e-archive" "${HIST_DAYS[@]}"
  mkdir -p "$seed/epochs/e-archive/study"
  cp "$seed/study/runs-2026-09-19.csv" "$seed/epochs/e-archive/study/runs-2026-09-19.csv"
  ln -sf ../../bin "$seed/epochs/e-archive/bin"
}

# ---------- прогон одного режима ---------------------------------------------------------------

WORK=""; SEED=""; SHIMS=""; OUT=""
MODE_ENV=(); MODE_ARGS=(); MODE_MKDIRS=()

prepare_home() {  # $1 имя режима -> печатает путь свежей копии золотого дома
  local name="$1" home="$WORK/homes/$1"
  rm -rf "$home"; mkdir -p "$(dirname "$home")"
  cp -a "$SEED" "$home"
  printf '%s' "$home"
}

run_one_mode() {  # $1 имя режима, $2 скрипт (относительно bin/); аргументы — MODE_ARGS, окружение — MODE_ENV
  local name="$1" script="$2"
  if [ -n "${GATE_MODES:-}" ]; then
    case " $GATE_MODES " in *" $name "*) ;; *) return 0 ;; esac
  fi
  local home; home=$(prepare_home "$name")
  local d; for d in "${MODE_MKDIRS[@]}"; do mkdir -p "$home/$d"; done
  local log="$home/.argv.raw"; : > "$log"
  (
    export ALPHA_HOME="$home" ALPHA_BASE="$home" ARGV_LOG="$log" PATH="$SHIMS:$PATH" GATE_FIXED_NOW="$FIXED_NOW"
    for kv in "${MODE_ENV[@]}"; do export "${kv?}"; done
    cd "$home" || exit 99
    exec "$home/bin/$script" "${MODE_ARGS[@]}"
  ) > "$home/.run.log" 2>&1
  local rc=$?
  local esc=${home//\//\\/}
  { sed "s/$esc/<HOME>/g" "$log" 2>/dev/null | sort; echo "EXIT $rc"; } > "$OUT/$name.snap"
  echo "  $name: exit=$rc argv=$(wc -l < "$log" 2>/dev/null || echo 0) log=$home/.run.log"
}

# ---------- реестр режимов ----------------------------------------------------------------------

run_all_modes() {
  MODE_ENV=(OLD_BASE=1); MODE_ARGS=(); run_one_mode nightly-oldbase1 nightly-grid.sh
  MODE_ENV=(OLD_BASE=0); MODE_ARGS=(); run_one_mode nightly-oldbase0 nightly-grid.sh
  MODE_ENV=(GRID_THREADS=2 GRID_MEM=4G NIGHT_JOBS=3 BASE_SPLIT=2 GRID_SLICE=alpha.slice OLD_BASE=0 H3_JOBS=8)
  MODE_ARGS=(); run_one_mode nightly-deck nightly-grid.sh

  MODE_ENV=(); MODE_ARGS=(); run_one_mode oos-default oos-frozen.sh
  MODE_ENV=(SETS="t-bid-age-45:age=2700,side=bid"); MODE_ARGS=(); run_one_mode oos-sets oos-frozen.sh
  MODE_ENV=(FORM_EXIT="--stop-form pct1 --take-form tk1 --deadline-secs 3600 --exit-form none")
  MODE_ARGS=(); run_one_mode oos-formexit oos-frozen.sh
  MODE_ENV=(ORDER="--order-usd 500"); MODE_ARGS=(); run_one_mode oos-order oos-frozen.sh

  MODE_ENV=(); MODE_ARGS=(gate-label --day 2026-09-24 --h3-mode notional --h3-usd 50000)
  run_one_mode run-grid run-grid.sh

  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(gate); run_one_mode recompute-carry recompute-carry.sh
  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(); run_one_mode crash-stress crash-stress.sh

  MODE_ENV=(); MODE_ARGS=(gate); run_one_mode titrate titrate.sh
  MODE_ENV=(); MODE_ARGS=(fix gate); run_one_mode titrate-forms-fix titrate-forms.sh
  MODE_ENV=(); MODE_ARGS=(trail gate); run_one_mode titrate-forms-trail titrate-forms.sh
  MODE_ENV=(); MODE_ARGS=(wall gate); run_one_mode titrate-forms-wall titrate-forms.sh
  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(gone gate); run_one_mode titrate-forms-gone titrate-forms.sh
  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(be gate); run_one_mode titrate-forms-be titrate-forms.sh
  MODE_ENV=(); MODE_ARGS=(gate); run_one_mode titrate-exit titrate-exit.sh
  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(gate); run_one_mode titrate-gone titrate-gone.sh
  MODE_ENV=(BIN=bin/alpha); MODE_ARGS=(gate); run_one_mode titrate-be titrate-be.sh

  MODE_ENV=(IMPORT_JOBS=1); MODE_ARGS=(archive epochs/e-fresh 2026-09-05 2026-09-06)
  run_one_mode epoch-run-archive epoch-run.sh
  MODE_ENV=(); MODE_ARGS=(collected); run_one_mode epoch-run-collected epoch-run.sh

  MODE_ENV=(); MODE_ARGS=(20 2026-09-24 2026-09-25); run_one_mode approach-scan approach-scan.sh

  MODE_MKDIRS=()
  MODE_ENV=(OLD=bin/alpha NEW=bin/alpha OUT=gate-g10-out GATE_JOBS=1); MODE_ARGS=()
  run_one_mode gate-g10 gate-g10.sh
  MODE_ENV=(OLD=bin/alpha NEW=bin/alpha OUT=gate-carry-out); MODE_ARGS=(.:2026-09-24 .:2026-09-25)
  run_one_mode gate-carry gate-carry.sh
  # gate-merge.sh перенаправляет вывод бинарника в "$run/$day.log" до того, как заглушка успевает
  # создать каталог из --out-dir (mkdir делает сам bash при открытии `>`, каталога ещё нет) —
  # создаём каталоги прогонов заранее, как это в проде делает сам `mkdir -p $OUT` перед первым запуском.
  MODE_MKDIRS=(gate-merge-out/sep-ladder3x2..20w2-pct2 gate-merge-out/sep-ladder3x2..20w2-before \
    gate-merge-out/sep-single@fr-pct2 gate-merge-out/sep-single@fr-before gate-merge-out/merged gate-merge-out/merged-t1)
  MODE_ENV=(BIN=bin/alpha OUT=gate-merge-out); MODE_ARGS=(.:2026-09-24)
  run_one_mode gate-merge gate-merge.sh
  MODE_MKDIRS=()
  MODE_ENV=(OLD=bin/alpha NEW=bin/alpha OUT=gate-t14-out); MODE_ARGS=(study/root-2026-09-24:BTCUSDT)
  run_one_mode gate-t14 gate-t14.sh
}

# ---------- команды -------------------------------------------------------------------------

cmd_snap() {
  local src="${1:?<dir-with-tools/compute>}" out="${2:?<out-dir>}"
  [ -d "$src/tools/compute" ] || { echo "gate-argv.sh: нет $src/tools/compute" >&2; exit 1; }
  src="$(cd "$src" && pwd)"
  mkdir -p "$out"; out="$(cd "$out" && pwd)"
  OUT="$out"
  WORK="$OUT/.work"; rm -rf "$WORK"; mkdir -p "$WORK"
  SHIMS="$WORK/shims"; build_shims "$SHIMS"
  SEED="$WORK/seed"; build_seed "$src" "$SEED"
  echo "gate-argv snap: src=$src out=$OUT (work=$WORK)"
  run_all_modes
  [ -n "${GATE_KEEP_WORK:-}" ] || rm -rf "$WORK"
  echo "gate-argv snap: готово, снимков $(ls "$OUT"/*.snap 2>/dev/null | wc -l)"
}

cmd_diff() {
  local a="${1:?<out-dir-A>}" b="${2:?<out-dir-B>}"
  local rc=0 f name
  for f in "$a"/*.snap; do
    name=$(basename "$f")
    if [ ! -f "$b/$name" ]; then echo "DIFF $name: нет в $b"; rc=1; continue; fi
    if cmp -s "$f" "$b/$name"; then
      echo "same $name"
    else
      echo "DIFF $name"
      diff -u "$f" "$b/$name" | head -20
      rc=1
    fi
  done
  for f in "$b"/*.snap; do
    name=$(basename "$f")
    [ -f "$a/$name" ] || { echo "DIFF $name: нет в $a"; rc=1; }
  done
  exit "$rc"
}

MODE="${1:-}"; shift || true
case "$MODE" in
  snap) cmd_snap "$@" ;;
  diff) cmd_diff "$@" ;;
  *) usage; exit 2 ;;
esac

# ---------- сознательно не охвачено --------------------------------------------------------
# p02-*.sh, f3-*.sh, f10-*.sh — вне поручения (не относятся к RTT/H3/queue-model/сетам).
# side-grid.sh — жёстко зашитый /opt/alpha-compute (cd и $BIN), не параметризован ALPHA_HOME/ALPHA_BASE:
#   прогон в песочнице тронул бы боевой каталог и systemd-юниты; не гоняется этим гейтом (см. отчёт).
# export-pass.sh, grid-slot.sh, p02-wave3-*.sh (lob trades) — не входят в список режимов задачи.
# gate-carry.sh MEM_ONLY, gate-merge.sh PSIM — доп. ветки, включаются отдельными переменными окружения,
#   не входят в снимок по умолчанию (не несут RTT/H3/queue-model строк, которые не покрыты иначе).
