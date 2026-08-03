use anduinos_quiet_engine::{suggest, Action, CommandEvent, Container, Query, WorldState};
use std::env;

fn main() {
    let line = env::args().skip(1).collect::<Vec<_>>().join(" ");
    if line.is_empty() {
        eprintln!("usage: anduinos-quiet-sim COMMAND-LINE");
        std::process::exit(2);
    }
    let world = demo_world();
    match suggest(
        Query {
            line: &line,
            cursor: line.len(),
            now_ms: 10_000,
        },
        &world,
    ) {
        Some(suggestion) => {
            println!("insertion={:?}", suggestion.insertion);
            println!("kind={:?}", suggestion.candidate.kind);
            println!("source={:?}", suggestion.candidate.source);
            println!("confidence={:.2}", suggestion.candidate.confidence);
            println!("evidence={:?}", suggestion.candidate.evidence);
        }
        None => println!("no-suggestion"),
    }
}

fn demo_world() -> WorldState {
    let mut world = WorldState {
        last_event: Some(CommandEvent {
            action: Action::DockerList { elevated: true },
            normalized: "docker ps".into(),
            exit_code: 0,
            at_ms: 9_000,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    world.docker.generation = 1;
    world.docker.refreshed_at_ms = 9_050;
    world.docker.containers = vec![Container {
        id: "59ab75d539d4".into(),
        name: "kind_bassi".into(),
        image: "ubuntu:26.04".into(),
        running: true,
        listing_rank: 0,
    }];
    world
}
