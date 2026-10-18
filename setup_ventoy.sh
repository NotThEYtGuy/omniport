#!/usr/bin/env bash
# setup_ventoy.sh — automate Ventoy + Omniport integration
#
# Sets up a USB drive with Ventoy + Omniport for testing.
# REQUIRES: explicit device argument, shows plan before making changes.
#
# Usage (fully automated):
#   ./setup_ventoy.sh /dev/sdX [--dry-run]
#
# Example:
#   ./setup_ventoy.sh /dev/sdc          # Set up /dev/sdc
#   ./setup_ventoy.sh /dev/sdc --dry-run # Preview only

set -euo pipefail

log() { echo "[setup] $*"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

DEVICE="${1:-}"
DRY_RUN=false

if [ -z "$DEVICE" ]; then
    echo "Usage (fully automated): $0 /dev/sdX [--dry-run]"
    echo ""
    echo "Available disks:"
    lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL 2>/dev/null | grep disk
    echo ""
    echo "Example: $0 /dev/sdc"
    exit 1
fi

if [ "${2:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

# Validate device exists
if [ ! -b "$DEVICE" ]; then
    log "ERROR: $DEVICE is not a block device."
    exit 1
fi

# Safety check: refuse to touch system disk
SYSTEM_DISK=$(lsblk -rn -o PKNAME "$(findmnt -n -o SOURCE /)" 2>/dev/null || echo "")
if [ "$DEVICE" = "/dev/$SYSTEM_DISK" ]; then
    log "ERROR: $DEVICE appears to be the system disk. Refusing."
    exit 1
fi

DEV_NAME=$(basename "$DEVICE")
DEV_SIZE=$(lsblk -rn -o SIZE "$DEVICE" 2>/dev/null | head -1)

log "Target device: $DEVICE ($DEV_SIZE)"
log "Mode: $([ "$DRY_RUN" = true ] && echo "DRY RUN (no changes)" || echo "LIVE")"
echo ""

# ---------------------------------------------------------------------------
# Show plan
# ---------------------------------------------------------------------------

echo "=== SETUP PLAN ==="
echo "Device: $DEVICE ($DEV_SIZE)"
echo ""
echo "This will:"
echo "  1. Install Ventoy on $DEVICE (creates boot + ISO + data partitions)"
echo "  2. Copy omniport tools to the data partition"
echo "  3. (Optional) Copy omniport-live ISO if available"
echo ""
echo "ALL DATA ON $DEVICE WILL BE ERASED!"
echo ""

if [ "$DRY_RUN" = true ]; then
    log "Dry run complete. No changes made."
    exit 0
fi

log "Proceeding with automatic setup (no confirmation needed)..."

# ---------------------------------------------------------------------------
# 1. Download Ventoy if not present
# ---------------------------------------------------------------------------

VENTOY_DIR="/tmp/opencode/ventoy"
VENTOY_VERSION="1.0.99"

if [ ! -f "$VENTOY_DIR/Ventoy2Disk.sh" ]; then
    log "Downloading Ventoy v$VENTOY_VERSION..."
    mkdir -p "$VENTOY_DIR"
    cd /tmp/opencode
    wget -q "https://github.com/ventoy/Ventoy/releases/download/v$VENTOY_VERSION/ventoy-$VENTOY_VERSION-linux.tar.gz" -O ventoy.tar.gz
    tar -xf ventoy.tar.gz -C "$VENTOY_DIR" --strip-components=1
    cd /home/casaos/project
    log "Ventoy downloaded."
else
    log "Ventoy already downloaded."
fi

# ---------------------------------------------------------------------------
# 2. Install Ventoy
# ---------------------------------------------------------------------------

log "Installing Ventoy on $DEVICE..."
"$VENTOY_DIR/Ventoy2Disk.sh" -i "$DEVICE" 2>&1 | tail -5

# Wait for partitions to settle
sleep 3
udevadm settle 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. Detect and mount partitions
# ---------------------------------------------------------------------------

ISO_PART=""
DATA_PART=""

for dev in $(lsblk -rn -o NAME,TYPE "$DEVICE" | awk '$2=="part"{print "/dev/"$1}'); do
    label=$(lsblk -rn -o LABEL "$dev" 2>/dev/null)
    if [ "$label" = "Ventoy" ]; then
        ISO_PART="$dev"
    elif [ -z "$DATA_PART" ] && [ "$label" != "VTOYEFI" ]; then
        DATA_PART="$dev"
    fi
done

# If no data partition found by label, use the last partition
if [ -z "$DATA_PART" ]; then
    DATA_PART=$(lsblk -rn -o NAME,TYPE "$DEVICE" | awk '$2=="part"{print "/dev/"$1}' | tail -1)
fi

log "ISO partition: $ISO_PART"
log "Data partition: $DATA_PART"

# Mount ISO partition
ISO_MOUNT=""
for mp in /media/casaos/* /tmp/opencode/*; do
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
    udisksctl mount -b "$ISO_PART" 2>/dev/null || sudo mount "$ISO_PART" "$ISO_MOUNT" 2>/dev/null || true
fi
log "ISO partition mounted at $ISO_MOUNT"

# Mount data partition
DATA_MOUNT=""
for mp in /media/casaos/* /tmp/opencode/*; do
    if mountpoint -q "$mp" 2>/dev/null; then
        dev=$(findmnt -n -o SOURCE "$mp" 2>/dev/null)
        if [ "$dev" = "$DATA_PART" ]; then
            DATA_MOUNT="$mp"
            break
        fi
    fi
done

if [ -z "$DATA_MOUNT" ]; then
    DATA_MOUNT="/tmp/opencode/ventoy-data"
    mkdir -p "$DATA_MOUNT"
    udisksctl mount -b "$DATA_PART" 2>/dev/null || sudo mount "$DATA_PART" "$DATA_MOUNT" 2>/dev/null || true
fi
log "Data partition mounted at $DATA_MOUNT"

# ---------------------------------------------------------------------------
# 4. Set up omniport on data partition
# ---------------------------------------------------------------------------

if [ -n "$DATA_MOUNT" ] && mountpoint -q "$DATA_MOUNT" 2>/dev/null; then
    log "Setting up omniport on data partition..."
    mkdir -p "$DATA_MOUNT/omniport-tools"
    mkdir -p "$DATA_MOUNT/omniport-archive"

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    for f in omniport.py prep_ui.py wizard_ui.py omniport-restore.sh; do
        if [ -f "$SCRIPT_DIR/$f" ]; then
            cp "$SCRIPT_DIR/$f" "$DATA_MOUNT/omniport-tools/"
        fi
    done
    chmod +x "$DATA_MOUNT/omniport-tools/omniport-restore.sh" 2>/dev/null || true
    log "Omniport tools copied."
else
    log "WARNING: Data partition not mounted. Skipping omniport setup."
fi

# ---------------------------------------------------------------------------
# 5. Copy ISO (if available)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_FILE=""
for f in "$SCRIPT_DIR"/omniport-live-*.iso; do
    [ -f "$f" ] && ISO_FILE="$f" && break
done

if [ -n "$ISO_FILE" ] && [ -n "$ISO_MOUNT" ] && mountpoint -q "$ISO_MOUNT" 2>/dev/null; then
    log "Copying ISO: $(basename "$ISO_FILE")"
    cp "$ISO_FILE" "$ISO_MOUNT/"
    log "ISO copied."
else
    log "No omniport-live ISO found. Build one with: ./build_iso.sh 1.0"
    log "Or download a live distro ISO and copy it to: $ISO_MOUNT/"
fi

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------

echo ""
log "=== Setup Complete ==="
log "Device: $DEVICE"
log "ISO partition: $ISO_MOUNT"
log "Data partition: $DATA_MOUNT"
echo ""
log "Next steps:"
log "  1. Build a migration archive: python3 wizard_ui.py → select $DATA_MOUNT"
log "  2. (Optional) Build live ISO: ./build_iso.sh 1.0 → copy to $ISO_MOUNT/"
log "  3. Boot from USB → Ventoy menu → select omniport-live ISO"
log "  4. Restore wizard launches automatically"