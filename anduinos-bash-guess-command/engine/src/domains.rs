use crate::candidate::{Candidate, CandidateKind, CandidateSource, Dependency, Evidence, Risk};
use crate::shell::ParsedLine;
use crate::slot::{Slot, SlotKind};
use crate::specs;
use crate::world::{Action, Container, WorldState};

pub(crate) fn generate(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
) -> Vec<Candidate> {
    let mut candidates = Vec::new();
    match slot.kind {
        SlotKind::Subcommand => subcommand_candidates(parsed, slot, &mut candidates),
        SlotKind::AptAction => apt_candidates(parsed, world, now_ms, &mut candidates),
        SlotKind::DockerContainer => {
            docker_candidates(parsed, slot, world, now_ms, &mut candidates)
        }
        SlotKind::GitCleanOption => git_clean_candidates(parsed, &mut candidates),
        SlotKind::Process => process_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::Service => service_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::GitRef => git_ref_candidates(parsed, slot, world, now_ms, &mut candidates),
        SlotKind::Path => path_candidates(parsed, slot, world, now_ms, &mut candidates),
        _ => {}
    }
    personal_candidates(parsed, slot, world, now_ms, &mut candidates);
    candidates
}

fn path_candidates(
    parsed: &ParsedLine,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    out: &mut Vec<Candidate>,
) {
    if world.files.cwd != world.current_cwd
        || now_ms.saturating_sub(world.files.refreshed_at_ms) > 30_000
    {
        return;
    }
    let directories_only = parsed.command_values().first() == Some(&"cd");
    let mut matches: Vec<String> = world
        .files
        .entries
        .iter()
        .filter(|entry| !directories_only || entry.directory)
        .filter(|entry| slot.prefix.starts_with('.') || !entry.name.starts_with('.'))
        .filter(|entry| entry.name.starts_with(&slot.prefix))
        .filter(|entry| {
            !entry.name.chars().any(|character| {
                character.is_whitespace() || character.is_control() || character == '\\'
            })
        })
        .map(|entry| {
            if entry.directory {
                format!("{}/", entry.name)
            } else {
                entry.name.clone()
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
        expires_at_ms: Some(world.files.refreshed_at_ms.saturating_add(30_000)),
    });
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
            personal_risk(&entry.command),
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

fn personal_risk(command: &str) -> Risk {
    let lower = command.trim_start().to_ascii_lowercase();
    if lower.starts_with("rm ")
        || lower.starts_with("sudo rm ")
        || lower.starts_with("dd ")
        || lower.starts_with("sudo dd ")
        || lower.starts_with("shutdown")
        || lower.starts_with("reboot")
        || lower.starts_with("git reset")
        || lower.starts_with("git clean")
        || lower.starts_with("docker rm")
        || lower.starts_with("docker system prune")
    {
        Risk::Dangerous
    } else {
        Risk::Safe
    }
}

fn subcommand_candidates(parsed: &ParsedLine, slot: &Slot, out: &mut Vec<Candidate>) {
    let values = parsed.command_values();
    let command = values[0];
    let Some(spec) = specs::find(command) else {
        return;
    };
    let actions = &spec.actions;
    let prefix = slot.prefix.as_str();
    let mut base = if values.len() == 1 {
        let mut value = parsed.source.clone();
        if !parsed.trailing_space {
            value.push(' ');
        }
        value
    } else {
        parsed.source[..slot.token_start].to_owned()
    };

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
}

fn push_entity(parsed: &ParsedLine, slot: &Slot, entity: EntitySet<'_>, out: &mut Vec<Candidate>) {
    let EntitySet {
        matches,
        filter,
        kind,
        dependency,
        refreshed_at_ms,
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
            | Dependency::GitGeneration(value) => value,
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
        expires_at_ms: Some(refreshed_at_ms.saturating_add(120_000)),
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
