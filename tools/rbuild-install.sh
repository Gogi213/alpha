# TK-614: установка собранного бинаря по содержимому. Не запускается сама — клиенты (tools/p42/build-srv.sh, tools/vps-check.sh bin)
# вставляют текст в файл команды раннера. Без одинарных кавычек: на Софии текст идёт в bash -c '...'.
# rb_install <собранный файл> <BIN_OUT>: кладёт файл в <BIN_OUT>.<md5, 8 символов> (прежние не затираются) и атомарно переставляет
# ссылку <BIN_OUT>. Время изменения ссылки = время прихода запроса, который её поставил (RBUILD_T0 от раннера): если ссылка новее прихода
# этого запроса — её поставил более новый запрос, старый её не трогает (инцидент v3 19.11: старая сборка из очереди затёрла новую).
rb_install() {
  local s="$1" o="$2" t="${RBUILD_T0:-$(date +%s.%N)}" m d m0
  m=$(md5sum "$s" | cut -d" " -f1); d="$o.${m:0:8}"
  if [ -e "$d" ] && [ "$(md5sum "$d" | cut -d" " -f1)" != "$m" ]; then d="$o.$m"; fi
  [ -e "$d" ] || { install -D -m 755 "$s" "$d.new$$" && mv -f "$d.new$$" "$d"; } || return 1
  echo "rbuild-install: $d"; md5sum "$d"
  if [ -e "$o" ] || [ -L "$o" ]; then
    if awk -v a="$(stat -c %.9Y "$o")" -v b="$t" "BEGIN{exit !(a > b)}"; then
      echo "rbuild-install: $o новее запроса (ссылку поставил более новый запрос) - оставлена на $(readlink "$o")"; return 0
    fi
    if [ ! -L "$o" ]; then m0=$(md5sum "$o" | cut -c1-8); [ -e "$o.$m0" ] || ln "$o" "$o.$m0"; fi   # прежний обычный файл сохраняем под его md5
  fi
  ln -sfn "$(basename "$d")" "$o.lnk$$" && touch -h -d "@$t" "$o.lnk$$" && mv -T "$o.lnk$$" "$o" && echo "rbuild-install: $o -> $(basename "$d")"
}
