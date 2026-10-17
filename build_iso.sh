#!/usr/bin/env bash
# build_iso.sh — builds the omniport live ISO
#
# Uses Debian live-build to create a minimal live ISO with omniport
# pre-installed. The resulting ISO is bootable via Ventoy.
#
# Requirements:
#   sudo apt install live-build
#   Internet connection (for package downloads)
#
# Usage:
#   ./build_iso.sh [version]
#   Example: ./build_iso.sh 1.0

set -euo pipefail

VERSION="${1:-1.0}"
ISO_NAME="omniport-live-v${VERSION}.iso"
BUILD_DIR="/tmp/omniport-iso-build"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[build] $*"; }

# ---------------------------------------------------------------------------
# 1. Clean and set up build directory
# ---------------------------------------------------------------------------

log "Setting up build directory: $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# ---------------------------------------------------------------------------
# 2. Configure live-build
# ---------------------------------------------------------------------------

log "Configuring live-build..."
lb config \
    --distribution bookworm \
    --architecture amd64 \
    --archive-areas "main contrib non-free non-free-firmware" \
    --bootappend-live "boot=live components quiet splash" \
    --debian-installer none \
    --memtest none \
    --win32-loader none \
    --iso-volume "OMNIPORT_LIVE" \
    --iso-publisher "Omniport Project" \
    --iso-application "Omniport Live Migration Environment"

# ---------------------------------------------------------------------------
# 3. Add omniport to the chroot
# ---------------------------------------------------------------------------

log "Adding omniport to chroot..."
mkdir -p config/hooks/normal
cat > config/hooks/normal/0100-omniport.chroot <<'HOOK'
#!/bin/bash
set -e

# Install dependencies
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-pyqt6 \
    git curl wget

# Install omniport
mkdir -p /opt/omniport
cd /opt/omniport

# Copy omniport files (from build context)
# These are injected by the build script
cp /tmp/omniport-src/* /opt/omniport/ 2>/dev/null || true

# Make scripts executable
chmod +x /opt/omniport/omniport-restore.sh 2>/dev/null || true

# Create auto-start script for live session
cat > /etc/xdg/autostart/omniport-restore.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Omniport Restore
Exec=/opt/omniport/omniport-restore.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
DESKTOP

# Also create a terminal-friendly entry point
cat > /usr/local/bin/omniport <<'WRAPPER'
#!/bin/bash
cd /opt/omniport
python3 omniport.py "$@"
WRAPPER
chmod +x /usr/local/bin/omniport

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
HOOK
chmod +x config/hooks/normal/0100-omniport.chroot

# ---------------------------------------------------------------------------
# 4. Copy omniport source files into build context
# ---------------------------------------------------------------------------

log "Copying omniport source files..."
mkdir -p /tmp/omniport-src
cp "$SCRIPT_DIR"/omniport.py /tmp/omniport-src/
cp "$SCRIPT_DIR"/prep_ui.py /tmp/omniport-src/
cp "$SCRIPT_DIR"/wizard_ui.py /tmp/omniport-src/
cp "$SCRIPT_DIR"/omniport-restore.sh /tmp/omniport-src/

# Copy source into chroot via a hook
cat > config/hooks/normal/0050-copy-src.chroot <<'HOOK'
#!/bin/bash
set -e
mkdir -p /opt/omniport
cp -r /tmp/omniport-src/* /opt/omniport/
chmod +x /opt/omniport/omniport-restore.sh
HOOK
chmod +x config/hooks/normal/0050-copy-src.chroot

# ---------------------------------------------------------------------------
# 5. Build the ISO
# ---------------------------------------------------------------------------

log "Building ISO (this will take a while)..."
lb build 2>&1 | tail -20

# ---------------------------------------------------------------------------
# 6. Rename and verify
# ---------------------------------------------------------------------------

if [ -f "live-image-amd64.hybrid.iso" ]; then
    mv "live-image-amd64.hybrid.iso" "$ISO_NAME"
    log "ISO built successfully: $ISO_NAME"
    log "Size: $(du -h "$ISO_NAME" | cut -f1)"
    log ""
    log "Next steps:"
    log "  1. Copy $ISO_NAME to your Ventoy USB's ISO partition"
    log "  2. Build a migration archive: python3 omniport.py build /path/to/usb-data"
    log "  3. Boot from the USB and test!"
else
    log "ERROR: ISO build failed. Check the output above."
    exit 1
fi
