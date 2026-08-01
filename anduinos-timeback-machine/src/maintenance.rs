use crate::retention::{
    RetentionCoordinator, RetentionExecutionError, RetentionExecutionErrorCode,
    RetentionExecutionReport, RetentionPlan,
};

pub trait RetentionMaintenance {
    fn inspect_retention(&self) -> Result<RetentionPlan, RetentionExecutionError>;
    fn apply_retention(&self) -> Result<RetentionExecutionReport, RetentionExecutionError>;
}

impl<B> RetentionMaintenance for RetentionCoordinator<B>
where
    B: crate::retention::RetentionBackend,
{
    fn inspect_retention(&self) -> Result<RetentionPlan, RetentionExecutionError> {
        self.inspect()
    }

    fn apply_retention(&self) -> Result<RetentionExecutionReport, RetentionExecutionError> {
        self.apply()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MaintenanceOutcome {
    UnsupportedLayout,
    Healthy {
        available_bytes: u64,
        free_space_target_bytes: u64,
    },
    PressureBlocked {
        available_bytes: u64,
        free_space_target_bytes: u64,
    },
    Cleaned {
        report: RetentionExecutionReport,
        pressure_remaining: bool,
    },
    Warning {
        code: RetentionExecutionErrorCode,
        message: String,
    },
}

/// Run one fail-open maintenance pass.
///
/// The caller deliberately receives a typed outcome instead of an error. The
/// systemd helper always exits successfully: automatic cleanup must never make
/// the machine unhealthy merely because recovery metadata needs attention.
pub fn run_maintenance<C>(coordinator: &C) -> MaintenanceOutcome
where
    C: RetentionMaintenance,
{
    let plan = match coordinator.inspect_retention() {
        Ok(plan) => plan,
        Err(error) if error.code == RetentionExecutionErrorCode::UnsupportedLayout => {
            return MaintenanceOutcome::UnsupportedLayout;
        }
        Err(error) => {
            return MaintenanceOutcome::Warning {
                code: error.code,
                message: error.message,
            };
        }
    };

    if plan.actions.is_empty() {
        if plan.under_space_pressure {
            return MaintenanceOutcome::PressureBlocked {
                available_bytes: plan.space.available_bytes,
                free_space_target_bytes: plan.free_space_target_bytes,
            };
        }
        return MaintenanceOutcome::Healthy {
            available_bytes: plan.space.available_bytes,
            free_space_target_bytes: plan.free_space_target_bytes,
        };
    }

    match coordinator.apply_retention() {
        Ok(report) => MaintenanceOutcome::Cleaned {
            pressure_remaining: report.final_space.is_under_pressure(plan.policy),
            report,
        },
        Err(error) => MaintenanceOutcome::Warning {
            code: error.code,
            message: error.message,
        },
    }
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use super::*;
    use crate::retention::{RetentionExecutionReport, RetentionPolicy, SpaceStatus};

    const GIB: u64 = 1024 * 1024 * 1024;

    struct FakeMaintenance {
        inspect: RefCell<Option<Result<RetentionPlan, RetentionExecutionError>>>,
        apply: RefCell<Option<Result<RetentionExecutionReport, RetentionExecutionError>>>,
    }

    impl RetentionMaintenance for FakeMaintenance {
        fn inspect_retention(&self) -> Result<RetentionPlan, RetentionExecutionError> {
            self.inspect.borrow_mut().take().unwrap()
        }

        fn apply_retention(&self) -> Result<RetentionExecutionReport, RetentionExecutionError> {
            self.apply.borrow_mut().take().unwrap()
        }
    }

    fn plan(under_pressure: bool, has_action: bool) -> RetentionPlan {
        let policy = RetentionPolicy::default();
        let space = SpaceStatus {
            total_bytes: 100 * GIB,
            available_bytes: if under_pressure { GIB } else { 20 * GIB },
        };
        let mut plan = RetentionPlan {
            policy,
            space,
            free_space_target_bytes: space.target(policy),
            under_space_pressure: under_pressure,
            restorable_deployments: 2,
            actions: Vec::new(),
        };
        if has_action {
            let deployment_id = crate::model::DeploymentId::new();
            plan.actions.push(crate::retention::RetentionAction {
                transaction_id: crate::package_transaction::PackageTransactionId::new(),
                deployment_id,
                kind: crate::model::DeploymentKind::AptPost,
                reason: crate::retention::RetentionReason::SpacePressurePost,
            });
        }
        plan
    }

    fn execution_report(final_available_bytes: u64) -> RetentionExecutionReport {
        RetentionExecutionReport {
            initial_space: SpaceStatus {
                total_bytes: 100 * GIB,
                available_bytes: GIB,
            },
            final_space: SpaceStatus {
                total_bytes: 100 * GIB,
                available_bytes: final_available_bytes,
            },
            free_space_target_bytes: 10 * GIB,
            deleted: Vec::new(),
        }
    }

    #[test]
    fn healthy_pass_never_invokes_the_destructive_coordinator() {
        let maintenance = FakeMaintenance {
            inspect: RefCell::new(Some(Ok(plan(false, false)))),
            apply: RefCell::new(None),
        };
        assert!(matches!(
            run_maintenance(&maintenance),
            MaintenanceOutcome::Healthy { .. }
        ));
    }

    #[test]
    fn unsupported_layout_is_a_clean_skip() {
        let maintenance = FakeMaintenance {
            inspect: RefCell::new(Some(Err(RetentionExecutionError {
                code: RetentionExecutionErrorCode::UnsupportedLayout,
                message: "unsupported test layout".into(),
            }))),
            apply: RefCell::new(None),
        };
        assert_eq!(
            run_maintenance(&maintenance),
            MaintenanceOutcome::UnsupportedLayout
        );
    }

    #[test]
    fn unsafe_metadata_is_reported_without_invoking_cleanup() {
        let maintenance = FakeMaintenance {
            inspect: RefCell::new(Some(Err(RetentionExecutionError {
                code: RetentionExecutionErrorCode::UnsafeMetadata,
                message: "unsafe test metadata".into(),
            }))),
            apply: RefCell::new(None),
        };
        assert_eq!(
            run_maintenance(&maintenance),
            MaintenanceOutcome::Warning {
                code: RetentionExecutionErrorCode::UnsafeMetadata,
                message: "unsafe test metadata".into(),
            }
        );
    }

    #[test]
    fn pressure_without_an_eligible_action_is_visible_but_fail_open() {
        let maintenance = FakeMaintenance {
            inspect: RefCell::new(Some(Ok(plan(true, false)))),
            apply: RefCell::new(None),
        };
        assert!(matches!(
            run_maintenance(&maintenance),
            MaintenanceOutcome::PressureBlocked { .. }
        ));
    }

    #[test]
    fn cleanup_reports_whether_space_pressure_remains() {
        let maintenance = FakeMaintenance {
            inspect: RefCell::new(Some(Ok(plan(true, true)))),
            apply: RefCell::new(Some(Ok(execution_report(12 * GIB)))),
        };
        assert!(matches!(
            run_maintenance(&maintenance),
            MaintenanceOutcome::Cleaned {
                pressure_remaining: false,
                ..
            }
        ));
    }
}
