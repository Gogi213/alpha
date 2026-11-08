"""П-40 G0: потоковый проход CHFT-книг (OKX/Bitget/Aster/Hyperliquid) по часам: rsync часа с бокса -> g0_chft_generic.one -> удалить.
Запуск: g0_stream.py <out.jsonl> <workers> <ex,ex,...>. Возобновляемо (пропускает единицы, уже записанные в out.jsonl). Метка готовности <out>.done.
Единица = (биржа, сутки, час); пропуск единицы (нет каталога на боксе) пишется как {"unit":..., "missing":true}."""
import sys, os, json, subprocess, shutil, glob, datetime as dt
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g0_fast import one

BOX = "u677479@u677479.your-storagebox.de:alpha/stakan/chft"
SSH = "ssh -p 23 -i /root/.ssh/id_box -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
TMP = "/root/p40/stream_tmp"
KEEP = ("file", "ex", "rows", "error", "types", "lag_ms_q", "event_time_nonmono", "final_id_nonmono", "prev_gaps", "prev_msgs",
        "checked", "crossed", "best_age_ms_q", "snapshots", "snap_interval_s_q")

def unit(u):
    ex, day, hh = u
    d = f"{TMP}/{ex}/{day}_{hh}"; os.makedirs(d, exist_ok=True)
    for attempt in range(3):
        r = subprocess.run(["rsync", "-a", "-e", SSH, "--include=*_orderbook.parquet", "--exclude=*", f"{BOX}/{ex}/{day}/{hh}/", d + "/"],
                           capture_output=True, text=True)
        if r.returncode == 0: break
        if "No such file" in r.stderr or r.returncode == 23 and "link_stat" in r.stderr: break
    files = sorted(glob.glob(d + "/*.parquet"))
    if not files:
        shutil.rmtree(d, ignore_errors=True)
        return [{"unit": list(u), "missing": True, "rc": r.returncode, "err": r.stderr[-120:]}]
    out = []
    for f in files:
        # one() берёт биржу из p.split('/')[-2]; кладём файл в каталог с именем биржи
        ed = f"{d}/{ex}"; os.makedirs(ed, exist_ok=True); nf = f"{ed}/{os.path.basename(f)}"; os.replace(f, nf)
        res = one(nf); res = {k: v for k, v in res.items() if k in KEEP}
        res["unit"] = list(u); out.append(res); os.remove(nf)
    shutil.rmtree(d, ignore_errors=True)
    return out

if __name__ == "__main__":
    outp, nw, exs = sys.argv[1], int(sys.argv[2]), sys.argv[3].split(",")
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            try: done.add(tuple(json.loads(l)["unit"]))
            except Exception: pass
    d0 = dt.date(2026, 9, 17); units = []
    for i in range(36):
        day = (d0 + dt.timedelta(days=i)).isoformat()
        for ex in exs:
            for h in range(24):
                u = (ex, day, f"{h:02d}")
                if u not in done: units.append(u)
    os.makedirs(TMP, exist_ok=True)
    with Pool(nw) as pool, open(outp, "a") as fo:
        for n, res in enumerate(pool.imap_unordered(unit, units, chunksize=1)):
            for r in res: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            fo.flush()
            if n % 20 == 0: print(n, "/", len(units), flush=True)
    open(outp + ".done", "w").write("ok\n")
