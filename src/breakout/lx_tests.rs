//! Тесты TK-668 (§13.68): ключ `lx` — уровни из источника (`D` вчера, `W` прошлая неделя, `P` профиль объёма) вместо пивотов.
//! Синтетические минутки на сетке кэша (минута 0 = 00:00 UTC начала сетки; сутки — 24 часовых бара, неделя — 168).

use super::data::{Cols, START_MIN};
use super::levels::{build_levels, Level};
use super::params::{parse_variant, LevelCfg, Params};
use super::sigcache::cfg_key_text;
use super::tests::{flat, level_world, lv_params, px, set, sym};
use super::tick::{candidates, fresh_ok, TSig};
use super::tick_cli::{write_levels, write_sigsx};
use super::Side;

fn lx_cfg(lx: u8, lw: usize) -> LevelCfg {
    let mut p = Params::base();
    p.sig.lv.lx = lx;
    p.sig.lv.lw = lw;
    p.sig.lv
}

/// Часовой бар `h` (номер с начала сетки): открытие = закрытие = `close`, максимум `hi`, минимум `lo`.
fn hour(c: &mut Cols, h: usize, hi: f64, lo: f64, close: f64) {
    for m in h * 60..(h + 1) * 60 {
        set(c, m, close, close, close, close);
    }
    set(c, h * 60, close, hi, lo, close);
}

/// Бар без данных (`ok = false`): все минуты часа — дыра.
fn hole(c: &mut Cols, h: usize) {
    for m in h * 60..(h + 1) * 60 {
        set(c, m, 0.0, 0.0, 0.0, 0.0);
        c.qv[m] = 0;
    }
}

/// Сутки `d` целиком: каждый час — максимум `hi`, минимум `lo`, закрытие `close`.
fn day(c: &mut Cols, d: usize, hi: f64, lo: f64, close: f64) {
    for h in d * 24..(d + 1) * 24 {
        hour(c, h, hi, lo, close);
    }
}

/// Сутки `d` для профиля: минута суток `i` — цена `p` (open = high = low = close) и оборот `qv`.
fn prof(c: &mut Cols, d: usize, n: usize, f: impl Fn(usize) -> (f64, i64)) {
    for i in 0..n {
        let (p, q) = f(i);
        let m = d * 1440 + i;
        set(c, m, p, p, p, p);
        c.qv[m] = q;
    }
}

// ───────────────────────────── lx = 1: D, вчерашние сутки ─────────────────────────────

#[test]
fn lx1_d_level_born_end_price_first_bar_exact() {
    let mut c = Cols::new();
    day(&mut c, 0, 100.0, 99.0, 99.5);
    hour(&mut c, 7, 105.0, 99.0, 99.5); // максимум суток 0
    hour(&mut c, 9, 105.0, 99.0, 99.5); // равный максимум позже: первый час с экстремумом — 7
    day(&mut c, 1, 101.0, 98.0, 99.5);
    let lv = build_levels(&sym(c), &lx_cfg(1, 0), Side::Long);
    assert_eq!(
        lv.len(),
        2,
        "уровни суток 1 (из суток 0) и 2 (из суток 1); суток 2 данных нет"
    );
    let l = &lv[0];
    assert_eq!((l.born, l.end, l.first_bar, l.src), (1440, 2880, 7, 1));
    assert_eq!(l.segs, vec![(1440, px(105.0))]);
    assert!(
        l.mem.is_empty() && l.far_edge(0, 1).is_none(),
        "журнала членов нет"
    );
    let l2 = &lv[1];
    assert_eq!((l2.born, l2.end, l2.first_bar), (2880, 4320, 24));
    assert_eq!(l2.segs, vec![(2880, px(101.0))]);
}

#[test]
fn lx1_d_level_ends_at_first_closed_bar_beyond_price() {
    let mut c = Cols::new();
    day(&mut c, 0, 100.0, 99.0, 99.5);
    hour(&mut c, 7, 105.0, 99.0, 99.5);
    day(&mut c, 1, 101.0, 98.0, 99.5);
    hour(&mut c, 26, 106.0, 98.0, 104.0); // прокол выше цены, закрытие ниже — не конец
    hour(&mut c, 27, 105.0, 98.0, 105.0); // закрытие ровно на цене — не конец (строго выше)
    hole(&mut c, 28); // дыра пропускается
    hour(&mut c, 30, 106.0, 98.0, 105.000_000_01); // закрытие на 1e-8 выше цены — конец
    hour(&mut c, 33, 110.0, 98.0, 108.0); // позже — на уровень не влияет
    let lv = build_levels(&sym(c), &lx_cfg(1, 0), Side::Long);
    assert_eq!(lv.len(), 1, "сутки 1 с дырой в баре 28: уровня суток 2 нет");
    assert_eq!(lv[0].born, 1440);
    assert_eq!(lv[0].end, (30 + 1) * 60, "конец — закрытие бара 30");
}

#[test]
fn lx1_missing_hour_gives_no_level() {
    let mut c = Cols::new();
    day(&mut c, 0, 100.0, 99.0, 99.5);
    hole(&mut c, 12);
    day(&mut c, 1, 101.0, 98.0, 99.5);
    let lv = build_levels(&sym(c), &lx_cfg(1, 0), Side::Long);
    assert_eq!(lv.len(), 1, "сутки 0 неполные: уровня суток 1 нет");
    assert_eq!((lv[0].born, lv[0].segs[0].1), (2880, px(101.0)));
    // нет данных вовсе — нет уровней
    assert!(build_levels(&sym(Cols::new()), &lx_cfg(1, 0), Side::Long).is_empty());
}

#[test]
fn lx1_d_short_side_uses_low_and_close_below() {
    let mut c = Cols::new();
    day(&mut c, 0, 100.0, 99.0, 99.5);
    hour(&mut c, 3, 100.0, 95.0, 99.5); // минимум суток 0
    hour(&mut c, 5, 100.0, 95.0, 99.5); // равный минимум позже
    day(&mut c, 1, 101.0, 98.0, 99.5);
    hour(&mut c, 30, 101.0, 90.0, 99.5); // прокол ниже цены, закрытие выше — не конец
    hour(&mut c, 40, 99.5, 94.9, 94.9); // закрытие ниже цены — конец
    let s = sym(c);
    let sh = build_levels(&s, &lx_cfg(1, 0), Side::Short);
    assert_eq!(sh.len(), 2, "уровни суток 1 и 2 (из суток 1)");
    assert_eq!(
        (sh[0].born, sh[0].end, sh[0].first_bar),
        (1440, (40 + 1) * 60, 3)
    );
    assert_eq!(
        sh[0].segs,
        vec![(1440, px(95.0))],
        "цена уровня шорта положительна"
    );
    assert_eq!(
        sh[1].segs,
        vec![(2880, px(90.0))],
        "минимум суток 1 — бар 30"
    );
    assert_eq!(sh[1].first_bar, 30);
    let lg = build_levels(&s, &lx_cfg(1, 0), Side::Long);
    assert_eq!(
        lg[0].segs,
        vec![(1440, px(100.0))],
        "лонг — максимум суток 0"
    );
    assert_eq!(lg[0].end, 2880, "закрытий выше 100 нет — до конца суток 1");
}

// ───────────────────────────── lx = 2: W, прошлая календарная неделя ─────────────────────────────

/// Мир недель: данные — сутки 2…15 (две недели от понедельника суток 2); во второй неделе бар 266 — прокол и закрытие выше максимума первой.
fn week_world() -> Cols {
    let mut c = Cols::new();
    for d in 2..=8 {
        day(&mut c, d, 100.0, 99.0, 99.5);
    }
    hour(&mut c, 2 * 24 + 30, 107.0, 99.0, 99.5); // максимум первой недели: бар 78
    for d in 9..=15 {
        day(&mut c, d, 101.0, 98.0, 99.5);
    }
    hour(&mut c, 266, 108.0, 98.0, 107.5);
    c
}

#[test]
fn lx2_w_levels_follow_lw_offset() {
    let s = sym(week_world());
    let lv = build_levels(&s, &lx_cfg(2, 2), Side::Long);
    assert_eq!(lv.len(), 2);
    let l = &lv[0];
    assert_eq!(l.born, (2 + 7) * 1440, "рождение — понедельник суток 9");
    assert_eq!(l.segs, vec![(l.born, px(107.0))]);
    assert_eq!(l.first_bar, 2 * 24 + 30);
    assert_eq!(l.end, (266 + 1) * 60, "конец — закрытие бара 266 выше 107");
    assert_eq!(l.src, 2);
    let l2 = &lv[1];
    assert_eq!(l2.born, (2 + 14) * 1440);
    assert_eq!(l2.segs, vec![(l2.born, px(108.0))]);
    assert_eq!(l2.first_bar, 266);
    assert_eq!(l2.end, l2.born + 7 * 1440, "до следующего понедельника");
    // другое смещение — другие недели: при lw = 0 полная неделя — сутки 7…13 (максимум 108 — бар 266)
    let l0 = build_levels(&s, &lx_cfg(2, 0), Side::Long);
    assert_eq!(l0.len(), 1);
    assert_eq!((l0[0].born, l0[0].first_bar), (14 * 1440, 266));
    assert_eq!(l0[0].segs, vec![(14 * 1440, px(108.0))]);
}

#[test]
fn lx2_w_needs_all_168_bars_and_skips_partial_first_week() {
    let mut c = week_world();
    hole(&mut c, 100); // дыра в первой неделе (бары 48…215)
    let lv = build_levels(&sym(c), &lx_cfg(2, 2), Side::Long);
    assert_eq!(lv.len(), 1, "неделя с дырой уровня не даёт");
    assert_eq!(lv[0].born, (2 + 14) * 1440);
    // сутки 0–1 — неполная первая неделя (данные до первого понедельника — 48 баров) уровня не дают
    let mut c = week_world();
    day(&mut c, 0, 120.0, 99.0, 99.5);
    day(&mut c, 1, 120.0, 99.0, 99.5);
    let lv = build_levels(&sym(c), &lx_cfg(2, 2), Side::Long);
    assert_eq!(lv.len(), 2);
    assert_eq!(
        lv[0].segs[0].1,
        px(107.0),
        "120 из суток 0–1 в неделю не входит"
    );
}

// ───────────────────────────── lx = 3: P, профиль объёма ─────────────────────────────

#[test]
fn lx3_p_level_is_poc_bin_centre_and_side_agnostic() {
    // средняя tp = 100,0 → корзина 0,1 (10 000 000 е-8); 99,0 — корзина 990, 101,0 — корзина 1010
    let mut c = Cols::new();
    prof(&mut c, 0, 1440, |i| {
        if i < 720 {
            (99.0, 10)
        } else {
            (101.0, 5)
        }
    });
    c.qv[307] = 50; // самый оборотный минутка корзины 990 — час 5
    prof(&mut c, 1, 1440, |_| (100.0, 0)); // сутки 1: свои данные есть, оборота нет — своего профиля нет
    let s = sym(c);
    let lg = build_levels(&s, &lx_cfg(3, 0), Side::Long);
    let sh = build_levels(&s, &lx_cfg(3, 0), Side::Short);
    assert_eq!(
        (lg.len(), sh.len()),
        (1, 1),
        "сутки 1 с нулевым оборотом уровня не дают"
    );
    for l in lg.iter().chain(&sh) {
        assert_eq!(
            l.segs,
            vec![(1440, px(99.05))],
            "центр корзины 990, цена одна для обеих сторон"
        );
        assert_eq!((l.born, l.first_bar, l.src), (1440, 5, 3));
    }
    assert_eq!(
        lg[0].end,
        (24 + 1) * 60,
        "лонг: закрытие бара 24 (100,0) выше 99,05"
    );
    assert_eq!(
        sh[0].end, 2880,
        "шорт: закрытие 100,0 не ниже 99,05 — до конца суток"
    );
}

#[test]
fn lx3_p_tie_goes_to_lower_bin_and_first_peak_minute() {
    let mut c = Cols::new();
    // равные обороты в корзинах 990 (99,0) и 1010 (101,0); верхняя — раньше по времени
    prof(&mut c, 0, 1440, |i| {
        if i < 720 {
            (101.0, 10)
        } else {
            (99.0, 10)
        }
    });
    let lv = build_levels(&sym(c), &lx_cfg(3, 0), Side::Long);
    assert_eq!(lv.len(), 1);
    assert_eq!(lv[0].segs[0].1, px(99.05), "ничья — нижняя корзина");
    assert_eq!(
        lv[0].first_bar,
        720 / 60,
        "равные минутки — первая по времени (минута 720, час 12)"
    );
}

#[test]
fn lx3_p_uses_typical_price_and_mean_based_bin_width() {
    // 720 минуток tp = (100,2 + 98,4 + 99,0) / 3 = 99,2 (оборот 10) и 720 минуток 101,0 (оборот 5): средняя tp 100,1,
    // ширина корзины 100,1 / 1000 = 0,1001, корзина 99,2 — номер 991, центр 991 · 0,1001 + 0,05005 = 99,24915
    let mut c = Cols::new();
    for m in 0..1440 {
        if m < 720 {
            set(&mut c, m, 99.0, 100.2, 98.4, 99.0);
            c.qv[m] = 10;
        } else {
            set(&mut c, m, 101.0, 101.0, 101.0, 101.0);
            c.qv[m] = 5;
        }
    }
    let lv = build_levels(&sym(c), &lx_cfg(3, 0), Side::Long);
    assert_eq!(lv.len(), 1);
    assert_eq!(lv[0].segs[0].1, 9_924_915_000);
}

#[test]
fn lx3_p_needs_1200_minutes_with_data() {
    for (n, want) in [(1199usize, 0usize), (1200, 1)] {
        let mut c = Cols::new();
        prof(&mut c, 0, n, |_| (100.0, 1));
        let lv = build_levels(&sym(c), &lx_cfg(3, 0), Side::Long);
        assert_eq!(lv.len(), want, "{n} минуток с данными");
        if want == 1 {
            assert_eq!(lv[0].segs[0].1, px(100.05), "корзина 1000, центр 100,05");
        }
    }
}

// ───────────────────────────── lx = 0 не меняется; ключи; свежий уровень; дамп ─────────────────────────────

#[test]
fn lx0_is_the_pivot_path_and_leaves_no_trace() {
    let s = sym(level_world());
    let a = build_levels(&s, &lv_params().sig.lv, Side::Long);
    let mut p = lv_params();
    p.sig.lv.lw = 5; // при lx = 0 не действует
    let b = build_levels(&s, &p.sig.lv, Side::Long);
    assert_eq!(a.len(), 1);
    assert_eq!(a.len(), b.len());
    for (x, y) in a.iter().zip(&b) {
        assert_eq!(
            (x.born, x.end, x.first_bar, x.src),
            (y.born, y.end, y.first_bar, 0)
        );
        assert_eq!(x.segs, y.segs);
    }
    // отпечаток params в meta.json при lx = 0 — как у версий до TK-668
    assert_eq!(
        format!("{:?}", Params::base().sig.lv),
        "LevelCfg { tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: false }"
    );
    assert!(format!("{:?}", lx_cfg(1, 3)).ends_with("pk: false, lx: 1, lw: 3 }"));
    assert_eq!(
        Params::base().sig.lv,
        lx_cfg(0, 0),
        "по умолчанию lx = 0, lw = 0"
    );
}

#[test]
fn lx_lw_parse_and_validation() {
    let (_, p) = parse_variant("x").expect("база");
    assert_eq!((p.sig.lv.lx, p.sig.lv.lw), (0, 0));
    let (_, p) = parse_variant("x lx=2 lw=3 fh=4 fm=50 pk=1 pb=1").expect("разбор");
    assert_eq!((p.sig.lv.lx, p.sig.lv.lw), (2, 3));
    // fh/fm остаются в строке варианта и в SigCfg: для lx ≠ 0 их игнорирует `fresh_ok`, а не разбор
    assert!(p.sig.fresh_on() && p.sig.lv.pk && p.sig.pb);
    for ok in ["x lx=1 tf=60", "x lw=6", "x lx=3"] {
        assert!(parse_variant(ok).is_ok(), "{ok}");
    }
    for bad in ["x lx=4", "x lw=7", "x lx=1 tf=30", "x lx=3 tf=240"] {
        assert!(parse_variant(bad).is_err(), "{bad}");
    }
}

#[test]
fn lx_lw_change_the_signal_cache_key() {
    let base = Params::base().sig;
    let k0 = cfg_key_text(&base);
    let mut d = base.clone();
    d.lv.lx = 1;
    let mut w = base.clone();
    w.lv.lx = 2;
    let mut w3 = w.clone();
    w3.lv.lw = 3;
    let keys = [k0, cfg_key_text(&d), cfg_key_text(&w), cfg_key_text(&w3)];
    let distinct: std::collections::HashSet<&String> = keys.iter().collect();
    assert_eq!(
        distinct.len(),
        keys.len(),
        "lx и lw входят в ключ: {keys:?}"
    );
}

fn plain_level(src: u8) -> Level {
    Level {
        born: 1000,
        end: u32::MAX,
        segs: vec![(1000, px(101.0))],
        first_bar: 10,
        mem: Vec::new(),
        src,
    }
}

#[test]
fn lx_fresh_ok_is_bypassed_for_source_levels() {
    // плоский мир 100,0, уровень 101,0: касаний нет — «свежий уровень» (fh = 5 мин) пивотный уровень отвергает
    let s = sym(flat(5000, 100.0));
    let mut cfg = Params::base().sig;
    cfg.fr_h_min = 5;
    assert!(cfg.fresh_on());
    let lp = px(101.0);
    assert!(
        !fresh_ok(&s, &cfg, &plain_level(0), 1, lp, 3000),
        "пивотный уровень отвергнут"
    );
    for src in 1..=3 {
        assert!(
            fresh_ok(&s, &cfg, &plain_level(src), 1, lp, 3000),
            "lx = {src}: допущен"
        );
    }
    let mut cfg_b = Params::base().sig;
    cfg_b.fr_share = 50;
    assert!(!fresh_ok(&s, &cfg_b, &plain_level(0), 1, lp, 3000));
    assert!(
        fresh_ok(&s, &cfg_b, &plain_level(1), 1, lp, 3000),
        "(б) тоже выключена для lx"
    );
}

/// Мир кандидатов: сутки 19–20 (после прогрева), цена 99,5, диапазон минутки 0,6 % (NATR ≥ 0,4 %), оборот часа 600 тыс.; вчерашний максимум суток 19 — 100,0.
fn cand_world() -> Cols {
    let mut c = Cols::new();
    for m in 19 * 1440..21 * 1440 {
        set(&mut c, m, 99.5, 99.7985, 99.2015, 99.5);
        c.qv[m] = 1_000_000_000;
    }
    c.h[19 * 1440 + 300] = px(100.0);
    c
}

#[test]
fn lx_candidates_ignore_fresh_rule_and_far_edge_is_the_level() {
    let s = sym(cand_world());
    let mut cfg = Params::base().sig;
    cfg.lv.lx = 1;
    let lv = build_levels(&s, &cfg.lv, Side::Long);
    assert_eq!(
        lv.len(),
        2,
        "D суток 20 (из суток 19) и суток 21 (из суток 20)"
    );
    assert_eq!(
        (lv[0].born as usize, lv[0].segs[0].1),
        (20 * 1440, px(100.0))
    );
    assert!(lv[0].born as usize >= START_MIN);
    let count = |cfg: &super::params::SigCfg, l: &Level| {
        let mut out = Vec::new();
        candidates(&s, std::slice::from_ref(l), cfg, Side::Long, &mut out);
        out
    };
    let off = count(&cfg, &lv[0]);
    assert_eq!(
        off.len(),
        1440,
        "без «свежего уровня» — кандидат в каждой минуте суток"
    );
    assert!(
        off.iter().all(|c| c.far == c.lp),
        "журнала членов нет: far = lp"
    );
    cfg.fr_h_min = 5;
    let on = count(&cfg, &lv[0]);
    assert_eq!(on.len(), 1440, "fh = 5 мин на уровень lx не действует");
    let mut piv = lv[0].clone();
    piv.src = 0;
    assert_eq!(
        count(&cfg, &piv).len(),
        0,
        "тот же уровень как пивотный fh отвергает: касаний нет"
    );
}

#[test]
fn lx_levels_dump_has_the_same_columns() {
    let mut c = Cols::new();
    day(&mut c, 0, 100.0, 99.0, 99.5);
    hour(&mut c, 7, 105.0, 99.0, 99.5);
    day(&mut c, 1, 101.0, 98.0, 99.5);
    let s = sym(c);
    let mut p = Params::base();
    p.sig.lv.lx = 1;
    let tmp = tempfile::tempdir().expect("tmp");
    write_levels(tmp.path(), std::slice::from_ref(&s), Side::Long, &p).expect("levels");
    let t = std::fs::read_to_string(tmp.path().join("long-levels.csv")).expect("файл");
    let mut it = t.lines();
    assert_eq!(it.next(), Some("sym,level,born,end,first_bar,segs,members"));
    let f: Vec<&str> = it.next().expect("строка").split(',').collect();
    assert_eq!(
        f,
        ["TEST", "0", "1440", "2880", "7", "1440:10500000000", ""]
    );
    assert_eq!(it.count(), 1, "второй уровень — суток 2");
    // сигналы по уровню lx: дамп с членами не падает, столбец членов пуст
    let sg = TSig {
        ms: 1500 * 60_000,
        sym: 0,
        level: 0,
        dist: 123,
        t: 1500,
        day: 1,
        idx: 0,
        lp: px(105.0),
        far: px(105.0),
        ws: false,
    };
    write_sigsx(
        tmp.path(),
        std::slice::from_ref(&s),
        Side::Long,
        &p,
        &[sg],
        (0, 100),
    )
    .expect("sigsx");
    let t = std::fs::read_to_string(tmp.path().join("long-sigsx.csv")).expect("файл");
    let row: Vec<&str> = t.lines().nth(1).expect("строка").split(',').collect();
    assert_eq!(row.len(), 10);
    assert_eq!(
        (row[0], row[3], row[4], row[5], row[6], row[7], row[9]),
        ("TEST", "123", "10500000000", "1440", "2880", "7", "")
    );
}
