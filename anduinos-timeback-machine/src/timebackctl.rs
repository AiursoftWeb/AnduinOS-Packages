use std::process::ExitCode;

use anduinos_timeback::layout;
use anduinos_timeback::{CONTRACT_VERSION, DEPLOYMENT_SCHEMA_VERSION, SNAPSHOT_ROOT};

fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [command] if command == "inspect" => print_inspection(false),
        [command, option] if command == "inspect" && option == "--json" => print_inspection(true),
        [command] if command == "contract" => {
            println!("D-Bus contract version: {CONTRACT_VERSION}");
            println!("Deployment schema version: {DEPLOYMENT_SCHEMA_VERSION}");
            println!("Snapshot root: {SNAPSHOT_ROOT}");
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("Usage: timebackctl inspect [--json]");
            eprintln!("       timebackctl contract");
            ExitCode::from(64)
        }
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
