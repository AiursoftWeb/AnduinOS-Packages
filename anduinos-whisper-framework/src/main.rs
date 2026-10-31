fn main() {
    if let Err(error) = anduinos_whisper_framework::service::run() {
        eprintln!("anduinos-whisper-framework: {error}");
        std::process::exit(1);
    }
}
