"""chron_metrics — числа записей летописи из файлов результатов в git (TK-647): запись × сторона × клетка × вселенная × окно.

Источник — только закоммиченные файлы результатов (docs/findings/**, docs/archive/findings/**, docs/research/freeze/**):
JSON — любой словарь с числом сделок (`n` / `trades` / `n_trades`) и суммой (`sum` / `sum_usd` / `usd`); CSV — строки
с колонками сделок и $. Числа берутся как есть (п. 9: измерено, не разбор текста); средний $ — из файла, иначе Σ / n.
$ на ставку 500 $ и Шарп (годовой по дневному, tk625_stats.sharpe_year / tk616_forward.sharpe_of) — только у движка П-42
(номинал 500 $); у других проектов ставка в файле не названа — сумма уходит в extra.usd_raw, поле $ пусто.

Какие файлы — чьи: ссылки записи и пути в её тексте (перенесённое из docs/findings/ в docs/archive/findings/ находится),
каталог с именем отчёта рядом с отчётом; файл с «tkNNN» в пути — только записям задачи TK-NNN (иначе базовая заморозка
чужой задачи стала бы числами этой записи).
"""
import csv
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

DATA_EXT = (".json", ".csv", ".tsv")
REPORT_DIRS = ("docs/findings/", "docs/archive/findings/")
PATH_RE = re.compile(r"(?<![\w/.-])((?:docs)/[\w./@{}-]*\w)")
SKIP_KEYS = {"pairs", "kinds", "rr", "stops", "diag", "seams", "noise", "months_base", "holm", "overlap", "overlap_n24_base", "thr",
             "coverage", "dmonths", "boot", "z_holm", "z_pow", "m_holm"}
CONTAINERS = {"cells", "rows", "sides", "desc", "gmk", "ladders", "pool", "selection", "bases", "families", "slices", "gates", "res"}
MONTHS = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
          "nov": "11", "dec": "12", "январь": "01", "февраль": "02", "март": "03", "апрель": "04", "май": "05", "июнь": "06", "июль": "07",
          "август": "08", "сентябрь": "09", "октябрь": "10", "ноябрь": "11", "декабрь": "12"}
UNI_RE = re.compile(r"(?<![a-z0-9])(h775|o566|u0|u1|t566|t544|gate566|old566|all|crypto|tradfi)(?:_t24)?(?![a-z0-9])")
LABEL_KEYS = ("name", "cid", "cell", "family", "id", "set", "hyp")
EXTRA_KEYS = ("p", "p_holm", "lo90", "hi90", "pass", "holm_reject", "maxdd", "maxdd_usd", "per_day", "usd_per_day", "rr", "few",
              "robust_days", "robust_coins", "in_base", "delta_sum", "delta_mean", "positive_months", "counted_months")
TEXT_VERDICTS = ("работает", "не работает", "не ясно", "описательно")


def git_out(root, args):
    r = subprocess.run(["git"] + args, cwd=str(root), capture_output=True, encoding="utf-8")
    return r.stdout if r.returncode == 0 else ""


class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self.files = set(git_out(self.root, ["ls-files"]).split("\n")) - {""}
        self.data = sorted(f for f in self.files if f.endswith(DATA_EXT) and f.startswith("docs/"))
        self._commit = {}

    def fix(self, p):
        """Путь из текста → путь в git (перенесённое в docs/archive/findings/ — туда)."""
        p = re.sub(r"[.,:;)`»]+$", "", p.split("#")[0])
        if p in self.files or any(f.startswith(p.rstrip("/") + "/") for f in self.data):
            return p
        if p.startswith("docs/findings/"):
            q = "docs/archive/findings/" + p[len("docs/findings/"):]
            if q in self.files or any(f.startswith(q.rstrip("/") + "/") for f in self.data):
                return q
        return p

    def expand(self, p):
        """Путь → файлы результатов: сам файл, каталог, каталог/файлы с именем отчёта (x.md → x/…, x.json, x-…)."""
        if p.endswith(DATA_EXT):
            return {p} if p in self.files else set()
        stem = p[:-3] if p.endswith(".md") else p.rstrip("/")
        return {f for f in self.data if f.startswith(stem + "/") or f.startswith(stem + ".") or f.startswith(stem + "-")}

    def commit(self, path):
        if path not in self._commit:
            self._commit[path] = git_out(self.root, ["log", "-1", "--format=%h", "--", path]).strip() or None
        return self._commit[path]


def entry_files(repo, entry, link_files):
    """Файлы записи: (файлы результатов, отчёты md) — ссылки file: и пути в тексте; tkNNN в пути — только своей задаче."""
    paths = {repo.fix(m.group(1)) for m in PATH_RE.finditer(entry["body"] or "")} | {repo.fix(p) for p in link_files}
    data, md = set(), set()
    for p in paths:
        data |= repo.expand(p)
        if p.endswith(".md") and p in repo.files and p.startswith(REPORT_DIRS):
            md.add(p)
    tasks = {t.lower().replace("-", "") for t in re.findall(r"TK-\d+", (entry.get("task") or "") + " " + entry["title"])}

    def own(f):
        t = set(re.findall(r"tk\d+", f.lower()))
        return not t or bool(t & tasks)
    return sorted(f for f in data if own(f)), sorted(f for f in md if own(f))


def side_of(tok):
    t = str(tok).lower()
    if t in ("l", "long", "лонг") or re.match(r"^l_", t) or re.search(r"(?:^|[-_])l$", t):
        return "long"
    if t in ("s", "short", "шорт") or re.match(r"^s_", t) or re.search(r"(?:^|[-_])s$", t):
        return "short"
    return None


def month_of(tok):
    t = str(tok).lower()
    if t in MONTHS:
        return t
    if re.match(r"^20\d\d-\d\d[a-zа-я]?$", t) or t in ("all", "01–13.11", "01-13.11"):
        return t
    return None


def side_word(x):
    t = x.lower()
    lo, sh = bool(re.search(r"лонг|long", t)), bool(re.search(r"шорт|short", t))
    return "long" if lo and not sh else "short" if sh and not lo else None


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def label(o, k):
    if isinstance(o, dict):
        for lk in LABEL_KEYS:
            if isinstance(o.get(lk), (str, int)) and not isinstance(o.get(lk), bool):
                return str(o[lk])
        if isinstance(o.get("month"), str):
            return o["month"]
    return str(k)


def metric_dict(o):
    """Словарь — строка чисел? → (сделок, сумма $) или None."""
    n = next((num(o.get(k)) for k in ("n", "trades", "n_trades") if num(o.get(k)) is not None), None)
    s = next((num(o.get(k)) for k in ("sum", "sum_usd", "usd") if num(o.get(k)) is not None), None)
    if n is None or s is None:
        return None
    return int(n), s


def json_rows(obj):
    """Все строки чисел JSON: [(путь ключей, словарь)]; внутрь строки — только в помесячные «months»."""
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            if metric_dict(o):
                out.append((path, o))
                ms = o.get("months")
                if isinstance(ms, list) and ms and all(isinstance(x, dict) and metric_dict(x) for x in ms):
                    for i, x in enumerate(ms):
                        out.append((path + [label(x, i)], x))
                return
            for k, v in o.items():
                if k in SKIP_KEYS:
                    continue
                walk(v, path + [str(k)])
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + [label(v, i)])
    walk(obj, [])
    return out


def describe(path, fname, top, o):
    """Путь ключей + имя файла → сторона, клетка, вселенная, окно."""
    side = side_of(o["side"]) if isinstance(o.get("side"), str) else None
    win = None
    cell_toks = []
    for t in path:
        s = side_of(t)
        if s and not side and t.lower() in ("l", "s", "long", "short", "лонг", "шорт"):
            side = s
            continue
        m = month_of(t)
        if m and t is path[-1]:
            win = m
            continue
        if t in CONTAINERS:
            continue
        cell_toks.append(t)
    if not side:
        for t in path:
            side = side or side_of(t)
    if not side and isinstance(top, dict) and top.get("side"):
        side = side_of(top["side"])
    if not side:
        m = re.search(r"[-_]([LS])\.(?:json|csv)$", fname)
        side = side_of(m.group(1)) if m else None
    uni = None
    if isinstance(top, dict) and isinstance(top.get("uni"), str):
        uni = top["uni"]
    if not uni:
        for t in path + [Path(fname).stem]:
            m = UNI_RE.search(str(t).lower())
            if m:
                uni = m.group(0)
                break
    if not uni:  # каталог П-42 «…-775-…» / «…-566-…»: h775 — честная вселенная 775 монет, o566 — старая 566 (§13.28.1)
        m = re.search(r"(?:^|[-_/])(775|566)(?:[-_/]|$)", str(Path(fname).parent))
        uni = {"775": "h775", "566": "o566"}[m.group(1)] if m else None
    if isinstance(o.get("month"), str):
        win = o["month"]
    if not win and isinstance(top, dict):
        for k in ("window", "window_dec", "period"):
            if isinstance(top.get(k), str):
                win = top[k]
                break
    cell = " / ".join(cell_toks) or "—"
    return side, cell, uni, win


def verdict_of(o):
    v = o.get("verdict")
    if isinstance(v, str):
        t = v.strip().lower()
        for x in TEXT_VERDICTS:
            if t == x or t.startswith(x + ":") or t.startswith(x + " "):
                return x if not (x == "работает" and t.startswith("не ")) else None
    return None


def same_data_of(text):
    return 1 if re.search(r"найден\w* (?:отбором )?на (?:этих|тех же) данных", text.lower().replace("ё", "е")) else None


def rows_from_json(repo, f, p42):
    raw = (repo.root / f).read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
    except ValueError:
        return []
    sd_file = same_data_of(raw)
    out = []
    for path, o in json_rows(obj):
        n, s = metric_dict(o)
        side, cell, uni, win = describe(path, f, obj, o)
        mean = num(o.get("mean")) if num(o.get("mean")) is not None else num(o.get("mean_usd"))
        if mean is None and n:
            mean = s / n
        extra = {k: o[k] for k in EXTRA_KEYS if k in o and (num(o[k]) is not None or isinstance(o[k], bool))}
        sharpe = num(o.get("sharpe"))
        row = {"side": side, "cell": cell, "uni": uni, "win": win, "trades": n, "verdict": verdict_of(o),
               "same_data": 1 if sd_file and "selection" in path else None, "from_text": 0, "src": f, "src_ptr": "/".join(path)}
        if p42:
            row.update(usd=s, mean_usd=mean, sharpe=sharpe)
        else:
            row.update(usd=None, mean_usd=None, sharpe=None)
            extra["usd_raw"] = s
            if sharpe is not None:
                extra["sharpe_raw"] = sharpe
            extra["note"] = "ставка в файле не названа — $ не пересчитан на 500 $"
        if isinstance(o.get("verdict"), str) and not row["verdict"]:
            extra["verdict_text"] = o["verdict"][:200]
        row["extra"] = extra or None
        out.append(row)
    return out


CSV_N = ("n", "trades")
CSV_USD = ("usd", "total_usd", "sum_usd")
CSV_LABEL = ("side", "family", "set", "form", "hyp", "hash", "mode", "feature", "bucket", "profile_id", "pair")


def rows_from_csv(repo, f, p42):
    text = (repo.root / f).read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    if not lines:
        return []
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ("\t" if f.endswith(".tsv") else ",")
    rd = [r for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim) if r]
    if not rd:
        return []
    head, body = rd[0], rd[1:]
    idx = {h: i for i, h in enumerate(head)}
    pairs = []  # (окно-префикс, колонка n, колонка $, колонка вердикта)
    n0 = next((c for c in CSV_N if c in idx), None)
    u0 = next((c for c in CSV_USD if c in idx), None)
    if n0 and u0:
        pairs.append((None, n0, u0, "verdict" if "verdict" in idx else None))
    for h in head:
        m = re.match(r"^(.+)_n$", h)
        if m and m.group(1) + "_usd" in idx:
            pairs.append((m.group(1), h, m.group(1) + "_usd", m.group(1) + "_verdict" if m.group(1) + "_verdict" in idx else None))
    # колонки, одинаковые во всех строках (база, повторённая у каждой гипотезы, «b1_…»), — не числа клетки
    def const(c):
        return len(body) > 1 and len({r[idx[c]] for r in body if len(r) > idx[c]}) == 1
    pairs = [p for p in pairs if not (p[0] and const(p[1]) and const(p[2]))]
    if not pairs:
        return []
    out = []
    for ln, r in enumerate(body, 2):
        if "coin" in idx and r[idx["coin"]] not in ("PORTFOLIO", "ALL", ""):
            continue  # по монетам — не клетка
        lab = [r[idx[c]] for c in CSV_LABEL if c in idx and c != "side" and r[idx[c]]]
        side = side_of(r[idx["side"]]) if "side" in idx else None
        win = r[idx["month"]] if "month" in idx else None
        for pre, cn, cu, cv in pairs:
            try:
                n, s = int(float(r[idx[cn]])), float(r[idx[cu]])
            except (ValueError, IndexError):
                continue
            row = {"side": side, "cell": " / ".join(lab) or "—", "uni": None, "win": pre or win, "trades": n, "from_text": 0,
                   "src": f, "src_ptr": "строка %d%s" % (ln + sum(1 for l in text.splitlines()[:ln] if l.startswith("#")), " · " + pre if pre else ""),
                   "verdict": None, "same_data": None}
            extra = {}
            if cv:
                vt = r[idx[cv]].strip()
                row["verdict"] = {"мало данных": "не ясно"}.get(vt) if vt not in TEXT_VERDICTS else vt
                if not row["verdict"] and vt:
                    extra["verdict_text"] = vt
            if p42:
                row.update(usd=s, mean_usd=s / n if n else None, sharpe=None)
            else:
                row.update(usd=None, mean_usd=None, sharpe=None)
                extra.update(usd_raw=s, note="ставка в файле не названа — $ не пересчитан на 500 $")
            row["extra"] = extra or None
            out.append(row)
    return out


# ── таблицы отчётов md — «из текста» (числа напечатаны скриптом счёта в отчёт, но читаются из текста): только таблица,
# где ровно одна колонка сделок и ровно одна колонка суммы $; иначе таблица пропускается (не угадывать, п. 9).
MD_TRADES = re.compile(r"^(?:сделок|сделки|сд\.?|n|N|trades)$")
MD_SUM = re.compile(r"(?i)(?:сумма|итог|σ|Σ|p&l|sum)[^|]*\$|\$\s*(?:итог|сумма)|^Σ\s*\$$|^\$$")
MD_NOT_SUM = re.compile(r"(?i)/\s*сут|/\s*сд|сделку|в сутки|за сутки|на сделку|bps|%|Δ|без |нижн|верхн|SE")
MD_MEAN = re.compile(r"(?i)^(?:среднее|ср\.?)[^|]*\$\s*/\s*сд|^\$\s*/\s*(?:сделку|сд\.?)(?:\s|$)|^средн\w* сделка, \$")
MD_SHARPE = re.compile(r"(?i)^(?:шарп|sharpe)(?:\s*\(?год\w*\)?)?$")
MD_WIN = re.compile(r"(?i)^(?:окно|месяц|половина|период)$")
MD_VERDICT = re.compile(r"(?i)^(?:вердикт|чтение|итог чтения)$")
COIN_RE = re.compile(r"[A-Z0-9]{2,}(?:USDT|USDC|PERP)")
MD_NUM = re.compile(r"[−+-]?\$?\s?\d{1,3}(?:[  ]\d{3})+(?:[,.]\d+)?|[−+-]?\$?\s?\d+(?:[,.]\d+)?")


def md_num(cell):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|`[^`]*`|\*", " ", cell).strip()
    m = MD_NUM.match(t)
    if not m or t[m.end():].strip()[:1] not in ("", "$"):
        return None
    v = m.group(0).replace("$", "").replace("−", "-").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def md_cells(line):
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def rows_from_md(repo, f, p42):
    lines = (repo.root / f).read_text(encoding="utf-8").splitlines()
    out, i, heading = [], 0, ""
    fside = side_word(Path(f).stem)
    while i < len(lines) - 1:
        if lines[i].startswith("#"):
            heading = lines[i].lstrip("#").strip()
        if not (lines[i].startswith("|") and re.match(r"^\|[\s:|-]*-[\s:|-]*\|?\s*$", lines[i + 1])):
            i += 1
            continue
        head = [re.sub(r"\*|`", "", h).strip() for h in md_cells(lines[i])]
        tcols = [j for j, h in enumerate(head) if MD_TRADES.match(h)]
        scols = [j for j, h in enumerate(head) if MD_SUM.search(h) and not MD_NOT_SUM.search(h)]
        mcols = [j for j, h in enumerate(head) if MD_MEAN.search(h)]
        shcols = [j for j, h in enumerate(head) if MD_SHARPE.match(h)]
        wcols = [j for j, h in enumerate(head) if MD_WIN.match(h)]
        vcols = [j for j, h in enumerate(head) if MD_VERDICT.match(h)]
        j = i + 2
        while j < len(lines) and lines[j].startswith("|"):
            j += 1
        coin_table = any(re.match(r"(?i)^(?:№|монета|монеты|coin|symbol|символ|контракт)$", h) for h in head[:2])
        if len(tcols) == 1 and len(scols) == 1 and not coin_table:  # таблица по монетам — не клетки
            used = set(tcols + scols + mcols + shcols + wcols + vcols)
            labc = []  # подписи — ведущие колонки до первой колонки чисел
            for k, h in enumerate(head):
                if k in used or re.search(r"\$|%|сут|Δ", h):
                    break
                labc.append(k)
            for ln in range(i + 2, j):
                c = md_cells(lines[ln])
                if len(c) < len(head):
                    continue
                n, u = md_num(c[tcols[0]]), md_num(c[scols[0]])
                if n is None or u is None or n != int(n):
                    continue
                lab = [re.sub(r"`|\*", "", c[k]).strip() for k in labc if c[k].strip()]
                if any(COIN_RE.search(x) for x in lab):
                    continue  # строка по монете — не клетка
                side = None
                for x in lab:
                    side = side or (side_of(x.split()[0]) if x.split() else None) or side_word(x)
                side = side or side_word(heading) or fside  # сторона — из строки, иначе из заголовка раздела / имени отчёта
                mean = md_num(c[mcols[0]]) if len(mcols) == 1 else None
                sh = md_num(c[shcols[0]]) if len(shcols) == 1 else None
                vt = re.sub(r"\*", "", c[vcols[0]]).strip().lower() if len(vcols) == 1 else ""
                um = re.search(r"(?<![\d.,])(h775|o566|775|566)(?![\d.,])", " ".join(lab)) if p42 else None
                row = {"side": side, "cell": " / ".join(lab) or "—", "uni": {"775": "h775", "566": "o566"}.get(um.group(1), um.group(1)) if um else None,
                       "win": c[wcols[0]] if len(wcols) == 1 else None,
                       "trades": int(n), "verdict": vt if vt in TEXT_VERDICTS else None, "same_data": None, "from_text": 1,
                       "src": f, "src_ptr": "строка %d" % (ln + 1)}
                extra = {"head": " | ".join(head)[:300], "section": heading[:200]}
                if p42:
                    row.update(usd=u, mean_usd=mean if mean is not None else (u / n if n else None), sharpe=sh)
                else:
                    row.update(usd=None, mean_usd=None, sharpe=None)
                    extra.update(usd_raw=u, note="ставка в отчёте не названа — $ не пересчитан на 500 $")
                    if sh is not None:
                        extra["sharpe_raw"] = sh
                row["extra"] = extra
                out.append(row)
        i = j
    return out


def row_hash(eid, r):
    key = json.dumps([eid, r["src"], r.get("src_commit"), r.get("src_ptr"), r["side"], r["cell"], r["uni"], r["win"], r["trades"],
                      r.get("usd"), r.get("mean_usd"), r.get("sharpe"), r.get("verdict"), r.get("same_data"), r["from_text"],
                      r.get("extra")], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build(root, entries):
    """entries: [{id, project, task, title, body, links: [file paths]}] → (строки метрик с entry / src_commit / h, {id: файлы})."""
    repo = Repo(root)
    cache, out, files_of = {}, [], {}
    for e in entries:
        data, md = entry_files(repo, e, e.get("links") or [])
        files_of[e["id"]] = data + md
        p42 = e.get("project") == "П-42"
        got = set()
        for f in data + md:
            k = (f, p42)
            if k not in cache:
                rows = (rows_from_json(repo, f, p42) if f.endswith(".json") else rows_from_md(repo, f, p42) if f.endswith(".md")
                        else rows_from_csv(repo, f, p42))
                c = repo.commit(f)
                for r in rows:
                    r["src_commit"] = c
                cache[k] = rows
            if f.endswith(".md") and got:
                continue  # есть числа из файлов результатов — текст отчёта не нужен
            for r in cache[k]:
                r2 = dict(r, entry=e["id"])
                r2["h"] = row_hash(e["id"], r2)
                out.append(r2)
                got.add(f)
    return out, files_of


METRIC_ARG_KEYS = {"side": "side", "cell": "cell", "uni": "uni", "universe": "uni", "win": "win", "window": "win", "trades": "trades",
                   "usd": "usd", "mean": "mean_usd", "mean_usd": "mean_usd", "sharpe": "sharpe", "verdict": "verdict",
                   "same_data": "same_data", "text": "from_text", "src": "src"}


def parse_metric_arg(s):
    """«side=long; cell=L_n24; uni=h775; win=01.07–13.11; trades=181; usd=294.31; sharpe=5.11; verdict=не ясно; same_data=1;
    src=docs/research/freeze/tk638-frozen.json#selection/cell_h775» → строка метрики (src обязателен; text=1 — «из текста»)."""
    parts = [p for p in re.split(r";" if ";" in s else r",(?=\s*[a-z_]+\s*=)", s) if p.strip()]
    r = {"from_text": 0}
    for p in parts:
        if "=" not in p:
            raise ValueError("--metric: «%s» — нужно ключ=значение" % p.strip())
        k, v = (x.strip() for x in p.split("=", 1))
        if k not in METRIC_ARG_KEYS:
            raise ValueError("--metric: ключ «%s» — только %s" % (k, ", ".join(sorted(METRIC_ARG_KEYS))))
        k = METRIC_ARG_KEYS[k]
        if k in ("trades",):
            v = int(float(v.replace(" ", "").replace(",", ".")))
        elif k in ("usd", "mean_usd", "sharpe"):
            v = float(v.replace("−", "-").replace(" ", "").replace(",", "."))
        elif k in ("same_data", "from_text"):
            v = {"1": 1, "да": 1, "0": 0, "нет": 0}[v]
        elif k == "side":
            v = side_of(v) or ({"both": "both", "обе": "both"}.get(v.lower()) if v else None)
            if not v:
                raise ValueError("--metric: side — long / short / both")
        r[k] = v
    if not r.get("src"):
        raise ValueError("--metric: нужен src= (файл результатов; для «из текста» — файл:строка и text=1)")
    src = r["src"]
    if "#" in src:
        src, r["src_ptr"] = src.split("#", 1)
    if "@" in src:
        src, r["src_commit"] = src.split("@", 1)
    r["src"] = src
    if r.get("trades") and r.get("usd") is not None and r.get("mean_usd") is None:
        r["mean_usd"] = r["usd"] / r["trades"]
    for k in ("side", "cell", "uni", "win", "trades", "usd", "mean_usd", "sharpe", "verdict", "same_data", "src_ptr", "src_commit", "extra"):
        r.setdefault(k, None)
    return r
