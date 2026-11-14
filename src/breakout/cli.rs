//! `alpha breakout convert|run|hand` (П-42). `run` читает кэш `.b42`, считает варианты параллельно (общие
//! уровни и сигналы — один раз на ключ), пишет на вариант: `<out>/<id>/<side>-trades.csv`, `<side>-daily.csv`,
//! `meta.json`; общий `<out>/summary.csv`.

use super::data::{self, Sym, HALF_DAYS, START_DAY};
use super::levels::{build_levels, Level};
use super::params::{parse_variant, LevelCfg, Params, SigCfg};
use super::portfolio::{self, settle, PortResult};
use super::signals::{scan, sort_sigs, Sig, SigCtr};
use super::trade::simulate;
use super::{par_map, Side};
use anyhow::{bail, Context, Result};
use clap::Subcommand;
use std::collections::HashMap;
use std::fmt::Write as _;
use std::fs;
use std::hash::Hash;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

#[derive(Debug, Subcommand)]
pub enum BreakoutCommand {
    /// Каталог `<SYM>/<день>.csv` + `funding.csv` → кэш `<SYM>.b42` (один раз; перезапуск — когда дозакачан фандинг).
    Convert {
        #[arg(long)]
        src: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 7)]
        threads: usize,
    },
    /// Варианты из файла (`<id> ключ=значение …` на строку) → сделки, суточный P&L, счётчики.
    Run {
        #[arg(long)]
        cache: PathBuf,
        #[arg(long)]
        variants: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 7)]
        threads: usize,
        /// Список символов через запятую (проверки на малом); по умолчанию — все.
        #[arg(long)]
        symbols: Option<String>,
    },
    /// Одна сделка базы по монете со всеми шагами и арифметикой P&L.
    Hand {
        #[arg(long)]
        cache: PathBuf,
        #[arg(long)]
        sym: String,
        #[arg(long)]
        side: String,
        #[arg(long, default_value_t = 0)]
        nth: usize,
        /// Переопределения `ключ=значение`.
        #[arg(long = "set")]
        set: Vec<String>,
    },
}

pub fn dispatch(cmd: BreakoutCommand) -> Result<()> {
    match cmd {
        BreakoutCommand::Convert { src, out, threads } => {
            let t0 = Instant::now();
            let stats = data::convert(&src, &out, threads)?;
            let rows: usize = stats.iter().map(|s| s.rows).sum();
            let (hw, ha, hb): (usize, usize, usize) = stats.iter().fold((0, 0, 0), |a, s| {
                (a.0 + s.holes_warm, a.1 + s.holes_a, a.2 + s.holes_b)
            });
            let nf: usize = stats.iter().map(|s| s.funding_rows).sum();
            let nofund = stats.iter().filter(|s| s.funding_rows == 0).count();
            println!(
                "convert: символов {}, строк {rows}, дыр внутри листинга: прогрев {hw}, A {ha}, B {hb}; фандинг строк {nf}, без фандинга монет {nofund}; {:.1} с",
                stats.len(),
                t0.elapsed().as_secs_f64()
            );
            Ok(())
        }
        BreakoutCommand::Run {
            cache,
            variants,
            out,
            threads,
            symbols,
        } => run(&cache, &variants, &out, threads, symbols),
        BreakoutCommand::Hand {
            cache,
            sym,
            side,
            nth,
            set,
        } => hand(&cache, &sym, &side, nth, &set),
    }
}

type Cache<K, V> = Mutex<HashMap<K, Arc<OnceLock<Arc<V>>>>>;

fn cached<K: Hash + Eq + Clone, V>(c: &Cache<K, V>, k: &K, f: impl FnOnce() -> V) -> Arc<V> {
    let cell = {
        let mut g = match c.lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
        g.entry(k.clone()).or_default().clone()
    };
    cell.get_or_init(|| Arc::new(f())).clone()
}

type Levels = Vec<Vec<Level>>;
type SigSet = (Vec<Sig>, SigCtr);

fn get_levels(
    c: &Cache<(LevelCfg, Side), Levels>,
    syms: &[Sym],
    lv: &LevelCfg,
    side: Side,
    inner: usize,
) -> Arc<Levels> {
    cached(c, &(lv.clone(), side), || {
        let idx: Vec<usize> = (0..syms.len()).collect();
        par_map(&idx, inner, |&i| build_levels(&syms[i], lv, side))
    })
}

fn get_sigs(
    lc: &Cache<(LevelCfg, Side), Levels>,
    sc: &Cache<(SigCfg, Side), SigSet>,
    syms: &[Sym],
    cfg: &SigCfg,
    side: Side,
    inner: usize,
) -> Arc<SigSet> {
    cached(sc, &(cfg.clone(), side), || {
        let levels = get_levels(lc, syms, &cfg.lv, side, inner);
        let idx: Vec<usize> = (0..syms.len()).collect();
        let parts = par_map(&idx, inner, |&i| {
            let mut v = Vec::new();
            let mut c = SigCtr::default();
            scan(&syms[i], i as u16, &levels[i], cfg, side, &mut v, &mut c);
            (v, c)
        });
        let mut all = Vec::new();
        let mut ctr = SigCtr::default();
        for (v, c) in parts {
            all.extend(v);
            ctr.add(&c);
        }
        sort_sigs(&mut all);
        (all, ctr)
    })
}

fn write_outputs(dir: &std::path::Path, syms: &[Sym], side: Side, r: &PortResult) -> Result<()> {
    let mut t = String::from("sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day\n");
    for x in &r.trades {
        let _ = writeln!(
            t,
            "{},{},{},{},{},{},{},{},{:.6},{:.6},{:.6},{:.6},{}",
            syms[x.sym as usize].name,
            x.level,
            x.sig_min,
            x.entry_min,
            x.exit_min,
            x.entry_px,
            x.kind.name(),
            x.takes,
            x.gross,
            x.fees,
            x.funding,
            x.net,
            x.entry_min as usize / 1440 - START_DAY
        );
    }
    fs::write(dir.join(format!("{}-trades.csv", side.name())), t)?;
    let mut d = String::from("day,pnl\n");
    for (i, v) in r.daily.iter().enumerate() {
        let _ = writeln!(d, "{i},{v:.6}");
    }
    fs::write(dir.join(format!("{}-daily.csv", side.name())), d)?;
    let mut cd = String::from(
        "sym,day,pnl
",
    );
    for ((si, day), v) in &r.coinday {
        let _ = writeln!(cd, "{},{day},{v:.6}", syms[*si as usize].name);
    }
    fs::write(dir.join(format!("{}-coinday.csv", side.name())), cd)?;
    Ok(())
}

fn mean(v: &[f64]) -> f64 {
    if v.is_empty() {
        0.0
    } else {
        v.iter().sum::<f64>() / v.len() as f64
    }
}

fn run(
    cache: &Path,
    variants: &Path,
    out: &Path,
    threads: usize,
    symbols: Option<String>,
) -> Result<()> {
    let t0 = Instant::now();
    let only: Option<Vec<String>> = symbols.map(|s| s.split(',').map(str::to_string).collect());
    let syms = data::load_all(cache, threads, only.as_deref())?;
    let nfund = syms.iter().filter(|s| s.fund.is_empty()).count();
    eprintln!(
        "загружено символов {} за {:.1} с; без фандинга: {nfund}",
        syms.len(),
        t0.elapsed().as_secs_f64()
    );
    let text = fs::read_to_string(variants).with_context(|| variants.display().to_string())?;
    let mut vs: Vec<(String, Params)> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        vs.push(parse_variant(line).with_context(|| line.to_string())?);
    }
    if vs.is_empty() {
        bail!("нет вариантов");
    }
    fs::create_dir_all(out)?;
    let lc: Cache<(LevelCfg, Side), Levels> = Mutex::new(HashMap::new());
    let sc: Cache<(SigCfg, Side), SigSet> = Mutex::new(HashMap::new());
    let outer = threads.min(vs.len()).max(1);
    let inner = (threads / outer).max(1);
    let done = std::sync::atomic::AtomicUsize::new(0);
    let total = vs.len();
    let rows = par_map(&vs, outer, |(id, p)| -> Result<Vec<String>> {
        let dir = out.join(id);
        fs::create_dir_all(&dir)?;
        let mut rows = Vec::new();
        let mut meta = serde_json::Map::new();
        meta.insert("id".into(), id.clone().into());
        meta.insert("params".into(), format!("{p:?}").into());
        for side in [Side::Long, Side::Short] {
            let sg = get_sigs(&lc, &sc, &syms, &p.sig, side, inner);
            let r = portfolio::run(&syms, &sg.0, side, &p.exit, &p.port);
            write_outputs(&dir, &syms, side, &r)?;
            let n = r.trades.len();
            let stops = r
                .trades
                .iter()
                .filter(|x| x.kind == super::trade::ExitKind::HardStop)
                .count();
            let total_net: f64 = r.daily.iter().sum();
            let ma = mean(&r.daily[..HALF_DAYS]);
            let mb = mean(&r.daily[HALF_DAYS..]);
            rows.push(format!(
                "{id},{},{n},{:.3},{:.4},{:.4},{:.4}",
                side.name(),
                total_net,
                ma,
                mb,
                if n > 0 { stops as f64 / n as f64 } else { 0.0 }
            ));
            meta.insert(
                side.name().into(),
                serde_json::json!({
                    "trades": n, "hard_stops": stops, "net_total": total_net,
                    "rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]": r.rej,
                    "signals_AB": sg.1.signals, "levels_total": sg.1.levels_total,
                    "levels_crossed_AB": sg.1.levels_crossed, "levels_skipped_AB": sg.1.levels_skipped,
                    "levels_skipped_geo_AB": sg.1.levels_skipped_geo,
                }),
            );
        }
        fs::write(dir.join("meta.json"), serde_json::to_string_pretty(&meta)?)?;
        let k = done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
        eprintln!("[{k}/{total}] {id}  {:.0} с", t0.elapsed().as_secs_f64());
        Ok(rows)
    });
    let mut summary =
        String::from("variant,side,trades,net_total,mean_day_A,mean_day_B,stop_share\n");
    for r in rows {
        for line in r? {
            summary.push_str(&line);
            summary.push('\n');
        }
    }
    fs::write(out.join("summary.csv"), summary)?;
    eprintln!(
        "готово: {total} вариантов, {:.1} с",
        t0.elapsed().as_secs_f64()
    );
    Ok(())
}

fn hand(cache: &Path, sym: &str, side: &str, nth: usize, set: &[String]) -> Result<()> {
    let side = match side {
        "long" => Side::Long,
        "short" => Side::Short,
        _ => bail!("side: long|short"),
    };
    let syms = data::load_all(cache, 1, Some(&[sym.to_string()]))?;
    let Some(s) = syms.first() else {
        bail!("нет символа {sym}");
    };
    let mut p = Params::base();
    for kv in set {
        let Some((k, v)) = kv.split_once('=') else {
            bail!("ожидалось ключ=значение: {kv}");
        };
        p.set(k, v)?;
    }
    p.validate()?;
    let levels = build_levels(s, &p.sig.lv, side);
    let mut sigs = Vec::new();
    let mut ctr = SigCtr::default();
    scan(s, 0, &levels, &p.sig, side, &mut sigs, &mut ctr);
    sort_sigs(&mut sigs);
    println!(
        "{sym} {}: уровней {}, сигналов {}",
        side.name(),
        levels.len(),
        sigs.len()
    );
    let r = portfolio::run(&syms, &sigs, side, &p.exit, &p.port);
    let Some(tr) = r.trades.get(nth) else {
        bail!("сделок {} — нет №{nth}", r.trades.len());
    };
    let lv = &levels[tr.level as usize];
    println!(
        "сделка №{nth}: сигнал t={}, уровень #{} (рождён m={}, версии цены {:?}), расстояние до уровня по сигналу — см. строку входа",
        tr.sig_min, tr.level, lv.born, lv.segs
    );
    let natr = s
        .natr_for(p.exit.natr_p)
        .map_or(0, |v| i64::from(v[tr.sig_min as usize]));
    let mut trace = Vec::new();
    let out = simulate(
        s,
        side,
        tr.sig_min as usize,
        natr,
        &p.exit,
        Some(&mut trace),
    );
    for line in &trace {
        println!("{line}");
    }
    let Some(out) = out else {
        bail!("исход не получен")
    };
    let st = settle(s, side, &out);
    println!("— арифметика P&L ($500, комиссии 3,5/1,4 bps −10 %):");
    for leg in &out.legs {
        println!(
            "  нога m={} цена={} доля={:.6}$ {}",
            leg.min,
            leg.px,
            leg.u as f64 / 1e6,
            if leg.maker {
                "мейкер"
            } else {
                "тейкер"
            }
        );
    }
    println!(
        "  валовая {:.6}$, комиссии {:.6}$, фандинг {:.6}$, чистая {:.6}$",
        st.gross,
        st.fees,
        st.funding,
        st.gross - st.fees + st.funding
    );
    println!("  суточные приращения (день с 15.09, $):");
    for (d, v) in &st.daily {
        println!("    день {d}: {v:.6}");
    }
    let sum: f64 = st.daily.iter().map(|x| x.1).sum();
    println!("  сумма по суткам {sum:.6}$ (должна совпасть с чистой)");
    Ok(())
}
