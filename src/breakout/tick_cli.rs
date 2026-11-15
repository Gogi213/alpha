//! `alpha breakout tick` (П-42 шаг B, §13.2): варианты из файла → сделки по Binance aggTrades. Файлы результата — как
//! у `breakout run` (`<out>/<id>/<side>-{trades,daily,coinday}.csv`, `meta.json`, `<out>/summary.csv`); в
//! `trades.csv` в конце добавлены `sig_ms,entry_ms,exit_ms` (мс от 01.09 00:00 UTC).

use super::data::{self, Sym, HALF_DAYS, MIN_MS, START_DAY};
use super::levels::build_levels_tracked;
use super::params::{parse_variant, Params, SigCfg};
use super::tick::{
    run_tick, signals_for_coin, sort_tsigs, TSig, TickCtr, TickResult, ZipSrc, DAY_MS,
};
use super::{par_map, Side};
use anyhow::{bail, Context, Result};
use std::collections::HashMap;
use std::fmt::Write as _;
use std::fs;
use std::path::Path;
use std::time::Instant;

fn mean(v: &[f64]) -> f64 {
    if v.is_empty() {
        0.0
    } else {
        v.iter().sum::<f64>() / v.len() as f64
    }
}

fn write_outputs(dir: &Path, syms: &[Sym], side: Side, r: &TickResult) -> Result<()> {
    let mut t = String::from(
        "sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day,sig_ms,entry_ms,exit_ms\n",
    );
    for x in &r.trades {
        let _ = writeln!(
            t,
            "{},{},{},{},{},{},{},{},{:.6},{:.6},{:.6},{:.6},{},{},{},{}",
            syms[x.sym as usize].name,
            x.level,
            x.sig_min,
            x.entry_ms / MIN_MS,
            x.exit_ms / MIN_MS,
            x.entry_px,
            x.kind.name(),
            x.takes,
            x.gross,
            x.fees,
            x.funding,
            x.net,
            x.entry_ms / DAY_MS - START_DAY as i64,
            x.sig_ms,
            x.entry_ms,
            x.exit_ms
        );
    }
    fs::write(dir.join(format!("{}-trades.csv", side.name())), t)?;
    let mut d = String::from("day,pnl\n");
    for (i, v) in r.daily.iter().enumerate() {
        let _ = writeln!(d, "{i},{v:.6}");
    }
    fs::write(dir.join(format!("{}-daily.csv", side.name())), d)?;
    let mut cd = String::from("sym,day,pnl\n");
    for ((si, day), v) in &r.coinday {
        let _ = writeln!(cd, "{},{day},{v:.6}", syms[*si as usize].name);
    }
    fs::write(dir.join(format!("{}-coinday.csv", side.name())), cd)?;
    Ok(())
}

/// `P42_DETAIL=1`: построчно (JSONL) всё, что нужно для графика сделки — уровень на сигнале с членами, ноги, NATR.
fn write_detail(dir: &Path, syms: &[Sym], side: Side, p: &Params, r: &TickResult) -> Result<()> {
    let mut cache: HashMap<u16, Vec<super::levels::Level>> = HashMap::new();
    let mut t = String::new();
    for (i, x) in r.trades.iter().enumerate() {
        let sym = &syms[x.sym as usize];
        let levels = cache
            .entry(x.sym)
            .or_insert_with(|| build_levels_tracked(sym, &p.sig.lv, side, true));
        let lv = &levels[x.level as usize];
        let si = lv
            .segs
            .iter()
            .rposition(|(m, _)| *m <= x.sig_min)
            .unwrap_or(0);
        let mut rec = serde_json::json!({
            "i": i, "sym": sym.name, "side": side.name(), "level": x.level,
            "born": lv.born, "end": lv.end, "first_bar": lv.first_bar,
            "level_px_e8": lv.segs[si].1, "members": lv.mem[si],
            "sig_ms": x.sig_ms, "sig_min": x.sig_min, "dist_e8": x.dist,
            "entry_ms": x.entry_ms, "entry_px_e8": x.entry_px, "exit_ms": x.exit_ms,
            "kind": x.kind.name(), "takes": x.takes, "natr_e8": x.natr_e8,
            "legs": x.legs.iter().map(|l| serde_json::json!([l.ms, l.px, l.u, l.maker])).collect::<Vec<_>>(),
            "gross": x.gross, "fees": x.fees, "funding": x.funding, "net": x.net,
        });
        if p.sig.ap != 0 {
            rec["ws"] = serde_json::Value::Bool(x.ws);
        }
        t.push_str(&rec.to_string());
        t.push('\n');
    }
    fs::write(dir.join(format!("{}-detail.jsonl", side.name())), t)?;
    Ok(())
}

pub fn run(
    cache: &Path,
    trades: &Path,
    variants: &Path,
    out: &Path,
    threads: usize,
    symbols: Option<String>,
    cap_gb: usize,
) -> Result<()> {
    let t0 = Instant::now();
    let only: Option<Vec<String>> = symbols.map(|s| s.split(',').map(str::to_string).collect());
    let syms = data::load_all(cache, threads, only.as_deref())?;
    eprintln!(
        "загружено символов {} за {:.1} с",
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
    // задания фазы 1: различные (SigCfg, сторона)
    let mut job_idx: HashMap<(SigCfg, Side), usize> = HashMap::new();
    let mut jobs: Vec<(SigCfg, Side)> = Vec::new();
    for (_, p) in &vs {
        for side in [Side::Long, Side::Short] {
            let k = (p.sig.clone(), side);
            if !job_idx.contains_key(&k) {
                job_idx.insert(k.clone(), jobs.len());
                jobs.push(k);
            }
        }
    }
    let src = ZipSrc::new(trades.to_path_buf(), cap_gb << 30);
    eprintln!(
        "фаза 1: монет {}, заданий сигналов {}",
        syms.len(),
        jobs.len()
    );
    let idx: Vec<usize> = (0..syms.len()).collect();
    let parts = par_map(&idx, threads, |&i| {
        let mut outs: Vec<Vec<TSig>> = vec![Vec::new(); jobs.len()];
        let mut ctrs: Vec<TickCtr> = vec![TickCtr::default(); jobs.len()];
        signals_for_coin(&syms[i], i as u16, &jobs, &src, &mut outs, &mut ctrs);
        outs.into_iter().zip(ctrs).collect::<Vec<_>>()
    });
    let mut sigs: Vec<Vec<TSig>> = vec![Vec::new(); jobs.len()];
    let mut ctr: Vec<TickCtr> = vec![TickCtr::default(); jobs.len()];
    for part in parts {
        for (j, (v, c)) in part.into_iter().enumerate() {
            sigs[j].extend(v);
            ctr[j].add(&c);
        }
    }
    for s in &mut sigs {
        sort_tsigs(s);
    }
    if vs.iter().any(|(_, p)| p.sig.lv.pk) {
        use std::sync::atomic::Ordering::Relaxed;
        eprintln!(
            "pk (§13.11): не родилось из-за предыстории {}, убито перепроверкой при смене версии {}, убито минутным проколом {}",
            super::levels::PK_BIRTH_BLOCKED.load(Relaxed),
            super::levels::PK_RETRO_KILLED.load(Relaxed),
            super::tick::PK_MINUTE_KILLED.load(Relaxed)
        );
    }
    if vs.iter().any(|(_, p)| p.sig.pb) {
        eprintln!(
            "pb (§13.13): уровней убито сделкой на уровне или за ним {}",
            super::tick::PB_KILLED.load(std::sync::atomic::Ordering::Relaxed)
        );
    }
    if vs.iter().any(|(_, p)| p.sig.fresh_on()) {
        eprintln!(
            "свежий уровень (§13.14): кандидатов отвергнуто {} (по всем заданиям сигналов)",
            super::tick::FR_REJECTED.load(std::sync::atomic::Ordering::Relaxed)
        );
    }
    let ls = src.stat.lock().map(|g| g.clone()).unwrap_or_default();
    eprintln!(
        "фаза 1 готова за {:.1} с; файлов {}, строк {}, не по порядку {}, вне суток {}, плохих строк {}, ошибок {}",
        t0.elapsed().as_secs_f64(),
        ls.files,
        ls.rows,
        ls.unsorted_files,
        ls.out_of_day,
        ls.bad_rows,
        ls.errors
    );
    // фаза 2: портфель и исходы — задания (вариант, сторона) параллельно
    let tasks: Vec<(usize, Side)> = (0..vs.len())
        .flat_map(|i| [(i, Side::Long), (i, Side::Short)])
        .collect();
    let done = std::sync::atomic::AtomicUsize::new(0);
    let total = tasks.len();
    let parts = par_map(
        &tasks,
        threads,
        |&(vi, side)| -> Result<(usize, Side, String, serde_json::Value)> {
            let (id, p) = &vs[vi];
            let dir = out.join(id);
            fs::create_dir_all(&dir)?;
            let Some(&j) = job_idx.get(&(p.sig.clone(), side)) else {
                bail!("{id}: нет задания сигналов");
            };
            let r = run_tick(&syms, &sigs[j], side, &p.exit, &p.port, &src);
            write_outputs(&dir, &syms, side, &r)?;
            if std::env::var_os("P42_DETAIL").is_some() {
                write_detail(&dir, &syms, side, p, &r)?;
            }
            let n = r.trades.len();
            let stops = r
                .trades
                .iter()
                .filter(|x| x.kind == super::trade::ExitKind::HardStop)
                .count();
            let total_net: f64 = r.daily.iter().sum();
            let ma = mean(&r.daily[..HALF_DAYS]);
            let mb = mean(&r.daily[HALF_DAYS..]);
            let row = format!(
                "{id},{},{n},{:.3},{:.4},{:.4},{:.4}",
                side.name(),
                total_net,
                ma,
                mb,
                if n > 0 { stops as f64 / n as f64 } else { 0.0 }
            );
            let mut meta = serde_json::json!({
                "trades": n, "hard_stops": stops, "net_total": total_net,
                "rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]": r.rej,
                "signals_AB": ctr[j].signals, "cand_minutes": ctr[j].cands,
                "missing_days": ctr[j].missing_days, "signals_total": sigs[j].len(),
            });
            if p.sig.ap != 0 {
                meta["ap_wrong_signals"] = ctr[j].ap_wrong.into();
            }
            let k = done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
            eprintln!(
                "[{k}/{total}] {id} {}  {:.0} с",
                side.name(),
                t0.elapsed().as_secs_f64()
            );
            Ok((vi, side, row, meta))
        },
    );
    let mut metas: Vec<serde_json::Map<String, serde_json::Value>> = vs
        .iter()
        .map(|(id, p)| {
            let mut m = serde_json::Map::new();
            m.insert("id".into(), id.clone().into());
            m.insert("params".into(), format!("{p:?}").into());
            m
        })
        .collect();
    let mut rows: Vec<String> = Vec::new();
    for part in parts {
        let (vi, side, row, meta) = part?;
        rows.push(row);
        metas[vi].insert(side.name().into(), meta);
    }
    for ((id, _), m) in vs.iter().zip(metas) {
        fs::write(
            out.join(id).join("meta.json"),
            serde_json::to_string_pretty(&m)?,
        )?;
    }
    let total = vs.len();
    let mut summary =
        String::from("variant,side,trades,net_total,mean_day_A,mean_day_B,stop_share\n");
    for line in rows {
        summary.push_str(&line);
        summary.push('\n');
    }
    fs::write(out.join("summary.csv"), summary)?;
    let ls = src.stat.lock().map(|g| g.clone()).unwrap_or_default();
    eprintln!(
        "готово: {total} вариантов, {:.1} с; файлов прочитано {}, ошибок загрузки {}",
        t0.elapsed().as_secs_f64(),
        ls.files,
        ls.errors
    );
    if ls.errors > 0 {
        bail!("ошибок загрузки: {}", ls.errors);
    }
    Ok(())
}
