# TK-048: где лежат бинлоги довеска D+1 для сутки из pflist-jf59 (sda/sdb/нет в корне) и сколько ГБ на sdb.
import os, datetime as dt
P = "/data/tk046/%s/home/alpha/epochs/e-%s/study/root-%s/%s-%s.binlog"
M = {1: "jan", 2: "feb"}
inl = set(l.split() and tuple(l.split()) for l in open("/data/tk048/pflist-jf59.txt") if l.strip())
st = {"sda_in_pflist": [0, 0], "sda_not_in_pflist": [0, 0], "sdb": [0, 0], "missing": [0, 0], "no_root": [0, 0]}
for sy, d in sorted(inl):
    n = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
    mon = M.get(int(n[5:7]))
    if not mon:
        k = "no_root"; sz = 0
    else:
        f = P % (mon, mon, n, sy, n)
        if not os.path.lexists(f):
            k = "missing"; sz = 0
        else:
            t = os.path.realpath(f); sz = os.path.getsize(t)
            k = ("sda_in_pflist" if (sy, n) in inl else "sda_not_in_pflist") if t.startswith("/alpha-sda/") else "sdb"
    st[k][0] += 1; st[k][1] += sz
for k, (c, b) in st.items():
    print(k, c, round(b / 1e9, 2), "GB")
