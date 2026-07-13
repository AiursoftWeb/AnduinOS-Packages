

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct ConnectionStat {
    pub pid: Option<i32>,
    pub process_name: String,
    pub remote_ip: String,
    pub domain_name: Option<String>,
    pub local_port: u16,
    pub remote_port: u16,
    pub protocol: String, // "TCP" or "UDP"
    pub direction: String,
    pub upload_speed: u64,   // Bytes per second
    pub download_speed: u64, // Bytes per second
    pub total_bytes: u64,
    pub total_uploaded: u64,
    pub total_downloaded: u64,
    #[serde(skip, default)]
    #[allow(dead_code)]
    pub inactivity_ticks: u32,
}
