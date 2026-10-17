#!/usr/bin/env bash
# omniport-restore.sh — runs inside the omniport live ISO
#
# Detects the Ventoy data partition, finds migration archives, and launches
# the restore wizard (or CLI fallback).

set -euo pipefail

OMNI_DIR="/opt/omniport"
DATA_MOUNT="/mnt/ventoy-data"
ARCHIVE_NAME="omniport-archive"

log() { echo "[omniport] $*"; }

# ---------------------------------------------------------------------------
# 1. Find and mount the Ventoy data partition
# ---------------------------------------------------------------------------

find_ventoy_data() {
    # Ventoy data partition is typically the largest exFAT/NTFS partition.
    # Look for partitions with the omniport-archive directory.
    for dev in $(lsblk -rn -o NAME,TYPE | awk '$2=="part"{print $1}'); do
        devpath="/dev/$dev"
        # Try mounting as exFAT, then NTFS, then vfat
        for fstype in exfat ntfs vfat; do
            mkdir -p "$DATA_MOUNT"
            if mount -t "$fstype" "$devpath" "$DATA_MOUNT" 2>/dev/null; then
                if [ -d "$DATA_MOUNT/$ARCHIVE_NAME" ]; then
                    log "Found migration archive on $devpath ($fstype)"
                    return 0
                fi
                umount "$DATA_MOUNT" 2>/dev/null || true
            fi
        done
    done
    return 1
}

# ---------------------------------------------------------------------------
# 2. Update omniport from git (optional, needs network)
# ---------------------------------------------------------------------------

update_from_git() {
    if [ -d "$OMNI_DIR/.git" ] && command -v git &>/dev/null; then
        if ping -c 1 github.com &>/dev/null 2>&1; then
            log "Updating omniport from git..."
            cd "$OMNI_DIR" && git pull --quiet 2>/dev/null || log "Git pull failed, using cached version"
        else
            log "No network, using cached omniport"
        fi
    fi
}

# ---------------------------------------------------------------------------
# 3. Launch restore
# ---------------------------------------------------------------------------

main() {
    log "Omniport Live Environment"
    log "========================"

    # Mount Ventoy data partition
    if find_ventoy_data; then
        log "Data partition mounted at $DATA_MOUNT"
        # Count archives
        archive_count=$(find "$DATA_MOUNT" -maxdepth 2 -name "manifest.json" -path "*/$ARCHIVE_NAME/*" 2>/dev/null | wc -l)
        log "Found $archive_count migration archive(s)"
    else
        log "No Ventoy data partition with migration archive found."
        log "You can build one by running: python3 omniport.py build /path/to/usb"
        DATA_MOUNT=""
    fi

    # Update from git if possible
    update_from_git

    # Check if GUI is available
    if [ -n "${DISPLAY:-}" ] && command -v python3 &>/dev/null; then
        # Try PyQt6 GUI
        if python3 -c "import PyQt6" 2>/dev/null; then
            log "Launching restore wizard (GUI)..."
            if [ -n "$DATA_MOUNT" ]; then
                python3 "$OMNI_DIR/wizard_ui.py" --archive-dir "$DATA_MOUNT/$ARCHIVE_NAME" 2>&1 || {
                    log "GUI failed, falling back to CLI"
                    run_cli
                }
            else
                python3 "$OMNI_DIR/wizard_ui.py" 2>&1 || run_cli
            fi
        else
            log "PyQt6 not available, using CLI"
            run_cli
        fi
    else
        run_cli
    fi
}

run_cli() {
    log "Launching restore (CLI)..."
    if [ -n "$DATA_MOUNT" ] && [ -d "$DATA_MOUNT/$ARCHIVE_NAME" ]; then
        python3 "$OMNI_DIR/omniport.py" preview "$DATA_MOUNT/$ARCHIVE_NAME"
        echo ""
        read -p "Proceed with restore? [y/N] " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            python3 "$OMNI_DIR/omniport.py" restore "$DATA_MOUNT/$ARCHIVE_NAME"
        fi
    else
        echo "No migration archive found."
        echo "Usage: python3 omniport.py build <destination>"
    fi
}

main "$@"
