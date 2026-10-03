use super::*;

fn at(ts_ns: i64, tick_e9: i64, step_e9: i64) -> StepAt {
    StepAt {
        ts_ns,
        tick_e9,
        step_e9,
    }
}

#[test]
fn empty_schedule_is_one_entry_equal_to_the_grid() {
    let s = StepSchedule::from_header(1_000_000, 100_000, &[]).unwrap();
    assert_eq!(s.at(i64::MIN), (1_000_000, 100_000));
    assert_eq!(s.at(0), (1_000_000, 100_000));
    assert_eq!(s.at(i64::MAX), (1_000_000, 100_000));
}

#[test]
fn at_picks_the_last_entry_not_later_than_t() {
    // Сетка данных 0.001 / 0.1; до смены шаг цены 0.01, потом 0.001.
    let s = StepSchedule::from_header(
        1_000_000,
        100_000_000,
        &[
            at(10, 10_000_000, 100_000_000),
            at(20, 1_000_000, 100_000_000),
            at(30, 1_000_000, 200_000_000),
        ],
    )
    .unwrap();
    // До первой записи — первая.
    assert_eq!(s.at(i64::MIN), (10_000_000, 100_000_000));
    assert_eq!(s.at(9), (10_000_000, 100_000_000));
    // Метка записи включается (ts <= t).
    assert_eq!(s.at(10), (10_000_000, 100_000_000));
    assert_eq!(s.at(19), (10_000_000, 100_000_000));
    assert_eq!(s.at(20), (1_000_000, 100_000_000));
    assert_eq!(s.at(29), (1_000_000, 100_000_000));
    assert_eq!(s.at(30), (1_000_000, 200_000_000));
    assert_eq!(s.at(i64::MAX), (1_000_000, 200_000_000));
}

#[test]
fn a_step_not_a_multiple_of_the_grid_is_an_error() {
    let grid = (1_000_000, 100_000_000);
    let err = |sched: &[StepAt]| StepSchedule::from_header(grid.0, grid.1, sched).unwrap_err();
    assert!(err(&[at(0, 1_500_000, 100_000_000)]).contains("не кратен"));
    assert!(err(&[at(0, 1_000_000, 150_000_000)]).contains("не кратен"));
    // Мельче сетки — тоже не кратно (сетка — мельчайший шаг суток).
    assert!(err(&[at(0, 500_000, 100_000_000)]).contains("не кратен"));
    assert!(err(&[at(0, 0, 100_000_000)]).contains("не положителен"));
    assert!(
        err(&[at(5, 1_000_000, 100_000_000), at(5, 2_000_000, 100_000_000)])
            .contains("по возрастанию")
    );
    assert!(StepSchedule::from_header(0, 1, &[]).is_err());
}
