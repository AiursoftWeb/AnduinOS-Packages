#!/bin/sh
set -e

# Versions through 2.0.1-1 diverted Ubuntu-owned files. The new package owns
# neither path; restore the distribution copies once after a successful unpack.
remove_legacy_diversion() {
    original="$1"
    diverted="$2"

    if [ "$(dpkg-divert --listpackage "$original")" != "anduinos-rime" ]; then
        return 0
    fi

    # During an upgrade dpkg normally removes our obsolete file before this
    # script runs. Handle interrupted/older upgrades without overwriting the
    # distribution copy waiting at the diverted path.
    if [ -e "$original" ] || [ -L "$original" ]; then
        rm -f "$original"
    fi
    dpkg-divert --package anduinos-rime --remove --rename \
        --divert "$diverted" "$original"
}

if [ "$1" = "configure" ]; then
    remove_legacy_diversion \
        /usr/share/rime-data/default.yaml \
        /usr/share/rime-data/default.yaml.prelude
    remove_legacy_diversion \
        /usr/share/language-selector/data/pkg_depends \
        /usr/share/language-selector/data/pkg_depends.ubuntu

    # Apkg stores package files with epoch timestamps. Rime compares those
    # timestamps with the user's last deployment and otherwise skips upgrades.
    for file in \
        /usr/share/rime-data/anduinos_defaults.yaml \
        /usr/share/rime-data/default.custom.yaml \
        /usr/share/rime-data/rime_ice.schema.yaml \
        /usr/share/rime-data/rime_ice.custom.yaml \
        /usr/share/rime-data/melt_eng.schema.yaml \
        /usr/share/rime-data/double_pinyin.schema.yaml \
        /usr/share/rime-data/double_pinyin.custom.yaml \
        /usr/share/rime-data/double_pinyin_mspy.schema.yaml \
        /usr/share/rime-data/double_pinyin_mspy.custom.yaml \
        /usr/share/rime-data/double_pinyin_flypy.schema.yaml \
        /usr/share/rime-data/double_pinyin_flypy.custom.yaml; do
        [ ! -f "$file" ] || touch "$file"
    done
fi

#DEBHELPER#
exit 0
