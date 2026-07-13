#!/bin/bash
set -e

if [ "$1" = "configure" ] || [ -z "$1" ]; then
    # Give the backend auditor capabilities to capture packets without asking for root password
    if [ -x "/usr/libexec/ufwall-gtk/ufwall-auditor" ]; then
        setcap cap_net_raw,cap_net_admin,cap_sys_ptrace,cap_dac_read_search=eip /usr/libexec/ufwall-gtk/ufwall-auditor || true
    fi
fi
