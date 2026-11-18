#!/usr/bin/env bash
# TK-599: безопасная остановка и перезапуск `tk599-run` на границе раунда (запускать НА srv; stop/start — только по «go» координатора, флаг --go).
#   tk599_switch.sh status                          — только чтение: юнит, дочерние процессы (движок / сборщик признаков), счётчики готового
#   tk599_switch.sh stop  --go [макс. минут=240]    — ждёт момент без дочерних процессов (между раундами и между таблицами), затем `systemctl stop`; снимок состояния до остановки — switch/stop-<время>/ (журнал, md5 серий, метки)
#   tk599_switch.sh verify <старый код> <новый код> — остановленное состояние: сухой повтор готовых раундов СТАРЫМ и НОВЫМ кодом (tk599_replay.py trace) и сверка следов; равны → метка switch/replay-ok (критик, условие 3)
#   tk599_switch.sh provenance                      — только чтение: по окнам, сколько таблиц собрал Python, сколько alpha-feat (md5) (критик, условие 4)
#   tk599_switch.sh shadow <окно> <vid> [procs=2] [каталог кода] — фоновая теневая пересборка таблицы Python-эталоном + побайтное сравнение (юнит tk599-shadow-*, результат shadow/<окно>/<vid>/result.json)
#   tk599_switch.sh start --go <каталог кода>       — запускает юнит из НОВОГО каталога кода: `run --reuse-gates`; `P42_FEAT_BIN` — из окружения этого вызова
# Что не теряется: серии (`npz/<хэш>.npz` пишутся атомарно, оборванный файл пересобирается из выходов движка), прогоны движка (метка DONE.tk599), таблицы признаков по (окно, поток) (`ftab/<окно>/ready/<хэш>`).
# Что теряется: только то, что было в работе в момент остановки — таблица (окно, поток) без метки `ready` (пересобирается; сигнальный дамп — только для окон без метки) или пакет движка без
# меток DONE (пересчитывается). Слияние в union идемпотентно: тот же ключ с другим значением — GuardError, молча не портится.
# ВАЖНО: новый код — в НОВЫЙ каталог (например /data/p42-tk599/code2): работающий процесс запускает `retitr_ftab.py` из текущего каталога кода при каждой сборке — его перезапись меняет прогон на ходу.
# Для процесса на новом коде есть второй путь: `touch /data/p42-tk599/STOP` — остановится сам перед следующим раундом (код 75, `run.rc` не пишется); текущий (старый) процесс его не читает.
set -euo pipefail
ROOT=/data/p42-tk599
SW=$ROOT/switch
U=tk599-run
die() { echo "tk599_switch: $*" >&2; exit 2; }

children() {                                    # "вид pid" на каждый дочерний процесс юнита, который держит не-сохранённую работу
  local cg p c
  cg=$(systemctl show -p ControlGroup --value "$U" 2>/dev/null) || return 0
  [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/cgroup.procs" ] || return 0
  for p in $(cat "/sys/fs/cgroup$cg/cgroup.procs"); do
    c=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || continue
    case "$c" in
      *alpha-master*) echo "engine $p" ;;
      *alpha-feat*|*rustfeat_run.py*) echo "feat-rust $p" ;;
      *tk594_feat.py*) echo "feat-python $p" ;;
      *retitr_ftab.py*) echo "ftab-build $p" ;;
    esac
  done
}

status() {
  echo "unit $U: $(systemctl is-active "$U" 2>&1 || true); run.rc: $([ -e $ROOT/run.rc ] && cat $ROOT/run.rc || echo нет); results.pkl: $([ -e $ROOT/results/results.pkl ] && echo есть || echo нет); STOP: $([ -e $ROOT/STOP ] && echo есть || echo нет)"
  ch=$(children)
  echo "дочерние (держат работу): $(echo "$ch" | awk 'NF{k[$1]++} END{for(x in k) printf "%s=%d ", x, k[x]; if(!length(k)) printf "нет — граница раунда/таблицы"}')"
  for w in wa wb wc; do echo "ready $w: $(ls $ROOT/ftab/$w/ready 2>/dev/null | wc -l)"; done
  echo "npz: $(ls $ROOT/npz 2>/dev/null | wc -l) файлов, последний: $(ls -t $ROOT/npz 2>/dev/null | head -1) $(date -r "$ROOT/npz/$(ls -t $ROOT/npz 2>/dev/null | head -1)" +%H:%M:%S 2>/dev/null || true); сейчас $(date +%H:%M:%S)"
  echo "свободно /data: $(df -h /data | awk 'NR==2{print $4}')"
}

provenance() {
  python3 - "$ROOT" <<'PY'
import collections, json, os, sys
root = sys.argv[1]
for w in ("wa", "wb", "wc"):
    base = os.path.join(root, "ftab", w)
    ready = len(os.listdir(os.path.join(base, "ready"))) if os.path.isdir(os.path.join(base, "ready")) else 0
    cnt = collections.Counter()
    for vid in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        d = os.path.join(base, vid)
        if vid == "ready" or not os.path.isfile(os.path.join(d, "ftab.csv")):
            continue
        bp = os.path.join(d, "built-by.json")
        if not os.path.isfile(bp):
            cnt["python (старый код, без built-by.json)"] += 1
            continue
        b = json.load(open(bp, encoding="utf-8"))
        cnt["python" if b.get("feat_bin") == "python" else "alpha-feat md5 %s" % b.get("feat_bin_md5")] += 1
    print("%s: ready %d; таблиц с ftab.csv: %s" % (w, ready, "; ".join("%s — %d" % kv for kv in sorted(cnt.items())) or "нет"))
PY
}

cmd=${1:-status}
case "$cmd" in
  status) status ;;
  stop)
    [ "${2:-}" = "--go" ] || die "нужен --go (приказ координатора)"
    max=${3:-240}
    deadline=$(( $(date +%s) + max * 60 )); idle=0
    systemctl is-active -q "$U" || die "юнит не активен"
    while systemctl is-active -q "$U"; do
      if [ -z "$(children)" ]; then idle=$((idle + 1)); else idle=0; fi
      [ "$idle" -ge 2 ] && break
      [ "$(date +%s)" -ge "$deadline" ] && die "за $max мин не нашёлся момент без дочерних процессов (ничего не остановлено)"
      sleep 0.3
    done
    had_rc=0; [ -e $ROOT/run.rc ] && had_rc=1
    systemctl is-active -q "$U" && systemctl stop "$U"
    sleep 1
    systemctl is-active -q "$U" && die "юнит всё ещё активен после stop"
    if [ "$had_rc" = 0 ] && [ -e $ROOT/run.rc ] && [ ! -e $ROOT/results/results.pkl ]; then
      mv $ROOT/run.rc $ROOT/run.rc.stopped.$(date +%s); echo "run.rc, появившийся от остановки, убран (run.rc.stopped.*)"
    fi
    snap=$SW/stop-$(date +%Y%m%d-%H%M%S); mkdir -p "$snap"            # состояние до смены кода: журнал, md5 серий, метки (старый процесс раунды не журналирует — след снимает `verify`)
    { status; provenance; } > "$snap/status.txt" 2>&1
    cp -p $ROOT/log/run.out "$snap/run.out" 2>/dev/null || true
    (cd $ROOT/npz && ls | grep -v '\.tmp\.npz$' | xargs -r md5sum) > "$snap/npz.md5" 2>/dev/null || true
    for w in wa wb wc; do ls $ROOT/ftab/$w/ready 2>/dev/null | sort > "$snap/ready-$w.txt"; done
    echo "$snap" > $SW/last-stop; rm -f $SW/replay-ok
    echo "остановлено на границе; снимок $snap; дальше — verify <старый код> <новый код>, затем start --go <каталог кода>"
    status ;;
  verify)
    old=${2:?старый каталог кода}; new=${3:?новый каталог кода}
    [ -e $SW/last-stop ] || die "нет switch/last-stop — сначала stop --go"
    systemctl is-active -q "$U" && die "юнит активен — verify только на остановленном состоянии"
    snap=$(cat $SW/last-stop); rm -f $SW/replay-ok
    tool=$new/tools/p42/tk599_replay.py; [ -f "$tool" ] || die "нет $tool"
    (cd $ROOT && PYTHONUTF8=1 nice -n 19 python3 "$tool" trace "$old" "$snap/replay-old.json" --root $ROOT > "$snap/replay-old.out" 2>&1; echo $? > "$snap/replay-old.rc") &
    (cd $ROOT && PYTHONUTF8=1 nice -n 19 python3 "$tool" trace "$new" "$snap/replay-new.json" --root $ROOT > "$snap/replay-new.out" 2>&1; echo $? > "$snap/replay-new.rc") &
    wait
    echo "старый код: rc=$(cat $snap/replay-old.rc): $(tail -1 $snap/replay-old.out)"; echo "новый код:  rc=$(cat $snap/replay-new.rc): $(tail -1 $snap/replay-new.out)"
    [ "$(cat $snap/replay-old.rc)" = 0 ] && [ "$(cat $snap/replay-new.rc)" = 0 ] || die "повтор не отработал (см. $snap/replay-*.out) — смену кода НЕ делать"
    if ! python3 "$tool" compare "$snap/replay-old.json" "$snap/replay-new.json" | tee "$snap/replay-compare.txt"; then die "следы РАЗНЫЕ — смену кода НЕ делать"; fi
    echo "$snap" > $SW/replay-ok; echo "метка switch/replay-ok записана; можно start --go" ;;
  provenance) provenance ;;
  shadow)
    w=${2:?окно wa|wb|wc}; vid=${3:?vid потока (каталог ftab/<окно>/<vid>)}; procs=${4:-2}; code=${5:-$ROOT/code2}
    [ -d "$ROOT/ftab/$w/$vid" ] || die "нет $ROOT/ftab/$w/$vid"
    mkdir -p $SW
    systemd-run --unit="tk599-shadow-$w-$vid" -p Nice=19 -p "CPUQuota=$((procs * 100))%" -p IOSchedulingClass=idle --collect --setenv=PYTHONUTF8=1       bash -c "cd $ROOT && python3 $code/tools/p42/tk599_replay.py shadow $code $w $vid --root $ROOT --procs $procs > $SW/shadow-$w-$vid.out 2>&1; echo \$? > $SW/shadow-$w-$vid.rc"
    echo "теневая сборка запущена: tk599-shadow-$w-$vid; результат $ROOT/shadow/$w/$vid/result.json, код возврата $SW/shadow-$w-$vid.rc" ;;
  start)
    [ "${2:-}" = "--go" ] || die "нужен --go (приказ координатора)"
    code=${3:?каталог кода (новый, не $ROOT/code)}
    [ -f "$code/tools/p42/retitr_full_run.py" ] && [ -f "$code/docs/research/freeze/tk599-grids.json" ] || die "в $code нет tools/p42 и docs/research/freeze"
    systemctl is-active -q "$U" && die "юнит активен — сначала stop"
    [ ! -e $ROOT/run.rc ] || die "run.rc есть ($(cat $ROOT/run.rc)): основной прогон закончен или упал — перезапуск не нужен"
    [ -e $ROOT/gate/gates.json ] || die "нет gate/gates.json — --reuse-gates невозможен"
    [ -e $SW/replay-ok ] && [ "$(cat $SW/replay-ok)" = "$(cat $SW/last-stop 2>/dev/null)" ] || die "нет метки switch/replay-ok для последней остановки — сначала verify <старый код> <новый код> (следы равны)"
    rm -f $SW/replay-ok
    systemctl reset-failed "$U" 2>/dev/null || true
    [ -e $ROOT/log/run.out ] && mv $ROOT/log/run.out $ROOT/log/run.out.$(date +%Y%m%d-%H%M%S)
    envs=(--setenv=TK599_GO=1 --setenv=PYTHONUTF8=1)
    [ -n "${P42_FEAT_BIN:-}" ] && envs+=("--setenv=P42_FEAT_BIN=$P42_FEAT_BIN")
    systemd-run --unit="$U" -p Nice=19 -p CPUQuota=600% -p CPUWeight=1000 -p IOSchedulingClass=idle --collect "${envs[@]}" \
      bash -c "cd $code && python3 tools/p42/retitr_full_run.py run --reuse-gates >> $ROOT/log/run.out 2>&1; rc=\$?; [ \$rc = 75 ] || echo \$rc > $ROOT/run.rc"
    echo "запущен $U из $code (P42_FEAT_BIN=${P42_FEAT_BIN:-не задан → Python}); ожидание — tools/srv-wait.sh file:$ROOT/run.rc" ;;
  *) die "status | stop --go [мин] | verify <старый код> <новый код> | start --go <каталог кода> | provenance | shadow <окно> <vid> [procs] [код]" ;;
esac
