use anduinos_quiet_engine::{suggest, Query, WorldState};

#[test]
fn every_generated_subcommand_has_a_working_unique_prefix_contract() {
    let mut checked = 0usize;
    for line in include_str!("../specs/generated-subcommands.tsv").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (command, encoded_actions) = line.split_once('\t').unwrap();
        let actions: Vec<&str> = encoded_actions.split(',').collect();
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
        checked >= 500,
        "generated contract corpus unexpectedly shrank"
    );
}

#[test]
fn generated_grammar_contains_no_build_host_entities() {
    for line in include_str!("../specs/generated-subcommands.tsv").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (_, encoded_actions) = line.split_once('\t').unwrap();
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
}
