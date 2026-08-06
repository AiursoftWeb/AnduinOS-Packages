// The UI and helper must never carry independent copies of the storage ABI.
// Re-export the trusted recovery engine's one fixed-layout implementation.
pub use anduinos_recovery_engine::layout::{
    LayoutReport, LayoutSupport, MountReport, inspect_current, inspect_mountinfo,
};
