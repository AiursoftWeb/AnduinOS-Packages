use anduinos_quiet_engine::{
    suggest, Action, Artifact, ArtifactKind, CommandEvent, Container, Query, Service, Suggestion,
    WorldState,
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

#[test]
fn adjacent_command_learning_uses_previous_command_and_cwd() {
    let now = 1_000_000;
    let mut world = WorldState::default();
    world.observe_command_with_cwd("git status", 0, now - 50, "/repo");
    world.observe_command_with_cwd("git push origin feature", 0, now - 40, "/repo");
    world.observe_command_with_cwd("git status", 0, now - 30, "/repo");
    world.observe_command_with_cwd("git push origin feature", 0, now - 20, "/repo");
    world.observe_command_with_cwd("git status", 0, now - 10, "/repo");

    let suggestion = query("git p", now, &world).expect("learned transition");
    assert_eq!(
        suggestion.candidate.resulting_line,
        "git push origin feature"
    );
    assert_eq!(
        suggestion.candidate.source,
        anduinos_quiet_engine::CandidateSource::Transition
    );
}

#[test]
fn failed_and_sensitive_commands_are_not_replayed_but_destructive_text_is() {
    let now = 1_000_000;
    let mut world = WorldState::default();
    world.observe_command_with_cwd("git status", 0, now - 30, "/repo");
    world.observe_command_with_cwd("deploy --token secret", 0, now - 20, "/repo");
    world.observe_command_with_cwd("git status", 0, now - 15, "/repo");
    world.observe_command_with_cwd("make release", 2, now - 10, "/repo");
    assert!(query("dep", now, &world).is_none());

    world.observe_command_with_cwd("git status", 0, now - 5, "/repo");
    world.observe_command_with_cwd("rm -rf build-output", 0, now - 4, "/repo");
    world.observe_command_with_cwd("git status", 0, now - 3, "/repo");
    assert_eq!(
        query("rm -", now, &world).unwrap().insertion,
        "rf build-output"
    );
}

#[test]
fn ssh_keygen_artifact_completes_read_and_copy_workflows() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.observe_command("ssh-keygen -t ed25519 -f ~/.ssh/id_work", 0, now - 20);
    world.artifacts.generation = 3;
    world.artifacts.refreshed_at_ms = now - 10;
    world.artifacts.artifacts = vec![Artifact {
        path: "~/.ssh/id_work.pub".into(),
        kind: ArtifactKind::PublicKey,
    }];

    assert_eq!(
        query("cat ~/.ssh/i", now, &world).unwrap().insertion,
        "d_work.pub"
    );
    assert_eq!(
        query("ssh-copy-id -i ~/.ssh/id_", now, &world)
            .unwrap()
            .insertion,
        "work.pub"
    );
}

#[test]
fn newly_created_directory_and_venv_become_cross_command_facts() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.observe_command("mkdir release-output", 0, now - 20);
    world.artifacts.generation = 1;
    world.artifacts.refreshed_at_ms = now - 10;
    world.artifacts.artifacts = vec![Artifact {
        path: "release-output".into(),
        kind: ArtifactKind::Directory,
    }];
    assert_eq!(
        query("cd rel", now, &world).unwrap().insertion,
        "ease-output"
    );

    world.observe_command("python3 -m venv .venv", 0, now - 5);
    world.artifacts.generation = 2;
    world.artifacts.refreshed_at_ms = now - 2;
    world.artifacts.artifacts = vec![Artifact {
        path: ".venv/bin/activate".into(),
        kind: ArtifactKind::ActivationScript,
    }];
    assert_eq!(
        query("source .v", now, &world).unwrap().insertion,
        "env/bin/activate"
    );
}

#[test]
fn docker_listing_predicts_the_full_next_command_only_when_unique() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.observe_command("docker ps", 0, now - 20);
    world.docker.generation = 4;
    world.docker.refreshed_at_ms = now - 10;
    world.docker.containers = vec![Container {
        id: "a01234567890".into(),
        name: "api".into(),
        image: "my-api:latest".into(),
        running: true,
        listing_rank: 0,
    }];
    assert_eq!(
        query("docker e", now, &world).unwrap().insertion,
        "xec -it api"
    );
    assert_eq!(
        query("docker l", now, &world).unwrap().insertion,
        "ogs -f api"
    );

    world.docker.containers.push(Container {
        id: "b01234567890".into(),
        name: "db".into(),
        image: "postgres:18".into(),
        running: true,
        listing_rank: 1,
    });
    assert_ne!(
        query("docker e", now, &world).map(|suggestion| suggestion.candidate.resulting_line),
        Some("docker exec -it api".into())
    );
}

#[test]
fn filtered_service_listing_predicts_status_command() {
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
    world.observe_command("systemctl list-units | grep docker", 0, now - 5);
    assert_eq!(
        query("systemctl st", now, &world).unwrap().insertion,
        "atus docker.service"
    );
}

#[test]
fn git_stage_and_commit_have_semantic_next_steps() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.observe_command("git add .", 0, now - 10);
    assert_eq!(query("git c", now, &world).unwrap().insertion, "ommit");
    world.observe_command("git commit -m release", 0, now - 5);
    assert_eq!(query("git p", now, &world).unwrap().insertion, "ush");
}

#[test]
fn systemctl_operation_remembers_the_unit_without_parsing_stdout() {
    let now = 10_000;
    let world = WorldState {
        last_event: Some(CommandEvent {
            action: Action::SystemctlOperation {
                verb: "restart".into(),
                unit: "docker.service".into(),
            },
            normalized: "systemctl restart docker.service".into(),
            exit_code: 0,
            at_ms: now - 5,
            focus_filter: None,
        }),
        ..WorldState::default()
    };
    assert_eq!(
        query("systemctl st", now, &world).unwrap().insertion,
        "atus docker.service"
    );
}

#[test]
fn all_contextual_predictions_remain_append_only_and_control_free() {
    let now = 10_000;
    let mut world = WorldState::default();
    world.observe_command("git add .", 0, now - 1);
    for line in ["git c", "git co", "sudo git c"] {
        if let Some(suggestion) = query(line, now, &world) {
            assert!(suggestion.candidate.resulting_line.starts_with(line));
            assert!(!suggestion.insertion.chars().any(char::is_control));
        }
    }
}
