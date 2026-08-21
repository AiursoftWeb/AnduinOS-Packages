use anduinos_quiet_engine::{suggest, Query, WorldState};
use std::collections::{BTreeMap, BTreeSet};

fn effective_grammar() -> BTreeMap<String, BTreeSet<String>> {
    let mut grammar: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for line in include_str!("../specs/generated-command-tree.tsv").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.splitn(4, '\t');
        let command = fields.next().unwrap();
        let encoded_actions = fields.next().unwrap();
        let actions = grammar.entry(command.to_owned()).or_default();
        if encoded_actions != "-" {
            actions.extend(encoded_actions.split(',').map(str::to_owned));
        }
    }
    for policy in [
        include_str!("../specs/commands.tsv"),
        include_str!("../specs/nested-subcommands.tsv"),
    ] {
        for line in policy.lines() {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let mut fields = line.splitn(3, '\t');
            let command = fields.next().unwrap();
            let _default = fields.next().unwrap();
            let encoded_actions = fields.next().unwrap();
            if encoded_actions != "-" {
                grammar.entry(command.to_owned()).or_default().extend(
                    encoded_actions
                        .split(',')
                        .filter(|action| !action.starts_with('-'))
                        .map(str::to_owned),
                );
            }
        }
    }
    grammar
}

fn generated_options() -> BTreeMap<String, BTreeSet<String>> {
    let mut grammar: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for line in include_str!("../specs/generated-command-tree.tsv").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.splitn(4, '\t');
        let command = fields.next().unwrap();
        let _actions = fields.next().unwrap();
        let encoded_options = fields.next().unwrap();
        if encoded_options != "-" {
            grammar
                .entry(command.to_owned())
                .or_default()
                .extend(encoded_options.split(',').map(str::to_owned));
        }
    }
    for policy in [
        include_str!("../specs/commands.tsv"),
        include_str!("../specs/nested-subcommands.tsv"),
    ] {
        for line in policy.lines() {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let mut fields = line.splitn(3, '\t');
            let command = fields.next().unwrap();
            let _default = fields.next().unwrap();
            let encoded_preferred = fields.next().unwrap();
            grammar.entry(command.to_owned()).or_default().extend(
                encoded_preferred
                    .split(',')
                    .filter(|action| action.starts_with('-'))
                    .map(str::to_owned),
            );
        }
    }
    grammar
}

#[test]
fn every_generated_grammar_node_has_a_working_unique_prefix_contract() {
    let mut checked = 0usize;
    for (command, action_set) in effective_grammar() {
        let actions: Vec<&str> = action_set.iter().map(String::as_str).collect();
        for action in &actions {
            let unique_prefix = (1..action.len()).find_map(|length| {
                let prefix = action.get(..length)?;
                (actions
                    .iter()
                    .filter(|candidate| candidate.starts_with(prefix))
                    .count()
                    == 1)
                    .then_some(prefix)
            });
            let Some(prefix) = unique_prefix else {
                continue;
            };
            let input = format!("{command} {prefix}");
            let suggestion = suggest(
                Query {
                    line: &input,
                    cursor: input.len(),
                    now_ms: 0,
                },
                &WorldState::default(),
            )
            .unwrap_or_else(|| panic!("generated grammar was silent for {input:?}"));
            assert_eq!(
                suggestion.candidate.resulting_line,
                format!("{command} {action}"),
                "wrong generated completion for {input:?}"
            );
            checked += 1;
        }
    }
    assert!(
        checked >= 6_500,
        "generated contract corpus unexpectedly shrank: {checked}"
    );
}

#[test]
fn every_generated_option_with_a_unique_prefix_reaches_the_runtime() {
    let mut checked = 0usize;
    for (command, option_set) in generated_options() {
        let options: Vec<&str> = option_set.iter().map(String::as_str).collect();
        for option in &options {
            let unique_prefix = (2..option.len()).find_map(|length| {
                let prefix = option.get(..length)?;
                (options
                    .iter()
                    .filter(|candidate| candidate.starts_with(prefix))
                    .count()
                    == 1)
                    .then_some(prefix)
            });
            let Some(prefix) = unique_prefix else {
                continue;
            };
            let input = format!("{command} {prefix}");
            let suggestion = suggest(
                Query {
                    line: &input,
                    cursor: input.len(),
                    now_ms: 0,
                },
                &WorldState::default(),
            )
            .unwrap_or_else(|| panic!("generated option grammar was silent for {input:?}"));
            assert_eq!(
                suggestion.candidate.resulting_line,
                format!("{command} {option}"),
                "wrong generated option completion for {input:?}"
            );
            checked += 1;
        }
    }
    assert!(
        checked >= 20_000,
        "generated option contract corpus unexpectedly shrank: {checked}"
    );
}

#[test]
fn generated_grammar_contains_no_build_host_entities() {
    let mut roots = 0usize;
    let mut nodes = 0usize;
    let mut path_slots = 0usize;
    for line in include_str!("../specs/generated-command-tree.tsv").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.splitn(4, '\t');
        let command = fields.next().unwrap();
        let encoded_actions = fields.next().unwrap();
        let encoded_options = fields.next().unwrap();
        let positional = fields.next().unwrap();
        nodes += 1;
        roots += usize::from(!command.contains(' '));
        path_slots += usize::from(positional == "path");
        assert!(matches!(positional, "path" | "-"));
        if encoded_actions != "-" {
            for action in encoded_actions.split(',') {
                assert!(
                    !action.ends_with('/'),
                    "directory leaked into grammar: {action}"
                );
                assert!(
                    !action.ends_with('@'),
                    "account leaked into grammar: {action}"
                );
                assert!(!action.contains(char::is_whitespace));
                assert!(!action.contains(char::is_control));
            }
        }
        if encoded_options != "-" {
            for option in encoded_options.split(',') {
                assert!(option.starts_with('-'));
                assert!(!option.contains(char::is_whitespace));
                assert!(!option.contains(char::is_control));
            }
        }
    }
    assert!(
        roots >= 700,
        "generated root command corpus unexpectedly shrank"
    );
    assert!(nodes >= 7_000, "generated command tree unexpectedly shrank");
    assert!(
        path_slots >= 1_900,
        "generated positional path corpus unexpectedly shrank"
    );
}
