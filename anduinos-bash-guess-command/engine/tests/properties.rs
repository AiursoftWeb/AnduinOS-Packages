use anduinos_quiet_engine::{parse_line, suggest, Query, WorldState};
use std::time::Instant;

#[test]
fn arbitrary_incomplete_input_never_panics_or_returns_a_replacement() {
    let alphabet = b"abc -_'\"|&;<>\\0123456789";
    let world = WorldState::default();
    let mut state = 0x4d59_5df4_d0f3_3173_u64;

    for _ in 0..20_000 {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        let length = (state as usize) % 96;
        let mut line = String::with_capacity(length);
        for _ in 0..length {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            line.push(alphabet[(state as usize) % alphabet.len()] as char);
        }

        let _ = parse_line(&line, line.len());
        if let Some(suggestion) = suggest(
            Query {
                line: &line,
                cursor: line.len(),
                now_ms: 1,
            },
            &world,
        ) {
            assert!(suggestion.candidate.resulting_line.starts_with(&line));
            assert!(!suggestion.insertion.is_empty());
            assert!(!suggestion.insertion.chars().any(char::is_control));
        }
    }
}

#[test]
fn every_byte_cursor_is_handled_without_panicking() {
    let line = "sudo docker exec 容器 -- sh";
    let world = WorldState::default();
    for cursor in 0..=line.len() {
        let _ = suggest(
            Query {
                line,
                cursor,
                now_ms: 1,
            },
            &world,
        );
    }
}

#[test]
fn foreground_query_stays_inside_a_conservative_cpu_budget() {
    let line = "sudo docker exec -it 59";
    let world = WorldState::default();
    let started = Instant::now();
    for now_ms in 0..100_000 {
        let _ = suggest(
            Query {
                line,
                cursor: line.len(),
                now_ms,
            },
            &world,
        );
    }
    let elapsed = started.elapsed();
    assert!(
        elapsed.as_millis() < 5_000,
        "100k foreground queries took {elapsed:?}"
    );
}
