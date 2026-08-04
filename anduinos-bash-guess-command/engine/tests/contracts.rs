use anduinos_quiet_engine::{
    suggest, Action, CommandEvent, Container, FileEntry, GitRef, Host, Process, Query, Service,
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
fn compact_nested_grammar_covers_common_cli_workflows() {
    let world = WorldState::default();
    assert_eq!(query("docker compose ", 0, &world).unwrap().insertion, "ps");
    assert_eq!(
        query("docker compose lo", 0, &world).unwrap().insertion,
        "gs"
    );
    assert_eq!(
        query("git remote get-", 0, &world).unwrap().insertion,
        "url"
    );
    assert_eq!(query("gh pr ch", 0, &world).unwrap().insertion, "eck");
    assert_eq!(
        query("kubectl rollout st", 0, &world).unwrap().insertion,
        "atus"
    );
    assert_eq!(query("go mod t", 0, &world).unwrap().insertion, "idy");
}

#[test]
fn compact_root_grammar_covers_modern_developer_and_ops_tools() {
    let world = WorldState::default();
    assert_eq!(query("terraform", 0, &world).unwrap().insertion, " plan");
    assert_eq!(query("uv sy", 0, &world).unwrap().insertion, "nc");
    assert_eq!(query("gcloud sec", 0, &world).unwrap().insertion, "rets");
    assert_eq!(
        query("systemd-analyze cr", 0, &world).unwrap().insertion,
        "itical-chain"
    );
    assert_eq!(query("restic sna", 0, &world).unwrap().insertion, "pshots");
}

#[test]
fn generated_multilevel_grammar_and_options_cover_diverse_workflows() {
    let world = WorldState::default();
    let cases = [
        ("docker builder pr", "une"),
        ("docker run --publ", "ish"),
        ("git commit --ame", "nd"),
        ("git clean build --excl", "ude"),
        ("kubectl create clusterroleb", "inding"),
        ("kubectl get --all-n", "amespaces"),
        ("kubectl get pods --all-n", "amespaces"),
        ("systemctl list-dep", "endencies"),
        ("cargo report future-i", "ncompat"),
        ("curl --fail-w", "ith-body"),
        ("openssl pkeyut", "l"),
        ("adb mdns ser", "vices"),
    ];
    for (input, insertion) in cases {
        assert_eq!(
            query(input, 0, &world)
                .unwrap_or_else(|| panic!("generated workflow was silent for {input:?}"))
                .insertion,
            insertion,
            "wrong generated workflow completion for {input:?}"
        );
    }
}

#[test]
fn cold_start_command_names_do_not_require_personal_history() {
    let mut world = WorldState::default();
    // Multiple installed-in-the-corpus commands share `terra`; extend only to
    // their common prefix instead of arbitrarily choosing Terraform.
    assert_eq!(query("terr", 0, &world).unwrap().insertion, "a");
    assert_eq!(query("terraf", 0, &world).unwrap().insertion, "orm");
    assert!(query("sudo kube", 0, &world).is_none());
    assert_eq!(query("sudo kubec", 0, &world).unwrap().insertion, "tl");
    assert!(query("py", 0, &world).is_none());
    assert_eq!(query("pyth", 0, &world).unwrap().insertion, "on");
    assert!(query("zz", 0, &world).is_none());

    for now in 1..=20 {
        world.observe_command_with_cwd("terraform plan -out release.tfplan", 0, now, "/repo");
    }
    world.current_cwd = "/repo".into();
    assert_eq!(
        query("terraf", 21, &world)
            .unwrap()
            .candidate
            .resulting_line,
        "terraform"
    );
}

#[test]
fn personal_history_uses_frequency_and_cwd_and_can_replay_destructive_text() {
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
    assert_eq!(
        query("rm -", now, &world).unwrap().insertion,
        "rf build-output"
    );
}

#[test]
fn dd_has_structured_input_and_output_path_slots() {
    let now = 1_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.files.generation = 5;
    world.files.cwd = "/repo".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "/dev/zero".into(),
            directory: false,
        },
        FileEntry {
            name: "disk-image.raw".into(),
            directory: false,
        },
    ];

    assert_eq!(
        query("sudo dd if=/", now, &world).unwrap().insertion,
        "dev/"
    );
    assert_eq!(
        query("sudo dd if=/dev/z", now, &world).unwrap().insertion,
        "ero"
    );
    assert_eq!(
        query("dd if=./disk-i", now, &world).unwrap().insertion,
        "mage.raw"
    );
    assert!(query("dd of=", now, &world).is_none());
    assert_eq!(query("dd of=/dev/z", now, &world).unwrap().insertion, "ero");
}

#[test]
fn complete_dd_device_writes_are_not_replayed_from_history() {
    let now = 1_000;
    let mut world = WorldState::default();
    world.observe_command_with_cwd(
        "sudo dd if=/tmp/disk.raw of=/dev/sda bs=4M",
        0,
        now - 1,
        "/repo",
    );
    assert!(query("sudo dd i", now, &world).is_none());

    world.observe_command_with_cwd(
        "dd if=/dev/zero of=./zero.img bs=1M count=1",
        0,
        now,
        "/repo",
    );
    assert_eq!(
        query("dd i", now + 1, &world)
            .unwrap()
            .candidate
            .resulting_line,
        "dd if=/dev/zero of=./zero.img bs=1M count=1"
    );
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
fn bounded_nested_snapshot_completes_common_path_consumers() {
    let now = 1_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.files.generation = 3;
    world.files.cwd = "/repo".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "src/components/button.rs".into(),
            directory: false,
        },
        FileEntry {
            name: "~/.ssh/id_ed25519.pub".into(),
            directory: false,
        },
    ];
    assert_eq!(
        query("nvim src/comp", now, &world).unwrap().insertion,
        "onents/button.rs"
    );
    assert_eq!(
        query("cat ~/.ssh/id_e", now, &world).unwrap().insertion,
        "d25519.pub"
    );
}

#[test]
fn generated_path_slots_cover_developer_and_archive_workflows() {
    let now = 9_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.files.generation = 12;
    world.files.cwd = "/repo".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "src/main.rs".into(),
            directory: false,
        },
        FileEntry {
            name: "release.tar.zst".into(),
            directory: false,
        },
        FileEntry {
            name: "scripts/deploy.py".into(),
            directory: false,
        },
    ];

    let cases = [
        ("git add src/ma", "in.rs"),
        ("tar rele", "ase.tar.zst"),
        ("rustc src/ma", "in.rs"),
        ("python3 scripts/de", "ploy.py"),
        ("gcc src/ma", "in.rs"),
    ];
    for (input, insertion) in cases {
        assert_eq!(
            query(input, now, &world)
                .unwrap_or_else(|| panic!("generated path slot was silent for {input:?}"))
                .insertion,
            insertion,
            "wrong generated path completion for {input:?}"
        );
    }
}

#[test]
fn common_path_valued_options_use_the_filesystem_snapshot() {
    let now = 11_000;
    let mut world = WorldState {
        current_cwd: "/repo".into(),
        ..WorldState::default()
    };
    world.files.generation = 13;
    world.files.cwd = "/repo".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "manifests/deployment.yaml".into(),
            directory: false,
        },
        FileEntry {
            name: "output.bin".into(),
            directory: false,
        },
        FileEntry {
            name: "commit-message.txt".into(),
            directory: false,
        },
        FileEntry {
            name: "Dockerfile.release".into(),
            directory: false,
        },
    ];

    let cases = [
        ("kubectl apply -f manifests/de", "ployment.yaml"),
        ("curl -o out", "put.bin"),
        ("curl --output=out", "put.bin"),
        ("git commit -F commit-m", "essage.txt"),
        ("sudo docker build -f Dockerfile.r", "elease"),
    ];
    for (input, insertion) in cases {
        assert_eq!(
            query(input, now, &world)
                .unwrap_or_else(|| panic!("path-valued option was silent for {input:?}"))
                .insertion,
            insertion,
            "wrong path-valued option completion for {input:?}"
        );
    }
}

#[test]
fn explicit_current_directory_prefix_is_preserved_for_hidden_paths() {
    let now = 1_000;
    let mut world = WorldState {
        current_cwd: "/home/anduin".into(),
        ..WorldState::default()
    };
    world.files.generation = 4;
    world.files.cwd = "/home/anduin".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: ".bash_history".into(),
            directory: false,
        },
        FileEntry {
            name: ".bash_logout".into(),
            directory: false,
        },
        FileEntry {
            name: ".bashrc".into(),
            directory: false,
        },
    ];
    let suggestion = query("cat ./.ba", now, &world).unwrap();
    assert_eq!(suggestion.insertion, "sh");
    assert_eq!(suggestion.candidate.resulting_line, "cat ./.bash");
}

#[test]
fn ls_options_still_leave_the_final_argument_as_a_path_slot() {
    let now = 1_000;
    let mut world = WorldState {
        current_cwd: "/".into(),
        ..WorldState::default()
    };
    world.files.generation = 6;
    world.files.cwd = "/".into();
    world.files.refreshed_at_ms = now;
    world.files.entries = vec![
        FileEntry {
            name: "dev".into(),
            directory: true,
        },
        FileEntry {
            name: "Desktop".into(),
            directory: true,
        },
    ];

    let suggestion = query("ls -ashl ./de", now, &world).unwrap();
    assert_eq!(suggestion.insertion, "v/");
    assert_eq!(suggestion.candidate.resulting_line, "ls -ashl ./dev/");
}

#[test]
fn ssh_alias_snapshot_completes_hosts_without_foreground_io() {
    let now = 1_000;
    let mut world = WorldState::default();
    world.hosts.generation = 4;
    world.hosts.refreshed_at_ms = now;
    world.hosts.hosts = vec![
        Host {
            name: "production-api".into(),
        },
        Host {
            name: "staging-api".into(),
        },
    ];
    assert_eq!(
        query("ssh prod", now, &world).unwrap().insertion,
        "uction-api"
    );
    assert_eq!(
        query("ssh deploy@stag", now, &world).unwrap().insertion,
        "ing-api"
    );
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
