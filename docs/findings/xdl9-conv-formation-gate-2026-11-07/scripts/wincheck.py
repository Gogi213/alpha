import os, sys, json, glob
import numpy as np
root = os.path.expanduser("~/xdl9/c2g")
res = {"window_reads_log": os.path.exists(os.path.join(root, "window_reads.log")), "token": os.path.exists(os.path.join(root, "go_window.token")),
       "token_used": len(glob.glob(os.path.join(root, "go_window.token*"))),
       "old_window_reads_log": os.path.exists(os.path.expanduser("~/xdl9/c2/window_reads.log"))}
mx = {}
bad = []
n = 0
for dp, dn, fn in os.walk(root):
    for f in fn:
        if not f.endswith(".npy"):
            continue
        p = os.path.join(dp, f)
        n += 1
        try:
            with open(p, "rb") as fh:
                v = np.lib.format.read_magic(fh)
                shp, fo, dt = (np.lib.format.read_array_header_1_0(fh) if v == (1, 0) else np.lib.format.read_array_header_2_0(fh))
        except Exception as e:
            bad.append((p, str(e)))
            continue
        rel = os.path.relpath(p, root)
        key = "train" if "/train/" in "/" + rel else ("calib_or_other")
        d0 = shp[0] if len(shp) == 1 else None
        if d0 is not None:
            lim = 10080 if key == "train" and "cache" in rel else 20160
            mx[key] = max(mx.get(key, 0), d0)
            if (("cache" in rel or "turn" in rel) and d0 > lim):
                bad.append((rel, shp))
res["npy_files"] = n
res["max_1d_len"] = mx
res["bad"] = bad[:10]
d1s = {}
for p in glob.glob(os.path.join(root, "**", "meta.json"), recursive=True):
    try:
        m = json.load(open(p))
    except Exception:
        continue
    if "d1" in m:
        d1s[m["d1"]] = d1s.get(m["d1"], 0) + 1
res["grid_meta_d1_counts"] = d1s
print(json.dumps(res, ensure_ascii=False, indent=1))
