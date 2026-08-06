use anduinos_quiet_engine::{
    parse_line, suggest, Action, AptPackage, CommandEvent, HistoryEntry, Query, TransitionEntry,
    WorldState,
};
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

#[test]
fn bounded_full_indexes_stay_inside_the_frontend_deadline_budget() {
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        last_event: Some(CommandEvent {
            action: Action::GitMutation,
            normalized: "git status".into(),
            exit_code: 0,
            at_ms: 1_000_000,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    for index in 0..2_000 {
        world.history.push(HistoryEntry {
            command: format!("terraform plan -var item={index}"),
            cwd: "/repo".into(),
            count: 1,
            last_used_ms: index,
        });
        world.transitions.push(TransitionEntry {
            previous: "git status".into(),
            next: format!("cargo test package_{index}"),
            cwd: "/repo".into(),
            count: 1,
            last_used_ms: index,
        });
    }

    let line = "terraform p";
    let started = Instant::now();
    for _ in 0..1_000 {
        let suggestion = suggest(
            Query {
                line,
                cursor: line.len(),
                now_ms: 1_000_001,
            },
            &world,
        );
        assert!(suggestion.is_some());
    }
    let elapsed = started.elapsed();
    assert!(
        elapsed.as_secs_f32() < 5.0,
        "1k full-index foreground queries took {elapsed:?}"
    );
}

#[test]
fn large_apt_snapshot_queries_are_bounded_in_memory_and_cpu() {
    let mut world = WorldState::default();
    world.apt.generation = 1;
    world.apt.packages = (0..100_000)
        .map(|index| AptPackage {
            name: format!("package-{index:05}"),
            installed: false,
        })
        .collect();
    world.apt.packages.push(AptPackage {
        name: "btop".into(),
        installed: false,
    });
    world
        .apt
        .packages
        .sort_by(|left, right| left.name.cmp(&right.name));

    let line = "sudo apt install b";
    let started = Instant::now();
    for now_ms in 0..10_000 {
        let suggestion = suggest(
            Query {
                line,
                cursor: line.len(),
                now_ms,
            },
            &world,
        )
        .unwrap();
        assert_eq!(suggestion.insertion, "top");
    }
    let elapsed = started.elapsed();
    assert!(
        elapsed.as_secs_f32() < 5.0,
        "10k large APT snapshot queries took {elapsed:?}"
    );
}
