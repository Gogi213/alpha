"""Вкладка «Гипотезы» (TK-271, В-26): статус каждой гипотезы и метода пула — из реестра автоматически (покрытие Летописи, очередь,
сито, статус пула); тексты простым языком — `hyp_texts.json` ({"Г-28": {"what": "...", "why": "..."}}, пишет Исследователь).
Чистые функции от данных реестра: ни сети, ни calc."""
import collections, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTS_FILE = os.path.join(HERE, "hyp_texts.json")
STATUS = [("ok", "Посчитана"), ("part", "Посчитана частично"), ("run", "В счёте"), ("wait", "Ждёт решения"),
          ("nodata", "Нет данных"), ("nocode", "Нет кода"), ("bad", "Отвергнута")]
CLASSES = ["Методы", "Стена", "Режим", "Подход", "Реакция", "Вход", "Выход", "Портфель", "Издержки", "Направление"]
REJECT_RE = re.compile(r"^(опровергнуто|отклонено|поиск[−-])")
DONE_RE = re.compile(r"^(подтверждено|не ясно|не определена|поиск[±+]?)")   # посчитано вне сетки F1 (П-02, T-32, поиск E##)
NODATA_RE = re.compile(r"внешн|нет в записи")
WAIT_RE = re.compile(r"заблокирован|владельц")
TAPE_RE = re.compile(r"сделки")


def clean(s, n=190):
    """Статус пула без разметки и ссылок на файлы, обрезан по слову."""
    s = re.sub(r"[*`]", "", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def load_texts(path=None):
    try:
        return json.load(open(path or TEXTS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def popcount(x):
    return bin(x).count("1")


def classify(h, cells, nmon, off_labels):
    """(код статуса, причина словами, F1{n, full, any, hm}) для строки hypotheses.jsonl и её клеток coverage(): [...оси, маска, очередь, код вне формы]."""
    ext = h.get("ext", {})
    pool = clean(ext.get("status_pool_0925"))
    full_mask = (1 << nmon) - 1
    n = len(cells)
    full = sum(1 for c in cells if c[-3] == full_mask)
    anyc = sum(1 for c in cells if c[-3])
    queued = sum(1 for c in cells if c[-2])
    hm = sum(popcount(c[-3]) for c in cells)
    f1 = {"n": n, "full": full, "any": anyc, "hm": hm}
    offs = collections.Counter(c[-1] for c in cells if c[-1] and not c[-3])
    data = ext.get("data") or ""
    if queued:
        return "run", f"В очереди или в счёте: {queued} из {n} хешей сетки", f1
    if ext.get("priority") == "не повторять" or REJECT_RE.match(pool):
        return "bad", pool or "в пуле помечена «не повторять»", f1
    if n and full == n:
        return "ok", f"Все {n} хешей сетки F1 посчитаны по {nmon} мес. ({hm} хеш-мес.)", f1
    if n and anyc:
        return "part", f"Посчитано {anyc} из {n} хешей сетки F1 ({hm} хеш-мес.), остальное не считалось", f1
    if DONE_RE.match(pool):
        return "ok", f"Посчитана вне сетки F1 (П-02, поиск): {pool}", f1
    if n and offs:
        code, k = offs.most_common(1)[0]
        route, text = off_labels[code - 1] if 0 < code <= len(off_labels) else ("", "вне формы")
        if route == "nodata":
            return "nodata", f"Нет данных для счёта ({k} из {n} хешей)", f1
        if route == "noline":
            return "nocode", f"Нет кодировки в счёте ({k} из {n} хешей)", f1
        if route == "outside":
            return "ok", f"Посчитана вне Летописи ({k} из {n} хешей)", f1
        return "wait", f"{text} ({k} из {n} хешей)", f1
    if n:
        return "wait", f"В сетке F1 {n} хешей, счёта ещё не было", f1
    if WAIT_RE.search(pool):
        return "wait", pool, f1
    if NODATA_RE.search(data):
        return "nodata", f"В записи этих данных нет: {data}", f1
    if TAPE_RE.search(data):
        return "nocode", "Нужен вывод ленты сделок (код не написан)", f1
    return "wait", "В сетку F1 не вошла" + (f"; в пуле: {pool}" if pool else ""), f1


def view(hyps, cov, months, off_labels, texts=None, sieve_ok=None, f2=None, f3n=0):
    """Строки вкладки: id, класс, заголовок (техн.), простыми словами, статус и причина, F1, число хешей Летописи.
    off_labels — [(маршрут, подпись)] в порядке OFF_ROUTES; sieve_ok — {гипотеза: число хешей, прошедших сито}."""
    texts = texts or {}
    sieve_ok = sieve_ok or {}
    f2 = f2 or {}   # {гипотеза: {n, ok, plat}} — сито F2 по sieve.jsonl (fraction=2); f3n — строк сита F3
    out = []
    for h in hyps:
        ext = h.get("ext", {})
        cells = cov.get(h["id"], {}).get("cells", [])
        st, why, f1 = classify(h, cells, len(months), off_labels)
        s2 = f2.get(h["id"])
        if s2:
            why += f"; в Летописи также F2: {s2['n']} хеш."   # счётчик «В реестре» видит и F2 (TK-303, Судья 20.10)
        t = texts.get(h["id"]) or {}
        method = ext.get("class") == "method"
        out.append({"id": h["id"], "cls": "Методы" if method else h.get("family", "").capitalize(), "title": h.get("title", ""),
                    "what": t.get("what") or "", "why_text": t.get("why") or "", "mech": ext.get("mechanism", ""),
                    "status": st, "why": why, "f1": f1, "elig": sieve_ok.get(h["id"], 0), "f2": s2, "pool": clean(ext.get("status_pool_0925"), 400)})
    out.sort(key=lambda r: (CLASSES.index(r["cls"]) if r["cls"] in CLASSES else len(CLASSES), int((re.search(r"\d+", r["id"]) or [0])[0])))
    return {"status": [list(s) for s in STATUS], "rows": out, "texts_n": sum(1 for r in out if r["what"]),
            "fr": {"f2_hyps": len(f2), "f2_n": sum(x["n"] for x in f2.values()), "f3_n": f3n}}
