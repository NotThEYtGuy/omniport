#!/usr/bin/env bash
# setup_ventoy.sh — automate Ventoy + Omniport integration
#
# Fully self-contained: no external downloads. Assumes Ventoy is already
# available on the system (in PATH, /opt/ventoy, or bundled in the repo).
#
# Usage:
#   ./setup_ventoy.sh /dev/sdX [--dry-run]
#
# Example:
#   ./setup_ventoy.sh /dev/sdc          # Set up /dev/sdc
#   ./setup_ventoy.sh /dev/sdc --dry-run # Preview only

set -euo pipefail

log() { echo "[setup] $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

DEVICE="${1:-}"
DRY_RUN=false

if [ -z "$DEVICE" ]; then
    echo "Usage: $0 /dev/sdX [--dry-run]"
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

DEV_SIZE=$(lsblk -rn -o SIZE "$DEVICE" 2>/dev/null | head -1)

log "Target device: $DEVICE ($DEV_SIZE)"
log "Mode: $([ "$DRY_RUN" = true ] && echo "DRY RUN (no changes)" || echo "LIVE")"
echo ""

# ---------------------------------------------------------------------------
# Find Ventoy (no downloads)
# ---------------------------------------------------------------------------

find_ventoy() {
    # Check common locations
    for dir in "$SCRIPT_DIR/ventoy" /opt/ventoy /usr/local/ventoy "$HOME/ventoy"; do
        if [ -f "$dir/Ventoy2Disk.sh" ]; then
            echo "$dir"
            return 0
        fi
    done
    # Check PATH
    if command -v Ventoy2Disk.sh &>/dev/null; then
        dirname "$(command -v Ventoy2Disk.sh)"
        return 0
    fi
    # Check if ventoy is installed as a package
    if command -v ventoy &>/dev/null; then
        echo "$(dirname "$(command -v ventoy)")"
        return 0
    fi
    return 1
}

VENTOY_DIR=$(find_ventoy) || {
    log "ERROR: Ventoy not found."
    echo ""
    echo "Ventoy must be available locally (no downloads). Options:"
    echo "  1. Download Ventoy from https://github.com/ventoy/Ventoy/releases"
    echo "     and extract to: $SCRIPT_DIR/ventoy/ or /opt/ventoy/"
    echo "  2. Install via your package manager if available"
    echo "  3. Place Ventoy2Disk.sh in PATH"
    echo ""
    echo "Expected: Ventoy2Disk.sh in one of:"
    echo "  - $SCRIPT_DIR/ventoy/"
    echo "  - /opt/ventoy/"
    echo "  - /usr/local/ventoy/"
    echo "  - ~/ventoy/"
    exit 1
}

log "Found Ventoy at: $VENTOY_DIR"

# ---------------------------------------------------------------------------
# Show plan
# ---------------------------------------------------------------------------

echo "=== SETUP PLAN ==="
echo "Device: $DEVICE ($DEV_SIZE)"
echo "Ventoy: $VENTOY_DIR"
echo ""
echo "This will:"
echo "  1. Install Ventoy on $DEVICE (creates boot + ISO + data partitions)"
echo "  2. Copy omniport tools to the data partition"
echo "  3. Copy omniport-live ISO if available (or any ISO in project dir)"
echo ""
echo "ALL DATA ON $DEVICE WILL BE ERASED!"
echo ""

if [ "$DRY_RUN" = true ]; then
    log "Dry run complete. No changes made."
    exit 0
fi

# ---------------------------------------------------------------------------
# 1. Install Ventoy
# ---------------------------------------------------------------------------

log "Installing Ventoy on $DEVICE..."
"$VENTOY_DIR/Ventoy2Disk.sh" -i "$DEVICE" 2>&1 | tail -5

# Wait for partitions to settle
sleep 3
udevadm settle 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Detect and mount partitions
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

if [ -z "$DATA_PART" ]; then
    DATA_PART=$(lsblk -rn -o NAME,TYPE "$DEVICE" | awk '$2=="part"{print "/dev/"$1}' | tail -1)
fi

log "ISO partition: $ISO_PART"
log "Data partition: $DATA_PART"

ISO_MOUNT=""
DATA_MOUNT=""

# Mount ISO partition
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
    udisksctl mount -b "$ISO_PART" 2>/dev/null || true
    ISO_MOUNT=$(findmnt -n -o TARGET "$ISO_PART" 2>/dev/null || echo "$ISO_MOUNT")
fi
log "ISO partition mounted at $ISO_MOUNT"

# Mount data partition
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
    udisksctl mount -b "$DATA_PART" 2>/dev/null || true
    DATA_MOUNT=$(findmnt -n -o TARGET "$DATA_PART" 2>/dev/null || echo "$DATA_MOUNT")
fi
log "Data partition mounted at $DATA_MOUNT"

# ---------------------------------------------------------------------------
# 3. Set up omniport on data partition
# ---------------------------------------------------------------------------

if [ -n "$DATA_MOUNT" ] && mountpoint -q "$DATA_MOUNT" 2>/dev/null; then
    log "Setting up omniport on data partition..."
    mkdir -p "$DATA_MOUNT/omniport-tools"
    mkdir -p "$DATA_MOUNT/omniport-archive"

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
# 4. Copy ISO (if available)
# ---------------------------------------------------------------------------

ISO_FILE=""
for f in "$SCRIPT_DIR"/omniport-live-*.iso "$SCRIPT_DIR"/*.iso; do
    [ -f "$f" ] && ISO_FILE="$f" && break
done

if [ -n "$ISO_FILE" ] && [ -n "$ISO_MOUNT" ] && mountpoint -q "$ISO_MOUNT" 2>/dev/null; then
    log "Copying ISO: $(basename "$ISO_FILE")"
    cp "$ISO_FILE" "$ISO_MOUNT/"
    log "ISO copied."
else
    log "No ISO found in project dir."
    log "Download a live distro ISO and copy it to: $ISO_MOUNT/"
    log "Or build one: ./build_iso.sh 1.0"
fi

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------

echo ""
log "=== Setup Complete ==="
log "Device: $DEVICE"
log "ISO partition: $ISO_MOUNT"
log "Data partition: $DATA_MOUNT"
echo ""
log "Next steps:"
log "  1. Build a migration archive: python3 wizard_ui.py → select $DATA_MOUNT"
log "  2. (Optional) Download a live ISO → copy to $ISO_MOUNT/"
log "  3. Boot from USB → Ventoy menu → select ISO → restore wizard launches"