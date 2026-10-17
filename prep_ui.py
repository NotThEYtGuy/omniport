#!/usr/bin/env python3
"""Omniport Prep — desktop UI for the Capture → Prepare stage.

Runs on the user's current OS (macOS / Linux / Windows) and wraps the core
build logic from omniport.py in a graphical interface.

Usage:
    python3 prep_ui.py
"""

import os
import sys
import threading
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog, QCheckBox,
    QGroupBox, QScrollArea, QTextEdit, QSplitter, QFrame,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QMessageBox,
    QSizePolicy, QSpacerItem,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QPalette, QColor, QIcon

# Import omniport core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omniport


# ---------------------------------------------------------------------------
# Worker threads (keep the UI responsive)
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    """Scan the system in the background and emit results."""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        try:
            home = omniport.home_dir()
            mgr = omniport.detect_package_manager()
            mgr_family = omniport.manager_family(mgr)
            packages = omniport.installed_packages()

            # Count files (excluding caches)
            file_count = 0
            total_size = 0
            cache_size = 0
            for entry in home.iterdir():
                if entry.name == ".cache":
                    cache_size += omniport._dir_size(entry)
                    continue
                for p in entry.rglob("*"):
                    if p.is_file():
                        file_count += 1
                        try:
                            total_size += p.stat().st_size
                        except OSError:
                            pass
                # Also count cache sizes for display
            for cache_name in (".cache", "Library/Caches", "AppData/Local/Temp"):
                cache_path = home / cache_name
                if cache_path.exists():
                    cache_size += omniport._dir_size(cache_path)

            result = {
                "home": str(home),
                "system": __import__("platform").system(),
                "release": __import__("platform").release(),
                "package_manager": mgr,
                "package_manager_family": mgr_family,
                "package_count": len(packages),
                "file_count": file_count,
                "total_size": total_size,
                "cache_size": cache_size,
                "packages": packages,
            }
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class BuildWorker(QThread):
    """Run omniport build in the background."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, dest_path, include_sensitive=True):
        super().__init__()
        self.dest_path = dest_path
        self.include_sensitive = include_sensitive

    def run(self):
        try:
            self.progress.emit("Scanning home directory...")
            # Monkey-patch to capture progress (best-effort)
            home = omniport.home_dir()
            archive_root = Path(self.dest_path).expanduser()
            if archive_root.exists():
                import shutil
                shutil.rmtree(archive_root)
            (archive_root / "home").mkdir(parents=True)

            files_copied = 0
            entries = sorted(home.iterdir())
            for i, entry in enumerate(entries):
                if entry.name == ".cache":
                    continue
                self.progress.emit(f"Copying: {entry.name}/")
                import shutil as _shutil
                _shutil.copytree(
                    entry,
                    archive_root / "home" / entry.name,
                    symlinks=True,
                    ignore=omniport._cache_ignored,
                )
                for p in (archive_root / "home" / entry.name).rglob("*"):
                    if p.is_file():
                        files_copied += 1

            self.progress.emit("Inventorying installed apps...")
            packages = omniport.installed_packages()

            self.progress.emit("Writing manifest...")
            manifest = {
                "app": "omniport",
                "version": "0.1",
                "created": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "host": {
                    "system": __import__("platform").system(),
                    "release": __import__("platform").release(),
                    "package_manager": omniport.detect_package_manager(),
                    "package_manager_family": omniport.manager_family(
                        omniport.detect_package_manager()
                    ),
                },
                "home_scanned": str(home),
                "files_copied": files_copied,
                "packages": packages,
            }
            omniport.write_json(archive_root / "manifest.json", manifest)
            omniport.write_json(
                archive_root / "packages.json", {"packages": packages}
            )

            size = sum(
                f.stat().st_size for f in archive_root.rglob("*") if f.is_file()
            )
            self.finished.emit(
                f"Archive created successfully!\n"
                f"  Location: {archive_root}\n"
                f"  Files: {files_copied}\n"
                f"  Apps: {len(packages)}\n"
                f"  Size: {size / 1024 / 1024:.1f} MB"
            )
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class PrepWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omniport — Prepare Migration")
        self.setMinimumSize(700, 600)
        self.resize(800, 700)

        self.scan_result = None
        self.build_worker = None
        self.scan_worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("🔄 Omniport — Prepare Migration")
        header.setFont(QFont("Sans", 16, QFont.Weight.Bold))
        layout.addWidget(header)

        subtitle = QLabel(
            "Capture your current system into a portable migration archive."
        )
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        # System info group
        self.system_group = QGroupBox("Current System")
        self.system_layout = QVBoxLayout(self.system_group)
        self.system_label = QLabel("Scanning...")
        self.system_label.setFont(QFont("Monospace", 10))
        self.system_layout.addWidget(self.system_label)
        layout.addWidget(self.system_group)

        # Capture options group
        self.options_group = QGroupBox("What to Capture")
        self.options_layout = QVBoxLayout(self.options_group)
        self.check_files = QCheckBox("Personal files (Documents, Pictures, Downloads, etc.)")
        self.check_files.setChecked(True)
        self.check_appdata = QCheckBox("Application data (.config, .local, browser profiles)")
        self.check_appdata.setChecked(True)
        self.check_ssh = QCheckBox("SSH/GPG keys (sensitive — will prompt on restore)")
        self.check_ssh.setChecked(True)
        self.check_packages = QCheckBox("Package inventory (installed apps)")
        self.check_packages.setChecked(True)
        self.check_flatpak = QCheckBox("Flatpak apps")
        self.check_flatpak.setChecked(True)
        self.check_snap = QCheckBox("Snap packages")
        self.check_snap.setChecked(True)
        for cb in (self.check_files, self.check_appdata, self.check_ssh,
                   self.check_packages, self.check_flatpak, self.check_snap):
            self.options_layout.addWidget(cb)
        layout.addWidget(self.options_group)

        # Destination group
        self.dest_group = QGroupBox("Destination")
        self.dest_layout = QHBoxLayout(self.dest_group)
        self.dest_label = QLabel("No destination selected")
        self.dest_label.setStyleSheet("color: #666;")
        self.dest_button = QPushButton("Browse...")
        self.dest_button.clicked.connect(self.browse_destination)
        self.dest_path = None
        self.dest_layout.addWidget(self.dest_label, 1)
        self.dest_layout.addWidget(self.dest_button)
        layout.addWidget(self.dest_group)

        # Progress group
        self.progress_group = QGroupBox("Progress")
        self.progress_layout = QVBoxLayout(self.progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666; font-size: 10px;")
        self.progress_layout.addWidget(self.progress_bar)
        self.progress_layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_group)

        # Result area
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(120)
        self.result_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.result_text)

        # Build button
        self.build_button = QPushButton("Build Migration Archive")
        self.build_button.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        self.build_button.setMinimumHeight(40)
        self.build_button.setEnabled(False)
        self.build_button.clicked.connect(self.start_build)
        layout.addWidget(self.build_button)

        # Start scanning
        self.start_scan()

    def start_scan(self):
        self.system_label.setText("Scanning system...")
        self.scan_worker = ScanWorker()
        self.scan_worker.finished.connect(self.on_scan_finished)
        self.scan_worker.error.connect(self.on_scan_error)
        self.scan_worker.start()

    def on_scan_finished(self, result):
        self.scan_result = result
        size_mb = result["total_size"] / 1024 / 1024
        cache_mb = result["cache_size"] / 1024 / 1024
        text = (
            f"  OS: {result['system']} {result['release']}\n"
            f"  Package Manager: {result['package_manager']} ({result['package_manager_family']})\n"
            f"  Installed Packages: {result['package_count']:,}\n"
            f"  Home Directory: {result['home']}\n"
            f"  Files to capture: {result['file_count']:,} ({size_mb:.1f} MB)\n"
            f"  Cache (excluded): {cache_mb:.1f} MB"
        )
        self.system_label.setText(text)
        self.build_button.setEnabled(self.dest_path is not None)

    def on_scan_error(self, msg):
        self.system_label.setText(f"Scan error: {msg}")

    def browse_destination(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select destination for migration archive"
        )
        if path:
            self.dest_path = os.path.join(path, "omniport-archive")
            self.dest_label.setText(self.dest_path)
            self.dest_label.setStyleSheet("color: #000;")
            if self.scan_result:
                self.build_button.setEnabled(True)

    def start_build(self):
        if not self.dest_path:
            QMessageBox.warning(self, "No destination", "Please select a destination first.")
            return

        self.build_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.result_text.clear()
        self.result_text.append("Building archive...")

        self.build_worker = BuildWorker(self.dest_path)
        self.build_worker.progress.connect(self.on_build_progress)
        self.build_worker.finished.connect(self.on_build_finished)
        self.build_worker.error.connect(self.on_build_error)
        self.build_worker.start()

    def on_build_progress(self, msg):
        self.progress_label.setText(msg)

    def on_build_finished(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append(msg)
        self.build_button.setEnabled(True)
        QMessageBox.information(self, "Build Complete", msg)

    def on_build_error(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append(f"ERROR: {msg}")
        self.build_button.setEnabled(True)
        QMessageBox.critical(self, "Build Failed", msg)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PrepWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
