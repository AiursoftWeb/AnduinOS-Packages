use std::process::ExitCode;

use anduinos_timeback::{client, layout};
use anduinos_timeback::{CONTRACT_VERSION, DEPLOYMENT_SCHEMA_VERSION, SNAPSHOT_ROOT};

fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [command] if command == "inspect" => print_inspection(false),
        [command, option] if command == "inspect" && option == "--json" => print_inspection(true),
        [command] if command == "list" => print_deployments(false),
        [command, option] if command == "list" && option == "--json" => print_deployments(true),
        [command] if command == "contract" => {
            println!("D-Bus contract version: {CONTRACT_VERSION}");
            println!("Deployment schema version: {DEPLOYMENT_SCHEMA_VERSION}");
            println!("Snapshot root: {SNAPSHOT_ROOT}");
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("Usage: timebackctl inspect [--json]");
            eprintln!("       timebackctl list [--json]");
            eprintln!("       timebackctl contract");
            ExitCode::from(64)
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
