//! Замеры на малом для выбора архитектуры (В-170): разбор кадра тремя способами, кодировщик, весь путь писателя в одном потоке,
//! передача кадра между потоками, zstd — размер и цена от периода сброса. Печатает таблицу; ничего не пишет на диск.
//!
//! Данные — синтетические кадры формы настоящих (`synth`), 64 символа; числа — наносекунд на кадр на одном ядре.

use std::io::Write as _;
use std::time::Instant;

use bytes::Bytes;
use clap::Args;
use serde::Deserialize;

use super::binance::Binance;
use super::codec::{Encoder, Rec, SymEnc};
use super::exchange::{Exchange, Parser};
use super::scan::parse_object;

#[derive(Debug, Args)]
pub struct BenchArgs {
    /// Сколько кадров на каждый замер.
    #[arg(long, default_value_t = 2_000_000)]
    pub frames: usize,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct BookFrame<'a> {
    #[serde(borrow)]
    stream: &'a str,
    #[serde(borrow)]
    data: BookData<'a>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)]
struct BookData<'a> {
    e: &'a str,
    u: i64,
    s: &'a str,
    ps: &'a str,
    b: &'a str,
    #[serde(rename = "B")]
    bq: &'a str,
    a: &'a str,
    #[serde(rename = "A")]
    aq: &'a str,
    #[serde(rename = "T")]
    t: i64,
    #[serde(rename = "E")]
    ev: i64,
    st: i64,
}

fn frames(n: usize) -> Vec<Vec<u8>> {
    (0..n)
        .map(|i| {
            let sym = format!("S{:03}USDT", i % 64);
            let e = 1_794_418_695_171i64 + i64::try_from(i).unwrap_or(0) / 64;
            let u = 1_000_000_000i64 + i64::try_from(i).unwrap_or(0);
            format!(
                r#"{{"stream":"{}@bookTicker","data":{{"e":"bookTicker","u":{u},"s":"{sym}","ps":"{sym}","b":"8.{:04}","B":"{}","a":"8.{:04}","A":"{}","T":{},"E":{e},"st":1}}}}"#,
                sym.to_lowercase(),
                6790 + i % 50,
                i % 97 + 1,
                6800 + i % 50,
                i % 89 + 1,
                e - 1
            )
            .into_bytes()
        })
        .collect()
}

fn ns_per(t: Instant, n: usize) -> f64 {
    #[allow(clippy::cast_precision_loss)]
    let v = t.elapsed().as_nanos() as f64 / n as f64;
    v
}

fn row(name: &str, ns: f64, note: &str) {
    println!("{name:<58} {ns:>9.1} ns/кадр  {:>10.0} кадр/с на ядро  {note}", 1e9 / ns);
}

pub fn run(args: &BenchArgs) {
    let set = frames(65_536);
    let n = args.frames;
    println!("кадров на замер: {n}, набор: {} разных кадров bookTicker (~{} байт)", set.len(), set.first().map_or(0, Vec::len));
    let cyc = |i: usize| set.get(i % set.len()).map_or(&[][..], Vec::as_slice);

    // 1. разбор кадра: три способа
    let mut p = Binance.new_parser();
    let (mut sink, t) = (0i64, Instant::now());
    for i in 0..n {
        p.parse(cyc(i), &mut |r: &Rec<'_>| sink = sink.wrapping_add(r.key()));
    }
    row("разбор: свой сканер + проверка набора ключей (выбран)", ns_per(t, n), &format!("(контроль {sink})"));

    let t = Instant::now();
    let mut keep = 0usize;
    for i in 0..n {
        if let Ok(v) = serde_json::from_slice::<serde_json::Value>(cyc(i)) {
            keep += v.get("data").map_or(0, |d| d.as_object().map_or(0, serde_json::Map::len));
        }
    }
    row("разбор: serde_json::Value (общий, с выделениями)", ns_per(t, n), &format!("(контроль {keep})"));

    let t = Instant::now();
    keep = 0;
    for i in 0..n {
        if let Ok(f) = serde_json::from_slice::<BookFrame<'_>>(cyc(i)) {
            keep += usize::try_from(f.data.u & 1).unwrap_or(0) + f.stream.len();
        }
    }
    row("разбор: serde_json типизированный, заимствование, deny_unknown", ns_per(t, n), &format!("(контроль {keep})"));

    let t = Instant::now();
    keep = 0;
    for i in 0..n {
        keep += parse_object(cyc(i)).map_or(0, |o| o.len());
    }
    row("разбор: только верхний объект (нижняя граница сканера)", ns_per(t, n), &format!("(контроль {keep})"));

    // 2. кодировщик
    let (mut enc, mut st, mut out) = (Encoder::new(), vec![SymEnc::default(); 64], Vec::with_capacity(1 << 20));
    let mut p = Binance.new_parser();
    let t = Instant::now();
    for i in 0..n {
        let k = i % 64;
        p.parse(cyc(i), &mut |r: &Rec<'_>| {
            if let Some(s) = st.get_mut(k) {
                enc.record(&mut out, 1_794_418_700_000_000_000 + i64::try_from(i).unwrap_or(0) * 300_000, s, r);
            }
        });
        if out.len() > 1 << 19 {
            out.clear();
        }
    }
    row("разбор + кодировщик строки формата", ns_per(t, n), "");

    // 3. вся цепочка писателя в одном потоке: разбор + дубли + строка + zstd-1 + сброс блока по 300 строк (≈ 100 мс при 3 тыс/с)
    let t = Instant::now();
    let (mut enc, mut st, mut block, mut last) = (Encoder::new(), vec![SymEnc::default(); 64], Vec::with_capacity(1 << 16), vec![-1i64; 64]);
    let mut z = zstd::stream::write::Encoder::new(Vec::with_capacity(1 << 20), 1).ok();
    let mut p = Binance.new_parser();
    let (mut rows, mut written) = (0usize, 0usize);
    for i in 0..n {
        let k = i % 64;
        p.parse(cyc(i), &mut |r: &Rec<'_>| {
            let (Some(l), Some(s)) = (last.get_mut(k), st.get_mut(k)) else { return };
            if r.key() <= *l {
                return;
            }
            *l = r.key();
            enc.record(&mut block, 1_794_418_700_000_000_000 + i64::try_from(i).unwrap_or(0) * 300_000, s, r);
            rows += 1;
        });
        if rows >= 300 {
            if let Some(z) = z.as_mut() {
                let _ = z.write_all(&block);
                let _ = z.flush();
                written += z.get_ref().len();
                z.get_mut().clear();
            }
            block.clear();
            rows = 0;
        }
    }
    row("весь путь писателя в одном потоке (разбор+дубли+строка+zstd-1)", ns_per(t, n), &format!("(на диск {written} байт)"));

    // 4. передача кадра между потоками: приём → очередь → писатель (выделение Bytes на кадр + std sync_channel)
    let (tx, rx) = std::sync::mpsc::sync_channel::<(i64, Bytes)>(1 << 15);
    let m = n.min(1_000_000);
    let cons = std::thread::spawn(move || {
        let mut got = 0usize;
        while let Ok((_, b)) = rx.recv() {
            got += b.len();
        }
        got
    });
    let t = Instant::now();
    for i in 0..m {
        let _ = tx.send((i64::try_from(i).unwrap_or(0), Bytes::copy_from_slice(cyc(i))));
    }
    drop(tx);
    let bytes = cons.join().unwrap_or(0);
    row("передача кадра: выделение + sync_channel(32768) между потоками", ns_per(t, m), &format!("({bytes} байт)"));

    // 5. zstd: размер и цена от периода сброса (строк на блок) и уровня
    println!("\nzstd на строках формата (поток ≈ 3 тыс. записей/с; блок = строк между сбросами):");
    let mut text = Vec::new();
    {
        let (mut enc, mut st) = (Encoder::new(), vec![SymEnc::default(); 64]);
        let mut p = Binance.new_parser();
        for i in 0..400_000usize {
            let k = i % 64;
            p.parse(cyc(i), &mut |r: &Rec<'_>| {
                if let Some(s) = st.get_mut(k) {
                    enc.record(&mut text, 1_794_418_700_000_000_000 + i64::try_from(i).unwrap_or(0) * 300_000, s, r);
                }
            });
        }
    }
    let lines: Vec<&[u8]> = text.split_inclusive(|&c| c == b'\n').collect();
    for level in [1, 3] {
        for per_block in [3_000usize, 600, 300, 150, 30] {
            let t = Instant::now();
            let mut z = zstd::stream::write::Encoder::new(Vec::new(), level).ok();
            for chunk in lines.chunks(per_block) {
                if let Some(z) = z.as_mut() {
                    for l in chunk {
                        let _ = z.write_all(l);
                    }
                    let _ = z.flush();
                }
            }
            let size = z.and_then(|z| z.finish().ok()).map_or(0, |v| v.len());
            #[allow(clippy::cast_precision_loss)]
            let (ratio, mbs) = (text.len() as f64 / size.max(1) as f64, text.len() as f64 / 1e6 / t.elapsed().as_secs_f64());
            println!("  уровень {level}, блок {per_block:>5} строк (≈ {:>4} мс при 3 тыс/с): {size:>9} байт, сжатие ×{ratio:.2}, {mbs:.0} МБ/с", per_block / 3);
        }
    }
    println!("  исходный текст: {} байт, {} строк", text.len(), lines.len());
}
