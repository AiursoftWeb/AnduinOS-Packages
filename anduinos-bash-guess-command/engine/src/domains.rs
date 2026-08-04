use crate::candidate::{Candidate, CandidateKind, CandidateSource, Dependency, Evidence, Risk};
use crate::shell::ParsedLine;
use crate::slot::{Slot, SlotKind};
use crate::specs;
use crate::world::{Action, ArtifactKind, Container, WorldState};

pub(crate) fn generate(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
) -> Vec<Candidate> {
    let mut candidates = Vec::new();
    command_name_candidates(parsed, slot, &mut candidates);
    match slot.kind {
        SlotKind::Subcommand => subcommand_candidates(parsed, slot, &mut candidates),
        SlotKind::AptAction => apt_candidates(parsed, world, now_ms, &mut candidates),
        SlotKind::DockerContainer => {
            docker_candidates(parsed, slot, world, now_ms, &mut candidates)
        }
        SlotKind::GitCleanOption => git_clean_candidates(parsed, &mut candidates),
        SlotKind::Option => grammar_option_candidates(parsed, slot, &mut candidates),
        SlotKind::Process => process_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::Service => service_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::GitRef => git_ref_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::Host => host_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::Path => path_candidates(parsed, slot, world, now_ms, &mut candidates),
        _ => {}
    }
    workflow_candidates(parsed, slot, world, now_ms, &mut candidates);
    transition_candidates(parsed, slot, world, now_ms, &mut candidates);
    personal_candidates(parsed, slot, world, now_ms, &mut candidates);
    candidates
}

fn command_name_candidates(parsed: &ParsedLine, slot: &Slot, out: &mut Vec<Candidate>) {
    let values = parsed.command_values();
    if !slot.allows(CandidateKind::Command)
        || values.len() != 1
        || parsed.trailing_space
        || parsed.current_prefix.len() < 2
        || parsed.current_prefix.contains('/')
    {
        return;
    }
    let Some(first) = parsed.command_tokens().first() else {
        return;
    };
    for command in specs::command_names().filter(|command| {
        command.starts_with(&parsed.current_prefix) && *command != parsed.current_prefix
    }) {
        out.push(Candidate::grammar(
            format!("{}{command}", &parsed.source[..first.start]),
            CandidateKind::Command,
            0.84,
        ));
    }
}

fn path_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    artifact_path_candidates(parsed, slot, world, now_ms, out);
    if world.files.cwd != world.current_cwd
        || now_ms.saturating_sub(world.files.refreshed_at_ms) > 120_000
    {
        return;
    }
    let directories_only = parsed.command_values().first() == Some(&"cd");
    let values = parsed.command_values();
    let dd_input = values.first() == Some(&"dd")
        && values.last().is_some_and(|value| value.starts_with("if="));
    let dd_empty_output = values.first() == Some(&"dd") && values.last() == Some(&"of=");
    if dd_empty_output {
        return;
    }
    if dd_input && slot.prefix == "/" {
        out.push(Candidate {
            resulting_line: format!("{}/dev/", &parsed.source[..slot.token_start]),
            kind: CandidateKind::Path,
            source: CandidateSource::Workflow,
            confidence: 0.88,
            risk: Risk::Safe,
            evidence: vec![Evidence::GrammarMatch],
            dependencies: Vec::new(),
            expires_at_ms: None,
        });
    }
    let (lookup_prefix, display_prefix) = slot
        .prefix
        .strip_prefix("./")
        .map(|prefix| (prefix, "./"))
        .unwrap_or((slot.prefix.as_str(), ""));
    let mut matches: Vec<String> = world
        .files
        .entries
        .iter()
        .filter(|entry| !directories_only || entry.directory)
        .filter(|entry| lookup_prefix.starts_with('.') || !entry.name.starts_with('.'))
        .filter(|entry| {
            if display_prefix == "./" && entry.name.starts_with("~/") {
                return false;
            }
            entry.name.starts_with(lookup_prefix)
        })
        .filter(|entry| {
            !entry.name.chars().any(|character| {
                character.is_whitespace() || character.is_control() || character == '\\'
            })
        })
        .map(|entry| {
            let displayed = format!("{display_prefix}{}", entry.name);
            if entry.directory {
                format!("{displayed}/")
            } else {
                displayed
            }
        })
        .collect();
    matches.sort();
    if matches.is_empty() {
        return;
    }
    let value = if matches.len() == 1 {
        matches[0].clone()
    } else {
        common_prefix(&matches)
    };
    if value == slot.prefix || !value.starts_with(&slot.prefix) {
        return;
    }
    out.push(Candidate {
        resulting_line: format!("{}{}", &parsed.source[..slot.token_start], value),
        kind: CandidateKind::Path,
        source: CandidateSource::Filesystem,
        confidence: if matches.len() == 1 { 0.78 } else { 0.64 },
        risk: Risk::Safe,
        evidence: vec![Evidence::LiveEntity {
            generation: world.files.generation,
        }],
        dependencies: vec![Dependency::FileGeneration(world.files.generation)],
        expires_at_ms: Some(world.files.refreshed_at_ms.saturating_add(120_000)),
    });
}

fn artifact_path_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if now_ms.saturating_sub(world.artifacts.refreshed_at_ms) > 600_000 {
        return;
    }
    let command = parsed.command_values().first().copied().unwrap_or_default();
    for artifact in &world.artifacts.artifacts {
        let relevant = match artifact.kind {
            ArtifactKind::Directory => command == "cd",
            ArtifactKind::ActivationScript => matches!(command, "source" | "."),
            ArtifactKind::PublicKey => {
                matches!(command, "cat" | "less" | "head" | "tail" | "ssh-copy-id")
            }
            ArtifactKind::File => command != "cd",
        };
        if !relevant
            || !artifact.path.starts_with(&slot.prefix)
            || artifact.path == slot.prefix
            || unsafe_shell_word(&artifact.path)
        {
            continue;
        }
        out.push(Candidate {
            resulting_line: format!("{}{}", &parsed.source[..slot.token_start], artifact.path),
            kind: CandidateKind::Path,
            source: CandidateSource::Workflow,
            confidence: 0.96,
            risk: Risk::Safe,
            evidence: vec![
                Evidence::ProducedArtifact,
                Evidence::LiveEntity {
                    generation: world.artifacts.generation,
                },
            ],
            dependencies: vec![Dependency::ArtifactGeneration(world.artifacts.generation)],
            expires_at_ms: Some(world.artifacts.refreshed_at_ms.saturating_add(600_000)),
        });
    }
}

fn transition_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    let Some(previous) = world
        .last_event
        .as_ref()
        .filter(|event| event.exit_code == 0 && now_ms.saturating_sub(event.at_ms) <= 1_800_000)
    else {
        return;
    };
    let Some(current) = normalized_command(parsed) else {
        return;
    };
    if current.trim().len() < 2 {
        return;
    }
    let Some(kind) = learned_kind(slot) else {
        return;
    };
    let typed_wrapper = &parsed.source[..parsed.command_tokens()[0].start];
    for entry in &world.transitions {
        if entry.previous != previous.normalized
            || entry.next == current
            || !entry.next.starts_with(current)
            || destructive_dd_device_replay(&entry.next)
        {
            continue;
        }
        let same_directory = !world.current_cwd.is_empty() && entry.cwd == world.current_cwd;
        let age = now_ms.saturating_sub(entry.last_used_ms);
        let mut confidence = 0.74 + (entry.count.min(8) as f32 * 0.025);
        if same_directory {
            confidence += 0.10;
        }
        if age <= 86_400_000 {
            confidence += 0.04;
        }
        let mut evidence = vec![
            Evidence::PreviousCommand("learned transition"),
            Evidence::TransitionFrequency(entry.count),
        ];
        if same_directory {
            evidence.push(Evidence::SameDirectory);
        }
        out.push(Candidate::transition(
            format!("{typed_wrapper}{}", entry.next),
            kind,
            confidence.min(0.96),
            Risk::Safe,
            evidence,
        ));
    }
}

fn learned_kind(slot: &Slot) -> Option<CandidateKind> {
    [
        CandidateKind::Command,
        CandidateKind::Subcommand,
        CandidateKind::Path,
    ]
    .into_iter()
    .find(|kind| slot.allows(*kind))
}

fn workflow_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    let Some(event) = world
        .last_event
        .as_ref()
        .filter(|event| event.exit_code == 0 && now_ms.saturating_sub(event.at_ms) <= 600_000)
    else {
        return;
    };
    let evidence = vec![
        Evidence::PreviousCommand("semantic workflow"),
        Evidence::SuccessfulExit,
    ];
    match &event.action {
        Action::DockerList { .. } => {
            if now_ms.saturating_sub(world.docker.refreshed_at_ms) <= 30_000 {
                if let Some(container) = focused_container(world, event.focus_filter.as_deref()) {
                    let dependencies = vec![Dependency::DockerGeneration(world.docker.generation)];
                    push_contextual(
                        parsed,
                        slot,
                        &format!("docker exec -it {}", container.name),
                        0.98,
                        Risk::Safe,
                        evidence.clone(),
                        dependencies.clone(),
                        Some(world.docker.refreshed_at_ms.saturating_add(30_000)),
                        out,
                    );
                    push_contextual(
                        parsed,
                        slot,
                        &format!("docker logs -f {}", container.name),
                        0.96,
                        Risk::Safe,
                        evidence.clone(),
                        dependencies,
                        Some(world.docker.refreshed_at_ms.saturating_add(30_000)),
                        out,
                    );
                }
            }
        }
        Action::ProcessList => {
            if let Some(filter) = event.focus_filter.as_deref() {
                let matches: Vec<&crate::world::Process> = world
                    .processes
                    .processes
                    .iter()
                    .filter(|process| {
                        process
                            .command
                            .to_ascii_lowercase()
                            .contains(&filter.to_ascii_lowercase())
                    })
                    .collect();
                if let [process] = matches.as_slice() {
                    push_contextual(
                        parsed,
                        slot,
                        &format!("kill {}", process.pid),
                        0.96,
                        Risk::Moderate,
                        evidence.clone(),
                        vec![Dependency::ProcessGeneration(world.processes.generation)],
                        Some(world.processes.refreshed_at_ms.saturating_add(30_000)),
                        out,
                    );
                }
            }
        }
        Action::ServiceList => {
            if let Some(filter) = event.focus_filter.as_deref() {
                let matches: Vec<&str> = world
                    .services
                    .services
                    .iter()
                    .map(|service| service.name.as_str())
                    .filter(|name| {
                        name.to_ascii_lowercase()
                            .contains(&filter.to_ascii_lowercase())
                    })
                    .collect();
                if let [service] = matches.as_slice() {
                    push_contextual(
                        parsed,
                        slot,
                        &format!("systemctl status {service}"),
                        0.97,
                        Risk::Safe,
                        evidence.clone(),
                        vec![Dependency::ServiceGeneration(world.services.generation)],
                        Some(world.services.refreshed_at_ms.saturating_add(60_000)),
                        out,
                    );
                }
            }
        }
        Action::SystemctlOperation { unit, .. } if !unsafe_shell_word(unit) => {
            push_contextual(
                parsed,
                slot,
                &format!("systemctl status {unit}"),
                0.94,
                Risk::Safe,
                evidence.clone(),
                Vec::new(),
                Some(event.at_ms.saturating_add(600_000)),
                out,
            );
        }
        Action::DockerBuild { image: Some(image) } if !unsafe_shell_word(image) => {
            push_contextual(
                parsed,
                slot,
                &format!("docker run --rm -it {image}"),
                0.91,
                Risk::Moderate,
                evidence.clone(),
                Vec::new(),
                Some(event.at_ms.saturating_add(600_000)),
                out,
            );
        }
        Action::GitStage => {
            push_contextual(
                parsed,
                slot,
                "git commit",
                0.92,
                Risk::Moderate,
                evidence.clone(),
                Vec::new(),
                Some(event.at_ms.saturating_add(600_000)),
                out,
            );
        }
        Action::GitCommit => {
            push_contextual(
                parsed,
                slot,
                "git push",
                0.88,
                Risk::Moderate,
                evidence.clone(),
                Vec::new(),
                Some(event.at_ms.saturating_add(600_000)),
                out,
            );
        }
        _ => {}
    }

    let artifact_kind = match event.action {
        Action::SshKeygen { .. } => Some(ArtifactKind::PublicKey),
        Action::MakeDirectory { .. } | Action::GitClone { .. } => Some(ArtifactKind::Directory),
        Action::PythonVenv { .. } => Some(ArtifactKind::ActivationScript),
        _ => None,
    };
    let Some(kind) = artifact_kind else {
        return;
    };
    if now_ms.saturating_sub(world.artifacts.refreshed_at_ms) > 600_000 {
        return;
    }
    for artifact in world
        .artifacts
        .artifacts
        .iter()
        .filter(|artifact| artifact.kind == kind)
    {
        if unsafe_shell_word(&artifact.path) {
            continue;
        }
        let dependencies = vec![Dependency::ArtifactGeneration(world.artifacts.generation)];
        let mut artifact_evidence = evidence.clone();
        artifact_evidence.push(Evidence::ProducedArtifact);
        match kind {
            ArtifactKind::PublicKey => {
                push_contextual(
                    parsed,
                    slot,
                    &format!("cat {}", artifact.path),
                    0.99,
                    Risk::Safe,
                    artifact_evidence.clone(),
                    dependencies.clone(),
                    Some(world.artifacts.refreshed_at_ms.saturating_add(600_000)),
                    out,
                );
                push_contextual(
                    parsed,
                    slot,
                    &format!("ssh-copy-id -i {}", artifact.path),
                    0.94,
                    Risk::Safe,
                    artifact_evidence.clone(),
                    dependencies.clone(),
                    Some(world.artifacts.refreshed_at_ms.saturating_add(600_000)),
                    out,
                );
            }
            ArtifactKind::Directory => push_contextual(
                parsed,
                slot,
                &format!("cd {}", artifact.path),
                0.98,
                Risk::Safe,
                artifact_evidence,
                dependencies,
                Some(world.artifacts.refreshed_at_ms.saturating_add(600_000)),
                out,
            ),
            ArtifactKind::ActivationScript => push_contextual(
                parsed,
                slot,
                &format!("source {}", artifact.path),
                0.99,
                Risk::Safe,
                artifact_evidence,
                dependencies,
                Some(world.artifacts.refreshed_at_ms.saturating_add(600_000)),
                out,
            ),
            ArtifactKind::File => {}
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn push_contextual(
    parsed: &ParsedLine,
    slot: &Slot,
    expected: &str,
    confidence: f32,
    risk: Risk,
    evidence: Vec<Evidence>,
    dependencies: Vec<Dependency>,
    expires_at_ms: Option<u64>,
    out: &mut Vec<Candidate>,
) {
    let Some(first) = parsed.command_tokens().first() else {
        return;
    };
    let resulting_line = format!("{}{}", &parsed.source[..first.start], expected);
    if resulting_line == parsed.source || !resulting_line.starts_with(&parsed.source) {
        return;
    }
    let Some(kind) = [
        CandidateKind::Workflow,
        CandidateKind::Command,
        CandidateKind::Subcommand,
        CandidateKind::Container,
        CandidateKind::Service,
        CandidateKind::Process,
        CandidateKind::Path,
    ]
    .into_iter()
    .find(|kind| slot.allows(*kind)) else {
        return;
    };
    out.push(Candidate {
        resulting_line,
        kind,
        source: CandidateSource::Workflow,
        confidence,
        risk,
        evidence,
        dependencies,
        expires_at_ms,
    });
}

fn focused_container<'a>(world: &'a WorldState, filter: Option<&str>) -> Option<&'a Container> {
    let matches: Vec<&Container> = world
        .docker
        .containers
        .iter()
        .filter(|container| container.running)
        .filter(|container| filter.is_none_or(|needle| container_matches(container, needle)))
        .collect();
    match matches.as_slice() {
        [container] => Some(*container),
        _ => None,
    }
}

fn unsafe_shell_word(value: &str) -> bool {
    value.is_empty()
        || value.chars().any(|character| {
            character.is_whitespace()
                || character.is_control()
                || matches!(character, '\\' | '\'' | '"' | '`' | '$' | ';' | '|' | '&')
        })
}

fn personal_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    let kind = if slot.allows(CandidateKind::Command) {
        CandidateKind::Command
    } else if slot.allows(CandidateKind::Subcommand) {
        CandidateKind::Subcommand
    } else {
        return;
    };
    if parsed.source.trim().len() < 2 {
        return;
    }
    let Some(current_command) = normalized_command(parsed) else {
        return;
    };
    let typed_wrapper = &parsed.source[..parsed.command_tokens()[0].start];
    for entry in &world.history {
        let history_command = normalized_history_command(&entry.command);
        if history_command == current_command || !history_command.starts_with(current_command) {
            continue;
        }
        if destructive_dd_device_replay(history_command) {
            continue;
        }
        let same_directory = !world.current_cwd.is_empty() && entry.cwd == world.current_cwd;
        let age = now_ms.saturating_sub(entry.last_used_ms);
        let mut confidence = 0.66 + (entry.count.min(10) as f32 * 0.020);
        if same_directory {
            confidence += 0.11;
        }
        if age <= 86_400_000 {
            confidence += 0.06;
        } else if age <= 604_800_000 {
            confidence += 0.03;
        }
        let mut evidence = vec![Evidence::PersonalFrequency(entry.count)];
        if same_directory {
            evidence.push(Evidence::SameDirectory);
        }
        out.push(Candidate::personal(
            format!("{typed_wrapper}{history_command}"),
            kind,
            confidence.min(0.86),
            Risk::Safe,
            evidence,
        ));
    }
}

fn normalized_command(parsed: &ParsedLine) -> Option<&str> {
    let start = parsed.command_tokens().first()?.start;
    parsed.source.get(start..)
}

fn normalized_history_command(command: &str) -> &str {
    let trimmed = command.trim_start();
    trimmed
        .strip_prefix("sudo ")
        .map(str::trim_start)
        .unwrap_or(trimmed)
}

fn destructive_dd_device_replay(command: &str) -> bool {
    let trimmed = command.trim_start();
    let normalized = trimmed
        .strip_prefix("sudo ")
        .map(str::trim_start)
        .unwrap_or(trimmed);
    if !normalized.starts_with("dd ") || !normalized.contains("of=/dev/") {
        return false;
    }
    let Some(parsed) = crate::shell::parse_line(trimmed, trimmed.len()) else {
        return false;
    };
    let values = parsed.command_values();
    values.first() == Some(&"dd")
        && values.iter().any(|value| {
            value
                .strip_prefix("of=")
                .is_some_and(|path| path.starts_with("/dev/"))
        })
}

fn subcommand_candidates(parsed: &ParsedLine, slot: &Slot, out: &mut Vec<Candidate>) {
    let values = parsed.command_values();
    let nested_base = if parsed.trailing_space {
        values.as_slice()
    } else {
        &values[..values.len().saturating_sub(1)]
    };
    let spec = specs::find_nested(nested_base).or_else(|| specs::find(values[0]));
    let Some(spec) = spec else { return };
    let actions = &spec.actions;
    let prefix = slot.prefix.as_str();
    let mut base = parsed.source[..slot.token_start].to_owned();
    if values.len() == 1 && !parsed.trailing_space {
        base.push(' ');
    }

    if prefix.is_empty() {
        if let Some(default) = spec.default {
            base.push_str(default);
            out.push(Candidate::grammar(base, CandidateKind::Subcommand, 0.90));
        }
        return;
    }
    for action in actions {
        if action.starts_with(prefix) && *action != prefix {
            let prominent = prefix.len() >= 2 && spec.preferred.contains(action);
            out.push(Candidate::grammar(
                format!("{base}{action}"),
                CandidateKind::Subcommand,
                if spec.default == Some(*action) {
                    0.70
                } else if prominent {
                    0.68
                } else {
                    0.62
                },
            ));
        }
    }
}

fn grammar_option_candidates(parsed: &ParsedLine, slot: &Slot, out: &mut Vec<Candidate>) {
    let values = parsed.command_values();
    let base_path = &values[..values.len().saturating_sub(1)];
    let Some(spec) = specs::find_options(base_path) else {
        return;
    };
    let base = &parsed.source[..slot.token_start];
    for option in &spec.options {
        if option.starts_with(&slot.prefix) && *option != slot.prefix {
            out.push(Candidate::grammar(
                format!("{base}{option}"),
                CandidateKind::Option,
                0.64,
            ));
        }
    }
}

fn process_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if now_ms.saturating_sub(world.processes.refreshed_at_ms) > 30_000 {
        return;
    }
    let filter = focused_filter(world, now_ms, Action::ProcessList);
    let matches = world
        .processes
        .processes
        .iter()
        .filter(|process| {
            filter.is_none_or(|value| {
                process
                    .command
                    .to_ascii_lowercase()
                    .contains(&value.to_ascii_lowercase())
            })
        })
        .map(|process| process.pid.to_string())
        .filter(|pid| pid.starts_with(&slot.prefix))
        .collect();
    push_entity(
        parsed,
        slot,
        EntitySet {
            matches,
            filter,
            kind: CandidateKind::Process,
            dependency: Dependency::ProcessGeneration(world.processes.generation),
            refreshed_at_ms: world.processes.refreshed_at_ms,
            ttl_ms: 30_000,
        },
        out,
    );
}

fn service_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if now_ms.saturating_sub(world.services.refreshed_at_ms) > 60_000 {
        return;
    }
    let filter = focused_filter(world, now_ms, Action::ServiceList);
    let matches = world
        .services
        .services
        .iter()
        .map(|service| service.name.clone())
        .filter(|name| {
            filter.is_none_or(|value| {
                name.to_ascii_lowercase()
                    .contains(&value.to_ascii_lowercase())
            })
        })
        .filter(|name| name.starts_with(&slot.prefix))
        .collect();
    push_entity(
        parsed,
        slot,
        EntitySet {
            matches,
            filter,
            kind: CandidateKind::Service,
            dependency: Dependency::ServiceGeneration(world.services.generation),
            refreshed_at_ms: world.services.refreshed_at_ms,
            ttl_ms: 60_000,
        },
        out,
    );
}

fn git_ref_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if slot.prefix.is_empty() || now_ms.saturating_sub(world.git.refreshed_at_ms) > 120_000 {
        return;
    }
    let matches = world
        .git
        .refs
        .iter()
        .map(|reference| reference.name.clone())
        .filter(|name| name.starts_with(&slot.prefix))
        .collect();
    push_entity(
        parsed,
        slot,
        EntitySet {
            matches,
            filter: None,
            kind: CandidateKind::GitRef,
            dependency: Dependency::GitGeneration(world.git.generation),
            refreshed_at_ms: world.git.refreshed_at_ms,
            ttl_ms: 120_000,
        },
        out,
    );
}

fn host_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if slot.prefix.is_empty() || now_ms.saturating_sub(world.hosts.refreshed_at_ms) > 300_000 {
        return;
    }
    let (user, host_prefix) = slot
        .prefix
        .rsplit_once('@')
        .map(|(user, host)| (Some(user), host))
        .unwrap_or((None, slot.prefix.as_str()));
    let matches = world
        .hosts
        .hosts
        .iter()
        .map(|host| match user {
            Some(user) => format!("{user}@{}", host.name),
            None => host.name.clone(),
        })
        .filter(|name| {
            name.starts_with(&slot.prefix)
                || name
                    .rsplit_once('@')
                    .is_some_and(|(_, host)| host.starts_with(host_prefix))
        })
        .collect();
    push_entity(
        parsed,
        slot,
        EntitySet {
            matches,
            filter: None,
            kind: CandidateKind::Host,
            dependency: Dependency::HostGeneration(world.hosts.generation),
            refreshed_at_ms: world.hosts.refreshed_at_ms,
            ttl_ms: 300_000,
        },
        out,
    );
}

fn focused_filter(world: &WorldState, now_ms: u64, action: Action) -> Option<&str> {
    world
        .last_event
        .as_ref()
        .filter(|event| {
            event.exit_code == 0
                && event.action == action
                && now_ms.saturating_sub(event.at_ms) <= 30_000
        })
        .and_then(|event| event.focus_filter.as_deref())
}

struct EntitySet<'a> {
    matches: Vec<String>,
    filter: Option<&'a str>,
    kind: CandidateKind,
    dependency: Dependency,
    refreshed_at_ms: u64,
    ttl_ms: u64,
}

fn push_entity(parsed: &ParsedLine, slot: &Slot, entity: EntitySet<'_>, out: &mut Vec<Candidate>) {
    let EntitySet {
        matches,
        filter,
        kind,
        dependency,
        refreshed_at_ms,
        ttl_ms,
    } = entity;
    if matches.is_empty() || (matches.len() > 1 && slot.prefix.is_empty() && filter.is_none()) {
        return;
    }
    let unique = matches.len() == 1;
    let value = if unique {
        matches[0].clone()
    } else {
        common_prefix(&matches)
    };
    if value == slot.prefix || !value.starts_with(&slot.prefix) {
        return;
    }
    let mut evidence = vec![Evidence::LiveEntity {
        generation: match dependency {
            Dependency::ProcessGeneration(value)
            | Dependency::ServiceGeneration(value)
            | Dependency::GitGeneration(value)
            | Dependency::HostGeneration(value) => value,
            _ => 0,
        },
    }];
    if unique {
        evidence.push(Evidence::UniqueMatch);
    }
    if let Some(filter) = filter {
        evidence.push(Evidence::FilterMatch(filter.to_owned()));
    }
    let mut resulting_line = parsed.source[..slot.token_start].to_owned();
    resulting_line.push_str(&value);
    out.push(Candidate {
        resulting_line,
        kind,
        source: CandidateSource::LiveEntity,
        confidence: if unique && filter.is_some() {
            0.99
        } else if unique {
            0.92
        } else {
            0.70
        },
        risk: Risk::Safe,
        evidence,
        dependencies: vec![dependency],
        expires_at_ms: Some(refreshed_at_ms.saturating_add(ttl_ms)),
    });
}

fn common_prefix(values: &[String]) -> String {
    let mut common = values[0].clone();
    for value in &values[1..] {
        while !value.starts_with(&common) {
            if common.pop().is_none() {
                break;
            }
        }
    }
    common
}

fn apt_candidates(parsed: &ParsedLine, world: &WorldState, now_ms: u64, out: &mut Vec<Candidate>) {
    let values = parsed.command_values();
    let command = values[0];
    let prefix = if values.len() >= 2 { values[1] } else { "" };
    let mut base = parsed.source[..parsed.source.len() - prefix.len()].to_owned();
    if values.len() == 1 && !parsed.trailing_space {
        base.push(' ');
    }
    let Some(spec) = specs::find(command) else {
        return;
    };
    for action in &spec.actions {
        if action.starts_with(prefix) && *action != prefix {
            out.push(Candidate::grammar(
                format!("{base}{action}"),
                CandidateKind::Subcommand,
                if prefix.is_empty() && spec.default == Some(*action) {
                    0.90
                } else {
                    0.62
                },
            ));
        }
    }

    let Some(event) = &world.last_event else {
        return;
    };
    let fresh = now_ms.saturating_sub(event.at_ms) <= 120_000;
    if event.action
        == (Action::AptUpdate {
            command: command.to_owned(),
        })
        && event.exit_code == 0
        && fresh
        && "upgrade".starts_with(prefix)
    {
        let mut evidence = vec![
            Evidence::PreviousCommand("apt update"),
            Evidence::SuccessfulExit,
        ];
        if world.apt.upgradable_packages > 0 {
            evidence.push(Evidence::UpgradesAvailable(world.apt.upgradable_packages));
        }
        out.push(Candidate {
            resulting_line: format!("{base}upgrade"),
            kind: CandidateKind::Workflow,
            source: CandidateSource::Workflow,
            confidence: if world.apt.upgradable_packages > 0 {
                0.98
            } else {
                0.88
            },
            risk: Risk::Moderate,
            evidence,
            dependencies: vec![Dependency::AptGeneration(world.apt.generation)],
            expires_at_ms: Some(event.at_ms.saturating_add(120_000)),
        });
    }
}

fn docker_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if now_ms.saturating_sub(world.docker.refreshed_at_ms) > 30_000 {
        return;
    }
    let event = world.last_event.as_ref();
    let filter = event
        .filter(|event| now_ms.saturating_sub(event.at_ms) <= 30_000)
        .and_then(|event| event.focus_filter.as_deref());
    let matches: Vec<&Container> = world
        .docker
        .containers
        .iter()
        .filter(|container| container.running)
        .filter(|container| filter.is_none_or(|needle| container_matches(container, needle)))
        .filter(|container| {
            slot.prefix.is_empty()
                || container.id.starts_with(&slot.prefix)
                || container.name.starts_with(&slot.prefix)
        })
        .collect();
    if matches.is_empty() {
        return;
    }

    let unique = matches.len() == 1;
    let value = if unique {
        let selected = matches[0];
        if selected.id.starts_with(&slot.prefix) && !slot.prefix.is_empty() {
            selected.id.clone()
        } else {
            selected.name.clone()
        }
    } else {
        let values: Vec<String> = matches
            .iter()
            .map(|container| {
                if !slot.prefix.is_empty() && container.id.starts_with(&slot.prefix) {
                    container.id.clone()
                } else {
                    container.name.clone()
                }
            })
            .collect();
        common_prefix(&values)
    };
    if value.is_empty() || !value.starts_with(&slot.prefix) || value == slot.prefix {
        return;
    }
    let mut evidence = vec![Evidence::LiveEntity {
        generation: world.docker.generation,
    }];
    if unique {
        evidence.push(Evidence::UniqueMatch);
    }
    if let Some(filter) = filter {
        evidence.push(Evidence::FilterMatch(filter.to_owned()));
    }
    let mut resulting_line = parsed.source[..slot.token_start].to_owned();
    if !resulting_line.ends_with(char::is_whitespace) {
        resulting_line.push(' ');
    }
    resulting_line.push_str(&value);
    out.push(Candidate {
        resulting_line,
        kind: CandidateKind::Container,
        source: CandidateSource::LiveEntity,
        confidence: if unique && filter.is_some() {
            0.99
        } else if unique {
            0.93
        } else {
            0.66
        },
        risk: Risk::Safe,
        evidence,
        dependencies: vec![Dependency::DockerGeneration(world.docker.generation)],
        expires_at_ms: Some(world.docker.refreshed_at_ms.saturating_add(30_000)),
    });
}

fn container_matches(container: &Container, needle: &str) -> bool {
    let needle = needle.to_ascii_lowercase();
    container.id.to_ascii_lowercase().contains(&needle)
        || container.name.to_ascii_lowercase().contains(&needle)
        || container.image.to_ascii_lowercase().contains(&needle)
}

fn git_clean_candidates(parsed: &ParsedLine, out: &mut Vec<Candidate>) {
    let prefix = &parsed.current_prefix;
    if "--dry-run".starts_with(prefix) && prefix != "--dry-run" {
        let mut resulting_line = parsed.source[..parsed.source.len() - prefix.len()].to_owned();
        resulting_line.push_str("--dry-run");
        let mut candidate = Candidate::grammar(resulting_line, CandidateKind::Option, 0.96);
        candidate.evidence.push(Evidence::DryRunGuard);
        out.push(candidate);
    }
}
