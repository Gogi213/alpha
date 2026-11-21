//! TK-607 (L2): кэш сигналов фазы 1. Фаза 1 зависит только от `(SigCfg, сторона)`, сетки, набора монет, минуток `.b42` и сделок;
//! её выход — `Vec<TSig>` (после `sort_tsigs`) и `TickCtr`. Запись на задание — файл `<кэш>/<ключ>.sg`; при попадании фаза 1 по заданию не идёт.
//!
//! Ключ задания = sha256 от: базы запуска (версии формата, sha256 исполняемого файла, `unzip -v`, `t0_ms`, константы сетки, список монет,
//! отпечатки `.b42` и ВСЕХ суточных zip монет окна — размер, mtime или «нет файла»; считается заново на каждом запуске, поэтому замена
//! любого zip или `.b42` меняет ключи) + сторона + исчерпывающий текст `SigCfg`/`LevelCfg` (без `..`: новое поле ломает сборку). Аудит входов —
//! `docs/findings/p42-speed-2026-11-18/phase1-inputs.txt`. Задания с `ap != 0` не кэшируются (`came_from_above` читает прежние сутки).
//! Запись — только если фаза 1 прошла без ошибок загрузки суток (ошибка неотличима от «нет файла» и закрепила бы усечённый результат).

use super::params::{LevelCfg, SigCfg};
use super::store::{compress, free_bytes, STORE_VERSION};
use super::tick::{TSig, TickCtr, AP_LOOKBACK_DAYS, DAY_MS};
use super::Side;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// Версия формата записи и состава ключа.
pub const SIGCACHE_VERSION: u32 = 1;
const MAGIC: [u8; 4] = *b"SG42";
const HEAD_LEN: usize = 4 + 4 + 4 + 5 * 8 + 4 + 4;
const TSIG_LEN: usize = 8 + 2 + 4 + 4 + 4 + 2 + 4 + 8 + 8 + 1;

#[derive(Debug, Clone)]
pub struct SigCacheOpts {
    pub dir: PathBuf,
    pub min_free_gb: u64,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct SigCacheCounters {
    pub hit: u64,
    pub miss: u64,
    pub written: u64,
    pub skipped: u64,
    pub bad: u64,
}

pub struct SigCache {
    dir: PathBuf,
    base: [u8; 32],
    min_free_bytes: u64,
    read_only: AtomicBool,
    hit: AtomicU64,
    miss: AtomicU64,
    written: AtomicU64,
    skipped: AtomicU64,
    bad: AtomicU64,
}

fn fingerprint(h: &mut Sha256, path: &Path) {
    match fs::metadata(path)
        .ok()
        .and_then(|m| super::store::SrcMeta::of(&m))
    {
        Some(m) => h.update(format!("{}:{}:{};", m.size, m.mtime_s, m.mtime_ns)),
        None => h.update(b"absent;"),
    }
}

/// Исчерпывающий текст конфигурации сигнала: каждое поле названо явно, без `..` — добавленное в `SigCfg`/`LevelCfg` поле не соберётся, пока его не внесут в ключ.
pub fn cfg_key_text(cfg: &SigCfg) -> String {
    let SigCfg {
        lv,
        natr_p,
        natr_min_pm,
        t1h_u,
        t24_u,
        track_pm,
        e_pm,
        color,
        f_pm,
        r_min,
        ban,
        ap,
        pb,
        fr_h_min,
        fr_k,
        fr_share,
        fr_b_pm,
        fg_m,
    } = cfg;
    let LevelCfg {
        tf_min,
        k,
        d_pm,
        n,
        tau_pm,
        l_days,
        pk,
        lx,
        lw,
    } = lv;
    // TK-669: `fg` — в тексте только при ≠ 0 (текст прежних конфигураций не меняется)
    let fg = if *fg_m != 0 {
        format!(",fg_m={fg_m}")
    } else {
        String::new()
    };
    format!(
        "lv[tf_min={tf_min},k={k},d_pm={d_pm},n={n},tau_pm={tau_pm},l_days={l_days},pk={pk},lx={lx},lw={lw}],natr_p={natr_p},natr_min_pm={natr_min_pm},t1h_u={t1h_u},t24_u={t24_u},\
         track_pm={track_pm},e_pm={e_pm},color={color},f_pm={f_pm},r_min={r_min},ban={ban},ap={ap},pb={pb},fr_h_min={fr_h_min},fr_k={fr_k},fr_share={fr_share},fr_b_pm={fr_b_pm}{fg}"
    )
}

fn put_ctr(h: &mut Vec<u8>, c: &TickCtr) {
    for v in [
        c.cands,
        c.signals[0],
        c.signals[1],
        c.missing_days,
        c.ap_wrong,
    ] {
        h.extend_from_slice(&v.to_le_bytes());
    }
}

fn encode(sigs: &[TSig], ctr: &TickCtr) -> Option<Vec<u8>> {
    let mut raw = Vec::with_capacity(sigs.len() * TSIG_LEN);
    for s in sigs {
        raw.extend_from_slice(&s.ms.to_le_bytes());
        raw.extend_from_slice(&s.sym.to_le_bytes());
        raw.extend_from_slice(&s.level.to_le_bytes());
        raw.extend_from_slice(&s.dist.to_le_bytes());
        raw.extend_from_slice(&s.t.to_le_bytes());
        raw.extend_from_slice(&s.day.to_le_bytes());
        raw.extend_from_slice(&s.idx.to_le_bytes());
        raw.extend_from_slice(&s.lp.to_le_bytes());
        raw.extend_from_slice(&s.far.to_le_bytes());
        raw.push(u8::from(s.ws));
    }
    let comp = compress(&raw)?;
    let mut f = Vec::with_capacity(HEAD_LEN + comp.len());
    f.extend_from_slice(&MAGIC);
    f.extend_from_slice(&SIGCACHE_VERSION.to_le_bytes());
    f.extend_from_slice(&u32::try_from(sigs.len()).ok()?.to_le_bytes());
    put_ctr(&mut f, ctr);
    f.extend_from_slice(&u32::try_from(raw.len()).ok()?.to_le_bytes());
    f.extend_from_slice(&u32::try_from(comp.len()).ok()?.to_le_bytes());
    f.extend_from_slice(&comp);
    Some(f)
}

fn decode(buf: &[u8]) -> Option<(Vec<TSig>, TickCtr)> {
    if buf.len() < HEAD_LEN || buf[..4] != MAGIC {
        return None;
    }
    let u32_at = |o: usize| u32::from_le_bytes([buf[o], buf[o + 1], buf[o + 2], buf[o + 3]]);
    let u64_at = |o: usize| {
        let mut a = [0u8; 8];
        a.copy_from_slice(&buf[o..o + 8]);
        u64::from_le_bytes(a)
    };
    if u32_at(4) != SIGCACHE_VERSION {
        return None;
    }
    let n = u32_at(8) as usize;
    let ctr = TickCtr {
        cands: u64_at(12),
        signals: [u64_at(20), u64_at(28)],
        missing_days: u64_at(36),
        ap_wrong: u64_at(44),
        fg: [0; 4],
    };
    let (raw_len, comp_len) = (u32_at(52) as usize, u32_at(56) as usize);
    if buf.len() != HEAD_LEN + comp_len || raw_len != n * TSIG_LEN {
        return None;
    }
    let raw = zstd::bulk::decompress(&buf[HEAD_LEN..], raw_len).ok()?;
    if raw.len() != raw_len {
        return None;
    }
    let v = raw
        .chunks_exact(TSIG_LEN)
        .map(|b| {
            let i64_at = |o: usize| {
                let mut a = [0u8; 8];
                a.copy_from_slice(&b[o..o + 8]);
                i64::from_le_bytes(a)
            };
            let u32_b = |o: usize| u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]]);
            TSig {
                ms: i64_at(0),
                sym: u16::from_le_bytes([b[8], b[9]]),
                level: u32_b(10),
                dist: u32_b(14),
                t: u32_b(18),
                day: u16::from_le_bytes([b[22], b[23]]),
                idx: u32_b(24),
                lp: i64_at(28),
                far: i64_at(36),
                ws: b[44] != 0,
            }
        })
        .collect();
    Some((v, ctr))
}

impl SigCache {
    /// `cache` — каталог `.b42`, `trades` — каталог суток `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip`, `names` — монеты запуска по порядку.
    pub fn open(
        o: &SigCacheOpts,
        cache: &Path,
        trades: &Path,
        names: &[&str],
    ) -> std::io::Result<SigCache> {
        fs::create_dir_all(&o.dir)?;
        let mut h = Sha256::new();
        h.update(format!(
            "tk607-sigcache v{SIGCACHE_VERSION} store v{STORE_VERSION}\n"
        ));
        let exe = std::env::current_exe().and_then(fs::read)?;
        h.update(Sha256::digest(&exe));
        let unzip = Command::new("unzip")
            .arg("-v")
            .output()
            .map(|x| x.stdout)
            .unwrap_or_default();
        h.update(unzip.split(|&c| c == b'\n').next().unwrap_or_default());
        h.update(format!(
            "\nt0={} start_day={} start_min={} count={} half={} n_min={} days={} scale={} natr={:?} tf={:?} day_ms={DAY_MS} ap_back={AP_LOOKBACK_DAYS}\n",
            super::data::t0_ms(),
            super::data::START_DAY,
            super::data::START_MIN,
            super::data::COUNT_DAYS,
            super::data::HALF_DAYS,
            super::data::N_MIN,
            super::data::DAYS_TOTAL,
            super::data::SCALE,
            super::data::NATR_PERIODS,
            super::data::TF_MINUTES
        ));
        for name in names {
            h.update(format!("{name}:"));
            fingerprint(&mut h, &cache.join(format!("{name}.b42")));
            for day in 0..super::data::DAYS_TOTAL {
                let z = trades.join(name).join(format!(
                    "{name}-aggTrades-{}.zip",
                    super::data::day_string(day)
                ));
                fingerprint(&mut h, &z);
            }
            h.update(b"\n");
        }
        Ok(SigCache {
            dir: o.dir.clone(),
            base: h.finalize().into(),
            min_free_bytes: o.min_free_gb << 30,
            read_only: AtomicBool::new(false),
            hit: AtomicU64::new(0),
            miss: AtomicU64::new(0),
            written: AtomicU64::new(0),
            skipped: AtomicU64::new(0),
            bad: AtomicU64::new(0),
        })
    }

    /// Ключ задания; `None` — задание не кэшируется (`ap != 0`).
    pub fn key(&self, cfg: &SigCfg, side: Side) -> Option<String> {
        // `ap` и `fg` (TK-669: счётчики приостановок в записи не хранятся) в кэш сигналов не идут
        if cfg.ap != 0 || cfg.fg_m != 0 {
            return None;
        }
        let mut h = Sha256::new();
        h.update(self.base);
        h.update(format!("{}|{}", side.name(), cfg_key_text(cfg)));
        Some(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
    }

    fn path(&self, key: &str) -> PathBuf {
        self.dir.join(format!("{key}.sg"))
    }

    pub fn get(&self, key: &str) -> Option<(Vec<TSig>, TickCtr)> {
        let Ok(buf) = fs::read(self.path(key)) else {
            self.miss.fetch_add(1, Ordering::Relaxed);
            return None;
        };
        match decode(&buf) {
            Some(x) => {
                self.hit.fetch_add(1, Ordering::Relaxed);
                Some(x)
            }
            None => {
                self.bad.fetch_add(1, Ordering::Relaxed);
                self.miss.fetch_add(1, Ordering::Relaxed);
                eprintln!("кэш сигналов: повреждён {key}.sg");
                None
            }
        }
    }

    pub fn put(&self, key: &str, sigs: &[TSig], ctr: &TickCtr) {
        if !self.read_only.load(Ordering::Relaxed)
            && free_bytes(&self.dir).is_some_and(|f| f < self.min_free_bytes)
        {
            self.read_only.store(true, Ordering::Relaxed);
        }
        let Some(file) = (!self.read_only.load(Ordering::Relaxed))
            .then(|| encode(sigs, ctr))
            .flatten()
        else {
            self.skipped.fetch_add(1, Ordering::Relaxed);
            return;
        };
        let p = self.path(key);
        let tmp = p.with_extension(format!("tmp.{}", std::process::id()));
        if fs::write(&tmp, &file)
            .and_then(|()| fs::rename(&tmp, &p))
            .is_ok()
        {
            self.written.fetch_add(1, Ordering::Relaxed);
        } else {
            let _ = fs::remove_file(&tmp);
            self.skipped.fetch_add(1, Ordering::Relaxed);
        }
    }

    pub fn counters(&self) -> SigCacheCounters {
        SigCacheCounters {
            hit: self.hit.load(Ordering::Relaxed),
            miss: self.miss.load(Ordering::Relaxed),
            written: self.written.load(Ordering::Relaxed),
            skipped: self.skipped.load(Ordering::Relaxed),
            bad: self.bad.load(Ordering::Relaxed),
        }
    }

    pub fn summary(&self) -> String {
        let c = self.counters();
        format!(
            "sig-cache: hit {} miss {} written {} skipped {} bad {}",
            c.hit, c.miss, c.written, c.skipped, c.bad
        )
    }
}

/// Тест: кодек записи без файловой системы.
#[cfg(test)]
pub(super) fn roundtrip(sigs: &[TSig], ctr: &TickCtr) -> Option<(Vec<TSig>, TickCtr)> {
    decode(&encode(sigs, ctr)?)
}
