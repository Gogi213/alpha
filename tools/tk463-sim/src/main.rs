//! TK-463: компилируемое ядро tk461_sim.trade + tk461_scan.one (+ строки контролей C-X из файла входов).
//! tk463-sim <минуты.csv[.gz]> <tf> <входы.csv[.gz]> <кэш фандинга> <ячейки.csv> <out.csv[.gz]> [--threads N] [--max-events N]
//! Входы: колонки ev,dir,side,t0,tt0,atr_bps (+ evk,cj у контролей). Ячейки: stop,m,take,par[,be_r]. Арифметика — в том же
//! порядке операций, что в Python (f64 IEEE, сумма фандинга — pairwise numpy), чтобы совпадать побитно.
use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Command, Stdio};

const MK: f64 = 1.26;
const TK: f64 = 3.15;
const N_BARS: i64 = 10;
const K_SWING: i64 = 2;

fn open_text(path: &str) -> Box<dyn BufRead> {
    if path.ends_with(".gz") {
        let ch = Command::new("gzip")
            .args(["-dc", path])
            .stdout(Stdio::piped())
            .spawn()
            .expect("gzip");
        Box::new(BufReader::with_capacity(1 << 20, ch.stdout.unwrap()))
    } else {
        Box::new(BufReader::with_capacity(
            1 << 20,
            std::fs::File::open(path).unwrap_or_else(|e| panic!("{path}: {e}")),
        ))
    }
}

type Table = (HashMap<String, usize>, Vec<Vec<String>>);

/// таблица csv: имя колонки -> номер, строки полей
fn read_table(r: Box<dyn BufRead>) -> Table {
    let mut it = r.lines();
    let head = it.next().expect("пустой файл").unwrap();
    let idx = head
        .trim_end()
        .split(',')
        .enumerate()
        .map(|(i, s)| (s.to_string(), i))
        .collect();
    let rows = it
        .map(|l| l.unwrap())
        .filter(|l| !l.trim().is_empty())
        .map(|l| l.trim_end().split(',').map(String::from).collect())
        .collect();
    (idx, rows)
}

fn num(s: &str) -> f64 {
    if s.is_empty() {
        f64::NAN
    } else {
        s.parse().unwrap_or_else(|_| panic!("число: {s}"))
    }
}

/// numpy pairwise_sum (n >= 8: 8 накопителей, блок 128)
fn pairwise(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut r = 0.0;
        for x in a {
            r += x;
        }
        r
    } else if n <= 128 {
        let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
        let mut i = 8;
        while i < n - (n % 8) {
            for j in 0..8 {
                r[j] += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise(&a[..n2]) + pairwise(&a[n2..])
    }
}

/// ndarray.sum(): pairwise по всем элементам (numpy редукция add стартует с единицы-нуля, не с a[0]); пустой — 0.0
fn np_sum(a: &[f64]) -> f64 {
    pairwise(a)
}

fn civil_ym(ms: i64) -> (i64, i64) {
    let z = ms.div_euclid(86_400_000) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m)
}

fn swings(h: &[f64], ok: &[bool], k: usize) -> Vec<i64> {
    let n = h.len();
    let mut out = vec![];
    if n < 2 * k + 1 {
        return out;
    }
    'i: for i in k..n - k {
        for s in 1..=k {
            if !(h[i] > h[i - s] && h[i] > h[i + s]) {
                continue 'i;
            }
        }
        for s in 0..=2 * k {
            if !ok[i + s - k] {
                continue 'i;
            }
        }
        out.push(i as i64);
    }
    out
}

struct Coin {
    sym: String,
    tf: i64,
    tm: Vec<i64>,
    px: [[Vec<f64>; 4]; 2], // [0] — лонг (d=1), [1] — зеркало (-o,-l,-h,-c) для шорта
    swh_i: Vec<i64>,
    swh_p: Vec<f64>,
    swl_i: Vec<i64>,
    swl_p: Vec<f64>,
    ft: Vec<i64>,
    fr: Vec<f64>,
    f_sorted: bool,
    pidx: Vec<u32>,          // минута -> индекс бара ТФ
    bar: [[Vec<f64>; 3]; 2], // по ориентации: open/high/low бара (зеркало: -o,-l,-h)
    sw: [Vec<f64>; 2], // TW: последний подтверждённый свинг-уровень на конец минуты (NaN — нет), в ориентации
}

impl Coin {
    fn load(path: &str, tf: i64, fcache: &str) -> Coin {
        let t_ld = std::time::Instant::now();
        // колонки 0..4: время, o, h, l, c; разбор без аллокаций на поле
        let mut text = String::new();
        open_text(path).read_to_string(&mut text).expect("свечи");
        let (mut tm, mut o, mut h, mut l, mut c) = (vec![], vec![], vec![], vec![], vec![]);
        for line in text.lines().skip(1) {
            let line = line.trim_end();
            if line.is_empty() {
                continue;
            }
            let mut f = line.split(',');
            tm.push(f.next().unwrap().parse::<i64>().unwrap());
            for v in [&mut o, &mut h, &mut l, &mut c] {
                v.push(num(f.next().unwrap()));
            }
        }
        drop(text);
        let n = tm.len();
        eprintln!(
            "загрузка: свечи {:.2} с ({n} минут)",
            t_ld.elapsed().as_secs_f64()
        );
        let neg = |v: &[f64]| v.iter().map(|x| -x).collect::<Vec<f64>>();
        let mir = [neg(&o), neg(&l), neg(&h), neg(&c)];
        // бары ТФ: группа = t // (tf*60000); дыры — пустые бары (не ok)
        let w = tf * 60_000;
        let g: Vec<i64> = tm.iter().map(|t| t.div_euclid(w)).collect();
        let g0 = g[0];
        let nb = (g[n - 1] - g0 + 1) as usize;
        let (mut bh, mut bl, mut bo, mut cnt) = (
            vec![f64::NAN; nb],
            vec![f64::NAN; nb],
            vec![f64::NAN; nb],
            vec![0i64; nb],
        );
        for i in 0..n {
            let p = (g[i] - g0) as usize;
            if cnt[p] == 0 {
                bo[p] = o[i];
                bh[p] = h[i];
                bl[p] = l[i];
            } else {
                if h[i] > bh[p] {
                    bh[p] = h[i];
                }
                if l[i] < bl[p] {
                    bl[p] = l[i];
                }
            }
            cnt[p] += 1;
        }
        let ok: Vec<bool> = cnt.iter().map(|&x| x == tf).collect();
        let nbl: Vec<f64> = bl.iter().map(|x| -x).collect();
        let swh_i = swings(&bh, &ok, K_SWING as usize);
        let swl_i = swings(&nbl, &ok, K_SWING as usize);
        let swh_p: Vec<f64> = swh_i.iter().map(|&i| bh[i as usize]).collect();
        let swl_p: Vec<f64> = swl_i.iter().map(|&i| bl[i as usize]).collect();
        // TW (P-TK461 §12.2 п.3): свинг k=2 подтверждён после закрытия бара i+2 (конец-минута бара i+2 — календарная:
        // (t+1 мин) кратно ТФ); уровень = последний подтверждённый, до входа — тоже (переносится по минутам)
        let sw_for = |idx: &[i64], lvl: &[f64], sign: f64| -> Vec<f64> {
            let (mut out, mut k, mut cur) = (vec![f64::NAN; n], 0usize, f64::NAN);
            for m in 0..n {
                let p = g[m] - g0;
                let cb = if (tm[m] + 60_000) % w == 0 { p } else { p - 1 };
                while k < idx.len() && idx[k] + K_SWING <= cb {
                    cur = sign * lvl[k];
                    k += 1;
                }
                out[m] = cur;
            }
            out
        };
        let sw = [sw_for(&swl_i, &swl_p, 1.0), sw_for(&swh_i, &swh_p, -1.0)];
        let pidx: Vec<u32> = g.iter().map(|x| (x - g0) as u32).collect();
        let bar = [
            [bo.clone(), bh.clone(), bl.clone()],
            [neg(&bo), neg(&bl), neg(&bh)],
        ];
        let sym = std::path::Path::new(path)
            .file_name()
            .unwrap()
            .to_string_lossy()
            .split('.')
            .next()
            .unwrap()
            .to_string();
        let t_fu = std::time::Instant::now();
        let (mut ft, mut fr) = (vec![], vec![]); // фандинг: месяцы от первой до последней минуты
        let (mut y, mut m) = civil_ym(tm[0]);
        let (y1, m1) = civil_ym(tm[n - 1]);
        loop {
            let z = format!("{fcache}/{sym}-fundingRate-{y:04}-{m:02}.zip");
            if std::path::Path::new(&z).exists() {
                let out = Command::new("unzip")
                    .args(["-p", &z])
                    .output()
                    .expect("unzip");
                let (ix, rs) = read_table(Box::new(std::io::Cursor::new(out.stdout)));
                let (a, b) = (ix["calc_time"], ix["last_funding_rate"]);
                for r in rs {
                    ft.push(r[a].parse::<i64>().unwrap());
                    fr.push(num(&r[b]));
                }
            } else {
                eprintln!("funding miss {sym} {y:04}-{m:02}");
            }
            if (y, m) == (y1, m1) {
                break;
            }
            m += 1;
            if m == 13 {
                m = 1;
                y += 1;
            }
        }
        eprintln!(
            "загрузка: бары+свинги до фандинга {:.2} с, фандинг {:.2} с",
            t_fu.duration_since(t_ld).as_secs_f64(),
            t_fu.elapsed().as_secs_f64()
        );
        let f_sorted = ft.windows(2).all(|w| w[0] <= w[1]);
        Coin {
            sym,
            tf,
            tm,
            px: [[o, h, l, c], mir],
            swh_i,
            swh_p,
            swl_i,
            swl_p,
            ft,
            fr,
            f_sorted,
            pidx,
            bar,
            sw,
        }
    }

    /// ближайший по цене подтверждённый на t0 свинг по выгодной стороне
    fn target_s(&self, d: i64, t0: i64, entry_abs: f64) -> Option<f64> {
        let mut best: Option<f64> = None;
        if d == 1 {
            for (&i, &p) in self.swh_i.iter().zip(&self.swh_p) {
                if i + K_SWING <= t0 && p > entry_abs && best.is_none_or(|b| p < b) {
                    best = Some(p);
                }
            }
        } else {
            for (&i, &p) in self.swl_i.iter().zip(&self.swl_p) {
                if i + K_SWING <= t0 && p < entry_abs && best.is_none_or(|b| p > b) {
                    best = Some(p);
                }
            }
        }
        best
    }

    fn funding_bps(&self, t_in: i64, t_out: i64, side: i64) -> f64 {
        if self.f_sorted {
            let lo = self.ft.partition_point(|&t| t <= t_in);
            let hi = self.ft.partition_point(|&t| t <= t_out).max(lo);
            return side as f64 * 1e4 * np_sum(&self.fr[lo..hi]);
        }
        let v: Vec<f64> = self
            .ft
            .iter()
            .zip(&self.fr)
            .filter(|(&t, _)| t > t_in && t <= t_out)
            .map(|(_, &r)| r)
            .collect();
        side as f64 * 1e4 * np_sum(&v)
    }
}

#[derive(Clone, Copy)]
enum Why {
    Stop,
    Target,
    Deadline,
    Ot,
}

impl Why {
    fn idx(self) -> usize {
        self as usize
    }
    fn s(self) -> &'static str {
        match self {
            Why::Stop => "stop",
            Why::Target => "target",
            Why::Deadline => "deadline",
            Why::Ot => "ot",
        }
    }
}

/// python max(a, b): b только если b > a
#[inline]
fn pmax(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}
/// python min(a, b): b только если b < a
#[inline]
fn pmin(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

struct Part {
    pt: f64,
    frac: f64,
    trail: f64,
}
struct Leg {
    f: f64,
    px: f64,
    maker: bool,
}

/// ОТ (P-TK461 §12.2 п.5): проверка на закрытии бара ТФ; kind 1/3/4/5 = OT1/OT3/OT4/OT5
struct OtCtx<'a> {
    kind: u8,
    p: f64,
    lvl: f64,    // OT1: уровень, ниже которого open и close бара
    atr_px: f64, // ATR14(t0-1) в цене
    r: f64,      // R в цене (NaN у ST — OT5 не срабатывает)
    entry: f64,
    cn: &'a Coin,
    orient: usize,
}

impl OtCtx<'_> {
    fn fires(&self, m: usize, mfe: f64, since: i64, close: f64) -> bool {
        let p = self.cn.pidx[m] as usize;
        let [bo, bh, bl] = &self.cn.bar[self.orient];
        match self.kind {
            1 => bo[p] < self.lvl && close < self.lvl,
            3 => since as f64 >= self.p,
            4 => close < bo[p] && (bh[p] - bl[p]) / self.atr_px >= self.p,
            _ => mfe >= self.entry + self.p * self.r && close <= self.entry,
        }
    }
}

/// tk461_sim.trade (формы D2a + БУ + TW-свинг + ОТ). Возврат (минута выхода, причина, mfe); ноги — в `legs`.
#[allow(clippy::too_many_arguments)]
fn trade(
    o: &[f64],
    h: &[f64],
    l: &[f64],
    c: &[f64],
    e: usize,
    last: usize,
    entry: f64,
    stop: f64,
    tgt: Option<f64>,
    trail: Option<f64>,
    part: Option<&Part>,
    be: Option<f64>,
    swing: Option<&[f64]>,
    ot: Option<&OtCtx>,
    tm: &[i64],
    legs: &mut Vec<Leg>,
) -> (usize, Why, f64) {
    legs.clear();
    let mut pos = 1.0f64;
    let mut ext = f64::NEG_INFINITY;
    let mut cur = stop;
    let mut part_done = part.is_none();
    let mut mfe = entry;
    let (mut since, mut mfe_prev) = (0i64, entry);
    let wms = ot.map_or(0, |o| o.cn.tf * 60_000);
    // граница окна ОТ: наименьшее кратное wms >= tm[m]+60_000 (tm не убывает) вместо деления на каждой минуте — тот же результат
    let mut nb = match tm.get(e) {
        Some(&t) if wms > 0 => (t + 60_000 + wms - 1).div_euclid(wms) * wms,
        _ => 0,
    };
    for m in e..=last {
        if cur.is_finite() && l[m] <= cur {
            let px = if m > e {
                pmin(cur, o[m])
            } else {
                pmin(cur, entry)
            };
            legs.push(Leg {
                f: pos,
                px,
                maker: false,
            });
            return (m, Why::Stop, mfe);
        }
        if let Some(t) = tgt {
            if h[m] > t {
                legs.push(Leg {
                    f: pos,
                    px: t,
                    maker: true,
                });
                return (m, Why::Target, mfe);
            }
        }
        if !part_done {
            let p = part.unwrap();
            if h[m] > p.pt {
                legs.push(Leg {
                    f: p.frac,
                    px: p.pt,
                    maker: true,
                });
                pos -= p.frac;
                part_done = true;
            }
        }
        if h[m] > ext {
            ext = h[m];
        }
        if h[m] > mfe {
            mfe = h[m];
        }
        if let Some(t) = trail {
            cur = pmax(cur, ext - t);
        }
        if let Some(p) = part {
            if part_done && p.frac < 1.0 {
                cur = pmax(cur, ext - p.trail);
            }
        }
        if let Some(sw) = swing {
            if sw[m].is_finite() {
                cur = pmax(cur, sw[m]);
            }
        }
        if let Some(b) = be {
            if h[m] >= b {
                cur = pmax(cur, entry);
            }
        }
        if let Some(ot) = ot {
            let v = tm[m] + 60_000;
            while nb < v {
                nb += wms;
            }
            if v == nb {
                since = if mfe > mfe_prev { 0 } else { since + 1 };
                mfe_prev = mfe;
                if ot.fires(m, mfe, since, c[m]) {
                    legs.push(Leg {
                        f: pos,
                        px: c[m],
                        maker: false,
                    });
                    return (m, Why::Ot, mfe);
                }
            }
        }
    }
    legs.push(Leg {
        f: pos,
        px: c[last],
        maker: false,
    });
    (last, Why::Deadline, mfe)
}

struct Cell {
    stop: String,
    m: f64,
    take: String,
    par: f64,
    be_r: Option<f64>,
    ot: u8, // 0 — нет, 1/3/4/5 — OT1/OT3/OT4/OT5
    otp: f64,
    a: String,   // ",stop,take,par"
    b: String,   // ",be_r" или пусто
    kev: String, // TK-467: область ячейки — ключ (ev, dir) событий; пусто — все события (умолчание)
    kdir: String,
}

struct Entry {
    ev: String,
    dir: String,
    side: i64,
    t0: i64,
    tt0: i64,
    atr: f64,
    evk: Option<i64>,
    cj: Option<i64>,
    ss_bps: Option<f64>,
    ot1_bps: Option<f64>,
    mid: String, // ",evk,cj" или пусто
    gk: u32,     // номер (ev, dir, mid) для режима --agg
    ck: u32,     // номер списка ячеек (по ключу (ev, dir)) в `lists`
}

/// сумма по группе (ячейка, ev/dir/mid, сторона, сутки UTC входа) — режим `--agg 1` (TK-464); net = gross − fee − fund
#[derive(Default)]
struct Acc {
    n: u64,
    nofill: u64,
    no_target: u64,
    why: [u64; 4],
    gross: f64,
    fund: f64,
    net: f64,
    net_tk: f64,
    net_mk: f64,
    sq: f64,
    win: u64,
    bars: f64,
    mfe: f64,
}

impl Acc {
    fn merge(&mut self, o: &Acc) {
        self.n += o.n;
        self.nofill += o.nofill;
        self.no_target += o.no_target;
        for i in 0..4 {
            self.why[i] += o.why[i];
        }
        self.gross += o.gross;
        self.fund += o.fund;
        self.net += o.net;
        self.net_tk += o.net_tk;
        self.net_mk += o.net_mk;
        self.sq += o.sq;
        self.win += o.win;
        self.bars += o.bars;
        self.mfe += o.mfe;
    }
}

type Aggm = BTreeMap<(usize, u32, i64, i64), Acc>;

fn slot<'a>(m: &'a mut Aggm, ci: usize, s: &Entry) -> &'a mut Acc {
    m.entry((ci, s.gk, s.side, s.tt0.div_euclid(86_400_000)))
        .or_default()
}

/// число как repr Python (кратчайшее круглое); NaN — пусто (как pandas); inf/-inf у Debug те же
fn fl(x: f64) -> String {
    let mut s = String::new();
    pf(&mut s, x);
    s
}

#[inline]
fn pf(out: &mut String, x: f64) {
    if !x.is_nan() {
        write!(out, "{x:?}").unwrap();
    }
}

fn search_left(a: &[i64], v: i64) -> usize {
    a.partition_point(|&x| x < v)
}

fn one(
    cn: &Coin,
    pre: &str,
    s: &Entry,
    cell: &Cell,
    legs: &mut Vec<Leg>,
    tcache: &mut Option<Option<f64>>,
    out: &mut String,
    ci: usize,
    agg: &mut Option<Aggm>,
) {
    let tf = cn.tf;
    let d = s.side;
    let [o, h, l, c] = &cn.px[if d == 1 { 0 } else { 1 }];
    let e = search_left(&cn.tm, s.tt0 + tf * 60_000);
    let last = search_left(&cn.tm, s.tt0 + (N_BARS + 1) * tf * 60_000) as i64 - 1;
    let ss = cell.stop == "SS";
    if (ss && s.ss_bps.is_none()) || (cell.ot == 1 && s.ot1_bps.is_none()) {
        return; // SS/OT1 без определённого расстояния — ячейка для строки пропущена (§12.2 п.1)
    }
    if agg.is_none() {
        out.push_str(pre);
        out.push_str(&cell.a);
        out.push_str(&s.mid);
        out.push_str(&cell.b);
    }
    if (e as i64) > last {
        match agg {
            Some(m) => slot(m, ci, s).nofill += 1,
            None => out.push_str(",nofill\n"),
        }
        return;
    }
    let last = last as usize;
    let ep = o[e];
    let st = cell.stop == "ST";
    let (mut stop, mut tgt, mut trail, mut part, mut be) =
        (f64::NEG_INFINITY, None, None, None, None);
    let mut dist = f64::NAN;
    if !st {
        dist = if ss {
            s.ss_bps.unwrap() / 1e4 * ep.abs()
        } else {
            cell.m * s.atr / 1e4 * ep.abs()
        };
        stop = ep - dist;
        let risk = dist;
        match cell.take.as_str() {
            "TP" => tgt = Some(ep + cell.par * risk),
            "TS" => match *tcache.get_or_insert_with(|| cn.target_s(d, s.t0, ep.abs())) {
                Some(tp) if d as f64 * tp > ep => tgt = Some(d as f64 * tp),
                _ => {
                    match agg {
                        Some(m) => slot(m, ci, s).no_target += 1,
                        None => out.push_str(",no_target\n"),
                    }
                    return;
                }
            },
            "TR" => trail = Some(cell.par * s.atr / 1e4 * ep.abs()),
            "PT" => {
                part = Some(Part {
                    pt: ep + risk,
                    frac: 0.5,
                    trail: 1.0 * s.atr / 1e4 * ep.abs(),
                })
            }
            "TN" | "TW" => {}
            t => panic!("take {t}"),
        }
        if let Some(b) = cell.be_r {
            be = Some(ep + b * risk);
        }
    }
    let orient = if d == 1 { 0 } else { 1 };
    let swing = (cell.take == "TW").then(|| cn.sw[orient].as_slice());
    let otc = (cell.ot != 0).then(|| OtCtx {
        kind: cell.ot,
        p: cell.otp,
        lvl: ep - (s.ot1_bps.unwrap_or(f64::NAN) + cell.otp * s.atr) * 1e-4 * ep.abs(),
        atr_px: s.atr / 1e4 * ep.abs(),
        r: dist,
        entry: ep,
        cn,
        orient,
    });
    let (mx, why, mfe) = trade(
        o,
        h,
        l,
        c,
        e,
        last,
        ep,
        stop,
        tgt,
        trail,
        part.as_ref(),
        be,
        swing,
        otc.as_ref(),
        &cn.tm,
        legs,
    );
    let (mut g, mut fee_s, mut mk_s, mut tk_s) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    for lg in legs.iter() {
        g += lg.f * 1e4 * (lg.px - ep) / ep.abs();
        fee_s += lg.f * if lg.maker { MK } else { TK };
        mk_s += lg.f * MK;
        tk_s += lg.f * TK;
    }
    let fund = cn.funding_bps(cn.tm[e], cn.tm[mx] + 60_000, d);
    let bars = (mx - e + 1) as f64 / tf as f64;
    let mfe_bps = 1e4 * (mfe - ep) / ep.abs();
    let risk_bps = if st {
        f64::NAN
    } else if ss {
        s.ss_bps.unwrap()
    } else {
        cell.m * s.atr
    };
    if let Some(m) = agg {
        let (fee, fee_mk, fee_tk) = (TK + fee_s, MK + mk_s, TK + tk_s);
        let net = g - fee - fund;
        let a = slot(m, ci, s);
        a.n += 1;
        a.why[why.idx()] += 1;
        a.gross += g;
        a.fund += fund;
        a.net += net;
        a.net_tk += g - fee_tk - fund;
        a.net_mk += g - fee_mk - fund;
        a.sq += net * net;
        a.win += (net > 0.0) as u64;
        a.bars += bars;
        a.mfe += mfe_bps;
        return;
    }
    out.push(',');
    out.push_str(why.s());
    for x in [
        g,
        TK + fee_s,
        MK + mk_s,
        TK + tk_s,
        fund,
        bars,
        mfe_bps,
        risk_bps,
    ] {
        out.push(',');
        pf(out, x);
    }
    out.push('\n');
}

/// входы из таблицы: колонки ev,dir,side,t0,tt0,atr_bps (+ evk,cj, ss_bps, ot1_bps)
fn entries(ix: &HashMap<String, usize>, rows: &[Vec<String>]) -> Vec<Entry> {
    let g = |r: &Vec<String>, k: &str| r[ix[k]].clone();
    rows.iter()
        .map(|r| Entry {
            ev: g(r, "ev"),
            dir: g(r, "dir"),
            side: num(&g(r, "side")) as i64,
            t0: num(&g(r, "t0")) as i64,
            tt0: num(&g(r, "tt0")) as i64,
            atr: num(&g(r, "atr_bps")),
            evk: ix.get("evk").map(|_| num(&g(r, "evk")) as i64),
            cj: ix.get("cj").map(|_| num(&g(r, "cj")) as i64),
            ss_bps: ix
                .get("ss_bps")
                .map(|_| num(&g(r, "ss_bps")))
                .filter(|v| v.is_finite()),
            ot1_bps: ix
                .get("ot1_bps")
                .map(|_| num(&g(r, "ot1_bps")))
                .filter(|v| v.is_finite()),
            mid: String::new(),
            gk: 0,
            ck: 0,
        })
        .collect()
}

/// парная сумма (режим `--pair`): событие против среднего net его контролей — как tk461_d2a.one (c, d, b, k, d2)
#[derive(Default)]
struct PAcc {
    c: u64,
    d: f64,
    b: f64,
    k: f64,
    d2: f64,
}

type Pairm = BTreeMap<(usize, u32, i64, i64), PAcc>;

/// net одной сделки (gross − fee − fund) или None: ячейка пропущена / nofill / no_target — через `one` в режиме агрегата,
/// чтобы арифметика была той же, что в построчном выводе
fn net_of(
    cn: &Coin,
    s: &Entry,
    cell: &Cell,
    legs: &mut Vec<Leg>,
    tcache: &mut Option<Option<f64>>,
    scratch: &mut Option<Aggm>,
) -> Option<f64> {
    scratch.as_mut().unwrap().clear();
    let mut dummy = String::new();
    one(cn, "", s, cell, legs, tcache, &mut dummy, 0, scratch);
    let a = scratch.as_ref().unwrap().values().next()?;
    (a.n == 1).then_some(a.net)
}

/// выход: `.gz` — через gzip, иначе файл
fn open_out(path: &str, gz_level: u32) -> (Box<dyn Write>, Option<std::process::Child>) {
    if path.ends_with(".gz") {
        let mut ch = Command::new("sh")
            .args(["-c", &format!("gzip -{gz_level} -c > '{path}'")])
            .stdin(Stdio::piped())
            .spawn()
            .unwrap();
        (Box::new(ch.stdin.take().unwrap()), Some(ch))
    } else {
        (Box::new(std::fs::File::create(path).unwrap()), None)
    }
}

fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    let opt = |k: &str| a.iter().position(|x| x == k).map(|i| a[i + 1].clone());
    let threads: usize = opt("--threads").map_or(1, |s| s.parse().unwrap());
    let gz_level: u32 = opt("--gz").map_or(6, |s| s.parse().unwrap());
    let maxev = opt("--max-events").map(|s| s.parse::<usize>().unwrap());
    let mut pos: Vec<&String> = vec![];
    let mut i = 0;
    while i < a.len() {
        if a[i].starts_with("--") {
            i += 2;
        } else {
            pos.push(&a[i]);
            i += 1;
        }
    }
    let t_start = std::time::Instant::now();
    let tf: i64 = pos[1].parse().unwrap();
    let cn = Coin::load(pos[0], tf, pos[3]);
    let (ix, rows) = read_table(open_text(pos[2]));
    let mut ents = entries(&ix, &rows);
    if let Some(m) = maxev {
        ents.truncate(m);
    }
    // TK-467 `--merge-layers 1`: слои дублей кодируются суффиксом "~k" в ev; в группе вывода слои складываются (ключ без суффикса)
    let merge = opt("--merge-layers").is_some_and(|v| v == "1");
    let base = |s: &str| {
        if merge {
            s.split('~').next().unwrap().to_string()
        } else {
            s.to_string()
        }
    };
    let mut gkeys: Vec<(String, String, String)> = vec![];
    let mut gmap: HashMap<(String, String, String), u32> = HashMap::new();
    for e in &mut ents {
        if let (Some(k), Some(j)) = (e.evk, e.cj) {
            e.mid = format!(",{k},{j}");
        }
        let key = (base(&e.ev), e.dir.clone(), e.mid.clone());
        e.gk = *gmap.entry(key.clone()).or_insert_with(|| {
            gkeys.push(key);
            gkeys.len() as u32 - 1
        });
    }
    let (cx, crow) = read_table(open_text(pos[4]));
    let cells: Vec<Cell> = crow
        .iter()
        .map(|r| {
            let be_r = cx.get("be_r").and_then(|&i| {
                let v = num(&r[i]);
                if v.is_nan() {
                    None
                } else {
                    Some(v)
                }
            });
            let (stop, take, par) = (
                r[cx["stop"]].clone(),
                r[cx["take"]].clone(),
                num(&r[cx["par"]]),
            );
            let (otn, otp) = cx
                .get("ot")
                .map_or(("", f64::NAN), |&i| (r[i].as_str(), num(&r[cx["otp"]])));
            let ot = match otn {
                "" => 0,
                "OT1" => 1,
                "OT3" => 3,
                "OT4" => 4,
                "OT5" => 5,
                x => panic!("ot {x}"),
            };
            let mut b = if cx.contains_key("be_r") {
                format!(",{}", be_r.map_or(String::new(), fl))
            } else {
                String::new()
            };
            if cx.contains_key("ot") {
                b += &format!(",{otn},{}", fl(otp));
            }
            Cell {
                a: format!(",{stop},{take},{}", fl(par)),
                b,
                kev: cx.get("ev").map_or(String::new(), |&i| r[i].clone()),
                kdir: cx.get("dir").map_or(String::new(), |&i| r[i].clone()),
                ot,
                otp,
                m: num(&r[cx["m"]]),
                stop,
                take,
                par,
                be_r,
            }
        })
        .collect();
    // TK-467: каноническая ячейка для сложения слоёв: (ev без суффикса, dir, текст ячейки, порядковый номер среди одинаковых в слое)
    let canon: Vec<usize> = if merge {
        let (mut m, mut seen) = (HashMap::new(), HashMap::new());
        cells
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let n = seen
                    .entry((c.kev.clone(), c.kdir.clone(), c.a.clone(), c.b.clone()))
                    .or_insert(0usize);
                *n += 1;
                *m.entry((base(&c.kev), c.kdir.clone(), c.a.clone(), c.b.clone(), *n))
                    .or_insert(i)
            })
            .collect()
    } else {
        (0..cells.len()).collect()
    };
    // TK-467: список ячеек на ключ (ev, dir) события; ячейка без области (колонок ev, dir нет или пусто) — для всех событий
    let mut lists: Vec<Vec<usize>> = vec![];
    let mut lmap: HashMap<(String, String), u32> = HashMap::new();
    for e in &mut ents {
        e.ck = *lmap
            .entry((e.ev.clone(), e.dir.clone()))
            .or_insert_with(|| {
                lists.push(
                    (0..cells.len())
                        .filter(|&ci| {
                            let c = &cells[ci];
                            (c.kev.is_empty() || c.kev == e.ev)
                                && (c.kdir.is_empty() || c.kdir == e.dir)
                        })
                        .collect(),
                );
                lists.len() as u32 - 1
            });
    }
    let mut head = String::from("sym,tf,ev,dir,t0,side,stop,take,par");
    if ents.first().is_some_and(|e| e.evk.is_some()) {
        head += ",evk,cj";
    }
    if cx.contains_key("be_r") {
        head += ",be_r";
    }
    if cx.contains_key("ot") {
        head += ",ot,otp";
    }
    head += ",reason,gross,fee,fee_mk,fee_tk,fund,bars,mfe_bps,risk_bps\n";
    let t_load = t_start.elapsed().as_secs_f64();
    let aggmode = opt("--agg").is_some_and(|v| v == "1");
    let pair_file = opt("--pair");
    let chunk = ents.len().div_ceil(threads.max(1)).max(1);
    if let Some(pf_) = pair_file {
        // парный режим: события pos[2] + контроли pf_ (evk = tt0 события, ev с префиксом из 2 знаков), группа
        // (ячейка, ev, dir, сторона, сутки tt0) -> c, d, b, k, d2; события без разрешённых контролей не считаются
        let (cix, crows) = read_table(open_text(&pf_));
        let ctrl = entries(&cix, &crows);
        let mut by: HashMap<(String, String, i64), Vec<usize>> = HashMap::new();
        for (i, c) in ctrl.iter().enumerate() {
            by.entry((c.ev[2..].to_string(), c.dir.clone(), c.evk.expect("evk")))
                .or_default()
                .push(i);
        }
        let parts: Vec<Pairm> = std::thread::scope(|sc| {
            let hs: Vec<_> = ents
                .chunks(chunk)
                .map(|ch| {
                    let (cn, cells, ctrl, by, lists, canon) =
                        (&cn, &cells, &ctrl, &by, &lists, &canon);
                    sc.spawn(move || {
                        let mut legs = Vec::with_capacity(4);
                        let mut scratch: Option<Aggm> = Some(BTreeMap::new());
                        let mut pm = Pairm::new();
                        for s in ch {
                            let cs: &[usize] = by
                                .get(&(s.ev.clone(), s.dir.clone(), s.tt0))
                                .map_or(&[], |v| v.as_slice());
                            if cs.is_empty() {
                                continue;
                            }
                            let mut tcs: Vec<Option<Option<f64>>> = vec![None; cs.len()];
                            let mut ts = None;
                            for &ci in &lists[s.ck as usize] {
                                let cell = &cells[ci];
                                let Some(net) =
                                    net_of(cn, s, cell, &mut legs, &mut ts, &mut scratch)
                                else {
                                    continue;
                                };
                                let (mut sum, mut cnt) = (0.0f64, 0u64);
                                for (j, &k) in cs.iter().enumerate() {
                                    if let Some(x) = net_of(
                                        cn,
                                        &ctrl[k],
                                        cell,
                                        &mut legs,
                                        &mut tcs[j],
                                        &mut scratch,
                                    ) {
                                        sum += x;
                                        cnt += 1;
                                    }
                                }
                                if cnt == 0 {
                                    continue;
                                }
                                let k = sum / cnt as f64;
                                let d = net - k;
                                let a = pm
                                    .entry((canon[ci], s.gk, s.side, s.tt0.div_euclid(86_400_000)))
                                    .or_default();
                                a.c += 1;
                                a.d += d;
                                a.b += net;
                                a.k += k;
                                a.d2 += d * d;
                            }
                        }
                        pm
                    })
                })
                .collect();
            hs.into_iter().map(|h| h.join().unwrap()).collect()
        });
        let mut tot = Pairm::new();
        for m in &parts {
            for (k, a) in m {
                let t = tot.entry(*k).or_default();
                t.c += a.c;
                t.d += a.d;
                t.b += a.b;
                t.k += a.k;
                t.d2 += a.d2;
            }
        }
        let mut h = String::from("sym,tf,ev,dir,day,side,stop,take,par");
        if cx.contains_key("be_r") {
            h += ",be_r";
        }
        if cx.contains_key("ot") {
            h += ",ot,otp";
        }
        h += ",c,d,b,k,d2";
        h.push(char::from(10));
        let mut o = h;
        for ((ci, gk, side, day), a) in &tot {
            let (ev, dir, _) = &gkeys[*gk as usize];
            let cell = &cells[*ci];
            write!(
                o,
                "{},{},{ev},{dir},{day},{side}{}{}",
                cn.sym, cn.tf, cell.a, cell.b
            )
            .unwrap();
            write!(o, ",{}", a.c).unwrap();
            for x in [a.d, a.b, a.k, a.d2] {
                o.push(',');
                pf(&mut o, x);
            }
            o.push(char::from(10));
        }
        let path = pos[5];
        let (mut w, child) = open_out(path, gz_level);
        w.write_all(o.as_bytes()).unwrap();
        drop(w);
        if let Some(mut ch) = child {
            ch.wait().unwrap();
        }
        eprintln!(
            "время: загрузка {t_load:.2} с, счёт {:.2} с; {path} {} строк",
            t_start.elapsed().as_secs_f64() - t_load,
            tot.len()
        );
        return;
    }
    let parts: Vec<(String, Option<Aggm>)> = std::thread::scope(|sc| {
        let hs: Vec<_> = ents
            .chunks(chunk)
            .map(|ch| {
                let (cn, cells, lists) = (&cn, &cells, &lists);
                sc.spawn(move || {
                    let (mut out, mut legs) = (String::new(), Vec::with_capacity(4));
                    let mut agg: Option<Aggm> = aggmode.then(BTreeMap::new);
                    for s in ch {
                        let mut tcache = None; // цель TS не зависит от ячейки: одна на вход
                        let pre = format!(
                            "{},{},{},{},{},{}",
                            cn.sym, cn.tf, s.ev, s.dir, s.tt0, s.side
                        );
                        for &ci in &lists[s.ck as usize] {
                            let cell = &cells[ci];
                            one(
                                cn,
                                &pre,
                                s,
                                cell,
                                &mut legs,
                                &mut tcache,
                                &mut out,
                                ci,
                                &mut agg,
                            );
                        }
                    }
                    (out, agg)
                })
            })
            .collect();
        hs.into_iter().map(|h| h.join().unwrap()).collect()
    });
    let t_calc = t_start.elapsed().as_secs_f64();
    let path = pos[5];
    let (mut w, child) = open_out(path, gz_level);
    let mut nlines = 0usize;
    if aggmode {
        let mut tot = Aggm::new();
        for (_, m) in &parts {
            for (k, a) in m.as_ref().unwrap() {
                tot.entry(*k).or_default().merge(a);
            }
        }
        let h = head.replace("sym,tf,ev,dir,t0,side,", "sym,tf,ev,dir,day,side,");
        let mut h = h.split(",reason,").next().unwrap().to_string();
        h += ",n,nofill,no_target,n_stop,n_target,n_deadline,n_ot,gross,fund,net,net_tk,net_mk,sq,win,bars,mfe_bps\n";
        w.write_all(h.as_bytes()).unwrap();
        let mut o = String::new();
        for ((ci, gk, side, day), a) in &tot {
            let (ev, dir, mid) = &gkeys[*gk as usize];
            let cell = &cells[*ci];
            write!(
                o,
                "{},{},{ev},{dir},{day},{side}{}{mid}{}",
                cn.sym, cn.tf, cell.a, cell.b
            )
            .unwrap();
            write!(
                o,
                ",{},{},{},{},{},{},{}",
                a.n, a.nofill, a.no_target, a.why[0], a.why[1], a.why[2], a.why[3]
            )
            .unwrap();
            for x in [a.gross, a.fund, a.net, a.net_tk, a.net_mk, a.sq] {
                o.push(',');
                pf(&mut o, x);
            }
            write!(o, ",{}", a.win).unwrap();
            for x in [a.bars, a.mfe] {
                o.push(',');
                pf(&mut o, x);
            }
            o.push('\n');
            nlines += 1;
        }
        w.write_all(o.as_bytes()).unwrap();
    } else {
        w.write_all(head.as_bytes()).unwrap();
        for (p, _) in &parts {
            w.write_all(p.as_bytes()).unwrap();
        }
        nlines = parts
            .iter()
            .map(|p| p.0.matches('\n').count())
            .sum::<usize>();
    }
    drop(w);
    if let Some(mut ch) = child {
        ch.wait().unwrap();
    }
    eprintln!(
        "время: загрузка {t_load:.2} с, счёт {:.2} с, всего до записи {t_calc:.2} с",
        t_calc - t_load
    );
    eprintln!("{path} {nlines} строк");
}
