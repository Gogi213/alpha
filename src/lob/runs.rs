//! Журнал прогонов `runs.csv` (план, §7.1).
//!
//! Ведётся **с шага 3.1**: пилотные прогоны — тоже прогоны. Строка на каждый
//! прогон, включая каждого кандидата пула и обе попытки при красном G1.
//! Канонический путь — `docs/plan/runs.csv`, файл в git; десятки строк, читает
//! человек (Decision 23: CSV только для метаданных-обочин). Путь передаёт
//! вызывающий — модуль его не хардкодит, поэтому тот же код пишет и читает
//! синтетику в тестах.
//!
//! # Когда пишется (каждая строка — из буквальной формулировки плана)
//!
//! - `pilot` — шаг 3.1, по строке на каждого кандидата пула, замеченного G0.
//! - `confirmatory` — первая попытка подтверждающего прогона (шаг 5.1).
//! - `rerun` — каждый перезапуск после шага 5 на тех же логах (раздел Failure)
//!   и вторая попытка шага 1 при красном G1 по методике (обе в журнале).
//! - `amendment` — изменение после того, как результаты увидены: отдельный
//!   коммит с причиной плюс строка здесь (раздел Data and state).
//! - `debt` — findings ревью ниже уровня correctness как долг (раздел GC) и
//!   факты остановок записи с причиной (шаг 0.3 ведёт их учёт).
//!
//! # Кто читает
//!
//! `lob::final_metrics::trials_from_runs_csv` — число испытаний для поправки
//! DSR/PBO: пилот каждого кандидата пула и каждый перезапуск — тоже
//! испытания (раздел Failure плана). Журнал хранит только сам факт
//! прогона, без значений Sharpe: значения идут своими отчётами, а журнал не
//! даёт занизить `N` задним числом. Поэтому битая строка — отказ чтения всего
//! файла (`None` у `trials_from_runs_csv`), а не пропуск строки: пропустить
//! значило бы ослабить поправку молча.
//!
//! # Точка интеграции с пилотом (зависимость 7.1 от 3.1)
//!
//! Шаг 3.1 (`lob pilot`) ещё не реализован, и его интерфейсы здесь не
//! выдумываются: когда он появится, каждый кандидат сразу после вердикта G0
//! пишет `log_pilot_run(путь, символ, момент, деталь)` — одну строку вида
//! «сколько уровней, какой `m`, какой вердикт». Живые строки придут оттуда;
//! до тех пор журнал тестируется на синтетике ниже.
//!
//! # Стиль CSV — как `gaps.csv`
//!
//! Шапка всегда (файл с нулём строк — это шапка без данных, а не
//! отсутствующий файл), дописывание создаёт файл и шапку само, чтение
//! пустого — ноль строк, а не ошибка разбора. Не горячий путь: пишется раз на
//! прогон, аллокации не нормируются.

use std::fs::OpenOptions;
use std::io;
use std::path::Path;

// ---------------------------------------------------------------------------
// Ошибки. Тот же приём, что `RecordError` в `commands::record`: сбой
// ввода-вывода и отторгнутый CSV, без паник.
// ---------------------------------------------------------------------------

/// Отказ журнала прогонов.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunsError {
    /// Файл не открылся / не записался.
    Io(String),
    /// Строка не разобралась как `RunRow` (чужая шапка, неизвестный `kind`).
    Csv(String),
}

impl std::fmt::Display for RunsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunsError::Io(e) => write!(f, "ввод-вывод: {e}"),
            RunsError::Csv(e) => write!(f, "CSV: {e}"),
        }
    }
}

impl std::error::Error for RunsError {}

impl From<io::Error> for RunsError {
    fn from(e: io::Error) -> Self {
        RunsError::Io(e.to_string())
    }
}

impl From<csv::Error> for RunsError {
    fn from(e: csv::Error) -> Self {
        RunsError::Csv(e.to_string())
    }
}

// ---------------------------------------------------------------------------
// runs.csv: шапка всегда, строки только на прогоны и факты.
// ---------------------------------------------------------------------------

/// Что фиксирует строка журнала. Сериализуется snake_case и читается назад
/// тем же именем — переименование варианта без `serde(rename)` молча
/// разойдётся со старыми файлами, поэтому имена зафиксированы тестом
/// `run_row_round_trips_and_header_is_stable` (тот же приём, что `GapKind`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunKind {
    /// Шаг 3.1: пилотный прогон одного кандидата. Испытание для DSR.
    Pilot,
    /// Шаг 5.1: первая попытка подтверждающего прогона. Испытание для DSR.
    Confirmatory,
    /// Перезапуск после шага 5 на тех же логах или вторая попытка шага 1 при
    /// красном G1. Испытание для DSR (раздел Failure: «каждый перезапуск —
    /// строка и +1 к числу испытаний»).
    Rerun,
    /// Изменение после увиденных результатов: отдельный коммит с причиной
    /// плюс строка. Не испытание само по себе — испытанием будет прогон,
    /// который за ней последует и запишется своей строкой.
    Amendment,
    /// Долг ревью ниже correctness (раздел GC) и факты остановок записи с
    /// причиной. Учёт, а не измерение, — в поправку не входит.
    Debt,
    /// Предрегистрация сетки форм и фильтров **до** первого прогона с вердиктом
    /// (В-58, тикет B1): строка фиксирует, что именно будет испытано, и сама
    /// испытанием не является — в поправку DSR входят состоявшиеся прогоны, а
    /// не их план. Заведена после того, как строка `kind=prereg` в боевом
    /// журнале сделала его нечитаемым целиком (аудит 2026-09-17, находка R1):
    /// неизвестный вариант — отказ всего чтения (`read_run_rows`), поэтому у
    /// каждого нового вида строки обязан быть вариант здесь, иначе одна строка
    /// гасит журнал, ради которого он ведётся.
    Prereg,
}

impl RunKind {
    /// Входит ли строка в число испытаний DSR/PBO. Правило живёт здесь, а не
    /// у вызывающих: иначе один забытый флаг занизил бы поправку, а журнал
    /// существует ровно для того, чтобы занизить её было нельзя.
    pub fn counts_as_trial(self) -> bool {
        matches!(
            self,
            RunKind::Pilot | RunKind::Confirmatory | RunKind::Rerun
        )
    }
}

/// Одна строка `runs.csv`. Колонки: момент (UTC, RFC 3339), символ, вид
/// прогона, человекочитаемая деталь (числа, пороги, вердикт, причина).
/// Символ может быть пустым — у долга ревью привязки к символу нет.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RunRow {
    pub ts_utc: String,
    pub symbol: String,
    pub kind: RunKind,
    pub detail: String,
}

/// Шапка `runs.csv` — те же имена и в том же порядке, что поля `RunRow`
/// (тот же приём, что `GAPS_HEADER`: ручная запись, дрейф ловит тест).
const RUNS_HEADER: [&str; 4] = ["ts_utc", "symbol", "kind", "detail"];

/// Создаёт `runs.csv` с шапкой, если его нет или он пуст. Существующий
/// непустой файл не трогает — дописывающий прогон не имеет права терять уже
/// записанные испытания.
pub fn ensure_runs_csv(path: &Path) -> Result<(), RunsError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let needs_header = std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true);
    if needs_header {
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(path)?;
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        w.write_record(RUNS_HEADER)?;
        w.flush()?;
    }
    Ok(())
}

/// Дописывает строку. Шапка пишется тем же вызовом, если файла не было, —
/// вызывающему не нужно помнить про `ensure_runs_csv` отдельно.
pub fn append_run_row(path: &Path, row: &RunRow) -> Result<(), RunsError> {
    ensure_runs_csv(path)?;
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.serialize(row)?;
    w.flush()?;
    Ok(())
}

/// Читает все строки. Отсутствующего или нулевого файла здесь быть не должно
/// после `ensure_runs_csv`, но если он подсунут напрямую — это ноль строк, а
/// не ошибка разбора: отсутствие данных не есть повреждённые данные (тот же
/// приём, что `read_gap_rows`).
pub fn read_run_rows(path: &Path) -> Result<Vec<RunRow>, RunsError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<RunRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(RunsError::from)
}

/// Число испытаний DSR/PBO среди прочитанных строк: пилоты, подтверждающие
/// прогоны и перезапуски; учётные строки (`amendment`, `debt`, `prereg`)
/// не в счёт.
/// Чистая функция — правило считается на синтетике без файлов.
pub fn count_trials(rows: &[RunRow]) -> usize {
    rows.iter().filter(|r| r.kind.counts_as_trial()).count()
}

/// Число испытаний для DSR/PBO из файла журнала. `None` — файл не читается
/// или строка битая: считать нельзя, а не ноль (ноль ослабил бы поправку
/// молча, см. шапку модуля). Отсутствие файла — `Some(0)`: прогонов ещё не
/// было, и это честный ноль, а не недоступность.
pub fn trials_from_runs_csv(path: &Path) -> Option<usize> {
    read_run_rows(path).ok().map(|rows| count_trials(&rows))
}

/// Точка интеграции шага 3.1 (зависимость 7.1 от 3.1): пилот пишет по строке
/// на кандидата сразу после вердикта G0. Заглушка в том смысле, что живого
/// вызывающего пока нет — тело настоящее, пишет через `append_run_row`.
/// Деталь — одна строка вида «сколько уровней, какой `m` на 10 с, вердикт».
pub fn log_pilot_run(
    path: &Path,
    symbol: &str,
    ts_utc: &str,
    detail: &str,
) -> Result<(), RunsError> {
    append_run_row(
        path,
        &RunRow {
            ts_utc: ts_utc.to_string(),
            symbol: symbol.to_string(),
            kind: RunKind::Pilot,
            detail: detail.to_string(),
        },
    )
}

/// Префикс детали строки испытания профиля сетки смертей: `profile <id>`
/// (`shortlist::log_profile_trials`).
pub const PROFILE_TRIAL_PREFIX: &str = "profile";

/// Префикс детали строки испытания профиля касаний (таск 37, В-44):
/// `touch_profile <id>` — отличим от смертей по префиксу.
pub const TOUCH_PROFILE_TRIAL_PREFIX: &str = "touch_profile";

/// Префикс детали строки испытания формы отскока (B5, В-58):
/// `bounce_form <стоп>-<дедлайн>-<ранний>` — по строке на форму сетки.
/// Поправка вердикта дефлирует по числу **этих** строк, а не по всем строкам
/// журнала: пилоты плотности и профили касаний — другие процедуры отбора, и
/// складывать их с формами отскока значило бы дефлировать Шарп выбором,
/// которого не было. Полное число строк журнала печатается рядом как контекст.
pub const BOUNCE_TRIAL_PREFIX: &str = "bounce_form";

/// Журнал испытаний профилей — один писатель на обе сетки (смертей и
/// касаний, ревью T37): по строке `RunKind::Confirmatory` с деталью
/// `<prefix> <id>` на каждый id — каждая входит в число испытаний DSR
/// (`count_trials`). Что считается испытанием, решает вызывающий: В-18
/// «несуществующая корзина не испытание» — `profiles`/`touch-profiles`
/// подают только корзины с наблюдениями. Журнал, не перезапись:
/// повторный прогон дописывает вторую партию строк. Метку времени строкой
/// передаёт вызывающий.
pub fn log_trials(
    path: &Path,
    ts_utc: &str,
    prefix: &str,
    ids: &[String],
) -> Result<(), RunsError> {
    for id in ids {
        append_run_row(
            path,
            &RunRow {
                ts_utc: ts_utc.to_string(),
                symbol: String::new(),
                kind: RunKind::Confirmatory,
                detail: format!("{prefix} {id}"),
            },
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(kind: RunKind, symbol: &str, detail: &str) -> RunRow {
        RunRow {
            ts_utc: "2026-09-08T00:00:00Z".to_string(),
            symbol: symbol.to_string(),
            kind,
            detail: detail.to_string(),
        }
    }

    /// Файл создаётся с шапкой и нулём строк; повторный вызов шапку не
    /// дублирует; пустой журнал — ноль испытаний, а не недоступность.
    #[test]
    fn ensure_writes_header_once_and_empty_journal_counts_zero() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sub").join("runs.csv");
        ensure_runs_csv(&path).unwrap();
        ensure_runs_csv(&path).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            text, "ts_utc,symbol,kind,detail\n",
            "двойной ensure обязан оставить одну шапку: {text:?}"
        );
        assert_eq!(read_run_rows(&path).unwrap(), Vec::new());
        assert_eq!(trials_from_runs_csv(&path), Some(0));
    }

    /// Строка переживает запись и чтение теми же значениями, включая запятую,
    /// кавычки и кириллицу в детали; шапка стабильна побайтово.
    #[test]
    fn run_row_round_trips_and_header_is_stable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        let want = row(
            RunKind::Pilot,
            "SOLUSDT",
            "n=231, m=8.1 bps, вердикт «годен», пометка с, запятой",
        );
        append_run_row(&path, &want).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(
            text.starts_with("ts_utc,symbol,kind,detail\n"),
            "шапка обязана идти первой и дословно: {text:?}"
        );
        let got = read_run_rows(&path).unwrap();
        assert_eq!(got, vec![want]);
    }

    /// Испытания — пилоты обоих кандидатов, подтверждающий прогон и
    /// перезапуск; правка после результатов и долг — учёт, в поправку DSR
    /// не входят.
    #[test]
    fn trials_count_runs_but_not_bookkeeping() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        for (kind, symbol, detail) in [
            (RunKind::Pilot, "SOLUSDT", "первый кандидат"),
            (RunKind::Pilot, "HYPEUSDT", "второй кандидат"),
            (RunKind::Confirmatory, "SOLUSDT", "первая попытка 5.1"),
            (RunKind::Rerun, "SOLUSDT", "перезапуск после шага 5"),
            (
                RunKind::Amendment,
                "SOLUSDT",
                "порог H3 сменён коммитом ab12",
            ),
            (RunKind::Debt, "", "finding ревью: переименовать колонку"),
            (
                RunKind::Prereg,
                "POOL",
                "V-58: 48 форм до первого прогона с вердиктом",
            ),
        ] {
            append_run_row(&path, &row(kind, symbol, detail)).unwrap();
        }
        let rows = read_run_rows(&path).unwrap();
        assert_eq!(rows.len(), 7, "все семь строк обязаны читаться");
        assert_eq!(count_trials(&rows), 4);
        assert_eq!(trials_from_runs_csv(&path), Some(4));
    }

    /// Предрегистрация (В-58) — учётная строка: журнал с ней читается целиком,
    /// а число испытаний не растёт. Это регрессия находки R1 аудита 2026-09-17:
    /// строка `kind=prereg` сделала боевой журнал нечитаемым, и DSR-дефляция
    /// (`trials_from_runs_csv` → `None`) отказала бы до первого прогона B5.
    #[test]
    fn prereg_row_is_bookkeeping_and_does_not_kill_the_journal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        append_run_row(&path, &row(RunKind::Prereg, "POOL", "В-58: 48 форм")).unwrap();
        append_run_row(&path, &row(RunKind::Pilot, "SOLUSDT", "вердикт G0")).unwrap();
        let rows = read_run_rows(&path).expect("журнал с предрегистрацией обязан читаться");
        assert_eq!(rows.len(), 2);
        assert_eq!(count_trials(&rows), 1, "предрегистрация — не испытание");
        assert_eq!(trials_from_runs_csv(&path), Some(1));
        // Имя варианта — часть файла в git (`kind=prereg` уже записан строкой
        // В-58), поэтому оно проверяется, а не подразумевается.
        assert!(
            std::fs::read_to_string(&path).unwrap().contains(",prereg,"),
            "строка предрегистрации пишется как kind=prereg"
        );
    }

    /// Боевой журнал репозитория обязан читаться: он в git, у него есть
    /// потребитель DSR, и одна строка неизвестного вида гасит чтение целиком
    /// (находка R1). Тест ловит новый вид строки без варианта в `RunKind` —
    /// та же по смыслу страховка, что у `GapKind`.
    #[test]
    fn the_repository_journal_reads() {
        let path = std::path::Path::new("docs/plan/runs.csv");
        assert!(path.exists(), "журнал обязан лежать в репозитории");
        let rows = read_run_rows(path).expect("боевой журнал обязан читаться");
        assert!(!rows.is_empty(), "журнал не пуст");
        assert!(
            rows.iter().any(|r| r.kind == RunKind::Prereg),
            "предрегистрация В-58 обязана быть в боевом журнале"
        );
    }

    /// Битая строка (неизвестный kind — опечатка человека в git-файле) даёт
    /// отказ всего чтения, а не тихий пропуск: пропуск занизил бы поправку
    /// DSR, и журнал перестал бы выполнять свою единственную задачу.
    #[test]
    fn malformed_journal_is_none_not_silent_zero() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        std::fs::write(
            &path,
            "ts_utc,symbol,kind,detail\n2026-09-08T00:00:00Z,SOLUSDT,pilott,опечатка в kind\n",
        )
        .unwrap();
        assert!(
            read_run_rows(&path).is_err(),
            "неизвестный kind обязан быть ошибкой чтения"
        );
        assert_eq!(
            trials_from_runs_csv(&path),
            None,
            "битый журнал — недоступность, а не ноль испытаний"
        );
    }

    /// Несуществующий файл — ноль испытаний и пустое чтение, а не ошибка:
    /// до первого запуска 3.1 журнала нет, и это честный ноль.
    #[test]
    fn missing_journal_is_zero_trials_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("нет-такого.csv");
        assert_eq!(read_run_rows(&path).unwrap(), Vec::new());
        assert_eq!(trials_from_runs_csv(&path), Some(0));
    }

    /// Хук пилота пишет именно пилотную строку, которая считается испытанием.
    #[test]
    fn pilot_hook_appends_a_pilot_row() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        log_pilot_run(
            &path,
            "SOLUSDT",
            "2026-09-08T02:00:00Z",
            "n=231, m=8.1 bps, вердикт годен",
        )
        .unwrap();
        let rows = read_run_rows(&path).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].kind, RunKind::Pilot);
        assert_eq!(rows[0].symbol, "SOLUSDT");
        assert_eq!(trials_from_runs_csv(&path), Some(1));
    }

    /// Один писатель на обе сетки (ревью T37): строка на id с префиксом
    /// вызывающего, каждая — подтверждающее испытание; повторный вызов
    /// дописывает вторую партию, не перезаписывает первую; пустой список —
    /// ни одной строки.
    #[test]
    fn trials_append_one_confirmatory_row_per_id_with_the_callers_prefix_and_accumulate() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        let ids = vec![
            "pool:marginal:side=bid".to_string(),
            "pool:cross:bounced|[0,10m)".to_string(),
        ];
        log_trials(
            &path,
            "2026-09-13T00:00:00Z",
            TOUCH_PROFILE_TRIAL_PREFIX,
            &ids,
        )
        .unwrap();
        let rows = read_run_rows(&path).unwrap();
        assert_eq!(rows.len(), 2);
        assert!(rows.iter().all(|r| r.kind == RunKind::Confirmatory));
        assert_eq!(rows[0].detail, "touch_profile pool:marginal:side=bid");
        assert_eq!(rows[1].detail, "touch_profile pool:cross:bounced|[0,10m)");
        assert_eq!(trials_from_runs_csv(&path), Some(2));
        log_trials(
            &path,
            "2026-09-13T01:00:00Z",
            PROFILE_TRIAL_PREFIX,
            &ids[..1],
        )
        .unwrap();
        let rows = read_run_rows(&path).unwrap();
        assert_eq!(rows.len(), 3, "журнал, не перезапись");
        assert_eq!(rows[2].detail, "profile pool:marginal:side=bid");
        assert_eq!(trials_from_runs_csv(&path), Some(3));
        log_trials(&path, "2026-09-13T02:00:00Z", PROFILE_TRIAL_PREFIX, &[]).unwrap();
        assert_eq!(
            trials_from_runs_csv(&path),
            Some(3),
            "пустой список — ни строки"
        );
    }
}
