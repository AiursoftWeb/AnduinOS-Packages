use std::collections::HashMap;
use std::time::{Duration, Instant};
use pcap::{Device, Capture};
use etherparse::{PacketHeaders, TransportHeader, NetHeaders};
use procfs::process::all_processes;
use std::net::IpAddr;
use std::str::FromStr;
use dns_lookup::lookup_addr;
use lazy_static::lazy_static;
use std::sync::Mutex;
use serde::{Deserialize, Serialize};

lazy_static! {
    static ref DNS_CACHE: Mutex<HashMap<String, Option<String>>> = Mutex::new(HashMap::new());
}

include!("../ufw/connection_stat.rs");

fn main() {
    let dns_pool = threadpool::ThreadPool::new(8);
    let device = match Device::lookup() {
        Ok(Some(d)) => d,
        _ => {
            eprintln!("Failed to find default network device");
            return;
        }
    };
    
    let all_devices = pcap::Device::list().unwrap_or_default();
    let local_ips: Vec<String> = all_devices.into_iter()
        .flat_map(|d| d.addresses.into_iter().map(|a| a.addr.to_string()))
        .collect();

    let mut cap = match Capture::from_device(device).unwrap().promisc(false).timeout(100).open() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Failed to open capture, probably needs root: {}", e);
            return;
        }
    };

    let mut traffic_map: HashMap<(u16, u16, String, String), ConnectionStat> = HashMap::new();
    let mut last_tick = Instant::now();

    loop {
        match cap.next_packet() {
            Ok(packet) => {
                let len = packet.header.len as u64;
                if let Ok(headers) = PacketHeaders::from_ethernet_slice(packet.data) {
                    let mut src_ip = String::new();
                    let mut dst_ip = String::new();
                    
                    if let Some(net) = headers.net {
                        match net {
                            NetHeaders::Ipv4(h, _) => {
                                src_ip = format!("{}.{}.{}.{}", h.source[0], h.source[1], h.source[2], h.source[3]);
                                dst_ip = format!("{}.{}.{}.{}", h.destination[0], h.destination[1], h.destination[2], h.destination[3]);
                            },
                            NetHeaders::Ipv6(h, _) => {
                                src_ip = std::net::Ipv6Addr::from(h.source).to_string();
                                dst_ip = std::net::Ipv6Addr::from(h.destination).to_string();
                            }
                        }
                    }
                    
                    if let Some(transport) = headers.transport {
                        let (src_port, dst_port, proto) = match transport {
                            TransportHeader::Tcp(h) => (h.source_port, h.destination_port, "TCP"),
                            TransportHeader::Udp(h) => (h.source_port, h.destination_port, "UDP"),
                            _ => continue,
                        };
                        
                        let is_packet_outbound = local_ips.contains(&src_ip);
                        
                        let (local_port, remote_port, remote_ip) = if is_packet_outbound {
                            (src_port, dst_port, dst_ip.clone())
                        } else {
                            (dst_port, src_port, src_ip.clone())
                        };

                        let key = (local_port, remote_port, remote_ip.clone(), proto.to_string());
                        
                        let stat = traffic_map.entry(key).or_insert_with(|| ConnectionStat {
                            pid: None,
                            process_name: "Unknown".to_string(),
                            remote_ip,
                            domain_name: None,
                            local_port,
                            remote_port,
                            protocol: proto.to_string(),
                            direction: if is_packet_outbound { "Outbound".to_string() } else { "Inbound".to_string() },
                            upload_speed: 0,
                            download_speed: 0,
                            total_bytes: 0,
                            total_uploaded: 0,
                            total_downloaded: 0,
                            inactivity_ticks: 0,
                        });

                        stat.total_bytes += len;
                        if local_port == src_port {
                            stat.upload_speed += len;
                            stat.total_uploaded += len;
                        } else {
                            stat.download_speed += len;
                            stat.total_downloaded += len;
                        }
                    }
                }
            },
            Err(pcap::Error::TimeoutExpired) => {},
            Err(_) => break,
        }

        if last_tick.elapsed() >= Duration::from_secs(1) {
            enrich_with_process_info(&mut traffic_map);

            for stat in traffic_map.values_mut() {
                let ip = stat.remote_ip.clone();
                let mut cache = DNS_CACHE.lock().unwrap();
                if !cache.contains_key(&ip) {
                    cache.insert(ip.clone(), None);
                    let ip_clone = ip.clone();
                    dns_pool.execute(move || {
                        if let Ok(addr) = IpAddr::from_str(&ip_clone) {
                            if let Ok(domain) = lookup_addr(&addr) {
                                let mut c = DNS_CACHE.lock().unwrap();
                                c.insert(ip_clone, Some(domain));
                            }
                        }
                    });
                }
                if let Some(Some(domain)) = cache.get(&ip) {
                    stat.domain_name = Some(domain.clone());
                }
            }
            
            let snapshot: Vec<ConnectionStat> = traffic_map.values().cloned().collect();
            println!("{}", serde_json::to_string(&snapshot).unwrap());
            let _ = std::io::Write::flush(&mut std::io::stdout());
            
            traffic_map.retain(|_, stat| {
                if stat.upload_speed == 0 && stat.download_speed == 0 {
                    stat.inactivity_ticks += 1;
                } else {
                    stat.inactivity_ticks = 0;
                }
                stat.inactivity_ticks < 10
            });

            for stat in traffic_map.values_mut() {
                stat.upload_speed = 0;
                stat.download_speed = 0;
            }
            
            last_tick = Instant::now();
        }
    }
}

fn enrich_with_process_info(traffic_map: &mut HashMap<(u16, u16, String, String), ConnectionStat>) {
    let mut port_to_inode: HashMap<u16, u64> = HashMap::new();

    if let Ok(tcp) = procfs::net::tcp() {
        for entry in tcp {
            port_to_inode.insert(entry.local_address.port(), entry.inode);
        }
    }
    if let Ok(tcp6) = procfs::net::tcp6() {
        for entry in tcp6 {
            port_to_inode.insert(entry.local_address.port(), entry.inode);
        }
    }
    if let Ok(udp) = procfs::net::udp() {
        for entry in udp {
            port_to_inode.insert(entry.local_address.port(), entry.inode);
        }
    }
    if let Ok(udp6) = procfs::net::udp6() {
        for entry in udp6 {
            port_to_inode.insert(entry.local_address.port(), entry.inode);
        }
    }

    if port_to_inode.is_empty() { return; }

    let mut inode_to_process = HashMap::new();
    if let Ok(procs) = all_processes() {
        for proc in procs.flatten() {
            if let Ok(fds) = proc.fd() {
                for fd in fds.flatten() {
                    if let procfs::process::FDTarget::Socket(inode) = fd.target {
                        inode_to_process.insert(inode, proc.pid());
                    }
                }
            }
        }
    }

    for stat in traffic_map.values_mut() {
        if stat.pid.is_none() {
            if let Some(inode) = port_to_inode.get(&stat.local_port) {
                if let Some(pid) = inode_to_process.get(inode) {
                    stat.pid = Some(*pid);
                    if let Ok(proc) = procfs::process::Process::new(*pid) {
                        if let Ok(cmd) = proc.cmdline() {
                            if let Some(name) = cmd.first() {
                                let basename = std::path::Path::new(name)
                                    .file_name()
                                    .unwrap_or_default()
                                    .to_string_lossy()
                                    .to_string();
                                stat.process_name = basename;
                            }
                        } else if let Ok(comm) = proc.stat() {
                            stat.process_name = comm.comm;
                        }
                    }
                }
            }
        }
    }
}
