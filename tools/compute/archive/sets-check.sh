#!/usr/bin/env bash
# У2 (T-16, Судья 213fefb): проверка реестра наборов — одно имя, одно определение.
#   sets-check.sh [файлы наборов...]
# Сверяет с реестром sets.txt (рядом со скриптом) каждое `имя:ключи` из sets-nightly.txt, titration-sets-*.txt (рядом со
# скриптом и в $ALPHA_HOME/study/, если есть) и переданных файлов; внутри реестра имя — не больше одного раза.
# Строки `--set`/`SETS=` в самих скриптах НЕ сверяются. На деке запускать при выкладке: titrate*/crash-stress/
# recompute-carry берут наборы из $ALPHA_HOME/study/titration-sets-*.txt, а не из реестра (Судья dbb673d).
# Печатает группы синонимов (одно определение — несколько имён: старые каталоги читаются по старому имени).
# Код 1 — имя вне реестра, другое определение или повтор имени в реестре.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REG="$SELF_DIR/sets.txt"
A="${ALPHA_HOME:-$HOME/alpha}"
files=("$SELF_DIR/sets-nightly.txt")
for f in "$SELF_DIR"/titration-sets-*.txt "$A"/study/titration-sets-*.txt "$@"; do [ -f "$f" ] && files+=("$f"); done
# Токены «имя:ключи» (строки # — комментарии; в файлах титрования наборы через пробел в одну строку).
grep -hv '^#' "${files[@]}" | tr -d '\r' | tr ' ' '\n' | grep ':' |
  awk -v reg="$REG" -F: '
    BEGIN {
      while ((getline line < reg) > 0) {
        if (line ~ /^#/ || line == "") continue
        n = substr(line, 1, index(line, ":") - 1); d = substr(line, index(line, ":") + 1)
        if (n in def) { print "ПОВТОР в реестре: " n; bad = 1 }
        def[n] = d; names[++k] = n
      }
    }
    {
      n = $1; d = substr($0, length(n) + 2)
      if (!(n in def)) { print "НЕТ в реестре: " $0; bad = 1 }
      else if (def[n] != d) { print "ДРУГОЕ определение: " $0 " (реестр: " def[n] ")"; bad = 1 }
      seen++
    }
    END {
      for (i = 1; i <= k; i++) { n = names[i]; by[def[n]] = (def[n] in by) ? by[def[n]] " " n : n; cnt[def[n]]++ }
      for (d in cnt) if (cnt[d] > 1) print "синонимы " d ": " by[d]
      printf "реестр: имён %d, сверено токенов %d — %s\n", k, seen, bad ? "ОШИБКИ" : "ок"
      exit bad
    }'
