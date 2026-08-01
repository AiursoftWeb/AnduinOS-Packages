use std::process::ExitCode;

use anduinos_timeback::boot::BootIntegration;
use anduinos_timeback::{client, layout};
use anduinos_timeback::{CONTRACT_VERSION, DEPLOYMENT_SCHEMA_VERSION, SNAPSHOT_ROOT};

fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [command] if command == "inspect" => print_inspection(false),
        [command, option] if command == "inspect" && option == "--json" => print_inspection(true),
        [command] if command == "list" => print_deployments(false),
        [command, option] if command == "list" && option == "--json" => print_deployments(true),
        [command, title] if command == "create" => create_recovery_point(title, false),
        [command, option, title] if command == "create" && option == "--pin" => {
            create_recovery_point(title, true)
        }
        [command, id] if command == "pin" => set_pinned(id, true),
        [command, id] if command == "unpin" => set_pinned(id, false),
        [command, id] if command == "delete" => delete_recovery_point(id),
        [command, id] if command == "verify" => verify_recovery_point(id),
        [command, action] if command == "restore" && action == "--cancel" => cancel_rollback(),
        [command, id] if command == "restore" => schedule_rollback(id),
        [command] if command == "retention" => print_retention(false),
        [command, option] if command == "retention" && option == "--json" => print_retention(true),
        [command, option] if command == "retention" && option == "--apply" => run_retention(),
        [command] if command == "emit-grub-config" => emit_grub_config(),
        [command] if command == "contract" => {
            println!("D-Bus contract version: {CONTRACT_VERSION}");
            println!("Deployment schema version: {DEPLOYMENT_SCHEMA_VERSION}");
            println!("Snapshot root: {SNAPSHOT_ROOT}");
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("Usage: timebackctl inspect [--json]");
            eprintln!("       timebackctl list [--json]");
            eprintln!("       timebackctl create [--pin] TITLE");
            eprintln!("       timebackctl pin|unpin|delete|verify DEPLOYMENT_ID");
            eprintln!("       timebackctl restore DEPLOYMENT_ID|--cancel");
            eprintln!("       timebackctl retention [--json|--apply]");
            eprintln!("       timebackctl contract");
            ExitCode::from(64)
        }
    }
}

fn create_recovery_point(title: &str, pinned: bool) -> ExitCode {
    finish_operation(client::create_recovery_point(
        title,
        "Manual recovery point created with timebackctl",
        pinned,
        print_progress,
    ))
}

fn set_pinned(id: &str, pinned: bool) -> ExitCode {
    finish_operation(client::set_pinned(id, pinned, print_progress))
}

fn delete_recovery_point(id: &str) -> ExitCode {
    finish_operation(client::delete_recovery_point(id, print_progress))
}

fn verify_recovery_point(id: &str) -> ExitCode {
    finish_operation(client::verify_recovery_point(id, print_progress))
}

fn schedule_rollback(id: &str) -> ExitCode {
    finish_operation(client::schedule_rollback(id, print_progress))
}

fn cancel_rollback() -> ExitCode {
    finish_operation(client::cancel_pending_rollback(print_progress))
}

fn run_retention() -> ExitCode {
    finish_operation(client::run_retention(print_progress))
}

fn print_retention(json: bool) -> ExitCode {
    let plan = match client::inspect_retention() {
        Ok(plan) => plan,
        Err(error) => {
            eprintln!("Could not inspect the retention policy: {error}");
            return ExitCode::FAILURE;
        }
    };
    if json {
        match serde_json::to_string_pretty(&plan) {
            Ok(serialized) => println!("{serialized}"),
            Err(error) => {
                eprintln!("Could not serialize the retention plan: {error}");
                return ExitCode::FAILURE;
            }
        }
    } else {
        println!(
            "Space: {} available of {} bytes; target {} bytes",
            plan.space.available_bytes, plan.space.total_bytes, plan.free_space_target_bytes
        );
        println!(
            "Space pressure: {}",
            if plan.under_space_pressure {
                "yes"
            } else {
                "no"
            }
        );
        println!("Planned deletions: {}", plan.actions.len());
        for action in plan.actions {
            println!(
                "{}  {:?}  {:?}",
                action.deployment_id, action.kind, action.reason
            );
        }
    }
    ExitCode::SUCCESS
}

fn emit_grub_config() -> ExitCode {
    match BootIntegration::default().recovery_menu_entry() {
        Ok(Some(entry)) => {
            print!("{entry}");
            ExitCode::SUCCESS
        }
        Ok(None) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("Could not generate the Timeback GRUB entry: {error}");
            ExitCode::FAILURE
        }
    }
}

fn print_progress(progress: client::OperationProgress) {
    println!(
        "[{phase}] {percent:>3}% {message}",
        phase = progress.phase,
        percent = (progress.fraction * 100.0).round() as u32,
        message = progress.message
    );
}

fn finish_operation(result: Result<client::OperationResult, impl std::fmt::Display>) -> ExitCode {
    match result {
        Ok(result) if result.success => {
            println!("{}", result.message);
            ExitCode::SUCCESS
        }
        Ok(result) => {
            eprintln!("{}: {}", result.error_code, result.message);
            ExitCode::FAILURE
        }
        Err(error) => {
            eprintln!("Recovery operation failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn print_deployments(json: bool) -> ExitCode {
    let report = match client::list_deployments() {
        Ok(report) => report,
        Err(error) => {
            eprintln!("Could not query the Timeback Machine service: {error}");
            return ExitCode::FAILURE;
        }
    };
    if json {
        match serde_json::to_string_pretty(&report) {
            Ok(serialized) => println!("{serialized}"),
            Err(error) => {
                eprintln!("Could not serialize deployment report: {error}");
                return ExitCode::FAILURE;
            }
        }
    } else {
        println!("Recovery points: {}", report.deployments.len());
        for deployment in &report.deployments {
            println!(
                "{}  {}  {:?}  {}",
                deployment.created_at.to_rfc3339(),
                deployment.id,
                deployment.state,
                deployment.title
            );
        }
        for problem in &report.issues {
            println!(
                "Issue: {} ({:?}): {}",
                problem.entry, problem.code, problem.message
            );
        }
    }
    if report.issues.is_empty() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    }
}

fn print_inspection(json: bool) -> ExitCode {
    let report = layout::inspect_current();
    if json {
        match serde_json::to_string_pretty(&report) {
            Ok(serialized) => println!("{serialized}"),
            Err(error) => {
                eprintln!("Could not serialize layout report: {error}");
                return ExitCode::FAILURE;
            }
        }
    } else {
        println!("Support: {:?}", report.support);
        println!(
            "Root filesystem: {}",
            report.root_filesystem.as_deref().unwrap_or("unknown")
        );
        println!(
            "Root source: {}",
            report.root_source.as_deref().unwrap_or("unknown")
        );
        for issue in &report.issues {
            println!("Issue: {issue}");
        }
    }
    if report.is_supported() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    }
}
