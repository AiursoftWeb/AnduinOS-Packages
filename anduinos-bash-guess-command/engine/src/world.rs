use crate::shell::parse_line;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    AptUpdate { command: String },
    AptMutation,
    DockerList { elevated: bool },
    DockerBuild { image: Option<String> },
    ProcessList,
    ServiceList,
    SystemctlOperation { verb: String, unit: String },
    SshKeygen { private_key: Option<String> },
    MakeDirectory { paths: Vec<String> },
    GitClone { destination: String },
    PythonVenv { path: String },
    GitStage,
    GitCommit,
    GitMutation,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandEvent {
    pub action: Action,
    pub normalized: String,
    pub exit_code: i32,
    pub at_ms: u64,
    /// A safe, adapter-produced focus filter; never raw terminal output.
    pub focus_filter: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Container {
    pub id: String,
    pub name: String,
    pub image: String,
    pub running: bool,
    /// Lower means newer, matching Docker's default listing order.
    pub listing_rank: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Process {
    pub pid: u32,
    pub command: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Service {
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitRef {
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Host {
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistoryEntry {
    pub command: String,
    pub cwd: String,
    pub count: u32,
    pub last_used_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransitionEntry {
    pub previous: String,
    pub next: String,
    pub cwd: String,
    pub count: u32,
    pub last_used_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArtifactKind {
    PublicKey,
    Directory,
    ActivationScript,
    File,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Artifact {
    /// Shell-ready path spelling (for example `~/.ssh/id_ed25519.pub`).
    pub path: String,
    pub kind: ArtifactKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileEntry {
    pub name: String,
    pub directory: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct DockerSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub containers: Vec<Container>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AptSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub upgradable_packages: u32,
    /// Sorted, bounded package names prepared by a background observer.
    pub packages: Vec<AptPackage>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AptPackage {
    pub name: String,
    pub installed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ProcessSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub processes: Vec<Process>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ServiceSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub services: Vec<Service>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct GitSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub cwd: String,
    pub refs: Vec<GitRef>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct HostSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub hosts: Vec<Host>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FileSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub cwd: String,
    pub entries: Vec<FileEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CommandSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub commands: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ArtifactSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub artifacts: Vec<Artifact>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct WorldState {
    pub last_event: Option<CommandEvent>,
    pub current_cwd: String,
    pub history: Vec<HistoryEntry>,
    pub transitions: Vec<TransitionEntry>,
    pub commands: CommandSnapshot,
    pub files: FileSnapshot,
    pub artifacts: ArtifactSnapshot,
    pub docker: DockerSnapshot,
    pub apt: AptSnapshot,
    pub processes: ProcessSnapshot,
    pub services: ServiceSnapshot,
    pub git: GitSnapshot,
    pub hosts: HostSnapshot,
}

impl WorldState {
    /// Records shell-visible facts only. Slow domain refreshes are performed by
    /// a separate observer and atomically assigned to `docker` / `apt`.
    pub fn observe_command(&mut self, line: &str, exit_code: i32, at_ms: u64) {
        let cwd = self.current_cwd.clone();
        self.observe_command_with_cwd(line, exit_code, at_ms, &cwd);
    }

    pub fn observe_command_with_cwd(
        &mut self,
        line: &str,
        exit_code: i32,
        at_ms: u64,
        cwd: &str,
    ) -> Option<(HistoryEntry, Option<TransitionEntry>)> {
        self.current_cwd = cwd.to_owned();
        let previous = self.last_event.clone();
        let current = derive_event(line, exit_code, at_ms);
        self.last_event = current.clone();
        if exit_code != 0 || !history_safe(line) {
            return None;
        }
        let command = line.trim().to_owned();
        if let Some(entry) = self
            .history
            .iter_mut()
            .find(|entry| entry.command == command && entry.cwd == cwd)
        {
            entry.count = entry.count.saturating_add(1);
            entry.last_used_ms = at_ms;
        } else {
            self.history.push(HistoryEntry {
                command: command.clone(),
                cwd: cwd.to_owned(),
                count: 1,
                last_used_ms: at_ms,
            });
        }
        if self.history.len() > 2_000 {
            self.history.sort_by_key(|entry| entry.last_used_ms);
            self.history.drain(..self.history.len() - 2_000);
        }
        let learned_transition = previous
            .as_ref()
            .filter(|event| event.exit_code == 0 && history_safe(&event.normalized))
            .and_then(|event| current.as_ref().map(|current| (event, current)))
            .and_then(|(previous, current)| {
                self.learn_transition(&previous.normalized, &current.normalized, cwd, at_ms)
            });
        Some((
            HistoryEntry {
                command,
                cwd: cwd.to_owned(),
                count: 1,
                last_used_ms: at_ms,
            },
            learned_transition,
        ))
    }

    pub(crate) fn observe_command_without_learning(
        &mut self,
        line: &str,
        exit_code: i32,
        at_ms: u64,
        cwd: &str,
    ) {
        self.current_cwd = cwd.to_owned();
        self.last_event = derive_event(line, exit_code, at_ms);
    }

    pub(crate) fn merge_history(&mut self, incoming: Vec<HistoryEntry>) {
        for entry in incoming {
            if let Some(existing) = self
                .history
                .iter_mut()
                .find(|existing| existing.command == entry.command && existing.cwd == entry.cwd)
            {
                existing.count = existing.count.saturating_add(entry.count);
                existing.last_used_ms = existing.last_used_ms.max(entry.last_used_ms);
            } else {
                self.history.push(entry);
            }
        }
        self.history
            .sort_by_key(|entry| std::cmp::Reverse(entry.last_used_ms));
        self.history.truncate(2_000);
    }

    pub(crate) fn merge_transitions(&mut self, incoming: Vec<TransitionEntry>) {
        for entry in incoming {
            if let Some(existing) = self.transitions.iter_mut().find(|existing| {
                existing.previous == entry.previous
                    && existing.next == entry.next
                    && existing.cwd == entry.cwd
            }) {
                existing.count = existing.count.saturating_add(entry.count);
                existing.last_used_ms = existing.last_used_ms.max(entry.last_used_ms);
            } else {
                self.transitions.push(entry);
            }
        }
        self.transitions
            .sort_by_key(|entry| std::cmp::Reverse(entry.last_used_ms));
        self.transitions.truncate(2_000);
    }

    fn learn_transition(
        &mut self,
        previous: &str,
        next: &str,
        cwd: &str,
        at_ms: u64,
    ) -> Option<TransitionEntry> {
        if previous == next || previous.is_empty() || next.is_empty() {
            return None;
        }
        if let Some(entry) = self
            .transitions
            .iter_mut()
            .find(|entry| entry.previous == previous && entry.next == next && entry.cwd == cwd)
        {
            entry.count = entry.count.saturating_add(1);
            entry.last_used_ms = at_ms;
            return Some(TransitionEntry {
                previous: previous.to_owned(),
                next: next.to_owned(),
                cwd: cwd.to_owned(),
                count: 1,
                last_used_ms: at_ms,
            });
        }
        let learned = TransitionEntry {
            previous: previous.to_owned(),
            next: next.to_owned(),
            cwd: cwd.to_owned(),
            count: 1,
            last_used_ms: at_ms,
        };
        self.transitions.push(learned.clone());
        if self.transitions.len() > 2_000 {
            self.transitions.sort_by_key(|entry| entry.last_used_ms);
            self.transitions.drain(..self.transitions.len() - 2_000);
        }
        Some(learned)
    }
}

pub(crate) fn history_safe(line: &str) -> bool {
    if line.starts_with(char::is_whitespace) {
        return false;
    }
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.len() > 4_096 || trimmed.chars().any(char::is_control) {
        return false;
    }
    let lower = trimmed.to_ascii_lowercase();
    let sensitive = [
        "password",
        "passwd",
        "token",
        "secret",
        "api_key",
        "api-key",
        "apikey",
        "authorization",
        "bearer ",
        "cookie",
        "private-key",
        "private_key",
    ];
    if sensitive.iter().any(|marker| lower.contains(marker))
        || (lower.contains("://")
            && lower.split("://").nth(1).is_some_and(|tail| {
                tail.split('/')
                    .next()
                    .is_some_and(|authority| authority.contains('@'))
            }))
    {
        return false;
    }
    let Some(parsed) = parse_line(trimmed, trimmed.len()) else {
        return false;
    };
    if parsed.tokens[..parsed.command_start]
        .iter()
        .any(|token| token.value.contains('='))
    {
        return false;
    }
    let values = parsed.command_values();
    let Some(command) = values.first().copied() else {
        return false;
    };
    if matches!(command, "sshpass" | "pass" | "secret-tool") {
        return false;
    }
    if values.windows(2).any(|pair| {
        matches!(
            pair,
            ["docker" | "podman" | "nerdctl", "login"]
                | ["npm" | "pnpm" | "yarn", "login" | "adduser"]
                | ["gh", "auth"]
                | ["aws", "configure"]
        )
    }) {
        return false;
    }
    let sensitive_flags = [
        "--password",
        "--passwd",
        "--passphrase",
        "--token",
        "--secret",
        "--api-key",
        "--apikey",
        "--authorization",
    ];
    if values.iter().any(|value| {
        let lower = value.to_ascii_lowercase();
        sensitive_flags
            .iter()
            .any(|flag| lower == *flag || lower.starts_with(&format!("{flag}=")))
    }) {
        return false;
    }
    if command == "curl"
        && values.iter().skip(1).any(|value| {
            matches!(*value, "-u" | "--user" | "--proxy-user" | "-H" | "--header")
                || value.starts_with("--user=")
                || value.starts_with("--proxy-user=")
                || value.starts_with("--header=")
        })
    {
        return false;
    }
    if matches!(command, "mysql" | "mariadb")
        && values
            .iter()
            .skip(1)
            .any(|value| *value == "-p" || value.starts_with("-p"))
    {
        return false;
    }
    true
}

fn derive_event(line: &str, exit_code: i32, at_ms: u64) -> Option<CommandEvent> {
    let segments = split_pipeline(line);
    let primary = segments.first()?.trim();
    let parsed = parse_line(primary, primary.len())?;
    let values = parsed.command_values();
    if values.is_empty() {
        return None;
    }
    let normalized = values.join(" ");
    let elevated = parsed.tokens[..parsed.command_start]
        .iter()
        .any(|token| token.value == "sudo");
    let action = match values.as_slice() {
        [command @ ("apt" | "apt-get"), "update", ..] => Action::AptUpdate {
            command: (*command).to_owned(),
        },
        ["apt" | "apt-get", action, ..]
            if matches!(
                *action,
                "install" | "reinstall" | "remove" | "purge" | "autoremove" | "autopurge"
            ) =>
        {
            Action::AptMutation
        }
        ["docker", "ps", ..] | ["docker", "container", "ls", ..] => Action::DockerList { elevated },
        ["docker", "build", ..] => Action::DockerBuild {
            image: option_value(&values, &["-t", "--tag"]),
        },
        ["ps", ..] => Action::ProcessList,
        ["systemctl", "list-units", ..] => Action::ServiceList,
        ["systemctl", rest @ ..] => systemctl_operation(rest).unwrap_or(Action::Other),
        ["ssh-keygen", ..] => Action::SshKeygen {
            private_key: option_value(&values, &["-f"]),
        },
        ["mkdir", rest @ ..] => Action::MakeDirectory {
            paths: mkdir_paths(rest),
        },
        [python @ ("python" | "python3"), "-m", "venv", rest @ ..] if !python.is_empty() => {
            Action::PythonVenv {
                path: rest
                    .iter()
                    .rev()
                    .find(|value| !value.starts_with('-'))
                    .map(|value| (*value).to_owned())
                    .unwrap_or_default(),
            }
        }
        ["git", "clone", rest @ ..] => clone_destination(rest)
            .map(|destination| Action::GitClone { destination })
            .unwrap_or(Action::GitMutation),
        ["git", "add", ..] => Action::GitStage,
        ["git", "commit", ..] => Action::GitCommit,
        ["git", ..] => Action::GitMutation,
        _ => Action::Other,
    };
    let focus_filter = segments.get(1).and_then(|segment| grep_filter(segment));
    Some(CommandEvent {
        action,
        normalized,
        exit_code,
        at_ms,
        focus_filter,
    })
}

fn option_value(values: &[&str], names: &[&str]) -> Option<String> {
    let separate = values
        .windows(2)
        .find_map(|pair| names.contains(&pair[0]).then(|| pair[1].to_owned()));
    separate.or_else(|| {
        values.iter().find_map(|value| {
            names.iter().find_map(|name| {
                value
                    .strip_prefix(&format!("{name}="))
                    .filter(|tail| !tail.is_empty())
                    .map(str::to_owned)
            })
        })
    })
}

fn clone_destination(args: &[&str]) -> Option<String> {
    let value_options = [
        "-b",
        "--branch",
        "-c",
        "--config",
        "--depth",
        "--filter",
        "-j",
        "--jobs",
        "-o",
        "--origin",
        "--reference",
        "--reference-if-able",
        "--separate-git-dir",
        "--shallow-exclude",
        "--shallow-since",
        "--template",
        "-u",
        "--upload-pack",
    ];
    let mut positional = Vec::new();
    let mut index = 0;
    while index < args.len() {
        let value = args[index];
        if value_options.contains(&value) {
            index += 2;
        } else if value.starts_with('-') {
            index += 1;
        } else {
            positional.push(value);
            index += 1;
        }
    }
    let source = *positional.first()?;
    if let Some(destination) = positional.get(1) {
        return Some((*destination).to_owned());
    }
    let tail = source.trim_end_matches('/').rsplit('/').next()?;
    let tail = tail.rsplit(':').next().unwrap_or(tail);
    let destination = tail.strip_suffix(".git").unwrap_or(tail);
    (!destination.is_empty()).then(|| destination.to_owned())
}

fn systemctl_operation(args: &[&str]) -> Option<Action> {
    let verb_index = args.iter().position(|value| {
        matches!(
            *value,
            "start" | "restart" | "reload" | "stop" | "enable" | "disable"
        )
    })?;
    let verb = args[verb_index];
    let unit = args[verb_index + 1..]
        .iter()
        .find(|value| !value.starts_with('-'))?;
    Some(Action::SystemctlOperation {
        verb: verb.to_owned(),
        unit: (*unit).to_owned(),
    })
}

fn mkdir_paths(args: &[&str]) -> Vec<String> {
    let mut paths = Vec::new();
    let mut index = 0;
    while index < args.len() {
        match args[index] {
            "-m" | "--mode" => index += 2,
            value if value.starts_with("--mode=") || value.starts_with('-') => index += 1,
            value => {
                paths.push(value.to_owned());
                index += 1;
            }
        }
    }
    paths
}

fn split_pipeline(line: &str) -> Vec<&str> {
    let bytes = line.as_bytes();
    let mut segments = Vec::new();
    let mut start = 0;
    let mut quote = None;
    let mut escaped = false;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if escaped {
            escaped = false;
        } else if byte == b'\\' && quote != Some(b'\'') {
            escaped = true;
        } else if let Some(active) = quote {
            if byte == active {
                quote = None;
            }
        } else if byte == b'\'' || byte == b'"' {
            quote = Some(byte);
        } else if byte == b'|' && bytes.get(index + 1) != Some(&b'|') {
            segments.push(&line[start..index]);
            start = index + 1;
        }
        index += 1;
    }
    segments.push(&line[start..]);
    segments
}

fn grep_filter(segment: &str) -> Option<String> {
    let segment = segment.trim();
    let parsed = parse_line(segment, segment.len())?;
    let values = parsed.command_values();
    if values.first() != Some(&"grep") {
        return None;
    }
    values
        .iter()
        .skip(1)
        .find(|value| !value.starts_with('-'))
        .map(|value| (*value).to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observes_typed_apt_action_through_sudo() {
        let mut world = WorldState::default();
        world.observe_command("sudo apt update", 0, 100);
        assert_eq!(
            world.last_event.unwrap().action,
            Action::AptUpdate {
                command: "apt".into()
            }
        );
    }

    #[test]
    fn observes_docker_listing_and_safe_pipeline_focus() {
        let mut world = WorldState::default();
        world.observe_command("sudo docker ps | grep 'mysql db'", 0, 100);
        let event = world.last_event.unwrap();
        assert_eq!(event.action, Action::DockerList { elevated: true });
        assert_eq!(event.focus_filter.as_deref(), Some("mysql db"));
    }

    #[test]
    fn quoted_pipe_is_not_a_pipeline() {
        let mut world = WorldState::default();
        world.observe_command("printf 'a|b'", 0, 100);
        let event = world.last_event.unwrap();
        assert_eq!(event.action, Action::Other);
        assert_eq!(event.normalized, "printf a|b");
    }

    #[test]
    fn learns_successful_commands_but_never_credentials() {
        let mut world = WorldState::default();
        assert!(world
            .observe_command_with_cwd("git push origin main", 0, 100, "/repo")
            .is_some());
        assert!(world
            .observe_command_with_cwd("curl --token supersecret", 0, 101, "/repo")
            .is_none());
        assert!(world
            .observe_command_with_cwd(" git status", 0, 102, "/repo")
            .is_none());
        assert_eq!(world.history.len(), 1);
        assert_eq!(world.history[0].command, "git push origin main");
    }

    #[test]
    fn parses_artifact_actions_through_realistic_options() {
        let mut world = WorldState::default();
        world.observe_command(
            "git clone --depth 1 https://example.test/team/app.git",
            0,
            1,
        );
        assert_eq!(
            world.last_event.as_ref().unwrap().action,
            Action::GitClone {
                destination: "app".into()
            }
        );
        world.observe_command("mkdir -p -m 700 secrets cache", 0, 2);
        assert_eq!(
            world.last_event.as_ref().unwrap().action,
            Action::MakeDirectory {
                paths: vec!["secrets".into(), "cache".into()]
            }
        );
        world.observe_command("python3 -m venv --copies .venv", 0, 3);
        assert_eq!(
            world.last_event.as_ref().unwrap().action,
            Action::PythonVenv {
                path: ".venv".into()
            }
        );
        world.observe_command("systemctl --user restart pipewire.service", 0, 4);
        assert_eq!(
            world.last_event.as_ref().unwrap().action,
            Action::SystemctlOperation {
                verb: "restart".into(),
                unit: "pipewire.service".into()
            }
        );
    }

    #[test]
    fn learns_only_successful_safe_adjacent_transitions() {
        let mut world = WorldState::default();
        world.observe_command_with_cwd("cargo test", 0, 1, "/repo");
        world.observe_command_with_cwd("git status", 0, 2, "/repo");
        assert_eq!(world.transitions.len(), 1);
        assert_eq!(world.transitions[0].previous, "cargo test");
        assert_eq!(world.transitions[0].next, "git status");

        world.observe_command_with_cwd("make release", 1, 3, "/repo");
        world.observe_command_with_cwd("curl --token secret", 0, 4, "/repo");
        assert_eq!(world.transitions.len(), 1);
    }
}
