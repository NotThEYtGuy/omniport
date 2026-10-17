# Omniport — personal environment backup & restore

## The vision

**One-click Linux migration.** Boot a migration USB/ISO, and migrate your entire
working environment from any Linux distro to any other — or from Linux to a
fresh install — in a single guided flow.

## The refined flow (as designed)

```
🔄 One-click Linux migration

You boot your special USB/ISO, and it gives you something like:

Current system
- Linux Mint
- 480 GB used
- 137 installed packages
- 42 user applications
- 85 GB personal files
- Browser profiles/bookmarks
- SSH keys/configuration
- Desktop settings
- Network settings

Choose destination
🐧 Fedora
🐧 Ubuntu
🐧 Arch
🐧 Debian
🐧 openSUSE
🐧 ...custom ISO

Then:

1. 📦 Capture
   - Personal files
   - Application data
   - Desktop configuration
   - Browser profiles
   - Package/application list
   - Flatpaks
   - Snap packages
   - AppImages
   - Selected system configuration
   - Maybe even container/VM definitions

2. 💾 Prepare
   The program creates a migration bundle on another disk, NAS, external drive,
   or network location.

3. 💿 Install
   It installs the new distro from the same bootable environment.
   No hunting for another USB.

4. 🔄 Restore
   It maps the old system onto the new one:
     Firefox → Firefox
     VS Code → VS Code
     Steam → Steam
     Documents → Documents
     Pictures → Pictures
     SSH configuration → SSH configuration

For packages that don't exist in the new distro, it could say:
   ⚠️ foo-package isn't available on Fedora.
   Alternative: bar-package
   [Install alternative] [Skip]
```

## The distro-agnostic migration USB

The ISO could be distro-agnostic. Instead of downloading a new ISO every time,
you have one permanent **"Linux Migration USB."**

You boot it, choose:
- **Install a new Linux distribution**

Then it could:
- Download the selected distro while running from the USB/network, **or**
- Use the destination ISO you provide.

Your USB becomes essentially a **Linux migration/recovery toolbox**, rather than
being tied to Ubuntu, Fedora, Mint, etc.

## Safety tiers (the key design insight)

The restore **should not blindly restore system files** — that could turn a
perfectly good new installation into a broken one. It should distinguish between
three tiers:

### ✅ Safe to migrate
- Personal files
- Application data
- Browser profiles
- Documents
- Desktop preferences

### 🔄 Needs translation
- Installed packages
- Services
- Drivers
- System configuration

→ Handled via per-package mapping: install what's available, warn on missing,
suggest alternatives, user picks install/skip.

### 🚫 Don't migrate automatically
- Kernel
- Bootloader configuration
- Hardware-specific drivers
- `/etc` wholesale

## Current implementation status

### Implemented in this repository

- `omniport.py` — cross-platform build / preview / restore tool (stdlib only)
- `design.md` — this architecture document
- `README.md` — usage instructions

### Implemented behavior

- **Build** (`omniport build`): scans home directory + installed apps, writes
  archive. Skips cache directories (`.cache`, `Library/Caches`, `AppData\Temp`).
- **Preview** (`omniport preview`): dry-run plan — which files would be copied,
  which would be overwritten, which packages need installing.
- **Restore** (`omniport restore`): executes plan; overwrites are backed up into
  the archive's `backup/` folder first.

### Not implemented yet

- The bootable ISO itself (needs distro-build tooling such as Cubic/Buildroot).
- The step-by-step wizard UI (the CLI is the reference implementation of the
  core flow; a wizard front-end can be layered on top of the same planning logic).
- Distro-agnostic migration USB generation.
- Demo mode (DistroSea integration).

### Implemented in the CLI

- **Safety-tier enforcement** — three tiers (safe/sensitive/blocked) with
  interactive prompting and safe defaults.
- **Package translation (preview)** — `PACKAGE_ALIASES` table for best-effort
  offline preview; authoritative resolution happens in the ISO.

## Package translation: preview vs. authoritative

The `PACKAGE_ALIASES` table and `translate_package()` in `omniport.py` are a
**best-effort preview aid** — they give the user a rough idea of what will
translate before they even boot the ISO. They are inherently incomplete.

The **authoritative translation happens in the ISO**, because the ISO runs the
target distro natively and has direct access to its package manager and
repositories:

```
Source machine (omniport build)
  → captures package list (names + versions)
  → archive stores them

ISO (target distro)
  → reads package list from archive
  → for each package:
      → try: package_manager install <name>
      → if fails: search repos for alternatives (apt search, dnf search, ...)
      → present: [Install alternative] / [Skip] / [Try same name]
  → user decides per-package
```

This means the ISO can use real package search, `provides`, dependency
resolution, etc. — far more accurate than any static alias table. The CLI's
translation is a convenience for offline preview only.

## Ventoy-based deployment

Instead of building a custom ISO from scratch, Omniport uses **Ventoy** as the
bootloader. This gives us:

- **One USB, multiple ISOs** — drop any live distro ISO onto the Ventoy
  partition and it becomes bootable
- **Separate data partition** — migration archives live on a separate partition,
  independent of the ISOs
- **No bootloader engineering** — Ventoy handles EFI/BIOS boot

### USB layout

```
64GB USB Drive
├── Ventoy Boot Partition (~32MB)
│   └── Ventoy bootloader (EFI + BIOS)
├── Ventoy ISO Partition (exFAT/NTFS)
│   └── omniport-live-v1.iso      ← custom live environment
└── Data Partition (remaining space)
    ├── omniport-archive/          ← migration archives
    │   ├── manifest.json
    │   ├── packages.json
    │   └── home/
    └── omniport-restore.sh        ← restore script (auto-runs on boot)
```

### omniport-live ISO

A minimal live Linux (Debian/Ubuntu-based) with:
- Python 3 + PyQt6 pre-installed
- omniport.py, wizard_ui.py, prep_ui.py
- omniport-restore.sh (auto-runs on boot)
- Pulls latest scripts from git if network is available

### Git integration

The live ISO can `git pull` the latest omniport scripts on boot, so users
always get the newest version without rebuilding the ISO. The source code,
ISO builder, and restore script all live in the same git repository.
