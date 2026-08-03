fn main() {
    if let Err(error) = anduinos_quiet_engine::runtime::serve_stdio() {
        eprintln!("anduinos-quietd: {error}");
        std::process::exit(1);
    }
}
