#!/usr/bin/env python3
"""TK-021 шаг 4: перепроверка сумм на Storage Box самим ящиком (`sha256sum -c`, данные по сети не идут).

Запуск на VPS (ящик смонтирован ro в /mnt/sb, ssh-ключ id_storagebox):
  verify-box-sums.py --out <каталог-результата> [--skip <подстрока>,...]

Находит все `*.sha256` под alpha/ (манифесты пачек, root/deep, коллектор, эпохи, vps, windows-data), для каждого
определяет, от какого каталога считаются пути (по первой строке: существует ли файл под /mnt/sb), отправляет
манифест ящику со стандартного входа с путями от его домашнего каталога и считает OK/FAILED/нет файла.
Результат:
  sums.csv       — манифест,основа,строк,ok,не_совпало,нет_файла,прочее,код,секунд
  sums-bad.txt   — каждая строка, не давшая OK (манифест → строка вывода sha256sum)
  uncovered.csv  — файлы ящика (alpha/, кроме derived/), которых нет ни в одном манифесте: путь,байт
Ничего на ящике не меняется. Перезапуск пропускает манифесты, уже записанные в sums.csv.
"""
import argparse, csv, os, re, subprocess, sys, time

SB = "/mnt/sb"
BOX = ["ssh", "-p", "23", "-i", "/root/.ssh/id_storagebox", "-o", "BatchMode=yes",
       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=8", "u677479@u677479.your-storagebox.de"]
BASES = ["", "alpha/", "alpha/epochs/", "alpha/root/", "alpha/deep/", "alpha/collector-2026-09-15/"]


def manifests():
    out = []
    for dp, dn, fn in os.walk(f"{SB}/alpha"):
        rel = os.path.relpath(dp, SB)
        if rel.startswith("alpha/derived") or rel.count("/") > 3:
            dn[:] = []
            continue
        out += [f"{rel}/{f}" for f in fn if f.endswith(".sha256")]
    return sorted(out)


def base_for(man_lines, man):
    first = man_lines[0].split("  ", 1)[1]
    sibling = os.path.dirname(man) + "/" + os.path.basename(man)[: -len(".sha256")]
    for b in [""] + [sibling + "/"] + BASES[1:]:
        if os.path.exists(f"{SB}/{b}{first}"):
            return b
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip", default="")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sc = f"{a.out}/sums.csv"
    done = set()
    if os.path.exists(sc):
        with open(sc) as f:
            done = {r[0] for r in csv.reader(f)}
    sf = open(sc, "a", newline="")
    sw = csv.writer(sf)
    bad = open(f"{a.out}/sums-bad.txt", "a")
    covered = set()
    skip = [s for s in a.skip.split(",") if s]
    for man in manifests():
        if any(s in man for s in skip):
            continue
        with open(f"{SB}/{man}") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
        if not lines:
            continue
        base = base_for(lines, man)
        if base is None:
            if man not in done:
                sw.writerow([man, "НЕ-ОПРЕДЕЛЕНА", len(lines), 0, 0, 0, 0, "", 0]); sf.flush()
            continue
        paths = [l.split("  ", 1)[1] for l in lines]
        covered.update(f"{base}{p}" for p in paths)
        if man in done:
            continue
        payload = "".join(f"{l.split('  ', 1)[0]}  {base}{l.split('  ', 1)[1]}\n" for l in lines)
        t0 = time.time()
        p = subprocess.run(BOX + ["sha256sum", "-c", "-"], input=payload, capture_output=True, text=True)
        ok = failed = missing = other = 0
        for l in (p.stdout + p.stderr).splitlines():
            if l.endswith(": OK"):
                ok += 1
            elif "No such file" in l or "нет такого файла" in l.lower():
                missing += 1; bad.write(f"{man}\t{l}\n")
            elif l.endswith(": FAILED"):
                failed += 1; bad.write(f"{man}\t{l}\n")
            elif l.strip():
                other += 1; bad.write(f"{man}\t{l}\n")
        sw.writerow([man, base or ".", len(lines), ok, failed, missing, other, p.returncode, int(time.time() - t0)])
        sf.flush(); bad.flush()
        print(f"{time.strftime('%FT%TZ', time.gmtime())} {man}: {len(lines)} строк, ok {ok}, не совпало {failed}, нет файла {missing}", flush=True)
    with open(f"{a.out}/uncovered.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["путь", "байт"])
        for dp, dn, fn in os.walk(f"{SB}/alpha"):
            rel = os.path.relpath(dp, SB)
            if rel.startswith("alpha/derived"):
                dn[:] = []
                continue
            for name in fn:
                r = f"{rel}/{name}"
                if not name.endswith(".sha256") and r not in covered:
                    w.writerow([r, os.path.getsize(f"{dp}/{name}")])
    print(f"{time.strftime('%FT%TZ', time.gmtime())} конец", flush=True)


main()
