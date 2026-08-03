use std::sync::OnceLock;

#[derive(Debug)]
pub(crate) struct CommandSpec {
    pub command: &'static str,
    pub default: Option<&'static str>,
    /// Small auditable tier of human-facing actions. This ranks grammar; it
    /// never defines which actions are syntactically valid.
    pub preferred: Vec<&'static str>,
    pub actions: Vec<&'static str>,
}

pub(crate) fn find(command: &str) -> Option<&'static CommandSpec> {
    registry().iter().find(|spec| spec.command == command)
}

fn registry() -> &'static [CommandSpec] {
    static REGISTRY: OnceLock<Vec<CommandSpec>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let generated = include_str!("../specs/generated-subcommands.tsv");
        include_str!("../specs/commands.tsv")
            .lines()
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .filter_map(|line| {
                let mut fields = line.splitn(3, '\t');
                let command = fields.next()?;
                let default = fields.next()?;
                let preferred_actions = fields.next()?;
                let generated_actions = generated
                    .lines()
                    .filter(|line| !line.is_empty() && !line.starts_with('#'))
                    .find_map(|line| {
                        let (generated_command, actions) = line.split_once('\t')?;
                        (generated_command == command).then_some(actions)
                    });
                Some(CommandSpec {
                    command,
                    default: (default != "-").then_some(default),
                    preferred: if preferred_actions == "-" {
                        Vec::new()
                    } else {
                        preferred_actions.split(',').collect()
                    },
                    actions: generated_actions
                        .unwrap_or(preferred_actions)
                        .split(',')
                        .collect(),
                })
            })
            .collect()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_has_unique_commands_and_valid_defaults() {
        let specs = registry();
        assert!(specs.len() >= 35);
        for (index, spec) in specs.iter().enumerate() {
            assert!(!spec.command.is_empty());
            assert!(!spec.actions.is_empty());
            assert!(!specs[..index]
                .iter()
                .any(|other| other.command == spec.command));
            if let Some(default) = spec.default {
                assert!(spec.actions.contains(&default));
                assert!(spec.preferred.contains(&default));
            }
        }
    }
}
