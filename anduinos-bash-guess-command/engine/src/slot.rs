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
    Host,
    GitCleanOption,
    Option,
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
    if values[0] == "dd" && !parsed.trailing_space {
        if let Some((name, path)) = parsed.current_prefix.split_once('=') {
            if matches!(name, "if" | "of") {
                return slot(
                    SlotKind::Path,
                    path.to_owned(),
                    token_start + name.len() + 1,
                    &[CandidateKind::Path],
                    true,
                );
            }
        }
    }
    if (values[0] == "apt" || values[0] == "apt-get")
        && (values.len() == 1 || (values.len() == 2 && !parsed.trailing_space))
        && !prefix.starts_with('-')
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
    if ((values[0] == "ssh" && ssh_host_position(&values[1..], parsed.trailing_space, false))
        || (values[0] == "ssh-copy-id"
            && ssh_host_position(&values[1..], parsed.trailing_space, true)))
        && !prefix.contains(':')
    {
        return slot(
            SlotKind::Host,
            prefix,
            token_start,
            &[CandidateKind::Host],
            true,
        );
    }
    if values.starts_with(&["git", "clean"]) && (parsed.trailing_space || prefix == "-") {
        return slot(
            SlotKind::GitCleanOption,
            prefix,
            token_start,
            &[CandidateKind::Option, CandidateKind::Command],
            true,
        );
    }
    let grammar_base = if parsed.trailing_space {
        values.as_slice()
    } else {
        &values[..values.len().saturating_sub(1)]
    };
    if let Some((path_prefix, path_start)) =
        option_path_position(&values, parsed.trailing_space, &prefix, token_start)
    {
        return slot(
            SlotKind::Path,
            path_prefix,
            path_start,
            &[CandidateKind::Path, CandidateKind::Command],
            false,
        );
    }
    if !parsed.trailing_space
        && prefix.starts_with('-')
        && !prefix.contains('=')
        && !grammar_base.is_empty()
        && specs::find_options(grammar_base).is_some()
    {
        return slot(
            SlotKind::Option,
            prefix,
            token_start,
            &[CandidateKind::Option, CandidateKind::Command],
            true,
        );
    }
    if !grammar_base.is_empty()
        && !prefix.starts_with('-')
        && specs::find_nested(grammar_base).is_some_and(|spec| {
            spec.positional_path
                && (prefix.is_empty()
                    || !spec
                        .actions
                        .iter()
                        .any(|action| action.starts_with(&prefix)))
        })
    {
        return slot(
            SlotKind::Path,
            prefix,
            token_start,
            &[CandidateKind::Path, CandidateKind::Command],
            false,
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
    let nested_base = if parsed.trailing_space {
        values.as_slice()
    } else {
        &values[..values.len().saturating_sub(1)]
    };
    if nested_base.len() >= 2 && specs::find_nested(nested_base).is_some_and(specs::has_actions) {
        return slot(
            SlotKind::Subcommand,
            if parsed.trailing_space {
                String::new()
            } else {
                prefix
            },
            token_start,
            &[CandidateKind::Subcommand],
            true,
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
    specs::find(command).is_some_and(specs::has_actions)
}

fn path_position(values: &[&str], trailing_space: bool) -> bool {
    let Some(command) = values.first() else {
        return false;
    };
    if *command == "ssh-copy-id" {
        return (trailing_space && values.last() == Some(&"-i"))
            || (!trailing_space
                && values.len() >= 3
                && values.get(values.len() - 2) == Some(&"-i"));
    }
    if !matches!(
        *command,
        "cd" | "ls"
            | "tree"
            | "du"
            | "cat"
            | "less"
            | "head"
            | "tail"
            | "wc"
            | "realpath"
            | "readlink"
            | "basename"
            | "dirname"
            | "md5sum"
            | "sha1sum"
            | "sha256sum"
            | "sha512sum"
            | "cksum"
            | "diff"
            | "cmp"
            | "comm"
            | "source"
            | "."
            | "vim"
            | "nvim"
            | "nano"
            | "code"
            | "mkdir"
            | "touch"
            | "rm"
            | "rmdir"
            | "cp"
            | "mv"
            | "ln"
            | "chmod"
            | "chown"
            | "stat"
            | "file"
    ) {
        return false;
    }
    let args = &values[1..];
    if args.is_empty() {
        return trailing_space;
    }
    if !trailing_space {
        return args.last().is_some_and(|value| !value.starts_with('-'));
    }
    !matches!(args.last(), Some(&("-n" | "--lines" | "-c" | "--bytes")))
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

fn option_path_position(
    values: &[&str],
    trailing_space: bool,
    prefix: &str,
    token_start: usize,
) -> Option<(String, usize)> {
    if trailing_space {
        let (option, context) = values.split_last()?;
        return specs::option_takes_path(context, option).then(|| (String::new(), token_start));
    }
    if let Some((option, path)) = prefix.split_once('=') {
        let context = &values[..values.len().saturating_sub(1)];
        return specs::option_takes_path(context, option)
            .then(|| (path.to_owned(), token_start + option.len() + 1));
    }
    if values.len() >= 2 {
        let option = values[values.len() - 2];
        let context = &values[..values.len() - 2];
        return specs::option_takes_path(context, option).then(|| (prefix.to_owned(), token_start));
    }
    None
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

fn ssh_host_position(args: &[&str], trailing_space: bool, copy_id: bool) -> bool {
    let value_options: &[&str] = if copy_id {
        &["-F", "-i", "-o", "-p"]
    } else {
        &[
            "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o",
            "-p", "-Q", "-R", "-S", "-W", "-w",
        ]
    };
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
        assert_eq!(kind("ssh -p 2222 prod"), SlotKind::Host);
        assert_eq!(kind("ssh-copy-id -i ~/.ssh/id.pub prod"), SlotKind::Host);
    }

    #[test]
    fn identifies_static_subcommand_slots() {
        assert_eq!(kind("sudo docker "), SlotKind::Subcommand);
        assert_eq!(kind("sudo git"), SlotKind::Subcommand);
        assert_eq!(kind("git st"), SlotKind::Subcommand);
        assert_eq!(kind("docker compose "), SlotKind::Subcommand);
        assert_eq!(kind("git remote g"), SlotKind::Subcommand);
        assert_eq!(kind("ls -ashl ./de"), SlotKind::Path);
        assert_eq!(kind("sha256sum ./ima"), SlotKind::Path);
    }
}
