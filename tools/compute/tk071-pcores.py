"""TK-071: prod_cores в окне замера по util.log (строки measure=1 между t_start и t_end задания) → 'min n' (stdout)."""
import json, sys, time

S = "/data/sched"


def window(i):
    j = json.load(open(f"{S}/jobs/{i}.json"))
    v = []
    for l in open(f"{S}/util.log"):
        if " measure=1" not in l:
            continue
        f = dict(x.split("=", 1) for x in l.split()[1:])
        if j["t_start"] <= time.mktime(time.strptime(l.split()[0], "%Y-%m-%dT%H:%M:%S")) <= j["t_end"]:
            v.append(int(f["prod_cores"]))
    return v


if __name__ == "__main__":
    v = window(sys.argv[1])
    print(min(v) if v else -1, len(v))
