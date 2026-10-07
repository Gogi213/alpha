#!/usr/bin/env python3
"""Летопись, TK-089 п.1: восстановить конфиг строк «неполно» из текста записи-источника и скриптов репо.
Поле config (JSON): что найдено и откуда; config_status: «восстановлен: …» или «не восстановим: <причина>» (всегда причина).
Идемпотентно: перезаписывает только строки, чей config_status начинался с «конфиг неполон» или «восстановлен»/«не восстановим»."""
import glob, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry as R

FLAG = re.compile(r"(?<![\w-])(--(?:cells|set|hold-step|exit-group|sigma-from|verdict-csv|h3-mode|threads|latency[-\w]*|queue|prob|months?|from|to|pool|root)(?:[ =]+[\w./:+\-]+)?)")
POOL = re.compile(r"\bv\d{3}[a-z]?\b")
BIN = re.compile(r"(?:bin/)?alpha-[0-9a-f]{7,8}(?:-v3)?")
MD5 = re.compile(r"\b[0-9a-f]{32}\b")
PER = re.compile(r"\b(\d\d\.\d\d\s*[–-]\s*\d\d\.\d\d(?:\.\d\d)?|янв\w*\s*[–-]\s*\w+|\d{4}-\d\d-\d\d\s*[–-]\s*\d{4}-\d\d-\d\d)")
HOST = {"89.163.242.211": "calc", "13.140.29.171": "vps", "139.99.91.22": "col", "Steam Deck": "deck", "deck@": "deck"}
SCRIPT = re.compile(r"(?:tools/compute/|~/alpha/bin/|/data/tk\d+/|\b)([\w.-]+\.(?:sh|py))\b")
SCR = {os.path.basename(p): os.path.relpath(p, R.ROOT).replace("\\", "/")
       for p in glob.glob(os.path.join(R.ROOT, "tools", "**", "*.*"), recursive=True) if p.endswith((".sh", ".py"))}
HDR = re.compile(r"^### (\d{4}-\d\d-\d\dT[\d:+]+) (\w+)", re.M)
_cache = {}
OUT = json.load(open(os.path.join(R.ROOT, "docs", "registry", "files", "tk089-outputs-2026-10-08.json"), encoding="utf-8"))  # опись выходов сервера счёта 08.10 (alsched tk089-inspect)
DATE = re.compile(r"20\d\d-\d\d-\d\d")
MON = re.compile(r"(?<![a-z])(jan|feb|mar|apr|may|jun|jul|aug|sep|oct)(?![a-z])")


def from_outputs(text):
    """Выходы, названные в записи: есть ли на диске, сколько файлов, период/пул/команда из имён и мелких файлов."""
    found, gone, per, scr = {}, [], set(), []
    for p in sorted(set(x.rstrip(".,;)") for x in re.findall(r"/data/[\w./-]+", text))):
        d = OUT.get(p)
        if d is None: continue
        if not d["exists"]: gone.append(p); continue
        names = d.get("names", [])
        found[p] = {"files": d.get("n", 1)}
        per |= set(DATE.findall(p + " " + " ".join(names))) | set(MON.findall(p))
        scr += [n for n in names if re.match(r"(cmd|run|B)\d*.*\.sh$", n, re.I)][:3]
        if "verify" in " ".join(d.get("small", {})): found[p]["small"] = {k: v[:60] for k, v in d["small"].items()}
    return found, gone, sorted(per), scr


def entry_text(note):
    m = re.match(r"(.+?)@(\d{4}-\d\d-\d\dT[\d:+]+)$", note or "")
    if not m: return None
    p, ts = os.path.join(R.ROOT, m[1]), m[2]
    if p not in _cache: _cache[p] = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    parts = HDR.split(_cache[p])
    for i in range(1, len(parts) - 2, 3):
        if parts[i] == ts: return parts[i + 2]
    return None


def git_sha(path):
    o = subprocess.run(["git", "-C", R.ROOT, "log", "-1", "--format=%h", "--", path], capture_output=True, text=True).stdout.strip()
    return o or None


def recover(r):
    text = entry_text(r.get("note")) or ""
    src = "запись лога тикета" if text else None
    if not text:
        text = " ".join(str(r.get(k, "")) for k in ("what", "outcome", "result_path"))
        src = "строка журнала/таблицы (what, outcome)"
    cfg, miss = {}, []
    flags = sorted(set(m.strip() for m in FLAG.findall(text)))
    if flags: cfg["flags"] = flags
    pools = sorted(set(POOL.findall(text)))
    if pools: cfg["pool"] = pools
    bins = sorted(set(BIN.findall(text)))
    md5 = r.get("binary_md5") or (MD5.findall(text) or [None])[0]
    if bins: cfg["binaries"] = bins
    if md5: cfg["binary_md5"] = md5
    if r.get("commit"): cfg["commit"] = r["commit"]
    per = PER.findall(text)
    if per: cfg["period"] = sorted(set(re.sub(r"\s+", "", p) for p in per))[:4]
    host = r.get("machine") or next((v for k, v in HOST.items() if k in text), None)
    if host: cfg["machine"] = host
    scripts = {}
    for n in SCRIPT.findall(text):
        if n in SCR: scripts[SCR[n]] = git_sha(SCR[n])
    if scripts: cfg["scripts"] = scripts
    found, gone, oper, oscr = from_outputs(text)
    if found: cfg["outputs"] = found
    if oscr: cfg["output_scripts"] = oscr
    if oper:
        cfg["period"] = sorted(set(cfg.get("period", [])) | set(oper))[:8]
    cfg["outputs_checked"] = "выходы из записи сверены с описью сервера счёта 08.10 (tk089-outputs-2026-10-08.json)" + (f"; исчезли с диска: {', '.join(gone[:3])}" if gone else "")
    cfg["source"] = src
    got = [k for k in ("flags", "pool", "binaries", "binary_md5", "commit", "period", "machine", "scripts", "outputs") if k in cfg]
    s = r["source"]
    if s == "backfill-runs":
        why = "строка runs.csv (до 22.09, тогда реестра не было): в ней итог, но не команда и не входы; файла-источника команды в репо нет"
    elif s == "backfill-journal":
        why = "строка журнала исследований: пересказ шага, команда и входы там не записаны (шаг без прогона или прогон описан в протоколе)"
    elif s == "backfill-experiments":
        why = "строка таблицы П: статус протокола, прогона нет — конфигурировать нечего до счёта"
    else:
        why = "запись лога описывает результат словами; команда и sha входов не приведены, в репо скрипт по имени не найден" if not scripts else \
              "запись лога без полной командной строки и sha входов; скрипт найден, но параметры запуска (env) не записаны"
    if found:  # выход на диске есть: по каждому недостающему полю — отдельная причина (TK-089, Судья 08.10)
        fr = {}
        if "pool" not in cfg: fr["pool"] = "ни запись, ни имена и мелкие файлы выхода не называют пул (v171b и т.п.); guard.json/cells-файлы в выходе не найдены"
        if "flags" not in cfg: fr["flags"] = "флаги запуска в выходе не сохранены (в описи только имена и файлы <2 КБ; cmd*.sh не прочитаны в описи)" if not oscr else "в выходе есть cmd/run-скрипт (output_scripts), его текст описью не снимался"
        if "period" not in cfg: fr["period"] = "в именах выхода нет дат и месяцев"
        if "binary_md5" not in cfg and "binaries" not in cfg: fr["binary"] = "ни md5, ни имя бинарника в записи и выходе"
        cfg["field_reasons"] = fr
    absent = [k for k in ("flags", "pool", "binary_md5", "period", "machine") if k not in cfg and not (k == "binary_md5" and "binaries" in cfg)]
    if found or gone:
        why = ("выход на диске есть (%d), но команда в нём не записана (имена/мелкие файлы дают период, не флаги и sha входов)" % len(found) if found
               else "выход, названный в записи, удалён с диска (проверено 08.10) — команду взять неоткуда") + "; " + why
    if got:
        st = f"восстановлено из текста: {', '.join(got)}; нет: {', '.join(absent) or '—'}; причина остатка: {why}"
    else:
        st = f"не восстановим: {why}"
    return cfg, st


def main():
    rows = R.load(); n = ok = 0
    for r in rows:
        if r.get("status") != "неполно": continue
        cfg, st = recover(r)
        r["config"] = cfg if len(cfg) > 2 else r.get("config")
        r["config"] = r["config"] or None
        r["config_status"] = st
        n += 1; ok += st.startswith("восстановлено")
    with open(R.CANON, "w", encoding="utf-8", newline="\n") as f:
        for r in rows: f.write(json.dumps({k: r[k] for k in R.FIELDS if r.get(k) not in (None, "")}, ensure_ascii=False) + "\n")
    print(f"неполно: {n}; восстановлено частично: {ok}; не восстановим с причиной: {n - ok}")


if __name__ == "__main__":
    main()
