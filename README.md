# Omniport

Your personal environment, portable.

**The idea:** switching Linux distros (or switching to Linux at all) usually
means a weekend of backing up files, remembering every app, and rebuilding a
USB stick. Omniport is the tool that makes it one process:

1. **Setup** — you run the tool on your current machine and it gathers your
   files and app inventory into a small, portable archive.
2. **Bootable media / ISO** — a live Linux environment boots from the same
   media and runs the restore flow for you.
3. **Wizard** — you pick a distro, choose restore vs. fresh install, and the
   environment comes back.

This repository contains the first working slice of the idea:

- `design.md` — the architecture, decisions and roadmap.
- `omniport.py` — cross-platform build / preview / restore tool (stdlib only).
- `prep_ui.py` — PyQt6 desktop UI for the Capture → Prepare stage.
- `wizard_ui.py` — step-by-step wizard (5 pages: system info, file selection, package selection, destination & build, USB setup).
- `omniport-restore.sh` — runs inside the live ISO to detect Ventoy data partition and launch restore.
- `build_iso.sh` — builds the omniport live ISO using Debian live-build.

## Current usage

```bash
# 1. Build an archive from this computer
python3 omniport.py build ~/omniport-archive

# 2. See what a restore would do (dry run — nothing is changed)
python3 omniport.py preview ~/omniport-archive

# 3. Restore into a target home directory (or the current one)
python3 omniport.py restore ~/omniport-archive --home /mnt/usb/home

# The restore will interactively ask whether to include sensitive files
# (SSH/GPG keys, browser profiles). For scripted use:
python3 omniport.py restore ~/omniport-archive --home /mnt/usb/home --restore-sensitive
python3 omniport.py restore ~/omniport-archive --home /mnt/usb/home --skip-sensitive
```

The archive is a plain directory:

```
~/omniport-archive/
  manifest.json    metadata about the machine it came from
  packages.json    installed-app inventory
  home/            your files (dotfiles, documents, browser data, ...)
  backup/          created by restore — files it replaced
  plan.json        created by preview — the restore plan
```

## Desktop UI (prep stage)

A PyQt6 graphical interface for the Capture → Prepare stage:

```bash
python3 prep_ui.py        # Simple UI
python3 wizard_ui.py      # Step-by-step wizard (recommended)
```

The UI provides:
- System summary (OS, package manager, package count, file count, sizes)
- File browser with preview (select/deselect individual files)
- Package selection (grouped by source)
- Destination picker + build with progress
- USB setup (detect drives, install Ventoy, copy tools)

## Design decisions

- **Storage-optimized backup.** We chose a file-level home-directory backup
  plus an installed-app inventory over a full disk image: it keeps the archive
  small enough for a spare USB, and covers ~95% of what people actually miss
  when switching distros. A full-image mode is planned as an opt-in feature.
- **Cross-platform.** The same script runs on macOS, Linux and Windows; app
  inventory is detected per platform (dpkg/pacman/rpm/flatpak/snap, Homebrew /
  MacPorts, winget).
- **Safe by default.** The restore flow moves anything it is about to replace
  into the archive's `backup/` folder before copying, so a restore is
  recoverable.
- **Three-tier safety policy.** Implemented in the CLI:
  - *Safe to migrate*: personal files, app data, browser profiles, documents,
    desktop preferences — auto-restored.
  - *Needs confirmation*: SSH/GPG keys, browser profiles with passwords —
    interactive prompt, safe default is skip.
  - *Don't migrate automatically*: kernel, bootloader config, hardware-specific
    drivers, `/etc` wholesale — never restored.
- **Package translation (preview).** Best-effort alias table for offline preview.
  Authoritative resolution happens in the ISO using the target distro's package
  manager.

## Not implemented yet

- The bootable ISO itself (live environment carrying the archive + wizard).
- Distro-agnostic migration USB (the "one permanent USB" concept).
- Demo mode, integrated with DistroSea (distrosea.com) for trying a distro
  before committing to it.

## Ventoy setup (for testing with a USB drive)

1. **Install Ventoy** on your USB drive:
   ```bash
   # Download Ventoy from https://github.com/ventoy/Ventoy/releases
   sudo ./Ventoy2Disk.sh -i /dev/sdX  # Replace sdX with your USB device
   ```

2. **Build the omniport live ISO** (optional, for full testing):
   ```bash
   sudo apt install live-build
   ./build_iso.sh 1.0
   # Copy omniport-live-v1.0.iso to the Ventoy ISO partition
   ```

3. **Build a migration archive** to the USB's data partition:
   ```bash
   python3 wizard_ui.py
   # Select the USB data partition as destination
   ```

4. **Boot from USB** → Ventoy menu → select omniport-live ISO → restore wizard launches

## Git workflow

Clone and contribute:
```bash
git clone https://github.com/NotThEYtGuy/omniport.git
cd omniport
python3 wizard_ui.py  # Test the prep UI
```

The live ISO can pull the latest scripts from the repo on boot (if network is available).