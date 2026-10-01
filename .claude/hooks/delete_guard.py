"""Удаление — только внутри своей папки (владелец 27.09: «нельзя удалять ничего кроме чего то внутри своей папки»).

Вызывается из PreToolUse-хука Bash/PowerShell (`~/.claude/hooks/alpha_one_build.py`, только в проекте alpha).
Своя папка: локально — папка проекта и scratchpad сессии; на Steam Deck — `~/alpha/<подкаталог>`; на VPS —
`/opt/alpha-compute/<подкаталог>`. Никогда: коллектор (`/opt/alpha`), Storage Box, записи `root/` и `deep/`
(единственные копии, В-106). Цель, которую нельзя проверить (переменная без буквального префикса, относительный путь
после `ssh`/`cd` не в проект), — отказ: переписать явным путём.
"""
import re

VERBS = re.compile(
    r"(?:^|[\s;&|(`'\"])(?:sudo\s+)?(rm|rmdir|unlink|shred|Remove-Item|ri|del|erase|rd)(?=\s)"
    r"|find\s[^;&|\n]*-delete|rsync\s[^;&|\n]*--delete|git\s+clean|shutil\.rmtree|os\.(?:remove|unlink|rmdir)"
    r"|\.unlink\(|rmtree\(", re.I)
SEP = re.compile(r"[;&|\n)`]")
LOCAL_ROOTS = ("c:/visual projects/alpha/", "/c/visual projects/alpha/")
SCRATCH = "/appdata/local/temp/claude/"
REMOTE_ROOTS = ("~/alpha/", "$home/alpha/", "${home}/alpha/", "/home/deck/alpha/", "/opt/alpha-compute/")
FORBIDDEN_SEG = ("root", "deep")
REASON = ("Удаление запрещено вне своей папки (владелец 27.09: «нельзя удалять ничего кроме чего то внутри своей "
          "папки»). Можно только явным путём: локально — внутри C:/visual projects/alpha или scratchpad сессии; "
          "на Steam Deck — ~/alpha/<подкаталог>; на VPS — /opt/alpha-compute/<подкаталог>. Коллектор, Storage Box, "
          "записи root/ и deep/ — никогда (только владелец через CEO). Непроверяемая цель: {t}")


def norm(p):
    return p.strip().strip("'\"").replace("\\", "/").lower()


def targets(cmd, m):
    verb = (m.group(1) or "").lower()
    if not verb:  # find/rsync/git clean/python — цель берём из хвоста выражения
        tail = cmd[m.start():SEP.search(cmd, m.end()).start() if SEP.search(cmd, m.end()) else len(cmd)]
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", tail)
        words = [w for w in re.split(r"\s+", tail) if w and not w.startswith("-")]
        if "find" in tail[:6].lower():
            return words[1:2] or ["<find без пути>"]
        if "rsync" in tail[:7].lower():
            return words[-1:] or ["<rsync без цели>"]
        if "git" in tail[:4].lower():
            return ["."]
        return quoted or ["<цель из кода не видна>"]
    end = SEP.search(cmd, m.end())
    tail = cmd[m.end():end.start() if end else len(cmd)]
    # без цели (одни ключи, упоминание в тексте) — удалять нечего, не мешаем
    return [w for w in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", tail) if not w.startswith("-") and w not in (">", "2>")]


def allowed(t, cmd, cwd):
    p = norm(t)
    if not p or p.startswith("<"):
        return False
    literal = re.split(r"\$(?!home\b|\{home\})", p, maxsplit=1)[0] if "$" in p else p
    if ".." in literal.split("/"):
        return False
    segs = [s for s in literal.split("/") if s]
    if any(s in FORBIDDEN_SEG for s in segs) or "storagebox" in cmd.lower() or "139.99.91.22" in cmd:
        return False
    for root in LOCAL_ROOTS + REMOTE_ROOTS:
        if literal.startswith(root):
            rest = literal[len(root):]
            return bool(rest.strip("/*"))  # не саму корневую папку
    if SCRATCH in literal:
        return True
    if literal.startswith(("/", "~", "$", "c:", "%")) or re.match(r"[a-z]:", literal):
        return False
    # относительный путь: только локально, в проекте, без ssh и без cd не в проект
    cwd_n = norm(cwd or "") + "/"
    if "ssh" in cmd.lower() or not any(cwd_n.startswith(r) or cwd_n.startswith(r.replace("c:/", "/c/")) for r in LOCAL_ROOTS):
        return False
    for d in re.findall(r"\b(?:cd|Set-Location|pushd)\s+(\"[^\"]*\"|'[^']*'|\S+)", cmd, re.I):
        dn = norm(d) + "/"
        if ".." in dn.split("/"):
            return False
        if dn.startswith(("/", "~", "$", "c:")) and not any(dn.startswith(r) for r in LOCAL_ROOTS):
            return False
    return bool(literal.strip("./*"))


# Узкое исключение (В-154, владелец 02.10: «удаляй лишнее точно»): дедуп TK-020 — только скриптом по манифесту,
# который перед удалением каждого файла заново сверяет sha256 с каноном на Storage Box. Срок — до 2026-10-09.
DEDUPE_APPLY = re.compile(r"tk020-dedupe-apply\.(?:sh|py)")
DEDUPE_UNTIL = "2026-10-09"


def check(cmd, cwd):
    """Причина отказа или None."""
    import datetime
    if DEDUPE_APPLY.search(cmd or "") and datetime.date.today().isoformat() <= DEDUPE_UNTIL:
        return None
    for m in VERBS.finditer(cmd or ""):
        for t in targets(cmd, m):
            if not allowed(t, cmd, cwd):
                return REASON.format(t=t)
    return None
