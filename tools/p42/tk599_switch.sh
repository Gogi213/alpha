#!/usr/bin/env bash
# TK-599: безопасная остановка и перезапуск `tk599-run` на границе раунда (запускать НА srv; stop/start — только по «go» координатора, флаг --go).
#   tk599_switch.sh status                          — только чтение: юнит, дочерние процессы (движок / сборщик признаков), счётчики готового
#   tk599_switch.sh stop  --go [макс. минут=240]    — ждёт момент без дочерних процессов (между раундами и между таблицами), затем `systemctl stop`
#   tk599_switch.sh start --go <каталог кода>       — запускает юнит из НОВОГО каталога кода: `run --reuse-gates`; `P42_FEAT_BIN` — из окружения этого вызова
# Что не теряется: серии (`npz/<хэш>.npz` пишутся атомарно, оборванный файл пересобирается из выходов движка), прогоны движка (метка DONE.tk599), таблицы признаков по (окно, поток) (`ftab/<окно>/ready/<хэш>`).
# Что теряется: только то, что было в работе в момент остановки — таблица (окно, поток) без метки `ready` (пересобирается; сигнальный дамп — только для окон без метки) или пакет движка без
# меток DONE (пересчитывается). Слияние в union идемпотентно: тот же ключ с другим значением — GuardError, молча не портится.
# ВАЖНО: новый код — в НОВЫЙ каталог (например /data/p42-tk599/code2): работающий процесс запускает `retitr_ftab.py` из текущего каталога кода при каждой сборке — его перезапись меняет прогон на ходу.
# Для процесса на новом коде есть второй путь: `touch /data/p42-tk599/STOP` — остановится сам перед следующим раундом (код 75, `run.rc` не пишется); текущий (старый) процесс его не читает.
set -euo pipefail
ROOT=/data/p42-tk599
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
    echo "остановлено на границе; продолжение — tk599_switch.sh start --go <каталог кода>"
    status ;;
  start)
    [ "${2:-}" = "--go" ] || die "нужен --go (приказ координатора)"
    code=${3:?каталог кода (новый, не $ROOT/code)}
    [ -f "$code/tools/p42/retitr_full_run.py" ] && [ -f "$code/docs/research/freeze/tk599-grids.json" ] || die "в $code нет tools/p42 и docs/research/freeze"
    systemctl is-active -q "$U" && die "юнит активен — сначала stop"
    [ ! -e $ROOT/run.rc ] || die "run.rc есть ($(cat $ROOT/run.rc)): основной прогон закончен или упал — перезапуск не нужен"
    [ -e $ROOT/gate/gates.json ] || die "нет gate/gates.json — --reuse-gates невозможен"
    systemctl reset-failed "$U" 2>/dev/null || true
    [ -e $ROOT/log/run.out ] && mv $ROOT/log/run.out $ROOT/log/run.out.$(date +%Y%m%d-%H%M%S)
    envs=(--setenv=TK599_GO=1 --setenv=PYTHONUTF8=1)
    [ -n "${P42_FEAT_BIN:-}" ] && envs+=("--setenv=P42_FEAT_BIN=$P42_FEAT_BIN")
    systemd-run --unit="$U" -p Nice=19 -p CPUQuota=600% -p CPUWeight=1000 -p IOSchedulingClass=idle --collect "${envs[@]}" \
      bash -c "cd $code && python3 tools/p42/retitr_full_run.py run --reuse-gates >> $ROOT/log/run.out 2>&1; rc=\$?; [ \$rc = 75 ] || echo \$rc > $ROOT/run.rc"
    echo "запущен $U из $code (P42_FEAT_BIN=${P42_FEAT_BIN:-не задан → Python}); ожидание — tools/srv-wait.sh file:$ROOT/run.rc" ;;
  *) die "status | stop --go [мин] | start --go <каталог кода>" ;;
esac
