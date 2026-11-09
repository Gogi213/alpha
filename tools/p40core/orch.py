#!/usr/bin/env python3
"""TK-500: оркестратор по (актив, сутки) для ядра П-40 (сервер 5.231.25.224, Linux). Один проход сырья бокса -> таблицы эпизодов; `.bin` и сырьё живут только в /dev/shm и удаляются после суток.
Запуск: orch.py <pairs.csv> <out> --days 2026-09-17,2026-09-18 [--jobs 2] [--fetch-p 10] [--shm /dev/shm/p40o] [--assets 1,2] [--chunk K] [--n-usdt 1000]
pairs.csv: asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB   (по строке на пару; ноги с одним (биржа,символ) в актив-сутки читаются и конвертируются ОДИН раз;
  mult — множитель размера Gate (quanto_multiplier), step — шаг количества очереди, пусто = 1 и 1e-9; биржи: binance okx bitget aster hyperliquid bybit gate;
  lot пусто/auto -> lot = N/(mid_open*ct): N = --n-usdt (умолч. 1000, П-40 §8), mid_open = середина первого снимка книги ноги в сутках, ct = монет в единице количества книги (колонки ctA/ctB в конце, пусто = 1; OKX = ctVal))
Выход: <out>/<asset>/<day>/<pair>{,.fills.csv,.exits.csv,.features.csv,.path.csv,.aux.csv}, <out>/specs.txt (строки для assemble.py), <out>/orch.csv (замеры по актив-суткам), метка DONE (повтор пропускает).
TK-510: бюджет /dev/shm. Пик актива×чанка = все .bin чанка (они в 2–37 раз больше сырья: 0,07–0,38 ГБ на ногу×сутки) + сырьё одних суток. Задание берёт «пилот» (PILOT_LEG_B на ногу), считает первые сутки,
  меряет du(wd) и бронирует оценку пика ((оставшееся + .bin K суток) × SHM_MARGIN); сумма броней ≤ SHM_FRAC (0,8) от размера shm минус чужое на старте, иначе ждёт; оценка одного задания > бюджета -> отказ сразу.
  Сбой ENOSPC (rsync rc 11, pq2bin/ядро с «No space left») — отдельное исключение ShmFull, строка «СБОЙ ENOSPC», код выхода 28 (умолч. сбой = 1); mark/OI-дыры поставщика (aux_feats) копятся в <out>/auxholes.csv.
Флаги ядра P40_* (NOTRADE, THETAS, VARIANTS, TRUNC, K2, SHIFT_B_MS ...) передаются средой как есть. Binance-стакан читается с локального диска (BINANCE_BOOK), остальное — rsync --files-from с бокса в P постоянных соединениях.
TK-518 (В-116): ноги Bybit/Gate — НЕ сырьё (его на боксе убирает reaper), а L1 бокса `p40l1/<биржа>_futures/<символ>/<сутки>/` (`<ЧЧ>.l1.bin.zst` + `.ok`), распаковка zstd -> `<ЧЧ>.l1.bin`; нет суток/метки/файлов по метке = сбой задания.
  Нужен P40_L1=1 на весь прогон (признаки без глубины, В-115) и ядро TK-515+ (P40_CORE; старое ядро с P40_L1 — отказ при старте). `--raw-legs` — прежний путь из сырья. Хвост суток после 00:00:00 не берётся: прежний конвертер (bybit::scan) его тоже отбрасывал — разбор в тикете TK-518."""
import argparse, calendar, concurrent.futures as cf, csv, gzip, os, shutil, subprocess, sys, threading, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CHFT = {"binance", "okx", "bitget", "aster", "hyperliquid"}
KINDS = ["orderbook", "trades", "mark_price", "open_interest", "liquidations"]
BINANCE_BOOK = os.environ.get("BINANCE_BOOK", "/data/stakan/chft/binance_futures")
CORE = os.environ.get("P40_CORE", "/root/p40core/rs/target/release/p40core")
PQ2BIN = os.environ.get("P40_PQ2BIN", "/root/p40core/pq2bin/target/release/pq2bin")
SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
LOG_LOCK = threading.Lock()
EXIT_SHM = 28                  # errno ENOSPC: хотя бы одно задание упало или отказано по месту в /dev/shm
SHM_FRAC = 0.8                 # доля shm под брони
SHM_MARGIN = 1.25              # запас оценки пика на разброс суток (оценка и факт пишутся в orch.log: «shm: …»)
PILOT_LEG_B = 600 * 1024 ** 2  # бронь на ногу в первые сутки: замер 09.11 (AAVE 17.09) — .bin+сырьё ≤ 0,43 ГБ на ногу×сутки
SEEN_AUXH = set()
L1_EX = {"bybit": "bybit_futures", "gate": "gate_futures"}   # TK-518/В-116: сырьё этих бирж на боксе удаляется reaper'ом -> вход ног = L1 бокса alpha/stakan/p40l1/<биржа>_futures/<символ>/<сутки>/


class ShmFull(RuntimeError):
    """/dev/shm не хватило (факт ENOSPC или оценка пика больше бюджета) — не «обычный» сбой"""


class ShmBudget:
    """брони /dev/shm: сумма ≤ cap = frac·размер − чужое на старте; set() ждёт, пока влезет; оценка > cap -> ShmFull сразу.
    Без взаимной блокировки: если ни у кого другого нет полной брони (все в пилоте), задание идёт, пока оно само ≤ cap."""

    def __init__(self, shm, frac=SHM_FRAC, total=None, foreign=None):
        os.makedirs(shm, exist_ok=True)
        st = os.statvfs(shm)
        total = st.f_blocks * st.f_frsize if total is None else total
        foreign = total - st.f_bavail * st.f_frsize if foreign is None else foreign
        self.cap = int(total * frac) - foreign
        self.res, self.full, self.cv = {}, set(), threading.Condition()

    def set(self, key, need, full=False, wait=60.0):
        if need > self.cap:
            raise ShmFull(f"оценка пика {need / 2**30:.1f} ГБ > бюджета shm {self.cap / 2**30:.1f} ГБ ({SHM_FRAC:.0%} размера минус чужое): уменьшите --chunk")
        with self.cv:
            while sum(v for k, v in self.res.items() if k != key) + need > self.cap and (self.full - {key}):
                self.cv.wait(wait)
            self.res[key] = need
            if full:
                self.full.add(key)
            self.cv.notify_all()

    def release(self, key):
        with self.cv:
            self.res.pop(key, None)
            self.full.discard(key)
            self.cv.notify_all()


def peak_estimate(peak1, kept1, k_days):
    """peak1 = du(wd) первых суток до drop_heavy, kept1 = после (≈ .bin суток + малое сырьё); пик K суток = K·kept1 + тяжёлое сырьё одних суток"""
    return int((k_days * kept1 + (peak1 - kept1)) * SHM_MARGIN)


def du(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs if os.path.exists(os.path.join(d, f)))


def nospace(log, shm=None):
    """упал ли шаг из-за места: «No space left»/StorageFull в хвосте лога или shm почти полон"""
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            tail = f.read().decode("utf-8", "replace")
        if "No space left" in tail or "StorageFull" in tail:
            return True
    except OSError:
        pass
    if shm:
        st = os.statvfs(shm)
        return st.f_bavail * st.f_frsize < 64 * 1024 ** 2
    return False


def run(cmd, env=None, log=None):
    """cmd -> (rc, wall_s, cpu_s); cpu = user+sys самого процесса и его детей (wait4), не всей программы"""
    t = time.time()
    f = open(log, "ab") if log else subprocess.DEVNULL
    p = subprocess.Popen(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    _, st, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(st)
    if log:
        f.close()
    return p.returncode, time.time() - t, ru.ru_utime + ru.ru_stime


def leg_key(ex, sym):
    return f"{ex}_{sym}"


def box_files(ex, sym, day):
    """пути на боксе от alpha/stakan/ (то, что нужно ядру и aux_feats)"""
    if ex == "bybit":
        return [f"bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"]
    if ex == "gate":
        d = day.replace("-", "")
        return [f"gate-futures/{sym}/{sym}-{d}{h:02d}.csv.gz" for h in range(24)]
    kinds = KINDS[1:] if ex == "binance" else KINDS
    return [f"chft/{ex}_futures/{day}/{h:02d}/{sym}_{k}.parquet" for h in range(24) for k in kinds]


def fetch(files, raw, p, log):
    os.makedirs(raw, exist_ok=True)
    lst = f"{raw}.list"
    chunks = [files[i::p] for i in range(p)]
    procs = []
    for i, c in enumerate(chunks):
        if not c:
            continue
        open(f"{lst}{i}", "w").write("\n".join(c) + "\n")
        procs.append(subprocess.Popen(["rsync", "-q", "-R", "--ignore-missing-args", f"--files-from={lst}{i}", "-e", SSH, "box:alpha/stakan/", raw + "/"],
                                      stdout=subprocess.DEVNULL, stderr=open(log, "ab")))
    bad = [q.wait() for q in procs]
    for i in range(len(chunks)):
        if os.path.exists(f"{lst}{i}"):
            os.remove(f"{lst}{i}")
    if any(rc not in (0, 23, 24) for rc in bad):
        if 11 in bad and nospace(log, os.path.dirname(raw)):
            raise ShmFull(f"ENOSPC /dev/shm при rsync rc {bad}")
        raise RuntimeError(f"rsync rc {bad}")
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(raw) for f in fs)


def l1_rel(ex, sym, day):
    return f"p40l1/{L1_EX[ex]}/{sym}/{day}"


def l1_leg(args, ex):
    return ex in L1_EX and not args.raw_legs


def fetch_l1(ex, sym, day, raw, log):
    """L1-сутки ноги с бокса (каталог целиком, несколько малых файлов) -> список `<ЧЧ>.l1.bin.zst`. СТРОГО (TK-518): нет каталога, метки .ok, `files=0`,
    число файлов или их размер не по метке -> RuntimeError (нога не пропускается молча: rsync --ignore-missing-args здесь не используется)"""
    rel = l1_rel(ex, sym, day)
    dst = f"{raw}/{rel}"
    os.makedirs(dst, exist_ok=True)
    rc = subprocess.call(["rsync", "-q", "-a", "-e", SSH, f"box:alpha/stakan/{rel}/", dst + "/"], stdout=subprocess.DEVNULL, stderr=open(log, "ab"))
    if rc != 0:
        if rc == 11 and nospace(log, os.path.dirname(raw)):
            raise ShmFull(f"ENOSPC /dev/shm при rsync L1 {rel}")
        raise RuntimeError(f"L1 {rel}: rsync rc {rc} (нет суток на боксе?)")
    try:
        kv = dict(x.split("=", 1) for x in open(f"{dst}/.ok").read().split() if "=" in x)
        nf, size = int(kv["files"]), int(kv["l1"])
    except (OSError, KeyError, ValueError) as e:
        raise RuntimeError(f"L1 {rel}: нет метки .ok или она не разобрана ({e})") from e
    fs = sorted(f for f in os.listdir(dst) if f.endswith(".l1.bin.zst"))
    if nf <= 0 or len(fs) != nf or sum(os.path.getsize(f"{dst}/{f}") for f in fs) != size:
        raise RuntimeError(f"L1 {rel}: по метке файлов {nf}, байт {size}; на диске файлов {len(fs)}")
    return [f"{dst}/{f}" for f in fs]


def unpack_l1(ex, sym, day, raw, bins, log):
    """-> (fetch_s, байт L1, wall, cpu распаковки): `<ЧЧ>.l1.bin.zst` -> `<bins>/B/<ЧЧ>.l1.bin` (ядро читает `.l1.bin`, merge() линкует как `<idx>.bin`); сделок у Bybit/Gate нет — `T` пуст, как у прежнего конвертера"""
    t = time.time()
    fs = fetch_l1(ex, sym, day, raw, log)
    tf, nb = time.time() - t, sum(os.path.getsize(f) for f in fs)
    os.makedirs(f"{bins}/B", exist_ok=True)
    os.makedirs(f"{bins}/T", exist_ok=True)
    rc, w, c = run(["zstd", "-d", "-q", "-f", "--output-dir-flat", f"{bins}/B", *fs], log=log)
    if rc != 0:
        fail(f"zstd -d L1 {l1_rel(ex, sym, day)} rc {rc}", log, bins)
    return tf, nb, w, c


def fail(msg, log, shm_path):
    if nospace(log, os.path.dirname(shm_path)):
        raise ShmFull("ENOSPC /dev/shm: " + msg)
    raise RuntimeError(msg)


def convert(ex, sym, day, raw, bins, mult, log):
    """-> (wall, cpu); bins/A или B: <bins>/B (книга), <bins>/T (сделки; пусто, если сделок нет)"""
    os.makedirs(f"{bins}/T", exist_ok=True)
    w = c = 0.0
    if ex == "bybit":
        z = f"{raw}/bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"
        r = run([PQ2BIN, "bybit", z, f"{bins}/B"], log=log)
    elif ex == "gate":
        r = run([PQ2BIN, "gate", f"{raw}/gate-futures/{sym}", sym, day.replace("-", ""), f"{bins}/B", str(mult), "2"], log=log)
    else:
        root = f"{raw}/chft/{ex}_futures/{day}"
        book_root = f"{BINANCE_BOOK}/{day}" if ex == "binance" else root
        r = run([PQ2BIN, "book", book_root, f"{sym}_orderbook.parquet", f"{bins}/B", "2"], log=log)
        w, c = r[1], r[2]
        if r[0] != 0:
            fail(f"pq2bin book {ex} {sym} rc {r[0]}", log, bins)
        r2 = run([PQ2BIN, "trades", root, f"{sym}_trades.parquet", f"{bins}/T", "2"], log=log)
        r = (r2[0], w + r2[1], c + r2[2])
    if r[0] != 0:
        fail(f"pq2bin {ex} {sym} rc {r[0]}", log, bins)
    return r[1], r[2]


REC = np.dtype([("et", "<i8"), ("fu", "<i8"), ("px", "<f8"), ("qt", "<f8"), ("fl", "u1")])


def mid_open(path):
    """середина первого снимка книги ноги (первый <ЧЧ>.bin каталога; .bin 33 Б/запись: бит0 = снимок, бит1 = bid); нет снимка в начале -> медиана цен первых 20000 записей"""
    hs = sorted(f for f in os.listdir(path) if f.endswith(".bin"))
    r = np.fromfile(f"{path}/{hs[0]}", dtype=REC, count=400000) if hs else np.empty(0, REC)
    if len(r) == 0:
        raise RuntimeError(f"пустая книга {path}")
    n = int(np.argmin(r["fl"] & 1)) if (r["fl"][0] & 1) and (r["fl"] & 1 == 0).any() else (len(r) if r["fl"][0] & 1 else 0)
    g = r[:n]
    if n and (g["fl"] & 2).any() and (~g["fl"] & 2).any():
        bid, ask = g["px"][(g["fl"] & 2) > 0], g["px"][(g["fl"] & 2) == 0]
        return (bid.max() + ask.min()) / 2
    return float(np.median(r["px"][:20000]))


VEX = {"okx": "okx_futures", "bitget": "bitget_futures", "aster": "aster_futures", "hyperliquid": "hyperliquid_futures", "binance": "binance_futures"}


def load_validity(paths):
    """validity.csv.gz G0 (ex,contract,date,hour,status,...) -> {(ex,contract,date): {негодные часы}}; статус не ok = негодный час, 'day' не ok = все 24 часа; строки, которых нет в таблице, негодными не считаются"""
    bad = {}
    for p in paths:
        for r in csv.reader(gzip.open(p, "rt")):
            if r[4] == "ok" or r[3] == "hour":
                continue
            bad.setdefault((r[0], r[1], r[2]), set()).update(range(24) if r[3] == "day" else [int(r[3])])
    return bad


def write_holes(path, bad, legs, days):
    """дыры пары = объединение негодных часов обеих ног по суткам чанка -> строки `начало_мс,конец_мс` (P40_HOLES), склеены"""
    iv = []
    for i, day in enumerate(days):
        t0 = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
        for ex, sym in legs:
            iv += [(t0 + h * 3600000, t0 + (h + 1) * 3600000) for h in bad.get((VEX.get(ex, ex), sym, day), ())]
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    open(path, "w").write("".join(f"{a},{b}" + chr(10) for a, b in out))
    return len(out)


def drop_heavy(raw):
    """после конвертации сырьё книги/сделок не нужно (aux читает только mark/OI/ликвидации CHFT; у ног Bybit/Gate таких рядов нет); Bybit/Gate — целиком"""
    for d, _, fs in os.walk(raw):
        for f in fs:
            if f.endswith(("_orderbook.parquet", "_trades.parquet", ".zip", ".csv.gz", ".l1.bin.zst")):
                os.remove(os.path.join(d, f))


def merge(srcs, dst, sub):
    """часовые .bin суток подряд -> <dst>/<sub>/<idx>.bin симлинками (idx = 24*номер суток + час; ядро читает 0..P40_HOURS-1)"""
    os.makedirs(f"{dst}/{sub}", exist_ok=True)
    for di, src in enumerate(srcs):
        for h in range(24):
            f = f"{src}/{sub}/{h:02d}.bin"
            if not os.path.exists(f):
                f = f"{src}/{sub}/{h:02d}.l1.bin"   # L1-файл (TK-518): имя другое, содержимое — те же 33-байтные строки
            if os.path.exists(f):
                os.symlink(f, f"{dst}/{sub}/{24 * di + h:02d}.bin")


def collect_auxholes(out, path):
    """дыры mark/OI/ликвидаций поставщика по ногам пары -> <out>/auxholes.csv без повторов (ноги общие для пар актива)"""
    with LOG_LOCK:
        new = [r for r in csv.reader(open(path)) if r and r[0] != "exchange" and tuple(r) not in SEEN_AUXH]
        SEEN_AUXH.update(map(tuple, new))
        dst = f"{out}/auxholes.csv"
        head = not os.path.exists(dst)
        with open(dst, "a", newline="") as f:
            w = csv.writer(f)
            if head:
                w.writerow(["exchange", "contract", "date", "hour", "kind"])
            w.writerows(new)


def job(args, asset, days, pairs, outlog):
    t_all = time.time()
    cid = days[0] if len(days) == 1 else f"{days[0]}+{len(days)}"
    out_d = f"{args.out}/{asset}/{cid}"
    if os.path.exists(f"{out_d}/DONE"):
        return
    os.makedirs(out_d, exist_ok=True)
    wd = f"{args.shm}/{asset}_{cid}"
    shutil.rmtree(wd, ignore_errors=True)
    log = f"{out_d}/orch.log"
    try:
        legs = {}
        for p in pairs:
            for s in "AB":
                legs.setdefault((p["ex" + s], p["sym" + s]), float(p["mult" + s] or 1))
        nbytes = 0
        t_fetch = t_conv = c_conv = 0.0
        key = f"{asset}_{cid}"
        args.budget.set(key, PILOT_LEG_B * len(legs))
        est = peak = 0
        for di, day in enumerate(days):
            raw = f"{wd}/raw/{day}"
            t = time.time()
            nbytes += fetch([f for (ex, sym) in legs if not l1_leg(args, ex) for f in box_files(ex, sym, day)], raw, args.fetch_p, log)
            t_fetch += time.time() - t
            for (ex, sym), mult in legs.items():
                bins = f"{wd}/bin/{leg_key(ex, sym)}/{day}"
                if l1_leg(args, ex):
                    tf, nb, w, c = unpack_l1(ex, sym, day, raw, bins, log)   # fetch внутри: L1-сутки малы; нет суток -> сбой задания, не пропуск ноги
                    t_fetch, nbytes = t_fetch + tf, nbytes + nb
                else:
                    w, c = convert(ex, sym, day, raw, bins, mult, log)
                t_conv, c_conv = t_conv + w, c_conv + c
            peak = max(peak, du(wd))
            if di == 0:
                p1 = peak
            drop_heavy(raw)
            if di == 0 and len(days) > 1:
                est = peak_estimate(p1, du(wd), len(days))
                args.budget.set(key, est, full=True)
            elif di == 0:
                est = int(p1 * SHM_MARGIN)
        with open(log, "a") as f:
            f.write(f"shm: оценка пика {est} Б, факт {peak} Б, бюджет {args.budget.cap} Б" + chr(10))
        mrg = {}
        for (ex, sym) in legs:
            k = leg_key(ex, sym)
            mrg[(ex, sym)] = f"{wd}/mrg/{k}"
            for sub in "BT":
                merge([f"{wd}/bin/{k}/{day}" for day in days], mrg[(ex, sym)], sub)
        env = dict(os.environ, P40_HOURS=str(24 * len(days)))
        t_core = c_core = c_aux = 0.0
        for p in pairs:
            a, b = (mrg[(p["ex" + s], p["sym" + s])] for s in "AB")
            base = f"{out_d}/{p['pair']}"
            lots = []
            for s_, bd in zip("AB", (a, b)):
                lot = (p["lot" + s_] or "").strip()
                if lot in ("", "auto"):
                    lot = repr(float(args.n_usdt / (mid_open(f"{bd}/B") * float(p.get("ct" + s_) or 1))))
                lots.append(lot)
            with LOG_LOCK, open(f"{args.out}/lots.csv", "a") as f:
                f.write(f"{asset},{cid},{p['pair']},{lots[0]},{lots[1]}" + chr(10))
            cmd = [CORE, base, f"{a}/B", f"{b}/B", f"{a}/T", f"{b}/T", lots[0], lots[1], str(args.l_ms), p["stepA"] or "1e-9", p["stepB"] or "1e-9"]
            penv = env
            if args.validity:
                nh = write_holes(f"{base}.holes", args.bad, [(p["exA"], p["symA"]), (p["exB"], p["symB"])], days)
                penv = dict(env, P40_TRUNC="1", P40_HOLES=f"{base}.holes", **({"P40_END_MS": str(args.end_ms)} if args.end_ms else {}))
            rc, w, c = run(cmd, env=penv, log=log)
            if rc != 0:
                raise RuntimeError(f"ядро rc {rc} пара {p['pair']}")
            t_core, c_core = t_core + w, c_core + c
            if p["exA"] in CHFT or p["exB"] in CHFT:
                ra, rb = (",".join(f"{wd}/raw/{day}/chft/{p['ex' + s]}_futures/{day}" for day in days) if p["ex" + s] in CHFT else "-" for s in "AB")   # Bybit/Gate: рядов mark/OI/ликвидаций на боксе нет -> «-»
                rc, w, c = run([sys.executable, f"{HERE}/aux_feats.py", base, ra, p["symA"], rb, p["symB"]], log=log)
                if rc != 0:
                    raise RuntimeError(f"aux rc {rc} пара {p['pair']}")
                c_aux += c
                collect_auxholes(args.out, f"{base}.auxholes.csv")
            with LOG_LOCK, open(f"{args.out}/specs.txt", "a") as f:
                byb = int("bybit" in (p["exA"], p["exB"]))
                f.write(f"{asset}:{days[0].replace('-', '') if len(days) == 1 else 't0'}:{p['pair']}:{byb}:{base}" + chr(10))
        open(f"{out_d}/DONE", "w").write("")
        row = [asset, cid, len(pairs), len(legs), nbytes, f"{t_fetch:.1f}", f"{t_conv:.1f}", f"{c_conv:.1f}", f"{t_core:.1f}", f"{c_core:.1f}", f"{c_aux:.1f}", f"{time.time() - t_all:.1f}"]
        with LOG_LOCK, open(outlog, "a") as f:
            f.write(",".join(map(str, row)) + chr(10))
        print("готово", *row, flush=True)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
        args.budget.release(f"{asset}_{cid}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs"), ap.add_argument("out")
    ap.add_argument("--days", required=True)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--fetch-p", type=int, default=10)
    ap.add_argument("--shm", default="/dev/shm/p40o")
    ap.add_argument("--assets", default="")
    ap.add_argument("--l-ms", type=int, default=500)
    ap.add_argument("--n-usdt", type=float, default=1000.0)
    ap.add_argument("--validity", default="", help="validity.csv.gz G0 (через запятую): негодные часы ног -> P40_HOLES пары + P40_TRUNC=1 (усечение §2.2); без флага усечения нет")
    ap.add_argument("--end-ms", type=int, default=0, help="конец данных для усечения (P40_END_MS, мс UTC; умолч. — последний момент рядов чанка)")
    ap.add_argument("--raw-legs", action="store_true", help="TK-518: ноги Bybit/Gate читать из сырья бокса (прежний путь; сырьё удаляется reaper'ом, В-116 — для сверок и смоков). Умолчание — L1 бокса p40l1 (строго: нет суток = сбой)")
    ap.add_argument("--chunk", type=int, default=1, help="сутки подряд в одном прогоне ядра (умолч. 1 = как раньше); K>1: уровень P̄_L и эпизоды идут сквозь полночь, колонка day в assemble — по t0 (spec day=t0)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    args.bad = load_validity(args.validity.split(",")) if args.validity else {}
    outlog = f"{args.out}/orch.csv"
    if not os.path.exists(outlog):
        open(outlog, "w").write("asset,day,pairs,legs,bytes,fetch_s,convert_s,convert_cpu_s,core_s,core_cpu_s,aux_cpu_s,total_s\n")
    by_asset = {}
    for r in csv.DictReader(open(args.pairs)):
        by_asset.setdefault(r["asset"], []).append(r)
    keep = set(filter(None, args.assets.split(",")))
    if not args.raw_legs and "P40_L1" not in os.environ and any(r[f"ex{s}"] in L1_EX for r in sum(by_asset.values(), []) for s in "AB"):
        sys.exit("TK-518: ноги Bybit/Gate идут из L1 (без глубины) -> признаки depth_slip/bookimb невозможны; задайте P40_L1=1 на весь прогон (ядро берёт l1_notional/bookimb1 по лучшим ценам) или --raw-legs, пока сырьё на боксе есть")
    ds = args.days.split(",")
    todo = [(a, ds[i:i + args.chunk], ps) for i in range(0, len(ds), args.chunk) for a, ps in by_asset.items() if not keep or a in keep]
    if "P40_L1" in os.environ and b"P40_L1" not in open(CORE, "rb").read():
        sys.exit(f"TK-518: P40_L1 задан, но ядро {CORE} его не знает (сборка до TK-515: бит2 L1 и признаки без глубины не поддержаны) — задайте P40_CORE на сборку TK-515+")
    err = shm_err = 0
    args.budget = ShmBudget(args.shm)
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(job, args, a, d, ps, outlog): (a, d) for a, d, ps in todo}
        for f in cf.as_completed(futs):
            try:
                f.result()
            except ShmFull as e:
                err += 1
                shm_err += 1
                print("СБОЙ ENOSPC", futs[f][0], futs[f][1][0], e, file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                err += 1
                print("СБОЙ", futs[f], e, file=sys.stderr, flush=True)
    sys.exit(EXIT_SHM if shm_err else 1 if err else 0)


if __name__ == "__main__":
    main()
