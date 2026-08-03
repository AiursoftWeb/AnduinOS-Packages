use anduinos_quiet_engine::{
    suggest, Action, CommandEvent, Container, FileEntry, GitRef, Process, Query, Service,
    Suggestion, WorldState,
};

fn query(line: &str, now_ms: u64, world: &WorldState) -> Option<Suggestion> {
    suggest(
        Query {
            line,
            cursor: line.len(),
            now_ms,
        },
        world,
    )
}

fn docker_world(now_ms: u64) -> WorldState {
    let mut world = WorldState {
        last_event: Some(CommandEvent {
            action: Action::DockerList { elevated: false },
            normalized: "docker ps".into(),
            exit_code: 0,
            at_ms: now_ms - 100,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    world.docker.generation = 7;
    world.docker.refreshed_at_ms = now_ms - 50;
    world.docker.containers = vec![
        Container {
            id: "59ab75d539d4".into(),
            name: "kind_bassi".into(),
            image: "ubuntu:26.04".into(),
            running: true,
            listing_rank: 0,
        },
        Container {
            id: "349eb1bc73fb".into(),
            name: "jovial_ptolemy".into(),
            image: "marktohtml:latest".into(),
            running: true,
            listing_rank: 1,
        },
    ];
    world
}

#[test]
fn apt_update_transitions_to_upgrade() {
    let now = 50_000;
    let mut world = WorldState {
        last_event: Some(CommandEvent {
            action: Action::AptUpdate {
                command: "apt".into(),
            },
            normalized: "apt update".into(),
            exit_code: 0,
            at_ms: now - 1_000,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    world.apt.generation = 4;
    world.apt.upgradable_packages = 20;
    let suggestion = query("sudo apt up", now, &world).unwrap();
    assert_eq!(suggestion.insertion, "grade");
    assert_eq!(suggestion.candidate.resulting_line, "sudo apt upgrade");
}

#[test]
fn empty_apt_action_has_a_safe_default() {
    let world = WorldState::default();
    assert_eq!(query("sudo apt ", 0, &world).unwrap().insertion, "update");
    assert_eq!(query("sudo apt", 0, &world).unwrap().insertion, " update");
    assert_eq!(query("apt", 0, &world).unwrap().insertion, " update");
    assert_eq!(query("apt upd", 0, &world).unwrap().insertion, "ate");
}

#[test]
fn apt_grammar_is_complete_and_small_ambiguity_still_speaks() {
    let world = WorldState::default();
    let suggestion = query("apt auto", 0, &world).expect("apt auto must not be silent");
    assert!(matches!(
        suggestion.candidate.resulting_line.as_str(),
        "apt autoclean" | "apt autopurge" | "apt autoremove"
    ));
    assert_eq!(query("apt autor", 0, &world).unwrap().insertion, "emove");
}

#[test]
fn personal_history_matches_across_sudo_wrappers() {
    let now = 1_000_000;
    let mut world = WorldState::default();
    world.observe_command("sudo apt autoremove", 0, now - 20);
    world.observe_command("sudo apt autoremove", 0, now - 10);
    world.observe_command("sudo apt autoremove", 0, now - 5);
    assert_eq!(query("apt auto", now, &world).unwrap().insertion, "remove");
}

#[test]
fn failed_update_does_not_create_workflow() {
    let now = 50_000;
    let world = WorldState {
        last_event: Some(CommandEvent {
            action: Action::AptUpdate {
                command: "apt".into(),
            },
            normalized: "apt update".into(),
            exit_code: 100,
            at_ms: now - 100,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    let suggestion = query("apt up", now, &world).unwrap();
    assert_eq!(
        suggestion.candidate.source,
        anduinos_quiet_engine::CandidateSource::Grammar
    );
    assert_ne!(suggestion.candidate.resulting_line, "apt upgrade");
}

#[test]
fn common_command_skeletons_survive_sudo_without_guessing_ambiguity() {
    let world = WorldState::default();
    assert_eq!(query("sudo docker ", 0, &world).unwrap().insertion, "ps");
    assert_eq!(query("sudo git", 0, &world).unwrap().insertion, " status");
    assert_eq!(query("git st", 0, &world).unwrap().insertion, "atus");
    assert_eq!(query("docker p", 0, &world).unwrap().insertion, "s");
    assert!(query("git c", 0, &world).is_none());
    assert_eq!(query("git che", 0, &world).unwrap().insertion, "ckout");
}

#[test]
fn personal_history_uses_frequency_and_cwd_but_blocks_dangerous_replay() {
    let now = 1_000_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.observe_command_with_cwd("git push origin feature", 0, now - 20, "/repo");
    world.observe_command_with_cwd("git push origin feature", 0, now - 10, "/repo");
    world.observe_command_with_cwd("git push origin main", 0, now - 5, "/other");
    world.current_cwd = "/repo".into();
    assert_eq!(
        query("git push origin ", now, &world).unwrap().insertion,
        "feature"
    );
    world.observe_command_with_cwd("rm -rf build-output", 0, now, "/repo");
    assert!(query("rm -", now, &world).is_none());
}

#[test]
fn current_directory_snapshot_completes_paths_without_foreground_io() {
    let now = 1_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.files.generation = 2;
    world.files.cwd = "/repo".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "Source".into(),
            directory: true,
        },
        FileEntry {
            name: "README.md".into(),
            directory: false,
        },
    ];
    assert_eq!(query("cd So", now, &world).unwrap().insertion, "urce/");
    assert_eq!(query("cat RE", now, &world).unwrap().insertion, "ADME.md");
}

#[test]
fn recent_docker_listing_does_not_choose_among_multiple_containers() {
    let now = 10_000;
    let world = docker_world(now);
    assert!(query("sudo docker exec -it ", now, &world).is_none());
    assert!(query("sudo docker logs -f ", now, &world).is_none());
}

#[test]
fn a_single_running_container_is_sufficient_evidence() {
    let now = 10_000;
    let mut world = docker_world(now);
    world.docker.containers.truncate(1);
    assert_eq!(
        query("sudo docker logs -f ", now, &world)
            .unwrap()
            .insertion,
        "kind_bassi"
    );
}

#[test]
fn typed_id_prefix_uses_live_entity() {
    let now = 10_000;
    let world = docker_world(now);
    let suggestion = query("docker exec -it 349e", now, &world).unwrap();
    assert_eq!(suggestion.insertion, "b1bc73fb");
}

#[test]
fn pipeline_filter_focuses_unique_container() {
    let now = 10_000;
    let mut world = docker_world(now);
    world.last_event.as_mut().unwrap().focus_filter = Some("marktohtml".into());
    let suggestion = query("docker logs -f ", now, &world).unwrap();
    assert_eq!(suggestion.insertion, "jovial_ptolemy");
}

#[test]
fn stale_entity_snapshot_is_silent() {
    let now = 100_000;
    let world = docker_world(10_000);
    assert!(query("docker logs -f ", now, &world).is_none());
}

#[test]
fn ambiguous_entities_without_evidence_are_silent() {
    let now = 10_000;
    let mut world = docker_world(now);
    world.last_event = None;
    assert!(query("docker exec -it ", now, &world).is_none());
}

#[test]
fn git_clean_prefers_a_dry_run() {
    let world = WorldState::default();
    let suggestion = query("git clean . -", 0, &world).unwrap();
    assert_eq!(suggestion.insertion, "-dry-run");
}

#[test]
fn suggestions_are_always_append_only_and_control_free() {
    let now = 10_000;
    let world = docker_world(now);
    for line in [
        "docker exec -it ",
        "docker exec -it 59",
        "docker logs --since 10m -f ",
        "git clean . -",
    ] {
        if let Some(suggestion) = query(line, now, &world) {
            assert!(suggestion.candidate.resulting_line.starts_with(line));
            assert!(!suggestion.insertion.chars().any(char::is_control));
        }
    }
}

#[test]
fn cursor_edits_are_refused_until_frontend_can_render_them_safely() {
    let world = docker_world(10_000);
    assert!(suggest(
        Query {
            line: "docker exec ",
            cursor: 7,
            now_ms: 10_000
        },
        &world
    )
    .is_none());
}

#[test]
fn process_pipeline_focus_resolves_a_live_pid() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.processes.generation = 2;
    world.processes.refreshed_at_ms = now - 10;
    world.processes.processes = vec![
        Process {
            pid: 4242,
            command: "mysqld".into(),
        },
        Process {
            pid: 7331,
            command: "nginx".into(),
        },
    ];
    world.observe_command("ps aux | grep mysqld", 0, now - 20);
    assert_eq!(query("sudo kill ", now, &world).unwrap().insertion, "4242");
}

#[test]
fn service_pipeline_focus_resolves_a_live_unit() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.services.generation = 3;
    world.services.refreshed_at_ms = now - 10;
    world.services.services = vec![
        Service {
            name: "docker.service".into(),
        },
        Service {
            name: "ssh.service".into(),
        },
    ];
    world.observe_command("systemctl list-units | grep docker", 0, now - 20);
    assert_eq!(
        query("systemctl status ", now, &world).unwrap().insertion,
        "docker.service"
    );
}

#[test]
fn git_ref_uses_common_prefix_without_arbitrary_selection() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.git.generation = 4;
    world.git.refreshed_at_ms = now - 10;
    world.git.refs = vec![
        GitRef {
            name: "feature-login".into(),
        },
        GitRef {
            name: "feature-logout".into(),
        },
        GitRef {
            name: "main".into(),
        },
    ];
    assert_eq!(
        query("git switch fea", now, &world).unwrap().insertion,
        "ture-log"
    );
    assert!(query("git switch feature-log", now, &world).is_none());
}
