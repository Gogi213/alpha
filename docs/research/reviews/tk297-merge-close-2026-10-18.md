# TK-297 — закрытие после слияния (Судья, 18.10)

| пункт договора | да/нет | на чём проверено |
|---|---|---|
| PR #61 влит на принятой голове | да | gh pr view 61: MERGED, head 01816b29, merge 8fd26bbc |
| PR #60 влит | да | gh pr view 60: MERGED, merge 0796e45c |
| голова в master | да | git merge-base --is-ancestor 01816b29 origin/master |
| тег archive/tk274 | да | ls-remote: 0629c1ba = голова tk274 |
| тег archive/tk291 | да | ls-remote: 01816b29 = голова tk291 |
| ветки tk274, tk291 удалены на origin | нет — ролям закрыто | ls-remote: обе есть; удаление на origin хук разрешает только CEO → хвост CEO |

Ключевые числа:

| число | источник |
|---|---|
| 01816b29 | docs/research/reviews/tk297-merge-close-2026-10-18.md:5 |
| 0629c1ba | docs/research/reviews/tk297-merge-close-2026-10-18.md:8 |

Вердикт: принято; хвост CEO — `git push origin --delete tk274 tk291` (теги archive/* сохраняют коммиты), локальные ветки — тоже удалить.
