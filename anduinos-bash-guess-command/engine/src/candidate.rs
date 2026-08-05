#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CandidateKind {
    Command,
    Subcommand,
    Option,
    Container,
    GitRef,
    Service,
    Process,
    Host,
    Path,
    Workflow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CandidateSource {
    Grammar,
    Executable,
    LiveEntity,
    Workflow,
    Transition,
    Personal,
    Recovery,
    Filesystem,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Risk {
    Safe,
    Moderate,
    Dangerous,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Evidence {
    GrammarMatch,
    Executable { generation: u64 },
    LiveEntity { generation: u64 },
    PreviousCommand(&'static str),
    SuccessfulExit,
    UniqueMatch,
    RecentListing,
    FilterMatch(String),
    UpgradesAvailable(u32),
    DryRunGuard,
    PersonalFrequency(u32),
    TransitionFrequency(u32),
    SameDirectory,
    ProducedArtifact,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Dependency {
    CommandGeneration(u64),
    DockerGeneration(u64),
    AptGeneration(u64),
    ProcessGeneration(u64),
    ServiceGeneration(u64),
    GitGeneration(u64),
    HostGeneration(u64),
    FileGeneration(u64),
    ArtifactGeneration(u64),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    /// The complete command line after accepting this candidate.
    pub resulting_line: String,
    pub kind: CandidateKind,
    pub source: CandidateSource,
    pub confidence: f32,
    pub risk: Risk,
    pub evidence: Vec<Evidence>,
    /// Versioned world facts that must still hold at arbitration time.
    pub dependencies: Vec<Dependency>,
    /// Absolute monotonic expiry time. `None` denotes snapshot-stable data.
    pub expires_at_ms: Option<u64>,
}

impl Candidate {
    pub(crate) fn grammar(resulting_line: String, kind: CandidateKind, confidence: f32) -> Self {
        Self {
            resulting_line,
            kind,
            source: CandidateSource::Grammar,
            confidence,
            risk: Risk::Safe,
            evidence: vec![Evidence::GrammarMatch],
            dependencies: Vec::new(),
            expires_at_ms: None,
        }
    }

    pub(crate) fn personal(
        resulting_line: String,
        kind: CandidateKind,
        confidence: f32,
        risk: Risk,
        evidence: Vec<Evidence>,
    ) -> Self {
        Self {
            resulting_line,
            kind,
            source: CandidateSource::Personal,
            confidence,
            risk,
            evidence,
            dependencies: Vec::new(),
            expires_at_ms: None,
        }
    }

    pub(crate) fn transition(
        resulting_line: String,
        kind: CandidateKind,
        confidence: f32,
        risk: Risk,
        evidence: Vec<Evidence>,
    ) -> Self {
        Self {
            resulting_line,
            kind,
            source: CandidateSource::Transition,
            confidence,
            risk,
            evidence,
            dependencies: Vec::new(),
            expires_at_ms: None,
        }
    }
}
