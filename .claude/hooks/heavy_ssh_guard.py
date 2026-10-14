#!/usr/bin/env python3
"""PreToolUse (Bash/PowerShell): замок тяжёлых ssh-команд на сервере счёта (TK-240; был heavy_guard.py плагина 1.8.x).

Команда из ssh-сессии — не юнит: замок замеров её не видит (05.10: `find` 57 мин съел HDD). Отказ тяжёлому (du/find/rsync/
vmtouch, `ls -R`, `grep -r`, `cp -r`, tar кроме распаковки со stdin, хеши каталогов/масок/бинлогов, cat/cp бинлогов,
python3-скрипт / `-m` / длинный `-c`) вне `systemd-run`, `/data/benchrun.sh` и разрешённых вызовов alsched.
Свободно: короткие cat/tail/head/ls/grep по файлам, systemctl, uptime, sha256sum отдельных файлов, `cat > f && mv`,
`python3 -m py_compile`, `python3 -c '<≤200 знаков>'`, `tar x` со stdin.
Хост — RPV_GUARD_HEAVY_HOST (умолчание 89.163.242.211), алиас ssh `calc`. Выключить на одну команду нельзя (так и было)."""
import json, os, re, shlex, sys

HOST = (os.environ.get("RPV_GUARD_HEAVY_HOST") or "89.163.242.211").strip().lower()
ALIASES = {"calc"}
ALLOW = re.compile(r"^python3 /data/sched/alsched[.]py (submit|ps|cancel|wave|stand|reprio|thaw)( |$)")
HINT = "Обёртка: /data/benchrun.sh stand <команда> (волна — wave; учёт дисков встроен)."
REASON = ("Тяжёлая команда на машине замеров ({h}) вне замка замеров запрещена ({t}). Почему: замок не видит ssh-сессии и не "
          "замораживает их, а du/find/tar/rsync/cp -r/vmtouch, md5sum и cmp по каталогам, cat больших файлов и "
          "python-скрипты из ssh едят диск и ЦП во время замеров и портят их. Как надо: юнитом — ssh … 'systemd-run --unit "
          "<имя> --collect -p CPUQuota=<N>% <обёртка замера> <команда>', вывод в файл, затем читать его tail/cat. Без "
          "юнита можно: короткие cat/tail/head/ls/grep по файлам, systemctl, uptime, sha256sum отдельных файлов, "
          "python3 -m py_compile, python3 -c до 200 символов, выкладка cat > файл && mv, tar x со stdin. " + HINT)

# --- классификация одной простой команды (логика heavy_guard.py плагина 1.8.48) ---
PY_NAMES = re.compile(r"^(?:python|pythonw|py)[0-9.]*$")
PY_ONELINER_MAX = 200
ALWAYS = {"du", "find", "rsync", "vmtouch", "tree", "ncdu", "locate", "plocate", "mlocate", "updatedb", "fd", "fdfind"}
HASH = {"md5sum", "sha1sum", "sha224sum", "sha256sum", "sha384sum", "sha512sum", "b3sum", "cksum", "shasum", "xxhsum", "cmp"}
GREPS = {"grep", "egrep", "fgrep"}
BIG_FILE = re.compile(r"\.(?:binlog|abin)\b", re.I)
TAR_MODES = {"c": "c", "t": "t", "d": "d", "r": "r", "u": "u", "A": "A", "x": "x"}
TAR_LONG = {"--create": "c", "--list": "t", "--diff": "d", "--compare": "d", "--append": "r", "--update": "u",
            "--concatenate": "A", "--catenate": "A", "--delete": "D", "--extract": "x", "--get": "x"}
TAR_VALUE_LETTERS = set("bCfFgHIKLNTVX")


def opts(args):
    short, long_, pos = [], set(), []
    options = True
    for a in args:
        if options and a == "--":
            options = False
        elif options and a.startswith("--"):
            long_.add(a.split("=", 1)[0])
        elif options and a.startswith("-") and len(a) > 1:
            short.append(a[1:])
        else:
            pos.append(a)
    return "".join(short), long_, pos


def unknown_path(p):
    return any(c in p for c in "*?[{$`") or p.endswith("/") or p in (".", "..")


def python_label(args, stdin):
    code = None
    k = 0
    while k < len(args):
        a = args[k]
        if a in ("-V", "-VV", "--version", "-h", "--help"):
            return None
        if re.fullmatch(r"-[A-Za-z]*m", a):
            mod = args[k + 1] if k + 1 < len(args) else ""
            return None if mod == "py_compile" else f"python3 -m {mod}".strip()
        if re.fullmatch(r"-[A-Za-z]*c", a) and k + 1 < len(args):
            code = [args[k + 1]]
            break
        if a == "-":
            break
        if a in ("-W", "-X"):
            k += 2
        elif a.startswith("-"):
            k += 1
        else:
            return f"python3 {a}"
    if code is None:
        code = list(stdin)
        if not code:
            return "python3 со stdin (код не виден)"
    n = len("\n".join(code))
    return None if n <= PY_ONELINER_MAX else f"python3 -c/stdin на {n} символов (больше {PY_ONELINER_MAX})"


def tar_label(args):
    if "--version" in args or "--help" in args:
        return None
    modes = set()
    farg = None
    k = 0
    n = len(args)
    while k < n:
        a = args[k]
        k += 1
        if a.startswith("--"):
            nm, eq, val = a.partition("=")
            if nm in TAR_LONG:
                modes.add(TAR_LONG[nm])
            elif nm == "--file":
                farg = val if eq else (args[k] if k < n else None)
                k += 0 if eq else 1
            elif nm in ("--directory", "--files-from", "--exclude-from", "--exclude", "--transform", "--to-command") and not eq:
                k += 1
            continue
        old = k == 1 and not a.startswith("-")
        letters = a if old else a[1:]
        for idx, ch in enumerate(letters):
            if ch in TAR_VALUE_LETTERS:
                rest = "" if old else letters[idx + 1:]
                if rest:
                    val = rest
                else:
                    val = args[k] if k < n else None
                    k += 1
                if ch == "f":
                    farg = val
                if not old:
                    break
            elif ch in TAR_MODES:
                modes.add(TAR_MODES[ch])
    if modes == {"x"} and farg in (None, "-"):
        return None
    return "tar " + ("".join(sorted(modes)) or "?") + (f" -f {farg}" if farg not in (None, "-") else "")


def heavy_label(name, args, stdin=(), xargs=False):
    """Метка тяжёлой команды `name args…` или None."""
    if ALLOW.search(" ".join([name, *args])):
        return None
    if name in ALWAYS:
        return name
    short, long_, pos = opts(args)
    if name == "ls":
        return "ls -R" if ("R" in short or "--recursive" in long_) else None
    if name in GREPS:
        return f"{name} -r" if ({"r", "R"} & set(short) or {"--recursive", "--dereference-recursive"} & long_) else None
    if name == "cp":
        if {"r", "R", "a"} & set(short) or {"--recursive", "--archive"} & long_:
            return "cp -r"
        return "cp бинлога" if any(BIG_FILE.search(p) for p in pos) else None
    if name == "tar":
        return tar_label(args)
    if name in HASH:
        if xargs:
            return f"{name} по списку из xargs"
        if name.endswith("sum") and ("c" in short or "--check" in long_):
            return f"{name} -c (список файлов из файла)"
        for p in pos:
            if p != "-" and (unknown_path(p) or BIG_FILE.search(p)):
                return f"{name} {p}"
        return None
    if name == "diff":
        if "r" in short or "R" in short or "--recursive" in long_:
            return "diff -r"
        return "diff бинлога" if any(BIG_FILE.search(p) for p in pos) else None
    if name == "cat":
        for p in pos:
            if BIG_FILE.search(p) or (p.startswith("/data/alpha/") and unknown_path(p)):
                return f"cat {p}"
        return None
    if PY_NAMES.match(name):
        return python_label(args, stdin)
    return None


# --- разбор команды: ssh → хост → удалённый текст → простые команды ---
SSH_VAL = {"-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w"}
SHELLS = {"bash", "sh", "zsh", "dash", "ash", "ksh"}
# обёртка → опции со значением
WRAPPERS = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-r", "-t", "-U"}, "env": {"-u", "-C", "-S"},
            "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"},
            "nohup": set(), "time": set(), "timeout": {"-s", "-k", "--signal", "--kill-after"}, "command": set(),
            "builtin": set(), "exec": set(), "setsid": set(), "stdbuf": {"-i", "-o", "-e"}, "flock": {"-w", "-E", "--timeout"},
            "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter",
                      "--arg-file", "--max-lines", "--eof"}}
RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "select", "case", "esac", "in",
            "!", "{", "}", "function"}
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][\w]*)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
HERESTR = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.S)         # here-string PowerShell
SEPS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;"}
REDIR = re.compile(r"^\d*(?:>>?|<<?<?|>&|<&|&>>?)$")
LEGACY_HEAVY = re.compile(r"(?:^|[\s;&|(`'\"])(?:du|find|vmtouch|rsync|tar)\s|\bpython3?(?:\.\d+)?\s+(?:-\w+\s+)*[^\s-]\S*\.py\b")
MAX_DEPTH = 6


def cmd_name(w):
    n = re.split(r"[\\/]", w)[-1].lower()
    return n[:-4] if n.endswith(".exe") else n


def host_of(arg):
    h = re.sub(r"^[^@\s]*@", "", arg or "").split(":", 1)[0].strip().strip("[]").lower()
    return h if h and "$" not in h else None


def is_calc(host):
    return bool(host) and (host == HOST or host in ALIASES)


def expand(s, vars_):
    return re.sub(r"\$(\w+)|\$\{(\w+)\}", lambda m: vars_.get(m.group(1) or m.group(2), m.group(0)), s)


def pull_subst(text):
    """(текст без `$(…)`/`` `…` ``, список внутренних текстов): подстановки сканируются отдельно."""
    inner, out, i, single = [], [], 0, False
    while i < len(text):
        if text[i] == "'":
            single = not single
            out.append(text[i])
            i += 1
        elif single:                                         # в одинарных кавычках подстановка исполнится уже на сервере
            out.append(text[i])
            i += 1
        elif text.startswith("$(", i) and not text.startswith("$((", i):
            depth, j = 1, i + 2
            while j < len(text) and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            inner.append(text[i + 2:j - 1])
            out.append(" SUBST ")
            i = j
        elif text[i] == "`":
            j = text.find("`", i + 1)
            if j < 0:
                out.append(text[i])
                i += 1
            else:
                inner.append(text[i + 1:j])
                out.append(" SUBST ")
                i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out), inner


def split_cmds(text):
    """Текст → список простых команд (списки слов) с пометкой, что вход идёт по конвейеру. ValueError — не разобрать."""
    lex = shlex.shlex(text.replace("\r", "").replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    cmds, cur, skip_next = [], [], False
    piped = False
    for tok in lex:
        if skip_next:
            skip_next = False
            continue
        if tok in SEPS:
            if cur:
                cmds.append((cur, piped))
            cur = []
            piped = tok in ("|", "|&")
            continue
        if REDIR.match(tok):
            skip_next = True
            continue
        cur.append(tok)
    if cur:
        cmds.append((cur, piped))
    return cmds


def split_heredocs(text):
    """(текст без heredoc/here-string, [тела])."""
    bodies = [m.group(3) for m in HEREDOC.finditer(text)] + [m.group(2) for m in HERESTR.finditer(text)]
    return HERESTR.sub(" ", HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], text)), bodies


def scan(text, remote, stdin, vars_, depth):
    """Ищет метки тяжёлых команд. remote — текст исполняется на сервере счёта; иначе ищутся только вызовы ssh на него."""
    if depth > MAX_DEPTH:
        return
    text, bodies = split_heredocs(text)
    stdin = list(stdin) + bodies
    text, inner = pull_subst(text)
    for sub in inner:
        yield from scan(sub, remote, stdin, vars_, depth + 1)
    prev_echo = None
    for words, piped in split_cmds(text):
        i = 0
        xargs = False
        while i < len(words):
            w = words[i]
            m = ASSIGN.match(w)
            if m:
                vars_[m.group(1)] = expand(m.group(2), vars_)
                i += 1
            elif w in RESERVED:
                i += 1
            else:
                break
        if i >= len(words):
            continue
        name = cmd_name(words[i])
        args = [expand(a, vars_) for a in words[i + 1:]]
        while name in WRAPPERS:                              # sudo/env/nohup/timeout/xargs … → сама команда
            if name == "xargs":
                xargs = True
            k = 0
            while k < len(args) and (args[k].startswith("-") or (name == "env" and ASSIGN.match(args[k]))):
                k += 2 if args[k] in WRAPPERS[name] else 1
            if name == "timeout" and k < len(args):
                k += 1
            args = args[k:]
            if not args:
                name = ""
                break
            name, args = cmd_name(args[0]), args[1:]
        if not name:
            continue
        if name == "systemd-run" or name.startswith("benchrun"):
            continue                                         # юнит / обёртка замера — под замком
        sin = stdin
        if piped and prev_echo is not None:
            sin = [prev_echo]
        elif piped and name in ("python", "python3", "bash", "sh"):
            sin = []
        prev_echo = " ".join(args) if name in ("echo", "printf") else None
        if name == "ssh" or name.endswith("opensshssh"):     # локальный shlex съедает обратные слэши пути ssh.exe
            yield from ssh_scan(args, stdin, vars_, depth)
            continue
        if name in SHELLS or name in ("powershell", "pwsh"):
            code = shell_code(args)
            if code is not None:
                yield from scan(code, remote, stdin, vars_, depth + 1)
                continue
            if name in SHELLS and ("-s" in args or not [a for a in args if not a.startswith("-")]):
                for body in stdin:                           # оболочка читает stdin
                    yield from scan(body, remote, (), vars_, depth + 1)
                continue
        if remote:
            label = heavy_label(name, args, sin, xargs)
            if label:
                yield label


def shell_code(args):
    for k, a in enumerate(args):
        if re.fullmatch(r"-[A-Za-z]*c", a) or a.lower() in ("-command", "-c"):
            return args[k + 1] if k + 1 < len(args) else ""
    return None


def ssh_scan(args, stdin, vars_, depth):
    j = 0
    while j < len(args):
        a = args[j]
        if a == "--":
            j += 1
            break
        if a.startswith("-") and len(a) > 1:
            j += 2 if a in SSH_VAL else 1
            continue
        break
    host = host_of(expand(args[j], vars_)) if j < len(args) else None
    if not is_calc(host):
        return
    rest = args[j + 1:]
    if rest:
        yield from scan(" ".join(rest), True, stdin, vars_, depth + 1)
    else:
        for body in stdin:
            yield from scan(body, True, (), vars_, depth + 1)


def legacy_heavy(cmd):
    """Страховка, когда разбор не удался (незакрытая кавычка): грубый поиск в тексте команды с этим хостом."""
    if not (HOST in cmd or re.search(r"\bssh\b.*\bcalc\b", cmd)) or "systemd-run" in cmd or "benchrun" in cmd:
        return None
    m = LEGACY_HEAVY.search(cmd)
    return m.group(0).strip(" ;&|(`'\"\t\n") if m else None


def verdict(cmd):
    """None — пропустить; строка — причина отказа."""
    if "ssh" not in cmd.lower() or not (HOST in cmd or re.search(r"\bcalc\b", cmd)):
        return None
    try:
        label = next(iter(scan(cmd, False, (), {}, 0)), None)
    except Exception:                                        # ValueError shlex и прочее: грубая проверка вместо пропуска
        label = legacy_heavy(cmd)
    return REASON.format(h=HOST, t=label) if label else None


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    if d.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    why = verdict((d.get("tool_input") or {}).get("command") or "")
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
