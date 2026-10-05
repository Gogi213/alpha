//! Замер потенциала подготовленных данных (TK-050): записи суток колонками (дельты varint) + zstd уровней 3/9/19
//! против исходного бинлога; время загрузки колонок в `Vec<Record>` против цепочки кадров бинлога; побитовое равенство.
//! Запуск: `prep_probe <файл.binlog>...` (печатает суммы по всем файлам).

use std::env;
use std::fs::File;
use std::time::Instant;

use alpha::binlog::{Reader, Record};

fn put_uv(buf: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        buf.push((v as u8) | 0x80);
        v >>= 7;
    }
    buf.push(v as u8);
}
fn put_zz(buf: &mut Vec<u8>, v: i64) {
    put_uv(buf, ((v << 1) ^ (v >> 63)) as u64);
}
fn get_uv(buf: &[u8], pos: &mut usize) -> u64 {
    let b = buf[*pos];
    *pos += 1;
    if b < 0x80 {
        return u64::from(b);
    }
    let mut r = u64::from(b & 0x7f);
    let mut sh = 7;
    loop {
        let b = buf[*pos];
        *pos += 1;
        r |= u64::from(b & 0x7f) << sh;
        if b < 0x80 {
            return r;
        }
        sh += 7;
    }
}
fn get_zz(buf: &[u8], pos: &mut usize) -> i64 {
    let v = get_uv(buf, pos);
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

const NCOL: usize = 6;

fn encode(recs: &[Record]) -> [Vec<u8>; NCOL] {
    let mut c: [Vec<u8>; NCOL] = Default::default();
    let (mut ex, mut lo, mut px, mut qy) = (0i64, 0i64, 0i64, 0i64);
    for r in recs {
        put_uv(&mut c[0], r.ev);
        put_zz(&mut c[1], r.exch_ts_ns.wrapping_sub(ex));
        put_zz(&mut c[2], r.local_ts_ns.wrapping_sub(lo));
        put_zz(&mut c[3], r.price_ticks.wrapping_sub(px));
        put_zz(&mut c[4], r.qty_lots.wrapping_sub(qy));
        c[5].push(u8::from(r.block) | (u8::from(r.rpi) << 1));
        ex = r.exch_ts_ns;
        lo = r.local_ts_ns;
        px = r.price_ticks;
        qy = r.qty_lots;
    }
    c
}

fn decode(c: &[Vec<u8>; NCOL], n: usize, out: &mut Vec<Record>) {
    let mut p = [0usize; NCOL];
    let (mut ex, mut lo, mut px, mut qy) = (0i64, 0i64, 0i64, 0i64);
    out.clear();
    out.reserve(n);
    for i in 0..n {
        let ev = get_uv(&c[0], &mut p[0]);
        ex = ex.wrapping_add(get_zz(&c[1], &mut p[1]));
        lo = lo.wrapping_add(get_zz(&c[2], &mut p[2]));
        px = px.wrapping_add(get_zz(&c[3], &mut p[3]));
        qy = qy.wrapping_add(get_zz(&c[4], &mut p[4]));
        let f = c[5][i];
        out.push(Record {
            ev,
            exch_ts_ns: ex,
            local_ts_ns: lo,
            price_ticks: px,
            qty_lots: qy,
            block: f & 1 != 0,
            rpi: f & 2 != 0,
        });
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let levels = [3, 9, 19];
    let (mut disk, mut nrec, mut raw) = (0u64, 0u64, 0u64);
    let mut zsz = [0u64; 3];
    let (mut t_src, mut t_raw, mut mism) = (0f64, 0f64, 0u64);
    let mut t_zd = [0f64; 3];
    let mut recs: Vec<Record> = Vec::new();
    let mut back: Vec<Record> = Vec::new();
    for path in env::args().skip(1) {
        disk += std::fs::metadata(&path)?.len();
        let t = Instant::now();
        recs.clear();
        let mut rd = Reader::open(File::open(&path)?)?;
        while let Some(fr) = rd.read_frame()? {
            recs.extend_from_slice(&fr);
        }
        t_src += t.elapsed().as_secs_f64();
        nrec += recs.len() as u64;
        let cols = encode(&recs);
        raw += cols.iter().map(|c| c.len() as u64).sum::<u64>();
        let t = Instant::now();
        decode(&cols, recs.len(), &mut back);
        t_raw += t.elapsed().as_secs_f64();
        if back != recs {
            mism += 1;
        }
        for (i, &lv) in levels.iter().enumerate() {
            let z: Vec<Vec<u8>> = cols
                .iter()
                .map(|c| zstd::bulk::compress(c, lv).unwrap())
                .collect();
            zsz[i] += z.iter().map(|v| v.len() as u64).sum::<u64>();
            let t = Instant::now();
            let un: Vec<Vec<u8>> = z
                .iter()
                .zip(cols.iter())
                .map(|(v, c)| zstd::bulk::decompress(v, c.len() + 1).unwrap())
                .collect();
            let un: [Vec<u8>; NCOL] = un.try_into().unwrap();
            decode(&un, recs.len(), &mut back);
            t_zd[i] += t.elapsed().as_secs_f64();
            if back != recs {
                mism += 1;
            }
        }
    }
    println!(
        "файлов={} записей={nrec} расхождений={mism}",
        env::args().count() - 1
    );
    println!("исходные бинлоги: {:.1} МБ (1,00); чтение кадров бинлога в Vec<Record> (zstd+decode_v3): {t_src:.2} с", disk as f64 / 1e6);
    println!(
        "колонки без сжатия: {:.1} МБ ({:.2}); загрузка в Vec<Record>: {t_raw:.2} с",
        raw as f64 / 1e6,
        raw as f64 / disk as f64
    );
    for (i, &lv) in levels.iter().enumerate() {
        println!(
            "колонки+zstd{lv}: {:.1} МБ ({:.2}); распаковка+загрузка: {:.2} с ({:.1}x быстрее чтения бинлога)",
            zsz[i] as f64 / 1e6,
            zsz[i] as f64 / disk as f64,
            t_zd[i],
            t_src / t_zd[i]
        );
    }
    Ok(())
}
