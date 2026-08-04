use std::sync::OnceLock;

#[derive(Debug)]
pub(crate) struct CommandSpec {
    pub command: &'static str,
    pub default: Option<&'static str>,
    /// Small auditable tier of human-facing actions. This ranks grammar; it
    /// never limits what the generated syntax corpus may contain.
    pub preferred: Vec<&'static str>,
    pub actions: Vec<&'static str>,
    pub options: Vec<&'static str>,
    pub positional_path: bool,
}

pub(crate) fn find(command: &str) -> Option<&'static CommandSpec> {
    find_key(command)
}

pub(crate) fn warm() {
    let _ = registry();
    let _ = path_option_registry();
}

pub(crate) fn command_names() -> impl Iterator<Item = &'static str> {
    registry()
        .iter()
        .filter(|spec| !spec.command.contains(' '))
        .map(|spec| spec.command)
}

pub(crate) fn find_nested(commands: &[&str]) -> Option<&'static CommandSpec> {
    let key = commands.join(" ");
    find_key(&key)
}

pub(crate) fn find_options(commands: &[&str]) -> Option<&'static CommandSpec> {
    (1..=commands.len())
        .rev()
        .filter_map(|length| find_nested(&commands[..length]))
        .find(|spec| !spec.options.is_empty())
}

pub(crate) fn has_actions(spec: &CommandSpec) -> bool {
    !spec.actions.is_empty() || spec.default.is_some()
}

pub(crate) fn option_takes_path(command_context: &[&str], option: &str) -> bool {
    path_option_registry().iter().any(|rule| {
        let path: Vec<&str> = rule.command.split_whitespace().collect();
        command_context.starts_with(&path) && rule.options.contains(&option)
    })
}

struct PathOptionSpec {
    command: &'static str,
    options: Vec<&'static str>,
}

fn path_option_registry() -> &'static [PathOptionSpec] {
    static REGISTRY: OnceLock<Vec<PathOptionSpec>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        include_str!("../specs/option-path-slots.tsv")
            .lines()
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .filter_map(|line| {
                let (command, options) = line.split_once('\t')?;
                Some(PathOptionSpec {
                    command,
                    options: options.split(',').collect(),
                })
            })
            .collect()
    })
}

fn find_key(key: &str) -> Option<&'static CommandSpec> {
    let specs = registry();
    specs
        .binary_search_by_key(&key, |spec| spec.command)
        .ok()
        .map(|index| &specs[index])
}

fn registry() -> &'static [CommandSpec] {
    static REGISTRY: OnceLock<Vec<CommandSpec>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let mut specs: Vec<CommandSpec> = include_str!("../specs/generated-command-tree.tsv")
            .lines()
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .filter_map(|line| {
                let mut fields = line.splitn(4, '\t');
                let command = fields.next()?;
                let encoded_actions = fields.next()?;
                let encoded_options = fields.next().unwrap_or("-");
                let positional = fields.next().unwrap_or("-");
                Some(CommandSpec {
                    command,
                    default: None,
                    preferred: Vec::new(),
                    actions: decode_actions(encoded_actions),
                    options: decode_actions(encoded_options),
                    positional_path: positional == "path",
                })
            })
            .collect();

        overlay_policy(&mut specs, include_str!("../specs/commands.tsv"));
        overlay_policy(&mut specs, include_str!("../specs/nested-subcommands.tsv"));
        overlay_paths(&mut specs, include_str!("../specs/path-slots.tsv"));
        specs.sort_unstable_by_key(|spec| spec.command);
        specs
    })
}

fn overlay_paths(specs: &mut Vec<CommandSpec>, paths: &'static str) {
    for command in paths
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
    {
        if let Some(spec) = specs.iter_mut().find(|spec| spec.command == command) {
            spec.positional_path = true;
        } else {
            specs.push(CommandSpec {
                command,
                default: None,
                preferred: Vec::new(),
                actions: Vec::new(),
                options: Vec::new(),
                positional_path: true,
            });
        }
    }
}

fn decode_actions(encoded: &'static str) -> Vec<&'static str> {
    if encoded == "-" {
        Vec::new()
    } else {
        encoded
            .split(',')
            .filter(|action| !action.is_empty())
            .collect()
    }
}

fn overlay_policy(specs: &mut Vec<CommandSpec>, policy: &'static str) {
    for line in policy
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
    {
        let mut fields = line.splitn(3, '\t');
        let Some(command) = fields.next() else {
            continue;
        };
        let Some(default) = fields.next() else {
            continue;
        };
        let Some(encoded_preferred) = fields.next() else {
            continue;
        };
        let preferred = decode_actions(encoded_preferred);
        let index = specs
            .iter()
            .position(|spec| spec.command == command)
            .unwrap_or_else(|| {
                specs.push(CommandSpec {
                    command,
                    default: None,
                    preferred: Vec::new(),
                    actions: Vec::new(),
                    options: Vec::new(),
                    positional_path: false,
                });
                specs.len() - 1
            });
        let spec = &mut specs[index];
        spec.default = (default != "-").then_some(default);
        spec.preferred = preferred.clone();
        for action in preferred {
            let destinations = if action.starts_with('-') {
                &mut spec.options
            } else {
                &mut spec.actions
            };
            if !destinations.contains(&action) {
                destinations.push(action);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_registry_is_large_sorted_unique_and_policy_defaults_are_valid() {
        let specs = registry();
        assert!(specs.len() >= 7_000);
        assert!(command_names().count() >= 700);
        for (index, spec) in specs.iter().enumerate() {
            assert!(!spec.command.is_empty());
            if index > 0 {
                assert!(specs[index - 1].command < spec.command);
            }
            if let Some(default) = spec.default {
                assert!(spec.actions.contains(&default) || spec.options.contains(&default));
                assert!(spec.preferred.contains(&default));
            }
        }
    }

    #[test]
    fn generated_tree_retains_deep_commands_and_manual_overlays() {
        let docker_builder = find_nested(&["docker", "builder"]).unwrap();
        assert!(docker_builder.actions.contains(&"prune"));
        let kubectl_create = find_nested(&["kubectl", "create"]).unwrap();
        assert!(kubectl_create.actions.contains(&"deployment"));
        let compose = find_nested(&["docker", "compose"]).unwrap();
        assert_eq!(compose.default, Some("ps"));
        assert!(find("docker").unwrap().actions.contains(&"compose"));
        assert!(find_nested(&["docker", "run"])
            .unwrap()
            .options
            .contains(&"--publish"));
        assert!(find_nested(&["git", "add"]).unwrap().positional_path);
        assert!(find("cat").unwrap().positional_path);
        assert!(find("python3").unwrap().positional_path);
        assert!(find_nested(&["docker", "build"]).unwrap().positional_path);
    }
}
