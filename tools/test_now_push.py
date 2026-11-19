"""TK-631: проверка разбора тредов в tools/now_push.py (запуск: python tools/test_now_push.py). Сеть, bb и ssh не трогает."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import now_push as n  # noqa: E402

H = 3600e3
NOW = 1_800_000_000_000.0


def th(id, title, status, upd_h_ago=0.0, created_h_ago=2.0, **kw):
    d = {"id": id, "title": title, "status": status, "createdAt": NOW - created_h_ago * H, "updatedAt": NOW - upd_h_ago * H, "archivedAt": None, "deletedAt": None}
    d.update(kw)
    return d


# 1. заголовок «роль · TK-NNN · тема · vN»
assert n.parse_title("исп · TK-631 · progress-now · v1") == {"role": "исп", "tk": "TK-631", "topic": "progress-now", "version": 1}
assert n.parse_title("пров · TK-580 · bingx-load-sensitivity · v12") == {"role": "пров", "tk": "TK-580", "topic": "bingx-load-sensitivity", "version": 12}
assert n.parse_title("  чит · TK-627 · project cleanup inventory · v2 ")["topic"] == "project cleanup inventory"
for bad in (None, "", "Role: executor", "исп · TK-1 · тема", "исп · progress · тема · v1", "исп · TK-631 · тема · vX", "координатор"):
    assert n.parse_title(bad) is None, bad

# 2. build: статусы, окно 6 ч, архив, порядок
rows = n.build([
    th("a", "исп · TK-1 · old-done · v1", "idle", upd_h_ago=7),                 # закрыт давно — не показывается
    th("b", "исп · TK-2 · fresh-done · v1", "idle", upd_h_ago=0.5),             # закрыт недавно — готово
    th("c", "исп · TK-3 · running · v2", "active", upd_h_ago=0.01, created_h_ago=1),
    th("d", "пров · TK-4 · broken · v1", "error", upd_h_ago=3),                 # ошибка — всегда
    th("e", "исп · TK-5 · archived · v1", "active", archivedAt=1),              # архив — нет
    th("f", "исп · TK-6 · edge-in · v1", "idle", upd_h_ago=5.99),
    th("g", "исп · TK-7 · edge-out · v1", "idle", upd_h_ago=6.01),
    th("h", "не по формату", "active", upd_h_ago=0.1),                          # без номера — всё равно показывается
    th("i", "исп · TK-8 · weird · v1", "starting"),                             # неизвестный статус — нет
], NOW)
ids = [r["id"] for r in rows]
assert ids == ["c", "h", "d", "b", "f"], ids          # run (новее выше) → fail → done (новее выше)
by = {r["id"]: r for r in rows}
assert by["c"]["state"] == "run" and by["c"]["tk"] == "TK-3" and by["c"]["topic"] == "running" and by["c"]["role"] == "исп" and by["c"]["version"] == 2
assert by["d"]["state"] == "fail" and by["b"]["state"] == "done"
assert by["c"]["started_ts"] == (NOW - 1 * H) / 1000 and by["c"]["updated_ts"] == (NOW - 0.01 * H) / 1000
assert by["h"]["tk"] is None and by["h"]["topic"] is None and by["h"]["title"] == "не по формату"
assert n.build([th("x", "исп · TK-9 · t · v1", "idle", upd_h_ago=2)], NOW, done_hours=1) == []

# 3. цепочка задачи: у задачи с живым/свежим тредом в now.json попадают и остальные неархивные треды этого TK (даже старые закрытые)
rows = n.build([
    th("e1", "исп · TK-10 · chain · v1", "idle", upd_h_ago=9, created_h_ago=12),             # старый исполнитель той же задачи
    th("v1", "пров · TK-10 · chain · v1", "active", upd_h_ago=0.1, created_h_ago=1),         # проверяющий идёт
    th("e2", "исп · TK-10 · chain · v1", "idle", upd_h_ago=8, created_h_ago=9, archivedAt=5),  # архивный — не входит
    th("o1", "исп · TK-11 · old-pair · v1", "idle", upd_h_ago=9),                            # вся задача давно закрыта — нет
    th("o2", "пров · TK-11 · old-pair · v1", "idle", upd_h_ago=8),
    th("n1", "не по формату", "idle", upd_h_ago=9),                                          # без номера — по себе, не по задаче
], NOW)
assert [r["id"] for r in rows] == ["v1", "e1"], [r["id"] for r in rows]

# 4. пустой и «битый» ввод не роняет
assert n.build([], NOW) == []
assert n.build([{"id": "z", "title": None, "status": "active", "createdAt": None, "updatedAt": None}], NOW)[0]["started_ts"] is None
print("test_now_push: OK")
