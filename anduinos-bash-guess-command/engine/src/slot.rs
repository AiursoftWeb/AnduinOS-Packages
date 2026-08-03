use crate::candidate::CandidateKind;
use crate::shell::ParsedLine;
use crate::specs;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SlotKind {
    Unknown,
    Command,
    Subcommand,
    AptAction,
    DockerContainer,
    Process,
    Service,
    GitRef,
    GitCleanOption,
    Path,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Slot {
    pub kind: SlotKind,
    pub prefix: String,
    pub token_start: usize,
    pub allowed: Vec<CandidateKind>,
    pub authoritative: bool,
}

impl Slot {
    pub fn allows(&self, kind: CandidateKind) -> bool {
        self.allowed.contains(&kind)
    }
}

pub fn classify_slot(parsed: &ParsedLine) -> Slot {
    let values = parsed.command_values();
    let prefix = parsed.current_prefix.clone();
    let token_start = if parsed.trailing_space {
        parsed.source.len()
    } else {
        parsed
            .tokens
            .last()
            .map(|token| token.start)
            .unwrap_or(parsed.source.len())
    };

    if values.is_empty() {
        return slot(
            SlotKind::Command,
            prefix,
            token_start,
            &[CandidateKind::Command],
            false,
        );
    }
    if (values[0] == "apt" || values[0] == "apt-get")
        && (values.len() == 1 || (values.len() == 2 && !parsed.trailing_space))
    {
        return slot(
            SlotKind::AptAction,
            prefix,
            token_start,
            &[CandidateKind::Subcommand, CandidateKind::Workflow],
            true,
        );
    }
    if values[0] == "docker" {
        if let Some(slot) = docker_slot(parsed, &values, token_start) {
            return slot;
        }
    }
    if values[0] == "kill"
        && positional_slot(
            &values[1..],
            parsed.trailing_space,
            &["-s", "--signal", "--timeout"],
        )
    {
        return slot(
            SlotKind::Process,
            prefix,
            token_start,
            &[CandidateKind::Process],
            true,
        );
    }
    if values[0] == "systemctl" && systemctl_entity_position(&values[1..], parsed.trailing_space) {
        return slot(
            SlotKind::Service,
            prefix,
            token_start,
            &[CandidateKind::Service],
            true,
        );
    }
    if values[0] == "git" && git_ref_position(&values[1..], parsed.trailing_space) {
        return slot(
            SlotKind::GitRef,
            prefix,
            token_start,
            &[CandidateKind::GitRef],
            true,
        );
    }
    if values.starts_with(&["git", "clean"]) && (parsed.trailing_space || prefix.starts_with('-')) {
        return slot(
            SlotKind::GitCleanOption,
            prefix,
            token_start,
            &[CandidateKind::Option],
            true,
        );
    }
    if path_position(&values, parsed.trailing_space) {
        return slot(
            SlotKind::Path,
            prefix,
            token_start,
            &[CandidateKind::Path, CandidateKind::Command],
            false,
        );
    }
    if grammar_command(values[0])
        && (values.len() == 1 || (values.len() == 2 && !parsed.trailing_space))
    {
        return slot(
            SlotKind::Subcommand,
            if values.len() == 1 {
                String::new()
            } else {
                prefix
            },
            if values.len() == 1 {
                parsed.source.len()
            } else {
                token_start
            },
            &[CandidateKind::Subcommand],
            true,
        );
    }

    slot(
        SlotKind::Unknown,
        prefix,
        token_start,
        &[CandidateKind::Command],
        false,
    )
}

fn grammar_command(command: &str) -> bool {
    specs::find(command).is_some()
}

fn path_position(values: &[&str], trailing_space: bool) -> bool {
    let Some(command) = values.first() else {
        return false;
    };
    if !matches!(*command, "cd" | "cat" | "less" | "head" | "tail") {
        return false;
    }
    let args = &values[1..];
    if args.is_empty() {
        return trailing_space;
    }
    args.len() == 1 && !trailing_space && !args[0].starts_with('-')
}

fn positional_slot(args: &[&str], trailing_space: bool, value_options: &[&str]) -> bool {
    let mut index = 0;
    while index < args.len() {
        let value = args[index];
        if index + 1 == args.len() && !trailing_space && !value.starts_with('-') {
            return true;
        }
        if value_options.contains(&value) {
            index += 2;
        } else if value.starts_with('-') {
            index += 1;
        } else {
            return false;
        }
    }
    trailing_space
}

fn systemctl_entity_position(args: &[&str], trailing_space: bool) -> bool {
    let mut index = 0;
    while args.get(index).is_some_and(|value| value.starts_with('-')) {
        index += 1;
    }
    matches!(
        args.get(index),
        Some(&("status" | "start" | "restart" | "reload" | "stop" | "enable" | "disable"))
    ) && positional_slot(&args[index + 1..], trailing_space, &[])
}

fn git_ref_position(args: &[&str], trailing_space: bool) -> bool {
    let Some(verb) = args.first() else {
        return false;
    };
    matches!(*verb, "switch" | "checkout" | "merge" | "rebase")
        && positional_slot(
            &args[1..],
            trailing_space,
            &["-b", "-B", "-c", "-C", "--track"],
        )
}

fn docker_slot(parsed: &ParsedLine, values: &[&str], token_start: usize) -> Option<Slot> {
    let subcommand_index = if values.get(1) == Some(&"container") {
        2
    } else {
        1
    };
    let subcommand = *values.get(subcommand_index)?;
    if subcommand != "exec" && subcommand != "logs" {
        return None;
    }
    let args = &values[subcommand_index + 1..];
    let completed_boolean_option = !parsed.trailing_space
        && args.last().is_some_and(|value| match subcommand {
            "exec" => matches!(
                *value,
                "-d" | "-i"
                    | "-t"
                    | "-it"
                    | "-ti"
                    | "--detach"
                    | "--interactive"
                    | "--tty"
                    | "--privileged"
            ),
            "logs" => matches!(
                *value,
                "-f" | "-t" | "--follow" | "--details" | "--timestamps"
            ),
            _ => false,
        });
    if completed_boolean_option {
        return Some(slot(
            SlotKind::DockerContainer,
            String::new(),
            parsed.source.len(),
            &[CandidateKind::Container],
            true,
        ));
    }
    if docker_container_position(subcommand, args, parsed.trailing_space) {
        Some(slot(
            SlotKind::DockerContainer,
            parsed.current_prefix.clone(),
            token_start,
            &[CandidateKind::Container],
            true,
        ))
    } else {
        None
    }
}

fn docker_container_position(subcommand: &str, args: &[&str], trailing_space: bool) -> bool {
    let mut index = 0;
    while index < args.len() {
        let value = args[index];
        let is_current = index + 1 == args.len() && !trailing_space;
        if is_current && !value.starts_with('-') {
            return true;
        }
        let consumes_value = matches!(
            (subcommand, value),
            (
                "exec",
                "-e" | "-u"
                    | "-w"
                    | "--env"
                    | "--env-file"
                    | "--user"
                    | "--workdir"
                    | "--detach-keys"
            ) | ("logs", "--since" | "--tail" | "--until" | "-n")
        );
        if consumes_value {
            index += 2;
            continue;
        }
        if value.starts_with('-') {
            index += 1;
            continue;
        }
        return false;
    }
    trailing_space
}

fn slot(
    kind: SlotKind,
    prefix: String,
    token_start: usize,
    allowed: &[CandidateKind],
    authoritative: bool,
) -> Slot {
    Slot {
        kind,
        prefix,
        token_start,
        allowed: allowed.to_vec(),
        authoritative,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse_line;

    fn kind(line: &str) -> SlotKind {
        classify_slot(&parse_line(line, line.len()).unwrap()).kind
    }

    #[test]
    fn identifies_docker_entity_after_flags() {
        assert_eq!(kind("sudo docker exec -it "), SlotKind::DockerContainer);
        assert_eq!(
            kind("docker logs --since 10m -f "),
            SlotKind::DockerContainer
        );
        assert_eq!(kind("docker logs -f stoic"), SlotKind::DockerContainer);
        assert_eq!(kind("docker logs -f"), SlotKind::DockerContainer);
        assert_eq!(kind("docker exec -u root "), SlotKind::DockerContainer);
    }

    #[test]
    fn entity_slot_ends_after_entity() {
        assert_eq!(kind("docker exec -it stoic bash"), SlotKind::Unknown);
    }

    #[test]
    fn identifies_process_service_and_git_slots() {
        assert_eq!(kind("sudo kill "), SlotKind::Process);
        assert_eq!(kind("kill -s TERM 42"), SlotKind::Process);
        assert_eq!(kind("systemctl --user restart dock"), SlotKind::Service);
        assert_eq!(kind("git switch fea"), SlotKind::GitRef);
        assert_eq!(kind("git merge main "), SlotKind::Unknown);
    }

    #[test]
    fn identifies_static_subcommand_slots() {
        assert_eq!(kind("sudo docker "), SlotKind::Subcommand);
        assert_eq!(kind("sudo git"), SlotKind::Subcommand);
        assert_eq!(kind("git st"), SlotKind::Subcommand);
    }
}
