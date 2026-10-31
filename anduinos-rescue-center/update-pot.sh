#!/bin/sh
set -eu

cd "$(dirname "$0")"
mkdir -p po
xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=tr \
    --flag=_:1:python-brace-format --flag=tr:1:python-brace-format \
    --package-name=anduinos-rescue-center --sort-by-file \
    --output=po/anduinos-rescue-center.pot \
    src/anduinos_rescue_center/*.py \
    scripts/anduinos-rescue-center-live-helper
xgettext --language=Desktop --join-existing --sort-by-file \
    --keyword=Name --keyword=Comment --keyword=Keywords \
    --keyword=Summary --keyword=Description \
    --output=po/anduinos-rescue-center.pot \
    data/com.anduinos.RescueCenter.desktop \
    data/anduinos-rescue-center-live-shortcut.desktop
