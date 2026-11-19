#!/usr/bin/env python3
"""TK-632: реестры решений владельца → карточки kb/decision/. Повторяемо и идемпотентно (тот же вход → те же байты).

  .claude/decisions.md                      → kb/decision/В-<N>.md   (реестр v2, 14.10 →)
  docs/plan/SETTLED.md + SETTLED-index.md   → kb/decision/S-<N>.md   (ранний реестр, до 12.10)

Номера двух реестров пересекаются, поэтому id разные: «В-N» (decisions.md) и «S-N» (SETTLED; в тексте реестра — «SETTLED В-N»).
«Заменяет: В-M» (только номер, без оговорок) → `supersedes: [В-M]` и статус superseded на В-M. Оговорка («в части…», текст) —
только ссылка и пометка «частично заменена» на старой карточке. Слово владельца и тексты записей — дословно.
Ссылки карточки (`links:`) собираются по упоминаниям В-N / SETTLED В-N / П-NN / TK-NNN / G-NNN — только на существующие карточки,
поэтому запускать после создания карточек протоколов и задач. Карточку без метки `source:` (написана руками) не трогаем.

Запуск из корня репозитория: python tools/kb_import_decisions.py [--dry-run] [--kb kb]
"""
import argparse
import json
import re
import sys
from pathlib import Path

SOURCE_MARK = "kb_import_decisions"
V = "В"  # кириллическая В — так в реестрах
HUB = {"В": "decisions-registry", "S": "settled-registry"}
SRC_FILE = {"В": ".claude/decisions.md", "S": "docs/plan/SETTLED.md"}
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


# ───────────────────────── разбор .claude/decisions.md ─────────────────────────

def parse_decisions(text):
    """→ [dict(num, date, time, head, sut, zam, tail)]; head — [('q', строка цитаты) | ('x', прочая строка)] по порядку."""
    out, cur, state = [], None, None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^## " + V + r"-(\d+) · (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?", line)
        if m:
            cur = {"num": int(m.group(1)), "date": m.group(2), "time": m.group(3) or "", "head": [], "sut": "", "zam": "", "tail": []}
            out.append(cur)
            state = "head"
            continue
        if cur is None or not line.strip():
            continue
        if line.startswith("Суть:"):
            cur["sut"], state = line[len("Суть:"):].strip(), "sut"
        elif line.startswith("Заменяет:"):
            cur["zam"], state = line[len("Заменяет:"):].strip(), "zam"
        elif line.startswith(">") and state == "head":
            cur["head"].append(("q", re.sub(r"^>\s?", "", line)))
        elif state == "head":
            cur["head"].append(("x", line.strip()))
        elif state == "sut":
            cur["sut"] += " " + line.strip()
        else:
            cur["tail"].append(line.strip())
    return out


# ───────────────────────── разбор SETTLED.md ─────────────────────────

_TABLE_REC = re.compile(r"^" + V + r"-(\d+)(?: \(([^)]*)\))? \| (.*)$")
_BOLD_REC = re.compile(r"^\*\*" + V + r"-(\d+)([.,])")


def parse_settled(text):
    """→ [dict(num, kind, lines, header)]: kind main | add (дополнение/правка к тому же номеру). Запись — до следующей записи или `## `."""
    recs, cur = [], None
    seen = set()
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            cur = None
            continue
        m, mb = _TABLE_REC.match(line), _BOLD_REC.match(line)
        if m or mb:
            num = int((m or mb).group(1))
            kind = "add" if num in seen else "main"  # повтор номера — дополнение/правка к первой записи
            seen.add(num)
            cur = {"num": num, "kind": kind, "lines": [line], "table": bool(m)}
            recs.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for r in recs:
        while r["lines"] and not r["lines"][-1].strip():
            r["lines"].pop()
    return recs


def parse_index(text):
    """SETTLED-index.md: `- **В-N** — строка` → {N: строка}."""
    return {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"^- \*\*" + V + r"-(\d+)\*\* — (.*)$", text, re.M)}


# ───────────────────────── карточки ─────────────────────────

def short(text, width=110):
    text = re.sub(r"\s+", " ", text.replace("**", "")).strip()
    if len(text) <= width:
        return text.rstrip(".")
    cut = text[:width].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:—-") + "…"


def first_clause(text):
    m = re.match(r"^(.+?)(?:\. |$)", text.strip())
    return m.group(1) if m else text


def refs_in(text, own_prefix, own_num, ids):
    """Упоминания → id существующих карточек. own_prefix — реестр источника: «В» (decisions.md) или «S» (SETTLED)."""
    out = []

    def add(cid):
        if cid in ids and cid != "%s-%d" % (own_prefix, own_num) and cid not in out:
            out.append(cid)

    plain = re.sub(r"`[^`\n]*`", " ", text)
    for m in re.finditer(r"(SETTLED(?:\.md)?\s+)?" + V + r"-(\d+)(?!\d)", plain):
        if own_prefix == "В":
            add(("S-%s" if m.group(1) else "В-%s") % m.group(2))
        else:
            add("S-%s" % m.group(2))
    for m in re.finditer(r"(?<![\w])[ПP]-(\d{1,2})(?!\d)", plain):
        add("P-%s" % m.group(1))
    for m in re.finditer(r"(?<![\w])TK-(\d{3,4})(?!\d)", plain):
        add("TK-%s" % m.group(1))
    for m in re.finditer(r"(?<![\w])G-(\d{3})(?!\d)", plain):
        add("G-%s" % m.group(1))
    return out


def yaml_list(items):
    return "[" + ", ".join(items) + "]"


def render(meta, body):
    fm = ["---", "id: %s" % meta["id"], "type: decision", "status: %s" % meta["status"],
          "title: %s" % json.dumps(meta["title"], ensure_ascii=False), "date: %s" % meta["date"],
          "links: %s" % yaml_list(meta["links"]), "supersedes: %s" % yaml_list(meta["supersedes"]),
          "source: %s" % SOURCE_MARK, "---"]
    return "\n".join(fm) + "\n" + body.strip("\n") + "\n"


def pure_replaces(zam):
    """«В-8» / «В-1, В-2» → [номера]; любая оговорка → None."""
    z = zam.strip().rstrip(".")
    if re.fullmatch(V + r"-\d+(\s*(,|и)\s*" + V + r"-\d+)*", z):
        return [int(n) for n in re.findall(r"\d+", z)]
    return None


def build_v_cards(decs, ids):
    nums = {d["num"] for d in decs}
    superseded_by, partial_by = {}, {}
    for d in decs:
        if not d["zam"] or d["zam"] in ("—", "-"):
            continue
        pure = pure_replaces(d["zam"])
        targets = [n for n in (pure or [int(x) for x in re.findall(V + r"-(\d+)", d["zam"])]) if n in nums and n != d["num"]]
        for n in targets:
            (superseded_by if pure else partial_by).setdefault(n, []).append(d)
    cards = {}
    for d in decs:
        cid = "%s-%d" % (V, d["num"])
        raw = "\n".join([t for _, t in d["head"]] + [d["sut"], d["zam"]] + d["tail"])
        sup = []
        if d["zam"] and d["zam"] not in ("—", "-"):
            pure = pure_replaces(d["zam"])
            if pure:
                sup = ["%s-%d" % (V, n) for n in pure if n in nums and n != d["num"]]
        links = [HUB["В"], SRC_FILE["В"]] + [x for x in refs_in(raw, "В", d["num"], ids) if x not in sup and x != HUB["В"]]
        body = []
        quotes = [(k, t) for k, t in d["head"]]
        if quotes:
            body.append("## Слово владельца\n")
            prev = None
            for k, t in quotes:
                if k == "q":
                    body.append(("> " + t).rstrip())
                else:
                    if prev == "q":
                        body.append("")
                    body.append(t + "\n")
                prev = k
            body.append("")
        body.append("## Суть\n\n" + d["sut"] + "\n")
        if d["zam"] and d["zam"] not in ("—", "-"):
            body.append("## Заменяет\n\n" + d["zam"] + "\n")
        notes = []
        for s in partial_by.get(d["num"], []):
            notes.append("- Частично заменена [[%s-%d]]: %s" % (V, s["num"], s["zam"]))
        if notes:
            body.append("## Замены\n\n" + "\n".join(notes) + "\n")
        if d["tail"]:
            body.append("## Повторено\n\n" + "\n".join("- " + t for t in d["tail"]) + "\n")
        stamp = d["date"] + (" " + d["time"] if d["time"] else " (время не зафиксировано)")
        body.append("Реестр — [[%s]], раздел «%s-%d» · записано %s." % (SRC_FILE["В"], V, d["num"], stamp))
        status = "superseded" if d["num"] in superseded_by else "done"
        cards[cid] = (dict(id=cid, status=status, title="%s — %s" % (cid, short(d["sut"] and first_clause(d["sut"]) or "")),
                           date=d["date"], links=links, supersedes=sup), "\n".join(body))
        pb = partial_by.get(d["num"], [])
        for s in pb:  # ссылка на частично заменившую — в links, чтобы граф видел связь
            c = "%s-%d" % (V, s["num"])
            if c not in cards[cid][0]["links"]:
                cards[cid][0]["links"].append(c)
    return cards


def build_s_cards(recs, index, ids):
    mains = {}
    for r in recs:
        if r["kind"] == "main":
            mains[r["num"]] = r
    adds = {}
    for r in recs:
        if r["kind"] == "add" and r["num"] in mains:
            adds.setdefault(r["num"], []).append(r)
    own = sorted(mains)
    dates = {}
    for n in own:
        m = ISO.search(mains[n]["lines"][0][:300]) if not mains[n]["table"] else None  # дата — только в заголовке записи
        dates[n] = m.group(0) if m else None
    inherited = set()
    nxt = None
    for n in reversed(own):  # запись без даты — дата ближайшей следующей записи (реестр хронологический)
        if dates[n]:
            nxt = dates[n]
        elif nxt:
            dates[n] = nxt
            inherited.add(n)
    cards = {}
    for n in own:
        r = mains[n]
        cid = "S-%d" % n
        raw = "\n".join(r["lines"] + [l for a in adds.get(n, []) for l in a["lines"]])
        links = [HUB["S"], SRC_FILE["S"], "docs/plan/SETTLED-index.md"] + [x for x in refs_in(raw, "S", n, ids) if x != HUB["S"]]
        head = r["lines"][0]
        if r["table"]:
            problem = _TABLE_REC.match(head).group(3).split(" | ")[0]
            title = short((index.get(n) or problem).split(" | ")[0])
        else:
            t = re.sub(r"^\*\*" + V + r"-\d+[.,]\s*", "", head)  # заголовок — до закрывающих ** или до « (ГГГГ-ММ-ДД»
            title = short(re.split(r"\*\*| \(\d{4}-\d{2}-\d{2}", t)[0].rstrip(". "))
        body = []
        if n in index:
            body.append("## Оглавление\n\n" + index[n] + "\n")
        body.append("## Запись реестра\n")
        if r["table"]:
            m = _TABLE_REC.match(head)
            parts = (m.group(3)).split(" | ", 1)
            qual = (" (" + m.group(2) + ")") if m.group(2) else ""
            body.append("**Возражение%s.** %s\n" % (qual, parts[0]))
            if len(parts) > 1:
                body.append("**Ответ плана.** %s\n" % parts[1])
            rest = "\n".join(r["lines"][1:]).strip("\n")
            if rest:
                body.append(rest + "\n")
        else:
            body.append("\n".join(r["lines"]).strip("\n") + "\n")
        for a in adds.get(n, []):
            body.append("## Дополнение\n\n" + "\n".join(a["lines"]).strip("\n") + "\n")
        foot = "Реестр — [[%s]], оглавление — [[docs/plan/SETTLED-index.md]]; номер в реестре «%s-%d»" % (SRC_FILE["S"], V, n)
        if n in inherited:
            foot += "; в записи нет даты — взята по следующей записи реестра"
        body.append(foot + ".")
        cards[cid] = (dict(id=cid, status="done", title="%s — %s" % (cid, title), date=dates[n] or "2026-09-10", links=links, supersedes=[]),
                      "\n".join(body))
    return cards


# ───────────────────────── запись ─────────────────────────

def existing_ids(kb_dir):
    ids = set()
    for d in kb_dir.iterdir():
        if d.is_dir() and not d.name.startswith((".", "_")):
            ids.update(f.stem for f in d.glob("*.md"))
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--kb", default="kb")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    repo = Path(a.repo)
    kb_dir = repo / a.kb
    out_dir = kb_dir / "decision"
    out_dir.mkdir(parents=True, exist_ok=True)
    decs = parse_decisions((repo / ".claude" / "decisions.md").read_text(encoding="utf-8"))
    recs = parse_settled((repo / "docs" / "plan" / "SETTLED.md").read_text(encoding="utf-8"))
    idx_path = repo / "docs" / "plan" / "SETTLED-index.md"
    index = parse_index(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    ids = existing_ids(kb_dir) | {"%s-%d" % (V, d["num"]) for d in decs} | {"S-%d" % r["num"] for r in recs if r["kind"] == "main"}
    cards = {}
    cards.update(build_v_cards(decs, ids))
    cards.update(build_s_cards(recs, index, ids))
    stat = {"new": 0, "changed": 0, "same": 0, "kept-manual": 0}
    for cid, (meta, body) in sorted(cards.items()):
        path = out_dir / (cid + ".md")
        text = render(meta, body)
        if path.exists():
            old = path.read_text(encoding="utf-8")
            if "\nsource: %s\n" % SOURCE_MARK not in old.split("\n---\n", 1)[0] + "\n":
                stat["kept-manual"] += 1
                continue
            if old == text:
                stat["same"] += 1
                continue
            stat["changed"] += 1
        else:
            stat["new"] += 1
        if not a.dry_run:
            path.write_text(text, encoding="utf-8", newline="\n")
    sup = sum(1 for m, _ in cards.values() if m["status"] == "superseded")
    print("decisions.md: %d, SETTLED: %d, карточек: %d, superseded: %d, %s" % (
        sum(1 for c in cards if c.startswith(V + "-")), sum(1 for c in cards if c.startswith("S-")), len(cards), sup,
        ", ".join("%s %d" % kv for kv in stat.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
