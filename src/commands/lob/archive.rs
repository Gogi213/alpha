//! `lob archive` — упаковка закрытых суток в контейнер `*.binlog.zst` (T46).
//!
//! Сама упаковка (формат контейнера, чтение, сверка) — в `binlog::archive`;
//! здесь только CLI, порядок шагов и решение про оригинал. Порядок жёсткий:
//!
//! 1. проверить, что вход — обычный суточный файл v3 (не архив и не v2);
//! 2. записать контейнер во временный файл `<out>.tmp`;
//! 3. **сверить round-trip** — перечитать и исходник, и временный контейнер с
//!    диска, побайтово по телам кадров и покадрово по записям;
//! 4. только после успешной сверки переименовать `<out>.tmp` в `<out>`;
//! 5. удалить оригинал — **только** по явному флагу `--delete-original` и
//!    только при маркере `verify-<SYMBOL>.status == ok` в том же каталоге.
//!
//! Почему так: ошибка на любом шаге обязана оставить оригинал ровно таким,
//! каким он был, а неполный архив — не остаться под боевым именем. Временный
//! файл при отказе убирается, оригинал не трогается вовсе (кроме случая,
//! когда всё сошлось и оператор сам просил удалить).
//!
//! Маркер сверки — единственное, что отделяет «данные проверены» от «данные
//! просто лежат»: `lob verify` пишет `ok` по всем частям символа в каталоге
//! (таск 26), и архив закрытых суток без него — не архивация, а потеря
//! возможности перепроверить (fail-closed, R44 — тот же приём, что у
//! `profiles`/`watch`). Отдельных суток в маркере нет: он про символ в
//! каталоге, поэтому и требуется, чтобы файл лежал в том же каталоге, где
//! маркер.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::archive::{self, ArchiveRead, ArchiveVerify, ArchiveWrite};
use crate::binlog::Reader;

/// Аргументы `lob archive`.
#[derive(Debug, Args)]
pub struct ArchiveArgs {
    /// Обычный суточный файл записи: `<SYMBOL>-<день>[-pN].binlog`.
    #[arg(long)]
    pub path: PathBuf,
    /// Уровень zstd. 19 — замер `docs/findings/archive-compression-2026-09-15.md`
    /// (92.3 % от файла на диске); 22 — «ultra», тоже доступен библиотеке.
    #[arg(long, default_value_t = archive::DEFAULT_LEVEL)]
    pub level: i32,
    /// Куда положить контейнер (по умолчанию `<path>.zst` — суффикс
    /// дописывается, имя суток сохраняется).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Удалить оригинал после успешной архивации и сверки round-trip.
    /// Требует `verify-<SYMBOL>.status == ok` в каталоге оригинала: без
    /// маркера команда отказывается, архив при этом остаётся.
    #[arg(long, default_value_t = false)]
    pub delete_original: bool,
}

/// Итог `lob archive` — размеры, времена и что стало с оригиналом.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchiveSummary {
    pub src: PathBuf,
    pub out: PathBuf,
    pub source_bytes: u64,
    pub archive_bytes: u64,
    pub level: i32,
    pub frames: u64,
    pub records: u64,
    pub write_ms: u128,
    pub verify_ms: u128,
    pub read_ms: u128,
    pub original_deleted: bool,
    pub marker: Option<PathBuf>,
}

impl ArchiveSummary {
    /// Доля архива от оригинала в процентах — то число, по которому
    /// проверяется критерий «≤ 93 %»; считается только при ненулевом
    /// источнике (иначе делить не на что).
    #[allow(clippy::cast_precision_loss)]
    pub fn ratio_pct(&self) -> f64 {
        if self.source_bytes == 0 {
            0.0
        } else {
            100.0 * self.archive_bytes as f64 / self.source_bytes as f64
        }
    }
}

/// Временный путь для контейнера: рядом с целевым (`<out>.tmp`), чтобы
/// `rename` в конце был в пределах одной файловой системы — иначе он не
/// атомарен и мог бы скопировать половину файла.
fn tmp_path(out: &Path) -> PathBuf {
    let mut name = out.as_os_str().to_owned();
    name.push(".tmp");
    PathBuf::from(name)
}

/// Символ из имени суточного файла: нужен, чтобы найти маркер сверки
/// (`verify-<SYMBOL>.status`) рядом с файлом.
fn symbol_of(path: &Path) -> anyhow::Result<String> {
    let name = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    super::parts::symbol_of_binlog_name(&name)
        .map(|s| s.to_string())
        .ok_or_else(|| {
            anyhow::anyhow!(
                "имя `{name}` не разбирается как `<SYMBOL>-<день>[-pN].binlog`: \
             архивируются файлы записи, а не произвольные"
            )
        })
}

/// Только те поля `session.json`, которые нужны сторожу: сам файл — не наш
/// формат, и читателю здесь незачем знать остальные три десятка полей (и
/// падать, если формат однажды поменяется, — ровно то, что сторож и должен
/// переживать). Значения по умолчанию те же, что у `SessionSummary`.
#[derive(serde::Deserialize)]
struct OpenSession {
    #[serde(default)]
    closed: bool,
    #[serde(default)]
    updated_utc: String,
    #[serde(default)]
    binlog_files: Vec<OpenPart>,
}

/// Часть записи в `session.json` — ровно три поля, нужных для сравнения.
#[derive(serde::Deserialize)]
struct OpenPart {
    symbol: String,
    part: u32,
    #[serde(default)]
    started_utc: String,
}

/// Часть, которую сессия ещё пишет (A1, 2026-09-17; аудит V8).
///
/// `<dir>/session.json` с `closed = false` перечисляет части по порядку
/// появления — последняя запись для символа и есть открытый файл. Архивировать
/// (и тем более удалять) его нельзя: `unlink` открытого файла теряет всё, что
/// допишется после, а `verify` по неполным суткам врёт.
///
/// `Ok(None)` — файла `session.json` нет или сессия закрыта; `Err` — файл есть,
/// но не разбирается: fail-closed, лучше отказ, чем удаление живого файла.
fn still_writing(src: &Path, symbol: &str) -> anyhow::Result<Option<String>> {
    let dir = src.parent().unwrap_or_else(|| Path::new("."));
    let path = dir.join("session.json");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Ok(None);
    };
    let summary: OpenSession = serde_json::from_str(&text).map_err(|e| {
        anyhow::anyhow!(
            "{} не разбирается ({e}) — что именно пишется сейчас, неизвестно",
            path.display()
        )
    })?;
    if summary.closed {
        return Ok(None);
    }
    let name = src
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let (day, part) = super::file_order_key(&format!("{symbol}-"), &name);
    let open = summary
        .binlog_files
        .iter()
        .rev()
        .find(|p| p.symbol == symbol && p.started_utc.starts_with(&day));
    Ok(open.filter(|p| p.part == part).map(|p| {
        format!(
            "это последняя часть символа в незакрытой сессии (part={}, started_utc={}, \
             updated_utc={})",
            p.part, p.started_utc, summary.updated_utc
        )
    }))
}

pub fn run_archive(args: &ArchiveArgs) -> anyhow::Result<ArchiveSummary> {
    let src = args.path.clone();
    if !src.is_file() {
        anyhow::bail!("файл {} не найден", src.display());
    }
    if archive::is_archive_path(&src) {
        anyhow::bail!(
            "{} — уже контейнер архива; архивировать архив значило бы обернуть \
             сжатое сжатым и потерять имя суток",
            src.display()
        );
    }
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| archive::default_out_path(&src));
    if out == src {
        anyhow::bail!(
            "--out совпал с --path ({}): контейнер не может лечь вместо оригинала",
            out.display()
        );
    }
    if out.exists() {
        anyhow::bail!(
            "{} уже существует — архивация ничего не затирает, укажите другой --out",
            out.display()
        );
    }
    let symbol = symbol_of(&src)?;
    // A1/V8: открытая часть не архивируется и не удаляется. Отказ — до записи
    // контейнера: незачем тратить час CPU на файл, который ещё дописывается.
    if let Some(reason) = still_writing(&src, &symbol)? {
        anyhow::bail!(
            "{} не архивируется: {reason}. Дождитесь перехода на новую часть \
             (ротация суток) или остановите запись файлом `<root>/stop` (В-41)",
            src.display()
        );
    }
    let source_bytes = archive::file_bytes(&src)?;

    // Источник открывается один раз: версия проверяется до записи, чтобы на
    // v2-файле не осталось даже временного контейнера.
    let src_file = std::fs::File::open(&src)?;
    let mut src_reader = Reader::open(src_file)
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", src.display()))?;
    if src_reader.version() != crate::binlog::VERSION {
        anyhow::bail!(
            "{} — версия {}, а архивируются сутки версии {}: перепишите файл \
             (`lob binlog-stats --path {} --rewrite-out <файл>`) или оставьте как есть",
            src.display(),
            src_reader.version(),
            crate::binlog::VERSION,
            src.display()
        );
    }

    let tmp = tmp_path(&out);
    let _ = std::fs::remove_file(&tmp);
    let write =
        match archive::write_container(&mut src_reader, args.level, std::fs::File::create(&tmp)?) {
            Ok(w) => w,
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(anyhow::anyhow!("{}: {e:?}", src.display()));
            }
        };

    // Сверка читает **то, что легло на диск**: исходник и временный контейнер
    // открываются заново, а не сверяются с буфером в памяти — иначе
    // проверялся бы энкодер, а не файл.
    let verify: ArchiveVerify = (|| -> anyhow::Result<ArchiveVerify> {
        let mut a = Reader::open(std::fs::File::open(&src)?)?;
        let mut b = Reader::open(std::fs::File::open(&tmp)?)?;
        archive::verify_round_trip(&mut a, &mut b).map_err(|e| anyhow::anyhow!("{e:?}"))
    })()
    .map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        anyhow::anyhow!(
            "round-trip не сошёлся ({e}) — временный контейнер убран, оригинал не тронут"
        )
    })?;

    std::fs::rename(&tmp, &out)
        .map_err(|e| anyhow::anyhow!("{} → {}: {e}", tmp.display(), out.display()))?;
    let archive_bytes = archive::file_bytes(&out)?;
    let read = archive::read_stats(&out)?;

    // Удаление оригинала — последним шагом и только по явному флагу.
    let (mut marker, mut deleted) = (None, false);
    if args.delete_original {
        let dir = src.parent().unwrap_or_else(|| Path::new("."));
        let path = super::verify::marker_path(dir, &symbol);
        if !super::profiles::read_verify_marker(&path) {
            anyhow::bail!(
                "оригинал не удалён: нет маркера `{}` со словом ok (fail-closed, R44) — \
                 сверьте сутки `lob verify --symbol {} --root {}` и повторите; \
                 архив {} уже готов и цел",
                path.display(),
                symbol,
                dir.display(),
                out.display()
            );
        }
        std::fs::remove_file(&src)
            .map_err(|e| anyhow::anyhow!("оригинал {} не удалён: {e}", src.display()))?;
        marker = Some(path);
        deleted = true;
    }

    Ok(summary_of(
        src,
        out,
        source_bytes,
        archive_bytes,
        write,
        verify,
        read,
        deleted,
        marker,
    ))
}

/// Собирает сводку из размеров и трёх замеров — отдельной функцией, чтобы
/// порядок аргументов не расползался по телу `run_archive`.
#[allow(clippy::too_many_arguments)]
fn summary_of(
    src: PathBuf,
    out: PathBuf,
    source_bytes: u64,
    archive_bytes: u64,
    write: ArchiveWrite,
    verify: ArchiveVerify,
    read: ArchiveRead,
    original_deleted: bool,
    marker: Option<PathBuf>,
) -> ArchiveSummary {
    ArchiveSummary {
        src,
        out,
        source_bytes,
        archive_bytes,
        level: write.level,
        frames: verify.frames,
        records: verify.records,
        write_ms: write.write_ms,
        verify_ms: verify.millis,
        read_ms: read.millis,
        original_deleted,
        marker,
    }
}

/// Строки отчёта — по одной на факт, чтобы диспетчер ничего не считал сам.
#[allow(clippy::cast_precision_loss)]
pub fn summary_lines(s: &ArchiveSummary) -> Vec<String> {
    let mib = |b: u64| b as f64 / (1024.0 * 1024.0);
    vec![
        format!(
            "archive: {} ({:.2} МиБ) → {} ({:.2} МиБ, {:.1} % от оригинала) · уровень zstd {}",
            s.src.display(),
            mib(s.source_bytes),
            s.out.display(),
            mib(s.archive_bytes),
            s.ratio_pct(),
            s.level
        ),
        format!(
            "archive: кадров {} · записей {} · round-trip сошёлся (тела кадров побайтово, записи покадрово)",
            s.frames, s.records
        ),
        format!(
            "archive: время — запись {} мс, сверка {} мс, чтение контейнера (binlog-стата) {} мс",
            s.write_ms, s.verify_ms, s.read_ms
        ),
        if s.original_deleted {
            format!(
                "archive: оригинал удалён, маркер сверки — {}",
                s.marker
                    .as_ref()
                    .map(|p| p.display().to_string())
                    .unwrap_or_default()
            )
        } else {
            "archive: оригинал оставлен (удаление — только с --delete-original и маркером ok)"
                .to_string()
        },
    ]
}

#[cfg(test)]
mod tests;
