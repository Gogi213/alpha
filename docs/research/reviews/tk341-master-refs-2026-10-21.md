# TK-341 — проверка после слияния PR81 (Судья, 21.10 ~16:00)

| пункт договора | да/нет | на чём проверено |
|---|---|---|
| PR81 слит | да | `gh pr view 81`: MERGED 12:05Z, d5e0a39 — предок origin/master |
| tk0279 удалён, +203 ГБ | да | принято ранее (reviews/tk341-refs-tools) |
| Г-135/136 «Отвергнута» | да | принято ранее |
| ссылки на tk0279 в tools/ = 0 | **нет** | `git grep -n tk0279 origin/master -- tools docs/registry` — 6 строк |

Ссылки пришли в master слиянием PR82 (TK-343, ветка tk289) после приёмки PR81:

- tools/compute/tk289-f1.sh:7, tk289-f2.sh:8, tk289-f6b.sh:7, tk289-f6v.sh:7, tk289-f8-psim.sh:8, tk289-f8.sh:16 —
  `/data/tk0279/days9.txt` (каталог удалён; файл теперь `/data/tk0341/keep/days9.txt`).

Вернуть: заменить путь на `/data/tk0341/keep/days9.txt` в 6 скриптах (или перенести их в docs/archive, если П-18 закрыт
и перезапуск не нужен), PR в master; критерий — `git grep tk0279 origin/master -- tools docs/registry` = 0.
