use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotSpace {
    pub referenced_bytes: Option<u64>,
    pub exclusive_bytes: Option<u64>,
}

impl SnapshotSpace {
    /// For a single snapshot qgroup, exclusive bytes are the best available
    /// deletion estimate. They are deliberately not presented as a guarantee.
    pub fn estimated_reclaimable_bytes(self) -> Option<u64> {
        self.exclusive_bytes
    }
}

pub fn parse_qgroup_numbers(output: &str) -> Option<SnapshotSpace> {
    for line in output.lines().rev() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() >= 3 && fields[0].contains('/') {
            if let (Ok(referenced), Ok(exclusive)) = (fields[1].parse(), fields[2].parse()) {
                return Some(SnapshotSpace {
                    referenced_bytes: Some(referenced),
                    exclusive_bytes: Some(exclusive),
                });
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parses_raw_qgroup_output() {
        let s = parse_qgroup_numbers("qgroupid rfer excl\n0/256 4096 1024\n").unwrap();
        assert_eq!(s.estimated_reclaimable_bytes(), Some(1024));
    }
}
