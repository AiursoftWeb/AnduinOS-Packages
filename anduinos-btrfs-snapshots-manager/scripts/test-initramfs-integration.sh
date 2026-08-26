#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

mkdir -p \
    "$TEST_ROOT/bin" \
    "$TEST_ROOT/etc/anduinos-btrfs-snapshots-manager" \
    "$TEST_ROOT/lib" \
    "$TEST_ROOT/proc" \
    "$TEST_ROOT/run/anduinos-btrfs-snapshots-manager" \
    "$TEST_ROOT/top/@root" \
    "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/transactions" \
    "$TEST_ROOT/usr/libexec"
touch "$TEST_ROOT/root-device"
printf '2\n' > "$TEST_ROOT/etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version"
printf '{}\n' > "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/transactions/pending-rollback.json"

cat > "$TEST_ROOT/lib/dracut-lib.sh" <<'EOF'
getarg()
{
    key="${1%=}"
    for argument in $(cat "$TEST_ROOT/proc/cmdline"); do
        case "$argument" in
            "$key"=*) printf '%s\n' "${argument#*=}"; return 0 ;;
        esac
    done
    return 1
}

label_uuid_to_dev()
{
    printf '%s\n' "$1"
}

warn()
{
    printf '%s\n' "$*" >> "$TEST_ROOT/warnings"
}

die()
{
    printf '%s\n' "$*" >> "$TEST_ROOT/failures"
    return 1
}
EOF

cat > "$TEST_ROOT/lib/fs-lib.sh" <<'EOF'
det_fs()
{
    printf '%s\n' "$TEST_FSTYPE"
}
EOF

cat > "$TEST_ROOT/bin/mount" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$TEST_ROOT/bin/umount" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$TEST_ROOT/bin/chmod" <<'EOF'
#!/bin/sh
# Model Dracut 110's read-only /usr at runtime. A regression that tries to
# activate the image member in place must fail; writable runtime copies remain
# valid.
if [ "${2:-}" = "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm" ]; then
    exit 1
fi
exec /usr/bin/chmod "$@"
EOF
cat > "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--protocol-version" ]; then
    printf '2\n'
    exit 0
fi
if [ "${1:-}" = "--stage-confirmation-artifact" ]; then
    mkdir -p "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot"
    cp "$2" \
        "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm"
    chmod 0700 \
        "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm"
    exit 0
fi
printf '%s\n' "${1:-no-request}" >> "$TEST_ROOT/invocations"
exit "${TEST_ENGINE_STATUS:-0}"
EOF
cat > "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 0755 \
    "$TEST_ROOT/bin/mount" \
    "$TEST_ROOT/bin/chmod" \
    "$TEST_ROOT/bin/umount" \
    "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs"
# Match the byte-preserving mode used while Dracut assembles the image.  The
# production pre-mount hook must activate this payload before staging it.
chmod 0644 "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm"

# Substitute only environment-specific absolute paths. The recovery state
# machine and Dracut stage behavior remain the production hook's code.
sed \
    -e 's#^command -v getarg.*dracut-lib.sh$#. "$TEST_ROOT/lib/dracut-lib.sh"#' \
    -e 's#^command -v det_fs.*fs-lib.sh$#. "$TEST_ROOT/lib/fs-lib.sh"#' \
    -e 's#^protocol_file=.*#protocol_file="$TEST_ROOT/etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version"#' \
    -e 's#^runtime_root=.*#runtime_root="$TEST_ROOT/run/anduinos-btrfs-snapshots-manager"#' \
    -e 's#^top_level=.*#top_level="$TEST_ROOT/top"#' \
    -e 's#^    reconciler_exec=.*#    reconciler_exec="$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm"#' \
    -e 's#^    reconciler_unit=.*#    reconciler_unit="$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"#' \
    -e 's#^    reconciler_wants=.*#    reconciler_wants="$TEST_ROOT/run/systemd/system/multi-user.target.wants"#' \
    -e 's#/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs#"$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs"#g' \
    -e 's#/usr/libexec/anduinos-btrfs-snapshots-manager-confirm#"$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm"#g' \
    -e 's#\[ ! -b "$root_device" \]#\[ ! -e "$root_device" \]#' \
    "$PROJECT_ROOT/data/dracut/91anduinos-btrfs-snapshots-manager/anduinos-btrfs-snapshots-manager.sh" \
    > "$TEST_ROOT/dracut-pre-mount"
chmod 0755 "$TEST_ROOT/dracut-pre-mount"

ROLLBACK_ID=11111111-2222-4333-8444-555555555555
TEST_PATH="$TEST_ROOT/bin:/usr/bin:/bin"

run_script()
{
    env -i \
        PATH="$TEST_PATH" \
        root="$TEST_ROOT/root-device" \
        TEST_ROOT="$TEST_ROOT" \
        TEST_FSTYPE="$1" \
        TEST_ENGINE_STATUS="${2:-0}" \
        /bin/sh -c '. "$1"' dracut-hook "$TEST_ROOT/dracut-pre-mount"
}

rm -f "$TEST_ROOT/invocations" "$TEST_ROOT/failures" "$TEST_ROOT/warnings"
printf 'root=/dev/ignored anduinos.btrfs_snapshots_manager=%s anduinos.btrfs_snapshots_manager_protocol=2\n' \
    "$ROLLBACK_ID" > "$TEST_ROOT/proc/cmdline"
run_script btrfs
grep -Fxq "$ROLLBACK_ID" "$TEST_ROOT/invocations"
test ! -x "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm"
test -x "$TEST_ROOT/run/anduinos-btrfs-snapshots-manager/confirmation-engine"
cmp "$TEST_ROOT/usr/libexec/anduinos-btrfs-snapshots-manager-confirm" \
    "$TEST_ROOT/run/anduinos-btrfs-snapshots-manager/confirmation-engine"
test -x "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm"
grep -Fq 'recovery-boot/confirm' \
    "$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
! grep -Fq 'ExecStart=/run/' \
    "$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
grep -Fq 'After=local-fs.target' \
    "$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
grep -Fq 'RequiresMountsFor=/.snapshots /boot' \
    "$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
! grep -Fq 'After=multi-user.target' \
    "$TEST_ROOT/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
test -L "$TEST_ROOT/run/systemd/system/multi-user.target.wants/anduinos-btrfs-snapshots-manager-confirm.service"

rm -f "$TEST_ROOT/invocations" "$TEST_ROOT/failures" "$TEST_ROOT/warnings"
: > "$TEST_ROOT/proc/cmdline"
run_script ext4
test ! -e "$TEST_ROOT/invocations"
test ! -e "$TEST_ROOT/failures"

rm -f "$TEST_ROOT/invocations" "$TEST_ROOT/failures" "$TEST_ROOT/warnings"
: > "$TEST_ROOT/proc/cmdline"
run_script btrfs
grep -Fxq 'no-request' "$TEST_ROOT/invocations"
test -x "$TEST_ROOT/top/@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm"

rm -f "$TEST_ROOT/invocations" "$TEST_ROOT/failures" "$TEST_ROOT/warnings"
printf 'anduinos.btrfs_snapshots_manager=%s anduinos.btrfs_snapshots_manager_protocol=2\n' \
    "$ROLLBACK_ID" > "$TEST_ROOT/proc/cmdline"
if run_script ext4; then
    echo "An explicit recovery request unexpectedly ignored a non-Btrfs root" >&2
    exit 1
fi
grep -Fq 'root filesystem is not Btrfs' "$TEST_ROOT/failures"

rm -f "$TEST_ROOT/invocations" "$TEST_ROOT/failures" "$TEST_ROOT/warnings"
printf 'anduinos.btrfs_snapshots_manager=%s anduinos.btrfs_snapshots_manager_protocol=1\n' \
    "$ROLLBACK_ID" > "$TEST_ROOT/proc/cmdline"
if run_script btrfs; then
    echo "An incompatible explicit recovery request unexpectedly continued" >&2
    exit 1
fi
grep -Fq 'requested recovery protocol is incompatible' "$TEST_ROOT/failures"
test ! -e "$TEST_ROOT/invocations"

echo "Dracut pre-mount recovery integration tests passed"
