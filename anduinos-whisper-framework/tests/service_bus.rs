#[test]
fn isolated_bus_contract_and_lifecycle() {
    let output = std::process::Command::new("dbus-run-session")
        .args([
            "--",
            "python3",
            "tests/integration/rust-service-smoke.py",
            env!("CARGO_BIN_EXE_anduinos-whisper-framework"),
            env!("CARGO_BIN_EXE_anduinos-voice-diagnostics"),
        ])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("ANDUINOS_ISOLATED_TEST_BUS", "1")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
#[ignore = "requires installed model and native worker; uses an inaccessible private PipeWire socket"]
fn native_preparation_cancel_and_shutdown() {
    let output = std::process::Command::new("dbus-run-session")
        .args([
            "--",
            "python3",
            "tests/integration/rust-service-smoke.py",
            env!("CARGO_BIN_EXE_anduinos-whisper-framework"),
            env!("CARGO_BIN_EXE_anduinos-voice-diagnostics"),
            "--native",
        ])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("ANDUINOS_ISOLATED_TEST_BUS", "1")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
