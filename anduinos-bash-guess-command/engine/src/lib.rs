//! A deterministic, side-effect-free intent engine.
//!
//! The foreground query path deliberately has no process, filesystem or
//! network APIs. Callers provide a world snapshot that a background observer
//! may refresh between prompts.

mod arbiter;
mod candidate;
mod domains;
mod history;
pub mod protocol;
pub mod runtime;
mod shell;
mod slot;
mod specs;
mod world;

pub use candidate::{Candidate, CandidateKind, CandidateSource, Dependency, Evidence, Risk};
pub use shell::{parse_line, ParsedLine, Token};
pub use slot::{classify_slot, Slot, SlotKind};
pub use world::{
    Action, Artifact, ArtifactKind, CommandEvent, Container, FileEntry, GitRef, HistoryEntry, Host,
    Process, Service, TransitionEntry, WorldState,
};

use arbiter::choose;
use domains::generate;

#[derive(Debug, Clone, PartialEq)]
pub struct Query<'a> {
    pub line: &'a str,
    pub cursor: usize,
    pub now_ms: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Suggestion {
    /// Bytes appended at the cursor. The engine never replaces typed input.
    pub insertion: String,
    pub candidate: Candidate,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    /// Authoritative slots must not fall through to unrelated providers even
    /// when the correct result is silence.
    pub authoritative: bool,
    pub suggestion: Option<Suggestion>,
}

/// Produces at most one append-only suggestion from an immutable snapshot.
pub fn suggest(query: Query<'_>, world: &WorldState) -> Option<Suggestion> {
    evaluate(query, world).suggestion
}

pub fn evaluate(query: Query<'_>, world: &WorldState) -> Decision {
    if query.cursor != query.line.len() || !query.line.is_char_boundary(query.cursor) {
        return Decision {
            authoritative: false,
            suggestion: None,
        };
    }
    let Some(parsed) = parse_line(query.line, query.cursor) else {
        return Decision {
            authoritative: false,
            suggestion: None,
        };
    };
    let slot = classify_slot(&parsed);
    let candidates = generate(&parsed, &slot, world, query.now_ms);
    Decision {
        authoritative: slot.authoritative,
        suggestion: choose(query.line, &slot, world, query.now_ms, candidates),
    }
}
