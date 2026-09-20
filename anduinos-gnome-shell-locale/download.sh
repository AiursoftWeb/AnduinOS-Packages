#!/usr/bin/env bash
# Pre-build: per-suite GNOME Shell locale override.
# Downloads Ubuntu langpacks, patches "Pin to Dash" / "Unpin" msgids
# with AnduinOS branding ("Add to Taskbar" / "Remove from Taskbar"),
# and ships the resulting .mo files to /usr/share/locale/ which has
# higher gettext priority than /usr/share/locale-langpack/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd msgunfmt gettext
need_cmd msgfmt gettext

# ── Translation tables ─────────────────────────────────────────────────

declare -A ADD=(
    ["en"]="Add to Taskbar"
    ["zh_CN"]="添加到任务栏"
    ["zh_TW"]="加入工作列"
    ["zh_HK"]="加入工作欄"
    ["ja"]="タスクバーに追加"
    ["ko"]="작업표시줄에 추가"
    ["vi"]="Thêm vào thanh tác vụ"
    ["th"]="เพิ่มไปยังแถบงาน"
    ["de"]="Zur Taskleiste hinzufügen"
    ["fr"]="Ajouter à la barre des tâches"
    ["es"]="Agregar a la barra de tareas"
    ["ru"]="Добавить на панель задач"
    ["it"]="Aggiungi alla barra delle applicazioni"
    ["pt"]="Adicionar à barra de tarefas"
    ["pt_BR"]="Adicionar à barra de tarefas"
    ["ar"]="إضافة إلى شريط المهام"
    ["nl"]="Toevoegen aan taakbalk"
    ["sv"]="Lägg till i aktivitetsfältet"
    ["pl"]="Dodaj do paska zadań"
    ["tr"]="Görev çubuğuna ekle"
    ["ro"]="Adăugați în bara de activități"
)

declare -A REMOVE=(
    ["en"]="Remove from Taskbar"
    ["zh_CN"]="从任务栏中移除"
    ["zh_TW"]="從工作列移除"
    ["zh_HK"]="從工作欄移除"
    ["ja"]="タスクバーから削除"
    ["ko"]="작업표시줄에서 제거"
    ["vi"]="Xóa khỏi thanh tác vụ"
    ["th"]="ลบออกจากแถบงาน"
    ["de"]="Aus der Taskleiste entfernen"
    ["fr"]="Retirer de la barre des tâches"
    ["es"]="Eliminar de la barra de tareas"
    ["ru"]="Удалить с панели задач"
    ["it"]="Rimuovi dalla barra delle applicazioni"
    ["pt"]="Remover da barra de tarefas"
    ["pt_BR"]="Remover da barra de tarefas"
    ["ar"]="إزالة من شريط المهام"
    ["nl"]="Verwijderen van taakbalk"
    ["sv"]="Ta bort från aktivitetsfältet"
    ["pl"]="Usuń z paska zadań"
    ["tr"]="Görev çubuğundan kaldır"
    ["ro"]="Anulați fixarea din bara de activități"
)

# ── Ubuntu archive URL per suite ────────────────────────────────────────
declare -A UBUNTU_MIRROR=(
    ["noble"]="https://mirror.aiursoft.com/ubuntu"
    ["resolute"]="https://mirror.aiursoft.com/ubuntu"
    ["stonking"]="https://mirror.aiursoft.com/ubuntu"
)

for SUITE in "${!GNOME_TARGETS[@]}"; do
    DEPLOY_DIR="deploy/$SUITE"
    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    MIRROR="${UBUNTU_MIRROR[$SUITE]}"
    echo "[$SUITE] Downloading GNOME Shell langpacks from $MIRROR $SUITE..."

    # Isolated apt directory
    APT_DIR="$(mktemp -d)"
    trap "rm -rf $APT_DIR" EXIT

    mkdir -p "$APT_DIR/lists" "$APT_DIR/cache"
    # [arch=amd64] keeps apt index downloads lean — the CI host runs amd64
    # and .mo files are architecture-independent data pulled from any arch.
    echo "deb [arch=amd64] $MIRROR $SUITE main universe" > "$APT_DIR/sources.list"

    apt-get update -qq \
        -o "Dir::Etc::SourceList=$APT_DIR/sources.list" \
        -o "Dir::Etc::SourceParts=/dev/null" \
        -o "Dir::State::Lists=$APT_DIR/lists" \
        -o "Dir::Cache=$APT_DIR/cache" 2>/dev/null

    # Download all language-pack-gnome-*-base packages
    DL_DIR="$APT_DIR/dl"
    mkdir -p "$DL_DIR"

    cd "$DL_DIR"
    apt-get download -qq \
        -o "Dir::Etc::SourceList=$APT_DIR/sources.list" \
        -o "Dir::Etc::SourceParts=/dev/null" \
        -o "Dir::State::Lists=$APT_DIR/lists" \
        -o "Dir::Cache=$APT_DIR/cache" \
        language-pack-gnome-en-base 2>/dev/null || true

    # For non-English: download langpacks we have translations for
    for lang in zh-hans zh-hant ja ko vi th de fr es ru it pt nl sv pl tr ar ro; do
        apt-get download -qq \
            -o "Dir::Etc::SourceList=$APT_DIR/sources.list" \
            -o "Dir::Etc::SourceParts=/dev/null" \
            -o "Dir::State::Lists=$APT_DIR/lists" \
            -o "Dir::Cache=$APT_DIR/cache" \
            "language-pack-gnome-${lang}-base" 2>/dev/null || true
    done
    cd "$SCRIPT_DIR"

    # ── Process each langpack ──────────────────────────────────────────
    patched=0

    for deb in "$DL_DIR"/*.deb; do
        [ -f "$deb" ] || continue
        deb_name=$(basename "$deb")

        # Extract .mo file
        EXTRACT_DIR="$APT_DIR/extract"
        rm -rf "$EXTRACT_DIR"
        mkdir -p "$EXTRACT_DIR"
        dpkg-deb -x "$deb" "$EXTRACT_DIR" 2>/dev/null || continue

        # Regional catalogues share langpacks (zh_HK/zh_TW, pt/pt_BR).
        # Process every catalogue, not just find's first result.
        while IFS= read -r -d '' mo_file; do
            locale_dir=${mo_file%/LC_MESSAGES/gnome-shell.mo}
            lang=${locale_dir##*/}
            [[ "$lang" =~ ^en ]] && continue
            [[ -n "${ADD[$lang]+isset}" ]] || continue

            echo "[$SUITE] Patching gnome-shell.mo: $lang"
            po_file="$APT_DIR/gnome-shell-$lang.po"
            msgunfmt "$mo_file" -o "$po_file"
            sed -i '/msgid "Pin to Dash"/{n;s/.*/msgstr "'"${ADD[$lang]}"'"/}' "$po_file"
            sed -i '/msgid "Unpin"/{n;s/.*/msgstr "'"${REMOVE[$lang]}"'"/}' "$po_file"

            out_dir="$DEPLOY_DIR/$lang/LC_MESSAGES"
            mkdir -p "$out_dir"
            msgfmt "$po_file" -o "$out_dir/gnome-shell.mo"
            patched=$((patched + 1))
        done < <(find "$EXTRACT_DIR" -name "gnome-shell.mo" -path "*/LC_MESSAGES/*" -print0)
    done

    # English: create from scratch (en langpack has no .mo — en is the source)
    if [ -n "${ADD[en]+isset}" ]; then
        echo "[$SUITE] Creating English gnome-shell.mo..."
        out_dir="$DEPLOY_DIR/en/LC_MESSAGES"
        mkdir -p "$out_dir"
        cat > "$APT_DIR/gnome-shell-en.po" << EOF
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

msgid "Pin to Dash"
msgstr "${ADD[en]}"

msgid "Unpin"
msgstr "${REMOVE[en]}"
EOF
        msgfmt "$APT_DIR/gnome-shell-en.po" -o "$out_dir/gnome-shell.mo"
        patched=$((patched + 1))
    fi

    rm -rf "$APT_DIR"
    trap - EXIT

    echo "[$SUITE] Built $patched locale overrides"
done

echo "Done."
