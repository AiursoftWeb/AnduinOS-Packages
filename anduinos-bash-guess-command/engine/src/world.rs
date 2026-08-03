use crate::shell::parse_line;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    AptUpdate { command: String },
    DockerList { elevated: bool },
    ProcessList,
    ServiceList,
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
pub struct HistoryEntry {
    pub command: String,
    pub cwd: String,
    pub count: u32,
    pub last_used_ms: u64,
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
pub struct FileSnapshot {
    pub generation: u64,
    pub refreshed_at_ms: u64,
    pub cwd: String,
    pub entries: Vec<FileEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct WorldState {
    pub last_event: Option<CommandEvent>,
    pub current_cwd: String,
    pub history: Vec<HistoryEntry>,
    pub files: FileSnapshot,
    pub docker: DockerSnapshot,
    pub apt: AptSnapshot,
    pub processes: ProcessSnapshot,
    pub services: ServiceSnapshot,
    pub git: GitSnapshot,
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
    ) -> Option<HistoryEntry> {
        self.current_cwd = cwd.to_owned();
        self.last_event = derive_event(line, exit_code, at_ms);
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
        Some(HistoryEntry {
            command,
            cwd: cwd.to_owned(),
            count: 1,
            last_used_ms: at_ms,
        })
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
    !parsed.tokens[..parsed.command_start]
        .iter()
        .any(|token| token.value.contains('='))
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
        ["docker", "ps", ..] | ["docker", "container", "ls", ..] => Action::DockerList { elevated },
        ["ps", ..] => Action::ProcessList,
        ["systemctl", "list-units", ..] => Action::ServiceList,
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
}
