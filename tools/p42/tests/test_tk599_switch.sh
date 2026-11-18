#!/usr/bin/env bash
# Тест защит tk599_switch.sh (stop-снимок, verify, метка replay-ok, отказы start) на пустом корне со ставками systemctl/systemd-run и заглушкой tk599_replay.py. Запуск: bash test_tk599_switch.sh
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); SWITCH=$HERE/../tk599_switch.sh
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export TK599_ROOT=$T/root TK599_UNIT=tk599-test PATH=$T/bin:$PATH
ROOT=$TK599_ROOT; fails=0
mkdir -p $T/bin $ROOT/npz $ROOT/gate $ROOT/log $ROOT/ftab/{wa,wb,wc}/ready
echo '{}' > $ROOT/gate/gates.json; echo a > $ROOT/npz/a.npz; echo b > $ROOT/npz/b.npz; touch $ROOT/ftab/wa/ready/h1
cat > $T/bin/systemctl <<'SH'
#!/usr/bin/env bash
case "$1" in
  is-active) [ -e "$TK599_ROOT/../active" ] && exit 0 || exit 3 ;;
  stop) rm -f "$TK599_ROOT/../active" ;;
  show) : ;;
esac
exit 0
SH
cat > $T/bin/systemd-run <<'SH'
#!/usr/bin/env bash
echo "$@" > "$TK599_ROOT/../sd-run.log"
SH
chmod +x $T/bin/*
mk_code() {
  mkdir -p $1/tools/p42 $1/docs/research/freeze
  echo "x" > $1/tools/p42/retitr_full_run.py; echo g > $1/docs/research/freeze/tk599-grids.json
  cat > $1/tools/p42/tk599_replay.py <<'PY'
import json, os, sys
a = sys.argv[1:]
if a[0] == "trace":
    if os.environ.get("STUB_TOUCH"):
        open(os.path.join(a[3 + 0 if False else a.index("--root") + 1], "npz", "new.npz"), "w").write("x")
    steps = int(os.environ.get("STUB_STEPS", "5"))
    json.dump({"depth": {"steps": steps, "ok": steps > 0}, "digest": "abc123"}, open(a[2], "w"))
    print("раундов 2, шагов %d" % steps); print("глубина: шагов %d; ЗАЧЁТ" % steps)
    sys.exit(0 if steps > 0 else 3)
print("следы равны"); sys.exit(0)
PY
}
mk_code $T/old; mk_code $T/new
expect() {  # expect <имя> <ожидаемый код: 0|nz> <шаблон в выводе> -- команда...
  local name=$1 want=$2 pat=$3; shift 4; local out rc
  out=$("$@" 2>&1); rc=$?
  if { [ "$want" = 0 ] && [ $rc != 0 ]; } || { [ "$want" = nz ] && [ $rc = 0 ]; } || ! grep -q -- "$pat" <<<"$out"; then
    echo "FAIL $name (rc=$rc, ждали $want / «$pat»): $(tail -3 <<<"$out")"; fails=$((fails+1)); else echo "ok   $name"; fi
}
S="bash $SWITCH"
do_stop() { touch $T/active; $S stop --go 1 >/dev/null 2>&1; }

expect "start без verify отказывает" nz "replay-ok" -- $S start --go $T/new
do_stop; expect "stop сделал снимок" 0 "stop-" -- cat $ROOT/switch/last-stop
test -s "$(cat $ROOT/switch/last-stop)/npz.md5" && echo "ok   снимок npz.md5 непуст" || { echo "FAIL снимок npz.md5"; fails=$((fails+1)); }
expect "verify зачтён, метка записана" 0 "replay-ok записана" -- $S verify $T/old $T/new
grep -q "^npz_hash=" $ROOT/switch/replay-ok && grep -q "^old_code_md5=" $ROOT/switch/replay-ok && grep -q "^new_code_md5=" $ROOT/switch/replay-ok && grep -q "^steps=5" $ROOT/switch/replay-ok && echo "ok   метка: md5 кодов, хэш npz, шаги" || { echo "FAIL содержимое метки"; fails=$((fails+1)); }
echo c > $ROOT/npz/c.npz
expect "start после изменения набора npz отказывает" nz "npz / меток ready изменился" -- $S start --go $T/new
rm -f $ROOT/npz/c.npz
expect "start с другим каталогом кода отказывает" nz "verify проверял" -- $S start --go $T/old
echo y >> $T/new/tools/p42/retitr_full_run.py
expect "start после правки кода отказывает" nz "изменился после verify" -- $S start --go $T/new
echo x > $T/new/tools/p42/retitr_full_run.py
expect "verify после возврата кэша к снимку проходит" 0 "replay-ok записана" -- $S verify $T/old $T/new            # c.npz удалён — кэш равен снимку stop
do_stop; echo z > $ROOT/npz/z.npz
expect "verify: кэш изменился между stop и verify — отказ" nz "между stop и verify" -- $S verify $T/old $T/new
rm -f $ROOT/npz/z.npz
do_stop; STUB_TOUCH=1 expect "verify: повтор изменил кэш — отказ" nz "ИЗМЕНИЛ кэш" -- $S verify $T/old $T/new
rm -f $ROOT/npz/new.npz; do_stop
STUB_STEPS=0 expect "verify: 0 шагов — не зачёт" nz "не зачтён" -- $S verify $T/old $T/new
test ! -e $ROOT/switch/replay-ok && echo "ok   метки нет после незачёта" || { echo "FAIL метка осталась после незачёта"; fails=$((fails+1)); }
do_stop; $S verify $T/old $T/new >/dev/null 2>&1
expect "start после verify проходит и гасит метку" 0 "запущен" -- $S start --go $T/new
test ! -e $ROOT/switch/replay-ok && test -s $T/sd-run.log && ls $(cat $ROOT/switch/last-stop)/replay-ok.start.txt >/dev/null 2>&1 && echo "ok   метка погашена, запуск вызван, копия в снимке" || { echo "FAIL после start"; fails=$((fails+1)); }
echo "$fails провалов"; exit $((fails > 0))
