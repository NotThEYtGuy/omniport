#!/usr/bin/env python3
"""omniport — build, preview and restore a portable "personality archive".

Your personal environment, portable:

  * build    — scan this computer, gather the home directory and the list of
               installed apps, and write a small, self-contained archive.
  * preview  — read an archive and show exactly what a restore would do
               (dry run, nothing is modified).
  * restore  — execute a restore: copy the files back and report the apps
               that would be installed. Files that would be overwritten are
               first moved into the archive's ``backup`` folder.

Cross-platform: macOS, Linux and Windows. Standard library only — no pip
install required.

Archive layout (created by ``build``):

    archive/
      manifest.json    metadata about the machine the archive came from
      packages.json    the installed-app inventory
      home/            a copy of the user's home directory (symlinks kept)
      backup/          (created by ``restore``) files replaced during restore
      plan.json        (created by ``preview``) the restore plan, re-used by
                      ``restore`` if it is still valid
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = "omniport"


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------

def run(cmd):
    """Run a command; return (returncode, stdout) or None if it cannot run."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode, proc.stdout


def _dir_size(path):
    """Recursively sum file sizes in a directory."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def home_dir():
    """Path of the current user's home directory (resolved per platform)."""
    return Path(os.path.expanduser("~"))


# --------------------------------------------------------------------------
# archive building
# --------------------------------------------------------------------------

def _cache_ignored(directory, names):
    """shutil.copytree ignore callback — skip cache directories."""
    rel = os.path.relpath(directory, home_dir())
    rel_parts = rel.split(os.sep) if rel else []
    ignored = []
    for name in names:
        if name == ".cache":
            ignored.append(name)
        elif name == "Caches" and "Library" in rel_parts:
            ignored.append(name)  # macOS ~/Library/Caches
        elif name == "Temp" and "AppData" in rel_parts:
            ignored.append(name)  # Windows AppData\Local\Temp
    return ignored


def build(archive_path):
    """Scan this computer and write a personality archive to *archive_path*."""
    home = home_dir()
    archive_root = Path(archive_path).expanduser()
    if archive_root.exists():
        shutil.rmtree(archive_root)
    (archive_root / "home").mkdir(parents=True)

    files_copied = 0
    for entry in sorted(home.iterdir()):
        if entry.name == ".cache":
            continue  # skip ~/​.cache (huge, useless to restore)
        dest_entry = archive_root / "home" / entry.name
        if entry.is_symlink():
            dest_entry.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(entry), str(dest_entry))
        elif entry.is_dir():
            shutil.copytree(
                entry, dest_entry, symlinks=True, ignore=_cache_ignored
            )
        else:
            shutil.copy2(entry, dest_entry)
        for p in dest_entry.rglob("*") if dest_entry.is_dir() else [dest_entry]:
            if p.is_file() or p.is_symlink():
                files_copied += 1

    packages = installed_packages()
    manifest = {
        "app": APP,
        "version": "0.1",
        "created": datetime.now(timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "package_manager": detect_package_manager(),
            "package_manager_family": manager_family(detect_package_manager()),
        },
        "home_scanned": str(home),
        "files_copied": files_copied,
        "packages": packages,
    }
    write_json(archive_root / "manifest.json", manifest)
    write_json(archive_root / "packages.json", {"packages": packages})

    size = sum(f.stat().st_size for f in archive_root.rglob("*") if f.is_file())
    print(f"Archive written to {archive_root}")
    print(f"  files copied : {files_copied}")
    print(f"  apps found   : {len(packages)}")
    print(f"  archive size : {size / 1024 / 1024:.1f} MB")


# --------------------------------------------------------------------------
# installed-app inventory (per platform, best effort)
# --------------------------------------------------------------------------

def _try(cmd):
    proc = run(cmd)
    if proc is None or proc[0] != 0:
        return None
    return proc[1]


def _parse_rows(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name = parts[0].strip().strip('"')
        version = parts[1].strip().strip('"').split(" ")[0]
        if name:
            # strip architecture suffixes like ":amd64" for clean names
            clean = name.split(":")[0]
            rows.append((clean, version))
    return rows


def linux_packages():
    found = []

    out = _try(["dpkg-query", "-W", "-f=${binary:Package} ${Version}\\n"])
    if out is not None:
        for name, version in _parse_rows(out):
            found.append({"name": name, "version": version, "source": "apt"})
    if not found:
        out = _try(["pacman", "-Q"])
        if out is not None:
            for name, version in _parse_rows(out):
                found.append(
                    {"name": name, "version": version, "source": "pacman"}
                )
    if not found:
        out = _try(["rpm", "-qa", "--qf", "%{NAME} %{VERSION}\\n"])
        if out is not None:
            for name, version in _parse_rows(out):
                found.append({"name": name, "version": version, "source": "rpm"})

    out = _try(["flatpak", "list", "--app", "--columns=name"])
    if out is not None:
        for name in out.split():
            found.append({"name": name, "version": "", "source": "flatpak"})

    out = _try(["snap", "list"])
    if out is not None:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and not parts[0].lower().startswith("name"):
                found.append(
                    {"name": parts[0], "version": parts[1], "source": "snap"}
                )

    return found


def darwin_packages():
    found = []
    if shutil.which("brew"):
        out = _try(["brew", "list", "--formula", "--json=v1"])
        if out is not None:
            try:
                data = json.loads(out)
                for formula in data.get("formulae", []):
                    found.append(
                        {
                            "name": formula["name"],
                            "version": formula.get("version") or "",
                            "source": "brew",
                        }
                    )
            except (ValueError, KeyError, TypeError):
                for name, version in _parse_rows(out):
                    found.append(
                        {"name": name, "version": version, "source": "brew"}
                    )
        if not found:
            out = _try(["brew", "list", "--formula"])
            if out is not None:
                for name, version in _parse_rows(out):
                    found.append(
                        {"name": name, "version": version, "source": "brew"}
                    )
    if not found and shutil.which("port"):
        out = _try(["port", "list"])
        if out is not None:
            for name, version in _parse_rows(out):
                found.append(
                    {"name": name, "version": version, "source": "macports"}
                )
    # Note: Mac App Store / iOS apps are not inventoried (no portable source).
    return found


def windows_packages():
    found = []
    out = _try(["winget", "list"])
    if out is not None:
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) >= 2 and not parts[0].lower().startswith("name"):
                found.append(
                    {
                        "name": parts[0].strip(),
                        "version": parts[1].strip().split(" ")[0],
                        "source": "winget",
                    }
                )
    return found


def installed_packages():
    system = platform.system()
    if system == "Linux":
        return linux_packages()
    if system == "Darwin":
        return darwin_packages()
    return windows_packages()


# --------------------------------------------------------------------------
# safety-tier classification (Capture → Prepare → Install → Restore)
# --------------------------------------------------------------------------

# Files that contain secrets and need explicit confirmation before restore.
SENSITIVE_PREFIXES = (
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    ".pki/",
    ".mozilla/",
    ".thunderbird/",
    ".config/chromium/",
    ".config/google-chrome/",
    ".config/BraveSoftware/",
    ".config/opera/",
    ".config/Signal/",
    ".config/Element/",
    ".config/keepassxc/",
)

# Files that must never be auto-restored (system-level, even if present).
BLOCKED_PREFIXES = (
    "etc/",
    "boot/",
    "usr/",
    "var/",
    "sys/",
    "proc/",
    "dev/",
    "kernel",
    "modules/",
)


def classify_file(rel):
    """Classify a relative path into a safety tier.

    Returns one of:
      "safe"       - auto-restore without confirmation
      "sensitive"  - contains secrets; needs explicit confirmation
      "blocked"    - system-level; never auto-restore
    """
    norm = rel.replace(os.sep, "/")
    for prefix in BLOCKED_PREFIXES:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return "blocked"
    for prefix in SENSITIVE_PREFIXES:
        if norm.startswith(prefix):
            return "sensitive"
    # Also flag any path whose basename looks like a private key.
    base = os.path.basename(norm).lower()
    if base.startswith("id_") or base.endswith(".pem") or base.endswith(".key"):
        return "sensitive"
    return "safe"


def tier_summary(plan):
    counts = {"safe": 0, "sensitive": 0, "blocked": 0}
    for entry in plan["files"]:
        tier = entry.get("tier", "safe")
        counts[tier] = counts.get(tier, 0) + 1
    return counts
# --------------------------------------------------------------------------
# package translation (cross-distro mapping)
# --------------------------------------------------------------------------

# Common package name mappings across distros.
# Keys are lowercase source package names; values are dicts mapping target
# package-manager families to an equivalent package name (or None if no
# equivalent exists).
PACKAGE_ALIASES = {
    "firefox": {"apt": "firefox", "dnf": "firefox", "pacman": "firefox", "zypper": "MozillaFirefox"},
    "firefox-esr": {"apt": "firefox-esr", "dnf": "firefox", "pacman": "firefox", "zypper": "MozillaFirefox"},
    "chromium": {"apt": "chromium", "dnf": "chromium", "pacman": "chromium", "zypper": "chromium"},
    "google-chrome": {"apt": "google-chrome-stable", "dnf": "google-chrome-stable", "pacman": "google-chrome", "zypper": "google-chrome-stable"},
    "code": {"apt": "code", "dnf": "code", "pacman": "visual-studio-code-bin", "zypper": "code"},
    "vscode": {"apt": "code", "dnf": "code", "pacman": "visual-studio-code-bin", "zypper": "code"},
    "steam": {"apt": "steam", "dnf": "steam", "pacman": "steam", "zypper": "steam"},
    "discord": {"apt": "discord", "dnf": "discord", "pacman": "discord", "zypper": "discord"},
    "spotify": {"apt": "spotify-client", "dnf": "spotify", "pacman": "spotify", "zypper": "spotify"},
    "vlc": {"apt": "vlc", "dnf": "vlc", "pacman": "vlc", "zypper": "vlc"},
    "gimp": {"apt": "gimp", "dnf": "gimp", "pacman": "gimp", "zypper": "gimp"},
    "libreoffice": {"apt": "libreoffice", "dnf": "libreoffice", "pacman": "libreoffice-fresh", "zypper": "libreoffice"},
    "docker": {"apt": "docker.io", "dnf": "docker", "pacman": "docker", "zypper": "docker"},
    "docker-ce": {"apt": "docker-ce", "dnf": "docker-ce", "pacman": "docker", "zypper": "docker"},
    "nodejs": {"apt": "nodejs", "dnf": "nodejs", "pacman": "nodejs", "zypper": "nodejs"},
    "python3": {"apt": "python3", "dnf": "python3", "pacman": "python", "zypper": "python3"},
    "git": {"apt": "git", "dnf": "git", "pacman": "git", "zypper": "git"},
    "curl": {"apt": "curl", "dnf": "curl", "pacman": "curl", "zypper": "curl"},
    "wget": {"apt": "wget", "dnf": "wget", "pacman": "wget", "zypper": "wget"},
    "htop": {"apt": "htop", "dnf": "htop", "pacman": "htop", "zypper": "htop"},
    "neofetch": {"apt": "neofetch", "dnf": "neofetch", "pacman": "neofetch", "zypper": "neofetch"},
    "obs-studio": {"apt": "obs-studio", "dnf": "obs-studio", "pacman": "obs-studio", "zypper": "obs-studio"},
    "blender": {"apt": "blender", "dnf": "blender", "pacman": "blender", "zypper": "blender"},
    "inkscape": {"apt": "inkscape", "dnf": "inkscape", "pacman": "inkscape", "zypper": "inkscape"},
    "audacity": {"apt": "audacity", "dnf": "audacity", "pacman": "audacity", "zypper": "audacity"},
    "thunderbird": {"apt": "thunderbird", "dnf": "thunderbird", "pacman": "thunderbird", "zypper": "MozillaThunderbird"},
    "telegram": {"apt": "telegram-desktop", "dnf": "telegram-desktop", "pacman": "telegram-desktop", "zypper": "telegram-desktop"},
    "slack": {"apt": "slack-desktop", "dnf": "slack", "pacman": "slack-desktop", "zypper": "slack"},
    "zoom": {"apt": "zoom", "dnf": "zoom", "pacman": "zoom", "zypper": "zoom"},
    "virtualbox": {"apt": "virtualbox", "dnf": "VirtualBox", "pacman": "virtualbox", "zypper": "virtualbox"},
    "qbittorrent": {"apt": "qbittorrent", "dnf": "qbittorrent", "pacman": "qbittorrent", "zypper": "qbittorrent"},
    "keepassxc": {"apt": "keepassxc", "dnf": "keepassxc", "pacman": "keepassxc", "zypper": "keepassxc"},
    "flatpak": {"apt": "flatpak", "dnf": "flatpak", "pacman": "flatpak", "zypper": "flatpak"},
    "snapd": {"apt": "snapd", "dnf": "snapd", "pacman": "snapd", "zypper": "snapd"},
}


def detect_package_manager():
    """Detect which package manager this system uses."""
    if shutil.which("dnf"):
        return "dnf"
    if shutil.which("apt") or shutil.which("apt-get"):
        return "apt"
    if shutil.which("pacman"):
        return "pacman"
    if shutil.which("zypper"):
        return "zypper"
    if shutil.which("rpm"):
        return "rpm"
    return "unknown"


def manager_family(mgr):
    """Map a package manager name to its family for alias lookup."""
    if mgr in ("apt", "apt-get", "dpkg"):
        return "apt"
    if mgr in ("dnf", "yum", "rpm"):
        return "dnf"
    if mgr in ("pacman",):
        return "pacman"
    if mgr in ("zypper",):
        return "zypper"
    return mgr


def translate_package(name, target_family):
    """Try to find an equivalent package name on the target distro.

    Returns (translated_name, status) where status is one of:
      "same"        - package name is identical on target
      "translated"  - found an alias for the target family
      "unavailable" - no known equivalent on target
    """
    clean = name.split(":")[0].lower()
    if clean in PACKAGE_ALIASES:
        alias = PACKAGE_ALIASES[clean].get(target_family)
        if alias is None:
            return None, "unavailable"
        if alias.lower() == clean:
            return alias, "same"
        return alias, "translated"
    # No alias entry: assume the name is the same (best guess).
    return name.split(":")[0], "same"


def file_list(archive_home):
    """Sorted relative paths (posix style) of files+links inside the archive."""
    files = []
    for path in archive_home.rglob("*"):
        if path.is_symlink() or path.is_file():
            files.append(path.relative_to(archive_home).as_posix())
    return sorted(files)


def plan_restore(archive_root, target_home):
    archive_home = Path(archive_root) / "home"
    files = []
    overwritten = []
    for rel in file_list(archive_home):
        dst = Path(target_home) / Path(rel)
        tier = classify_file(rel)
        files.append(
            {"path": rel, "overwrite": dst.exists() or dst.is_symlink(),
             "tier": tier}
        )
        if dst.exists() or dst.is_symlink():
            overwritten.append(rel)

    to_install = []
    current_names = {p["name"].lower() for p in installed_packages()}
    target_family = manager_family(detect_package_manager())
    for pkg in load_packages(archive_root):
        src_name = pkg["name"]
        clean_name = src_name.split(":")[0]
        if clean_name.lower() in current_names:
            action = "upgrade-or-same"
            translated = clean_name
            status = "same"
        else:
            action = "install"
            translated, status = translate_package(src_name, target_family)
        to_install.append(
            {
                "name": src_name,
                "version": pkg.get("version") or "",
                "action": action,
                "translated_name": translated,
                "translation_status": status,
                "target_family": target_family,
            }
        )

    return {"files": files, "overwritten": overwritten, "packages": to_install}


def load_packages(archive_root):
    path = Path(archive_root) / "packages.json"
    if not path.exists():
        return []
    return read_json(path).get("packages", [])


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def preview(archive_path, target_home):
    archive_root = Path(archive_path)
    plan = plan_restore(archive_root, target_home)
    write_json(
        archive_root / "plan.json",
        {
            "target_home": str(target_home),
            "generated": datetime.now(timezone.utc).isoformat(),
            "plan": plan,
        },
    )
    print(f"Restore plan for {target_home}")
    print(f"  files to copy    : {len(plan['files'])}")
    shown = 0
    for entry in plan["files"]:
        mark = "  [overwrite]" if entry["overwrite"] else ""
        print(f"    {entry['path']}{mark}")
        shown += 1
        if shown >= 50:
            break
    if len(plan["files"]) > shown:
        print(f"    ... and {len(plan['files']) - shown} more")
    print(f"  files to replace : {len(plan['overwritten'])}")
    counts = tier_summary(plan)
    print("  safety tiers:")
    print(f"    safe to migrate      : {counts['safe']}")
    print(f"    needs confirmation   : {counts['sensitive']}")
    print(f"    blocked (skipped)   : {counts['blocked']}")
    sensitive = [e for e in plan["files"] if e["tier"] == "sensitive"]
    if sensitive:
        print("  sensitive files (skipped unless --restore-sensitive):")
        for entry in sensitive[:20]:
            print(f"    {entry['path']}")
        if len(sensitive) > 20:
            print(f"    ... and {len(sensitive) - 20} more")
    blocked = [e for e in plan["files"] if e["tier"] == "blocked"]
    if blocked:
        print("  blocked files (never restored):")
        for entry in blocked[:20]:
            print(f"    {entry['path']}")
    installs = [p for p in plan["packages"] if p["action"] == "install"]
    upgrades = [p for p in plan["packages"] if p["action"] == "upgrade-or-same"]
    translated = [p for p in installs if p.get("translation_status") == "translated"]
    unavailable = [p for p in installs if p.get("translation_status") == "unavailable"]
    print(f"  packages           : {len(plan['packages'])} known to the source system")
    print(f"    already present  : {len(upgrades)}")
    print(f"    needs install    : {len(installs)}  (needs translation)")
    if translated:
        print(f"    translated names : {len(translated)}")
    if unavailable:
        print(f"    unavailable      : {len(unavailable)} (no known equivalent)")
    # Show unavailable packages first (most important).
    for pkg in unavailable[:20]:
        print(f"      ⚠️  {pkg['name']} — no equivalent on target")
    for pkg in translated[:20]:
        print(f"      🔄 {pkg['name']} → {pkg['translated_name']}")
    shown_pkgs = len(unavailable) + len(translated)
    remaining = len(installs) - shown_pkgs
    if remaining > 0:
        print(f"      ... and {remaining} more (same name)")


def restore(archive_path, target_home, restore_sensitive=None):
    archive_root = Path(archive_path)
    target_home = Path(target_home).expanduser()
    target_home.mkdir(parents=True, exist_ok=True)

    plan = plan_restore(archive_root, target_home)
    backup_dir = archive_root / "backup"
    archive_home = archive_root / "home"

    sensitive_files = [e for e in plan["files"] if e.get("tier") == "sensitive"]
    blocked_files = [e for e in plan["files"] if e.get("tier") == "blocked"]

    # Decide whether to restore sensitive files.
    if restore_sensitive is None and sensitive_files:
        if sys.stdin.isatty():
            print()
            print("⚠️  Found sensitive files (SSH keys, GPG keys, browser profiles):")
            for entry in sensitive_files:
                print(f"    {entry['path']}")
            print()
            try:
                answer = input("Restore these sensitive files? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            restore_sensitive = answer in ("y", "yes")
            print()
        else:
            # Non-interactive: safe default is to skip.
            restore_sensitive = False

    restored = 0
    skipped_sensitive = []
    skipped_blocked = []
    for entry in plan["files"]:
        src = archive_home / Path(entry["path"])
        dst = target_home / Path(entry["path"])
        tier = entry.get("tier", "safe")

        if tier == "blocked":
            skipped_blocked.append(entry["path"])
            print(f"  BLOCKED (not restored): {entry['path']}")
            continue
        if tier == "sensitive" and not restore_sensitive:
            skipped_sensitive.append(entry["path"])
            print(f"  SKIPPED (sensitive): {entry['path']}")
            continue

        if entry["overwrite"]:
            keep = backup_dir / "home" / Path(entry["path"])
            keep.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink() or dst.exists():
                shutil.move(str(dst), str(keep))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(os.readlink(src), str(dst))
        else:
            shutil.copy2(src, str(dst), follow_symlinks=False)
        restored += 1

    print(f"Restored into {target_home}")
    print(f"  files copied    : {restored}")
    print(f"  files replaced  : {len(plan['overwritten'])} (saved to {backup_dir})")
    if skipped_sensitive:
        print(f"  sensitive skipped : {len(skipped_sensitive)} "
              f"(re-run with --restore-sensitive to include SSH/GPG/browser secrets)")
    if skipped_blocked:
        print(f"  blocked skipped  : {len(skipped_blocked)} (system files, never restored)")

    # --- Package translation flow ---
    installs = [p for p in plan["packages"] if p["action"] == "install"]
    if installs:
        print()
        print("📦 Packages to install (needs translation):")
        target_family = installs[0].get("target_family", "unknown") if installs else "unknown"
        unavailable = [p for p in installs if p.get("translation_status") == "unavailable"]
        translated = [p for p in installs if p.get("translation_status") == "translated"]
        same = [p for p in installs if p.get("translation_status") == "same"]

        # Show same-name packages.
        if same:
            print(f"  ✅ Same name on target ({len(same)}):")
            for pkg in same[:20]:
                print(f"      {pkg['name']} {pkg['version']}".rstrip())
            if len(same) > 20:
                print(f"      ... and {len(same) - 20} more")

        # Show translated packages.
        if translated:
            print(f"  🔄 Translated names ({len(translated)}):")
            for pkg in translated[:20]:
                print(f"      {pkg['name']} → {pkg['translated_name']}")
            if len(translated) > 20:
                print(f"      ... and {len(translated) - 20} more")

        # Interactive flow for unavailable packages.
        if unavailable:
            print(f"  ⚠️  Unavailable on target ({len(unavailable)}):")
            decisions = {}
            for pkg in unavailable:
                alt = pkg.get("translated_name")
                print()
                print(f"  ⚠️  {pkg['name']} isn't available on {target_family}.")
                if alt:
                    print(f"     Alternative: {alt}")
                else:
                    print("     No known alternative.")

                if sys.stdin.isatty():
                    if alt:
                        print("     [1] Install alternative  [2] Skip  [3] Try same name")
                    else:
                        print("     [1] Skip  [2] Try same name")
                    try:
                        choice = input("     Choice: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        choice = "1" if alt else "1"
                else:
                    # Non-interactive: skip unavailable packages.
                    choice = "1" if alt else "1"

                if choice == "1" and alt:
                    decisions[pkg["name"]] = ("alternative", alt)
                    print(f"     → Will install {alt}")
                elif choice == "2" and alt:
                    decisions[pkg["name"]] = ("skip", None)
                    print(f"     → Skipped {pkg['name']}")
                elif (choice == "2" and not alt) or (choice == "3" and alt):
                    decisions[pkg["name"]] = ("try-same", pkg["name"])
                    print(f"     → Will try {pkg['name']}")
                else:
                    decisions[pkg["name"]] = ("skip", None)
                    print(f"     → Skipped {pkg['name']}")

            # Summary of decisions.
            install_list = []
            for pkg in same + translated:
                install_list.append(pkg.get("translated_name") or pkg["name"])
            for name, (action, alt_name) in decisions.items():
                if action in ("alternative", "try-same"):
                    install_list.append(alt_name)
            if install_list:
                print()
                print(f"  📋 Packages to install on target ({len(install_list)}):")
                for name in install_list[:30]:
                    print(f"      {name}")
                if len(install_list) > 30:
                    print(f"      ... and {len(install_list) - 30} more")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=APP,
        description="Build, preview and restore a portable "
        "personality archive.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser(
        "build", help="scan this computer and write an archive"
    )
    p_build.add_argument(
        "path", nargs="?", default="~/omniport-archive",
        help="where to write the archive (default: %(default)s)",
    )

    p_prev = sub.add_parser(
        "preview", help="show what a restore would do (dry run)"
    )
    p_prev.add_argument("path", help="the archive to inspect")
    p_prev.add_argument(
        "--home", default=None, help="target home directory (default: current)"
    )

    p_res = sub.add_parser("restore", help="carry out a restore")
    p_res.add_argument("path", help="the archive to apply")
    p_res.add_argument(
        "--home", default=None, help="target home directory (default: current)"
    )
    p_res.add_argument(
        "--restore-sensitive", action="store_true",
        help="restore sensitive files (SSH/GPG keys, browser profiles) "
             "without prompting. For scripted/non-interactive use.",
    )
    p_res.add_argument(
        "--skip-sensitive", action="store_true",
        help="skip sensitive files without prompting. For scripted/non-interactive use.",
    )

    args = parser.parse_args(argv)

    if args.command == "build":
        build(args.path)
    elif args.command == "preview":
        target = Path(args.home) if args.home else home_dir()
        preview(Path(args.path), target)
    elif args.command == "restore":
        target = Path(args.home) if args.home else home_dir()
        if args.restore_sensitive:
            sensitive_choice = True
        elif args.skip_sensitive:
            sensitive_choice = False
        else:
            sensitive_choice = None  # prompt interactively
        restore(Path(args.path), target, restore_sensitive=sensitive_choice)
    return 0


if __name__ == "__main__":
    sys.exit(main())
