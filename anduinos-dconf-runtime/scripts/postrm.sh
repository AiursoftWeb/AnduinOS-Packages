#!/bin/sh
set -e

# When this package (the dconf infrastructure owner) is removed,
# rebuild one last time to clean up the binary database. In chroots we compile
# only the anduinos database to avoid emitting host-visible D-Bus signals.

is_chroot() {
    if command -v systemd-detect-virt >/dev/null 2>&1 &&
       systemd-detect-virt --quiet --chroot >/dev/null 2>&1; then
        return 0
    fi

    if command -v ischroot >/dev/null 2>&1 && ischroot >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

rebuild_anduinos_db() {
    db_dir=/etc/dconf/db/anduinos.d
    db_file=/etc/dconf/db/anduinos

    if ! command -v dconf >/dev/null 2>&1; then
        return 0
    fi

    if is_chroot; then
        echo "anduinos-dconf-runtime: chroot detected; rebuilding /etc/dconf/db/anduinos directly to avoid host D-Bus notifications."
        if [ -d "$db_dir" ]; then
            dconf compile "$db_file" "$db_dir"
        else
            rm -f "$db_file"
        fi
    else
        dconf update
    fi
}

case "$1" in
    remove|purge)
        rebuild_anduinos_db
        ;;
esac
