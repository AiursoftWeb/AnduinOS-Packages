use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::browsing::{BrowserEntry, DirectoryListing, EntryKind};

pub const MAX_SEARCHED_ITEMS: u64 = 100_000;
pub const MAX_SEARCH_RESULTS: usize = 1_000;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotSearchHit {
    pub parent_tokens: Vec<String>,
    pub parent_names: Vec<String>,
    pub entry: BrowserEntry,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotSearchReport {
    pub hits: Vec<SnapshotSearchHit>,
    pub inspected_items: u64,
    pub complete: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SnapshotSearchError {
    Cancelled,
    InvalidQuery,
    Failed(String),
}

pub fn search_snapshot<L>(
    root_tokens: &[String],
    root_names: &[String],
    query: &str,
    show_hidden: bool,
    cancelled: &AtomicBool,
    mut list_directory: L,
) -> Result<SnapshotSearchReport, SnapshotSearchError>
where
    L: FnMut(&[String]) -> Result<DirectoryListing, String>,
{
    let query = query.trim();
    let query_length = query.chars().count();
    if !(2..=128).contains(&query_length) || query.chars().any(char::is_control) {
        return Err(SnapshotSearchError::InvalidQuery);
    }
    let query = query.to_lowercase();
    let mut pending = VecDeque::from([(root_tokens.to_vec(), root_names.to_vec())]);
    let mut hits = Vec::new();
    let mut inspected_items = 0u64;
    let mut complete = true;

    while let Some((parent_tokens, parent_names)) = pending.pop_front() {
        if cancelled.load(Ordering::Acquire) {
            return Err(SnapshotSearchError::Cancelled);
        }
        let listing = list_directory(&parent_tokens).map_err(SnapshotSearchError::Failed)?;
        complete &= !listing.truncated;

        for entry in listing.entries {
            if cancelled.load(Ordering::Acquire) {
                return Err(SnapshotSearchError::Cancelled);
            }
            inspected_items += 1;
            if inspected_items > MAX_SEARCHED_ITEMS {
                complete = false;
                break;
            }
            if entry.hidden && !show_hidden {
                continue;
            }
            if entry.display_name.to_lowercase().contains(&query) {
                hits.push(SnapshotSearchHit {
                    parent_tokens: parent_tokens.clone(),
                    parent_names: parent_names.clone(),
                    entry: entry.clone(),
                });
                if hits.len() == MAX_SEARCH_RESULTS {
                    complete = false;
                    break;
                }
            }
            if entry.kind == EntryKind::Directory {
                let mut child_tokens = parent_tokens.clone();
                child_tokens.push(entry.token.clone());
                let mut child_names = parent_names.clone();
                child_names.push(entry.display_name);
                pending.push_back((child_tokens, child_names));
            }
        }
        if !complete && (inspected_items > MAX_SEARCHED_ITEMS || hits.len() == MAX_SEARCH_RESULTS) {
            break;
        }
    }

    Ok(SnapshotSearchReport {
        hits,
        inspected_items: inspected_items.min(MAX_SEARCHED_ITEMS),
        complete,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, kind: EntryKind, hidden: bool) -> BrowserEntry {
        BrowserEntry {
            token: format!("token-{name}"),
            display_name: name.to_string(),
            kind,
            size: 0,
            modified_unix: 0,
            mode: 0o644,
            hidden,
        }
    }

    fn listing(path: &[String], entries: Vec<BrowserEntry>) -> DirectoryListing {
        DirectoryListing {
            path: path.to_vec(),
            total_entries: entries.len(),
            entries,
            next_offset: None,
            truncated: false,
        }
    }

    #[test]
    fn recursively_finds_names_and_preserves_their_parent_path() {
        let cancelled = AtomicBool::new(false);
        let report = search_snapshot(&[], &[], "报告", false, &cancelled, |path| {
            Ok(if path.is_empty() {
                listing(path, vec![entry("文档", EntryKind::Directory, false)])
            } else {
                listing(path, vec![entry("季度报告.txt", EntryKind::File, false)])
            })
        })
        .unwrap();

        assert!(report.complete);
        assert_eq!(report.inspected_items, 2);
        assert_eq!(report.hits.len(), 1);
        assert_eq!(report.hits[0].parent_names, ["文档"]);
        assert_eq!(report.hits[0].parent_tokens, ["token-文档"]);
    }

    #[test]
    fn hidden_directories_are_not_searched_unless_requested() {
        for (show_hidden, expected_calls, expected_hits) in [(false, 1, 0), (true, 2, 1)] {
            let cancelled = AtomicBool::new(false);
            let mut calls = 0;
            let report = search_snapshot(&[], &[], "secret", show_hidden, &cancelled, |path| {
                calls += 1;
                Ok(if path.is_empty() {
                    listing(path, vec![entry(".private", EntryKind::Directory, true)])
                } else {
                    listing(path, vec![entry("secret.txt", EntryKind::File, false)])
                })
            })
            .unwrap();
            assert_eq!(calls, expected_calls);
            assert_eq!(report.hits.len(), expected_hits);
        }
    }

    #[test]
    fn validates_and_cancels_queries() {
        let cancelled = AtomicBool::new(false);
        assert_eq!(
            search_snapshot(&[], &[], "x", false, &cancelled, |_| unreachable!()),
            Err(SnapshotSearchError::InvalidQuery)
        );
        cancelled.store(true, Ordering::Release);
        assert_eq!(
            search_snapshot(&[], &[], "valid", false, &cancelled, |_| unreachable!()),
            Err(SnapshotSearchError::Cancelled)
        );
    }

    #[test]
    fn caps_the_number_of_results() {
        let cancelled = AtomicBool::new(false);
        let entries = (0..MAX_SEARCH_RESULTS + 10)
            .map(|index| entry(&format!("match-{index}"), EntryKind::File, false))
            .collect::<Vec<_>>();
        let report = search_snapshot(&[], &[], "match", false, &cancelled, |path| {
            Ok(listing(path, entries.clone()))
        })
        .unwrap();
        assert_eq!(report.hits.len(), MAX_SEARCH_RESULTS);
        assert!(!report.complete);
    }
}
