#!/bin/bash
set -e

if [ "$1" = "configure" ] || [ -z "$1" ]; then
    # Give the backend auditor capabilities to capture packets without asking for root password
    if [ -x "/usr/libexec/ufwall-gtk/ufwall-auditor" ]; then
        # cap_net_raw: Required to open raw sockets for capturing packets (pcap)
        # cap_net_admin: Sometimes required by pcap for promiscuous mode/interface management
        # cap_sys_ptrace: Required to read /proc/<pid>/cmdline and /proc/<pid>/exe of processes owned by other users
        # cap_dac_read_search: Required to bypass file read permissions to read /proc/net and process info
        setcap cap_net_raw,cap_net_admin,cap_sys_ptrace,cap_dac_read_search=eip /usr/libexec/ufwall-gtk/ufwall-auditor || true
    fi
fi
