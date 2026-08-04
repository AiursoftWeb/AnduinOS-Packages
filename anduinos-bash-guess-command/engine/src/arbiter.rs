use crate::candidate::{Candidate, CandidateKind, CandidateSource, Dependency};
use crate::slot::{Slot, SlotKind};
use crate::world::WorldState;
use crate::Suggestion;

pub(crate) fn choose(
    line: &str,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    candidates: Vec<Candidate>,
) -> Option<Suggestion> {
    let mut eligible: Vec<Candidate> = candidates
        .into_iter()
        .filter(|candidate| eligible(line, slot, world, now_ms, candidate))
        .collect();
    eligible.sort_by(|left, right| score(right).total_cmp(&score(left)));
    let mut candidate = eligible.first()?.clone();

    // Equal evidence does not authorize an arbitrary choice. Extend only the
    // common prefix of tied candidates; when the user already typed it, quiet
    // is the only honest answer.
    let top_score = score(&candidate);
    let tied: Vec<&Candidate> = eligible
        .iter()
        .skip(1)
        .take_while(|other| (score(other) - top_score).abs() < f32::EPSILON)
        .collect();
    if !tied.is_empty() {
        let common = tied
            .iter()
            .fold(candidate.resulting_line.clone(), |prefix, other| {
                common_prefix(prefix, &other.resulting_line)
            });
        if common == line {
            // A small grammar-only ambiguity is still useful as quiet ghost
            // text. Carapace's stable specification order breaks the tie;
            // broad ambiguities remain silent.
            let grammar_only = candidate.kind != CandidateKind::Command
                && candidate.source == CandidateSource::Grammar
                && tied
                    .iter()
                    .all(|other| other.source == CandidateSource::Grammar);
            if !grammar_only || tied.len() + 1 > 3 {
                return None;
            }
        } else {
            candidate.resulting_line = common;
        }
    }

    Some(Suggestion {
        insertion: candidate.resulting_line[line.len()..].to_owned(),
        candidate,
    })
}

fn common_prefix(mut left: String, right: &str) -> String {
    while !right.starts_with(&left) {
        if left.pop().is_none() {
            break;
        }
    }
    left
}

fn eligible(
    line: &str,
    slot: &Slot,
    world: &WorldState,
    now_ms: u64,
    candidate: &Candidate,
) -> bool {
    if !slot.allows(candidate.kind)
        || !candidate.resulting_line.starts_with(line)
        || candidate.resulting_line == line
    {
        return false;
    }
    if candidate
        .resulting_line
        .bytes()
        .any(|byte| byte == 0 || byte == b'\n' || byte == b'\r' || byte < 0x20)
    {
        return false;
    }
    if candidate
        .expires_at_ms
        .is_some_and(|expires| now_ms > expires)
    {
        return false;
    }
    if candidate
        .dependencies
        .iter()
        .any(|dependency| match dependency {
            Dependency::DockerGeneration(generation) => *generation != world.docker.generation,
            Dependency::AptGeneration(generation) => *generation != world.apt.generation,
            Dependency::ProcessGeneration(generation) => *generation != world.processes.generation,
            Dependency::ServiceGeneration(generation) => *generation != world.services.generation,
            Dependency::GitGeneration(generation) => *generation != world.git.generation,
            Dependency::HostGeneration(generation) => *generation != world.hosts.generation,
            Dependency::FileGeneration(generation) => *generation != world.files.generation,
            Dependency::ArtifactGeneration(generation) => *generation != world.artifacts.generation,
        })
    {
        return false;
    }
    if slot.kind == SlotKind::DockerContainer && candidate.kind == CandidateKind::Path {
        return false;
    }
    true
}

fn score(candidate: &Candidate) -> f32 {
    let source_bonus = match candidate.source {
        CandidateSource::LiveEntity => 0.08,
        CandidateSource::Workflow => 0.06,
        CandidateSource::Transition => 0.04,
        CandidateSource::Grammar => 0.0,
        CandidateSource::Personal => -0.03,
        CandidateSource::Recovery => 0.02,
        CandidateSource::Filesystem => -0.08,
    };
    candidate.confidence + source_bonus
}
