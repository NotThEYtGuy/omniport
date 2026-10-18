#!/usr/bin/env bash
# setup_ventoy.sh — automate Ventoy + Omniport integration
#
# Detects an existing Ventoy installation, sets up the data partition for
# migration archives, and optionally copies the omniport live ISO.
#
# Usage:
#   ./setup_ventoy.sh [ventoy_iso_partition] [ventoy_data_partition]
#
# If no arguments given, auto-detects Ventoy partitions.

set -euo pipefail

log() { echo "[setup] $*"; }

# ---------------------------------------------------------------------------
# 1. Detect Ventoy partitions
# ---------------------------------------------------------------------------

detect_ventoy() {
    # Find Ventoy ISO partition (exfat/vfat with "Ventoy" label)
    for dev in $(lsblk -rn -o NAME,TYPE | awk '$2=="part"{print $1}'); do
        devpath="/dev/$dev"
        label=$(lsblk -rn -o LABEL "$devpath" 2>/dev/null)
        if [ "$label" = "Ventoy" ]; then
            echo "$devpath"
            return 0
        fi
    done
    return 1
}

# Auto-detect or use provided
if [ -n "${1:-}" ]; then
    ISO_PART="$1"
else
    ISO_PART=$(detect_ventoy | head -1)
fi

if [ -n "${2:-}" ]; then
    DATA_PART="$2"
else
    # Data partition is the largest non-boot partition
    DATA_PART=$(lsblk -rn -o NAME,SIZE,TYPE | awk '$3=="part" && $2 ~ /G/ {print $1, $2}' | sort -k2 -rh | head -1 | awk '{print "/dev/"$1}')
fi

if [ -z "$ISO_PART" ]; then
    log "ERROR: Could not detect Ventoy ISO partition."
    log "Usage: $0 <iso_partition> <data_partition>"
    log "Example: $0 /dev/sdb1 /dev/sdb4"
    exit 1
fi

log "Ventoy ISO partition: $ISO_PART"
log "Data partition: $DATA_PART"

# ---------------------------------------------------------------------------
# 2. Mount partitions
# ---------------------------------------------------------------------------

ISO_MOUNT=""
DATA_MOUNT=""
for mp in /media/casaos/*; do
    if mountpoint -q "$mp" 2>/dev/null; then
        dev=$(findmnt -n -o SOURCE "$mp" 2>/dev/null)
        if [ "$dev" = "$ISO_PART" ]; then
            ISO_MOUNT="$mp"
            break
        fi
    fi
done

if [ -z "$ISO_MOUNT" ]; then
    ISO_MOUNT="/tmp/opencode/ventoy-iso"
    mkdir -p "$ISO_MOUNT"
    udisksctl mount -b "$ISO_PART" 2>&1 || sudo mount "$ISO_PART" "$ISO_MOUNT"
fi
log "ISO partition mounted at $ISO_MOUNT"

# Mount data partition if not already mounted
DATA_MOUNT=""
# Check if already mounted
for mp in /media/casaos/*; do
    if mountpoint -q "$mp" 2>/dev/null; then
        dev=$(findmnt -n -o SOURCE "$mp" 2>/dev/null)
        if [ "$dev" = "$DATA_PART" ]; then
            DATA_MOUNT="$mp"
            break
        fi
    fi
done

if [ -z "$DATA_MOUNT" ]; then
    DATA_MOUNT="/tmp/opencode/omniport-data"
    mkdir -p "$DATA_MOUNT"
    udisksctl mount -b "$DATA_PART" 2>&1 || sudo mount "$DATA_PART" "$DATA_MOUNT"
fi
log "Data partition mounted at $DATA_MOUNT"

# ---------------------------------------------------------------------------
# 3. Set up omniport on data partition
# ---------------------------------------------------------------------------

log "Setting up omniport on data partition..."
mkdir -p "$DATA_MOUNT/omniport-archive"
mkdir -p "$DATA_MOUNT/omniport-tools"

# Copy restore script
cp "$(dirname "$0")/omniport-restore.sh" "$DATA_MOUNT/omniport-tools/" 2>/dev/null || \
    cp /home/casaos/project/omniport-restore.sh "$DATA_MOUNT/omniport-tools/" 2>/dev/null || true
chmod +x "$DATA_MOUNT/omniport-tools/omniport-restore.sh" 2>/dev/null || true

# Copy omniport scripts
for f in omniport.py prep_ui.py wizard_ui.py; do
    if [ -f "$(dirname "$0")/$f" ]; then
        cp "$(dirname "$0")/$f" "$DATA_MOUNT/omniport-tools/"
    elif [ -f "/home/casaos/project/$f" ]; then
        cp "/home/casaos/project/$f" "$DATA_MOUNT/omniport-tools/"
    fi
done

log "Omniport tools copied to data partition."

# ---------------------------------------------------------------------------
# 4. Copy ISO (if available)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_FILE=""
for f in "$SCRIPT_DIR"/omniport-live-*.iso; do
    [ -f "$f" ] && ISO_FILE="$f" && break
done

if [ -n "$ISO_FILE" ]; then
    log "Copying ISO: $(basename "$ISO_FILE")"
    cp "$ISO_FILE" "$ISO_MOUNT/"
    log "ISO copied to Ventoy partition."
else
    log "No omniport-live ISO found. Build one with: ./build_iso.sh 1.0"
    log "Or download a live distro ISO and copy it to: $ISO_MOUNT/"
fi

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------

log ""
log "=== Setup Complete ==="
log "Ventoy ISO partition: $ISO_MOUNT"
log "Data partition: $DATA_MOUNT"
log ""
log "Contents:"
log "  ISO partition: $(ls "$ISO_MOUNT"/*.iso 2>/dev/null | wc -l) ISO(s)"
log "  Data partition: omniport-tools/, omniport-archive/"
log ""
log "Next steps:"
log "  1. Build a migration archive: python3 wizard_ui.py → select $DATA_MOUNT as destination"
log "  2. (Optional) Build live ISO: ./build_iso.sh 1.0 → copy to $ISO_MOUNT/"
log "  3. Boot from USB → Ventoy menu → select omniport-live ISO"
log "  4. Restore wizard launches automatically"
