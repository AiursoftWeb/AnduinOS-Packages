#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version,
# then applies AnduinOS customizations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd msgunfmt gettext
need_cmd msgfmt gettext

UUID="arcmenu@arcmenu.com"

# ── Localization tables ────────────────────────────────────────────────

declare -A PIN=(
    ["ar"]="تثبيت في قائمة ابدأ"
    ["be"]="Замацаваць у меню Пуск"
    ["bg"]="Закачи в менюто Старт"
    ["ca"]="Fixa al menú Inici"
    ["cs"]="Připnout do nabídky Start"
    ["da"]="Fastgør til startmenuen"
    ["de"]="An Startmenü anheften"
    ["el"]="Καρφίτσωμα στο μενού Έναρξη"
    ["es"]="Fijar en el menú Inicio"
    ["et"]="Kinnita algusmenüüsse"
    ["fi"]="Kiinnitä Käynnistä-valikkoon"
    ["fr"]="Épingler au menu Démarrer"
    ["he"]="הצמד לתפריט התחל"
    ["hi_IN"]="स्टार्ट मेनू में पिन करें"
    ["hu"]="Rögzítés a Start menübe"
    ["id"]="Sematkan ke menu Start"
    ["it"]="Aggiungi al menu Start"
    ["ja"]="スタートメニューに追加"
    ["ko"]="시작 메뉴에 고정"
    ["nb_NO"]="Fest til startmenyen"
    ["nl"]="Vastzetten aan Startmenu"
    ["oc"]="Afichar al menú Inici"
    ["pl"]="Przypnij do menu Start"
    ["pt_BR"]="Fixar no menu Iniciar"
    ["ru"]="Закрепить в меню Пуск"
    ["si"]="ආරම්භ මෙනුවට අමුණන්න"
    ["sk"]="Pripnúť do ponuky Štart"
    ["sr"]="Закачи у мени Старт"
    ["sr@latin"]="Zakači u meni Start"
    ["sv"]="Fäst i startmenyn"
    ["szl"]="Przipnij do menu Start"
    ["tr"]="Başlat menüsüne sabitle"
    ["uk"]="Закріпити в меню Пуск"
    ["zh_CN"]="固定到开始菜单"
    ["zh_TW"]="固定到開始功能表"
)

declare -A UNPIN=(
    ["ar"]="إلغاء التثبيت من قائمة ابدأ"
    ["be"]="Адмацаваць з меню Пуск"
    ["bg"]="Откачи от менюто Старт"
    ["ca"]="Desfixa del menú Inici"
    ["cs"]="Odepnout z nabídky Start"
    ["da"]="Frigør fra startmenuen"
    ["de"]="Vom Startmenü lösen"
    ["el"]="Ξεκαρφίτσωμα από το μενού Έναρξη"
    ["es"]="Desfijar del menú Inicio"
    ["et"]="Eemalda algusmenüüst"
    ["fi"]="Irrota Käynnistä-valikosta"
    ["fr"]="Désépingler du menu Démarrer"
    ["he"]="הסר הצמדה מתפריט התחל"
    ["hi_IN"]="स्टार्ट मेनू से अनपिन करें"
    ["hu"]="Eltávolítás a Start menüből"
    ["id"]="Lepas dari menu Start"
    ["it"]="Rimuovi dal menu Start"
    ["ja"]="スタートメニューから削除"
    ["ko"]="시작 메뉴에서 고정 해제"
    ["nb_NO"]="Løsne fra startmenyen"
    ["nl"]="Losmaken van Startmenu"
    ["oc"]="Retirar del menú Inici"
    ["pl"]="Odepnij od menu Start"
    ["pt_BR"]="Desafixar do menu Iniciar"
    ["ru"]="Открепить от меню Пуск"
    ["si"]="ආරම්භ මෙනුවෙන් ගලවන්න"
    ["sk"]="Odopnúť z ponuky Štart"
    ["sr"]="Откачи из менија Старт"
    ["sr@latin"]="Otkači iz menija Start"
    ["sv"]="Lossa från startmenyn"
    ["szl"]="Odpnij od menu Start"
    ["tr"]="Başlat menüsünden çıkar"
    ["uk"]="Відкріпити з меню Пуск"
    ["zh_CN"]="从开始菜单取消固定"
    ["zh_TW"]="從開始功能表取消固定"
)

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # ── AnduinOS customizations ───────────────────────────────────────

    # 1. Replace ArcMenu logo with AnduinOS logo
    echo "[$SUITE] Replacing ArcMenu logo..."
    mkdir -p "$DEPLOY_DIR/icons"
    cp "$SCRIPT_DIR/anduinos-logo.svg" "$DEPLOY_DIR/icons/anduinos-logo.svg"

    # 2. Rename "ArcMenu" → "Start menu" in English source
    echo "[$SUITE] Patching ArcMenu → Start menu..."
    sed -i 's/Unpin from ArcMenu/Unpin from Start menu/g' "$DEPLOY_DIR/appMenu.js"
    sed -i 's/Pin to ArcMenu/Pin to Start menu/g' "$DEPLOY_DIR/appMenu.js"

    # 3. Patch localization
    locale_dir="$DEPLOY_DIR/locale"
    found=0

    if [[ -d "$locale_dir" ]]; then
        for lang_dir in "$locale_dir"/*/; do
            lang=$(basename "$lang_dir")
            mo_file="$lang_dir/LC_MESSAGES/arcmenu.mo"

            if [[ -f "$mo_file" ]] && [[ -n "${PIN[$lang]+isset}" ]]; then
                echo "[$SUITE] Patching arcmenu locale: $lang"
                msgunfmt "$mo_file" -o /tmp/arcmenu.po

                cat << EOF >> /tmp/arcmenu.po
msgid "Pin to Start menu"
msgstr "${PIN[$lang]}"

msgid "Unpin from Start menu"
msgstr "${UNPIN[$lang]}"

EOF
                msgfmt /tmp/arcmenu.po -o "$mo_file"
                rm -f /tmp/arcmenu.po
                found=$((found + 1))
            fi
        done
        echo "[$SUITE] Patched arcmenu.mo for $found languages"
    fi
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}arcmenu@arcmenu.com/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
