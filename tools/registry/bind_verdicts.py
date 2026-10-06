"""Привязка проверок Судьи к прогонам: отчётные ревью по протоколу/тикету и записи judge в логах тикетов -> verdicts.run_id + runs.judge."""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

ROOT = db.ROOT if hasattr(db, "ROOT") else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
METHOD = re.compile(r"protocol|amend|plan|rule|continuation|owner-delegated|PRECEDENTS|audit|decisions|defaults|canon|consistency|table|lineage|geometry|config")


def word(text):
    low = text.lower()
    m = re.search(r"вернуть|возврат|принят|принимаю|отклон", low)
    if not m:
        return "см. текст"
    return {"в": "вернуть", "п": "принято", "о": "отклонено"}[m.group(0)[0]]


def run(runs_path):
    runs = [json.loads(l) for l in open(runs_path, encoding="utf-8") if l.strip()]
    ver = db.jl("verdicts.jsonl")
    base = {}
    for v in ver:  # перезапуск: свернуть привязанные ревью обратно в исходные строки
        if "@" in v["id"] and not v["id"].startswith("V-TK-"):
            base.setdefault(v["id"].split("@")[0], dict(v, id=v["id"].split("@")[0], run_id=None))
        elif not v.get("run_id") or not v["id"].startswith("V-TK-"):
            if not v.get("run_id"):
                base.setdefault(v["id"], v)
    keep = []
    for v in base.values():
        v["note"] = re.sub(r"^\[[^\]]*\] ", "", v.get("note") or "")
        keep.append(v)
    fixed = [v for v in ver if v.get("run_id") and v["id"].startswith("V-TK-040-2026-10-06")]
    new = []

    def hasd(r, h):
        return h in (r.get("what") or "") + (r.get("hyp") or "")
    # 1) отчётные ревью по протоколу / тикету
    rest = []
    for v in keep:
        v.pop("run_id", None) if v.get("run_id") is None else None
        name = v["id"][2:]
        pm = re.match(r"(P-\d+)-", name); tm = re.match(r"(tk\d{3})-", name)
        head = open(os.path.join(ROOT, v["review_path"]), encoding="utf-8").readline() if os.path.exists(os.path.join(ROOT, v["review_path"])) else ""
        if v["verdict"] == "см. текст" and word(head) != "см. текст":
            v["verdict"] = word(head)
        if METHOD.search(name) or not (pm or tm):
            v["note"] = "[методика/протокол, без прогона] " + (v.get("note") or "")
            rest.append(v); continue
        ts = (v.get("ts") or "9999") + "T99"
        if pm:
            sel = [r for r in runs if (r.get("protocol") or "").split(" ")[0] == pm.group(1) and r["ts"] <= ts]
        else:
            t = "TK-" + tm.group(1)[2:]
            sel = [r for r in runs if r.get("ticket") == t and r["ts"] <= ts]
        if v.get("hyp_id") and pm:
            s2 = [r for r in sel if hasd(r, v["hyp_id"])]
            sel = s2 or sel
        if not sel:
            v["note"] = "[отчётный, прогона в реестре нет] " + (v.get("note") or "")
            rest.append(v); continue
        for r in sel:
            new.append(dict(v, id=f'{v["id"]}@{r["id"]}', run_id=r["id"]))
    # 2) записи judge в логах тикетов: раунд = прогоны тикета между прошлой и этой записью
    for p in sorted(glob.glob(os.path.join(ROOT, ".claude", "tickets", "TK-*.md")) + glob.glob(os.path.join(ROOT, ".claude", "tickets", "archive", "TK-*-log.md"))):
        tk = re.match(r"(TK-\d+)", os.path.basename(p)).group(1)
        txt = open(p, encoding="utf-8").read()
        ents = [(m.group(1), txt[m.end():m.end() + 500]) for m in re.finditer(r"^### (\d{4}-\d\d-\d\dT[\d:+]+) judge\s*$", txt, re.M)]
        prev = ""
        for ts, body in ents:
            sel = [r for r in runs if r.get("ticket") == tk and prev < r["ts"] <= ts]
            prev = max(prev, ts)
            w = word(body[:250])
            for r in sel:
                new.append({"id": f"V-{tk}-{ts[:16]}@{r['id']}", "run_id": r["id"], "hyp_id": None, "judge": "judge", "verdict": w,
                            "review_path": os.path.relpath(p, ROOT).replace("\\", "/") + "#" + ts, "ts": ts[:10],
                            "note": "запись judge в логе тикета: " + body[:160].replace("\n", " ")})
    seen = {}
    for v in new:
        seen[v["id"]] = v
    allv = fixed + rest + list(seen.values())
    db._wjl("verdicts.jsonl", allv)
    # runs.judge: последний по дате решающий вердикт
    by = {}
    for v in seen.values():
        by.setdefault(v["run_id"], []).append(v)
    n = 0
    for r in runs:
        vs = by.get(r["id"])
        if vs and not r.get("judge"):
            vs.sort(key=lambda v: (v["verdict"] != "см. текст", v["ts"] or ""))
            v = vs[-1]
            r["judge"] = f'{v["verdict"]} ({v["ts"]}): {v["review_path"]}'; n += 1
    with open(runs_path, "w", encoding="utf-8", newline="\n") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(seen), len(rest), n
