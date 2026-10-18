#!/usr/bin/env python3
"""Omniport Wizard — step-by-step UI for the Capture → Prepare stage.

A multi-page wizard that guides the user through:
  1. Gather System Info  — scan and display system summary
  2. Select Files         — browse home tree, pick what to include, preview contents
  3. Select Packages      — choose which installed apps to carry over
  4. Destination & Build  — pick where to save, build the archive
  5. USB Setup            — install Ventoy, copy tools, prepare for boot

Usage:
    python3 wizard_ui.py
"""

import os
import sys
import shutil
import subprocess
import threading
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QGroupBox, QTextEdit, QSplitter,
    QScrollArea, QWidget, QSizePolicy, QRadioButton, QButtonGroup,
    QFileDialog as QFD, QMessageBox, QFrame, QHeaderView,
    QAbstractItemView, QLineEdit, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView as QH,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QBrush

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omniport
import platform


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        try:
            home = omniport.home_dir()
            mgr = omniport.detect_package_manager()
            packages = omniport.installed_packages()

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
            for cache_name in ("Library/Caches", "AppData/Local/Temp"):
                cp = home / cache_name
                if cp.exists():
                    cache_size += omniport._dir_size(cp)

            result = {
                "home": str(home),
                "system": platform.system(),
                "release": platform.release(),
                "package_manager": mgr,
                "package_manager_family": omniport.manager_family(mgr),
                "package_count": len(packages),
                "file_count": file_count,
                "total_size": total_size,
                "cache_size": cache_size,
                "packages": packages,
                "home_path": home,
            }
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class BuildWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, dest_path, selected_files, selected_packages):
        super().__init__()
        self.dest_path = dest_path
        self.selected_files = selected_files
        self.selected_packages = selected_packages

    def run(self):
        try:
            home = omniport.home_dir()
            archive_root = Path(self.dest_path).expanduser()
            if archive_root.exists():
                shutil.rmtree(archive_root)
            (archive_root / "home").mkdir(parents=True)

            files_copied = 0
            entries = sorted(home.iterdir())
            for entry in entries:
                if entry.name == ".cache":
                    continue
                if self.selected_files is not None:
                    has_selected = any(
                        f.startswith(entry.name + "/") or f == entry.name
                        for f in self.selected_files
                    )
                    if not has_selected and entry.name not in self.selected_files:
                        continue

                self.progress.emit(f"Copying: {entry.name}/")
                shutil.copytree(
                    entry,
                    archive_root / "home" / entry.name,
                    symlinks=True,
                    ignore=omniport._cache_ignored,
                )
                for p in (archive_root / "home" / entry.name).rglob("*"):
                    if p.is_file():
                        files_copied += 1

            self.progress.emit("Inventorying installed apps...")
            all_packages = omniport.installed_packages()
            if self.selected_packages is not None:
                packages = [p for p in all_packages if p["name"] in self.selected_packages]
            else:
                packages = all_packages

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


class USBScanWorker(QThread):
    """Detect USB drives and their Ventoy status."""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            drives = []
            # Get system disk to exclude
            system_disk = ""
            try:
                system_part = __import__("subprocess").run(
                    ["findmnt", "-n", "-o", "SOURCE", "/"],
                    capture_output=True, text=True
                ).stdout.strip()
                import os as _os
                system_disk = _os.path.basename(
                    __import__("subprocess").run(
                        ["lsblk", "-rn", "-o", "PKNAME", system_part],
                        capture_output=True, text=True
                    ).stdout.strip()
                )
            except Exception:
                pass

            # List all block devices
            result = __import__("subprocess").run(
                ["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT"],
                capture_output=True, text=True
            )
            import json as _json
            data = _json.loads(result.stdout)

            for dev in data.get("blockdevices", []):
                if dev.get("type") != "disk":
                    continue
                name = dev["name"]
                if name == system_disk:
                    continue
                # Skip very small devices (likely not USB)
                size_str = dev.get("size", "0")
                # Parse size
                size_gb = 0
                if size_str.endswith("G"):
                    size_gb = float(size_str[:-1])
                elif size_str.endswith("T"):
                    size_gb = float(size_str[:-1]) * 1024
                elif size_str.endswith("M"):
                    size_gb = float(size_str[:-1]) / 1024
                if size_gb < 1:
                    continue

                # Check if Ventoy is installed
                has_ventoy = False
                has_iso_part = False
                has_data_part = False
                iso_part = ""
                data_part = ""
                for child in dev.get("children", []):
                    label = child.get("label", "") or ""
                    if label == "Ventoy":
                        has_ventoy = True
                        has_iso_part = True
                        iso_part = f"/dev/{child['name']}"
                    elif label == "VTOYEFI":
                        pass
                    else:
                        has_data_part = True
                        data_part = f"/dev/{child['name']}"

                drives.append({
                    "name": name,
                    "device": f"/dev/{name}",
                    "size": size_str,
                    "size_gb": size_gb,
                    "has_ventoy": has_ventoy,
                    "iso_part": iso_part,
                    "data_part": data_part,
                    "has_data_part": has_data_part,
                })

            self.finished.emit(drives)
        except Exception as e:
            self.error.emit(str(e))


class VentoySetupWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, device):
        super().__init__()
        self.device = device

    def run(self):
        try:
            script_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "setup_ventoy.sh"
            )
            if not os.path.exists(script_path):
                self.error.emit(f"setup_ventoy.sh not found at {script_path}")
                return

            self.progress.emit(f"Running setup on {self.device}...")
            proc = subprocess.Popen(
                ["bash", script_path, self.device],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = []
            for line in proc.stdout:
                line = line.rstrip()
                output.append(line)
                self.progress.emit(line)
            proc.wait()

            if proc.returncode == 0:
                self.finished.emit("\n".join(output))
            else:
                self.error.emit(f"Setup failed (exit {proc.returncode}):\n" + "\n".join(output))
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Wizard pages
# ---------------------------------------------------------------------------

class SystemInfoPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 1: Gather System Info")
        self.setSubTitle("Scanning your current system...")

        layout = QVBoxLayout(self)

        self.info_label = QLabel("Scanning...")
        self.info_label.setFont(QFont("Monospace", 10))
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.scan_worker = None
        self.scan_result = None

    def initializePage(self):
        if self.scan_worker is not None:
            return
        self.scan_worker = ScanWorker()
        self.scan_worker.finished.connect(self.on_scan_done)
        self.scan_worker.error.connect(self.on_scan_error)
        self.scan_worker.start()
        self.info_label.setText("Scanning system...")

    def on_scan_done(self, result):
        self.scan_result = result
        wizard = self.wizard()
        if wizard is not None:
            wizard.scan_result = result
        size_mb = result["total_size"] / 1024 / 1024
        cache_mb = result["cache_size"] / 1024 / 1024
        text = (
            f"  OS: {result['system']} {result['release']}\n"
            f"  Package Manager: {result['package_manager']} ({result['package_manager_family']})\n"
            f"  Installed Packages: {result['package_count']:,}\n"
            f"  Home Directory: {result['home']}\n"
            f"  Files to capture: {result['file_count']:,} ({size_mb:.1f} MB)\n"
            f"  Cache (excluded): {cache_mb:.1f} MB\n\n"
            f"  Ready to proceed. Click Next to select files."
        )
        self.info_label.setText(text)
        self.completeChanged.emit()

    def on_scan_error(self, msg):
        self.info_label.setText(f"Scan error: {msg}")

    def isComplete(self):
        return self.scan_result is not None


class FileSelectPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 2: Select Files")
        self.setSubTitle("Browse your home directory and choose what to include.")

        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / Folder", "Size", "Type"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 80)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_file_selected)
        splitter.addWidget(self.tree)

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_label = QLabel("File Preview")
        preview_label.setFont(QFont("Sans", 11, QFont.Weight.Bold))
        preview_layout.addWidget(preview_label)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        preview_layout.addWidget(self.preview_text)
        self.preview_info = QLabel("")
        self.preview_info.setStyleSheet("color: #666; font-size: 10px;")
        preview_layout.addWidget(self.preview_info)
        splitter.addWidget(preview_widget)

        splitter.setSizes([400, 300])
        layout.addWidget(splitter)

        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        self.expand_btn = QPushButton("Expand All")
        self.expand_btn.clicked.connect(self.tree.expandAll)
        self.collapse_btn = QPushButton("Collapse All")
        self.collapse_btn.clicked.connect(self.tree.collapseAll)
        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.deselect_all_btn)
        btn_layout.addWidget(self.expand_btn)
        btn_layout.addWidget(self.collapse_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.selected_files = None
        self._populated = False

    def initializePage(self):
        if self._populated:
            return
        self._populated = True
        scan_result = self.wizard().scan_result
        if not scan_result:
            return
        home = scan_result["home_path"]
        self.tree.clear()
        root_item = QTreeWidgetItem(self.tree, [str(home), "", "folder"])
        root_item.setCheckState(0, Qt.CheckState.Checked)
        root_item.setExpanded(True)
        self._populate_tree(root_item, home)
        self.tree.addTopLevelItem(root_item)

    def _populate_tree(self, parent_item, path):
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name == ".cache":
                continue
            if entry.is_dir():
                if entry.name in ("Caches", "Temp") and path.name in ("Library", "AppData"):
                    continue
                item = QTreeWidgetItem(parent_item, [entry.name, "", "folder"])
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry))
                self._populate_tree(item, entry)
            else:
                size = ""
                try:
                    sz = entry.stat().st_size
                    if sz < 1024:
                        size = f"{sz} B"
                    elif sz < 1024 * 1024:
                        size = f"{sz / 1024:.1f} KB"
                    else:
                        size = f"{sz / 1024 / 1024:.1f} MB"
                except OSError:
                    size = "?"
                item = QTreeWidgetItem(parent_item, [entry.name, size, "file"])
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry))

    def on_file_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)
        if path.is_file():
            try:
                sz = path.stat().st_size
                if sz > 1024 * 1024:
                    self.preview_text.setPlainText(f"[File too large to preview: {sz / 1024 / 1024:.1f} MB]")
                    self.preview_info.setText(f"{path.name} — {sz:,} bytes")
                    return
                try:
                    content = path.read_text(errors="replace")
                    self.preview_text.setPlainText(content[:50000])
                    self.preview_info.setText(f"{path.name} — {sz:,} bytes — text")
                except Exception:
                    self.preview_text.setPlainText(f"[Binary file — {sz:,} bytes]")
                    self.preview_info.setText(f"{path.name} — {sz:,} bytes — binary")
            except OSError as e:
                self.preview_text.setPlainText(f"[Cannot read: {e}]")
        elif path.is_dir():
            self.preview_text.setPlainText(f"[Directory: {path}]\n\nContents:")
            try:
                entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
                for e in entries[:50]:
                    kind = "📁" if e.is_dir() else "📄"
                    self.preview_text.append(f"  {kind} {e.name}")
                if len(entries) > 50:
                    self.preview_text.append(f"  ... and {len(entries) - 50} more")
            except PermissionError:
                self.preview_text.setPlainText("[Permission denied]")

    def select_all(self):
        self._set_check_state_recursive(self.tree.invisibleRootItem(), Qt.CheckState.Checked)

    def deselect_all(self):
        self._set_check_state_recursive(self.tree.invisibleRootItem(), Qt.CheckState.Unchecked)

    def _set_check_state_recursive(self, item, state):
        item.setCheckState(0, state)
        for i in range(item.childCount()):
            self._set_check_state_recursive(item.child(i), state)

    def get_selected_files(self):
        selected = set()
        root = self.tree.invisibleRootItem()
        self._collect_checked(root, selected, "")
        all_checked = all(
            root.child(i).checkState(0) == Qt.CheckState.Checked
            for i in range(root.childCount())
        )
        if all_checked and len(selected) > 100:
            return None
        return selected

    def _collect_checked(self, item, selected, prefix):
        for i in range(item.childCount()):
            child = item.child(i)
            name = child.text(0)
            rel = f"{prefix}/{name}" if prefix else name
            if child.checkState(0) == Qt.CheckState.Checked:
                if child.childCount() == 0:
                    selected.add(rel)
            self._collect_checked(child, selected, rel)


class PackageSelectPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 3: Select Packages")
        self.setSubTitle("Choose which installed apps to carry over.")

        layout = QVBoxLayout(self)

        self.info_label = QLabel("Loading packages...")
        layout.addWidget(self.info_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Package", "Version", "Source"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 100)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.deselect_all_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._populated = False

    def initializePage(self):
        if self._populated:
            return
        self._populated = True
        scan_result = self.wizard().scan_result
        if not scan_result:
            return
        packages = scan_result["packages"]
        self.info_label.setText(f"Found {len(packages)} installed packages. Select which to carry over.")

        by_source = {}
        for pkg in packages:
            src = pkg.get("source", "unknown")
            by_source.setdefault(src, []).append(pkg)

        for source in sorted(by_source.keys()):
            group = QTreeWidgetItem(self.tree, [source.upper(), "", ""])
            group.setCheckState(0, Qt.CheckState.Checked)
            group.setExpanded(False)
            for pkg in sorted(by_source[source], key=lambda p: p["name"].lower()):
                item = QTreeWidgetItem(group, [pkg["name"], pkg.get("version", ""), pkg.get("source", "")])
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, pkg["name"])

    def select_all(self):
        self._set_check_state_recursive(self.tree.invisibleRootItem(), Qt.CheckState.Checked)

    def deselect_all(self):
        self._set_check_state_recursive(self.tree.invisibleRootItem(), Qt.CheckState.Unchecked)

    def _set_check_state_recursive(self, item, state):
        item.setCheckState(0, state)
        for i in range(item.childCount()):
            self._set_check_state_recursive(item.child(i), state)

    def get_selected_packages(self):
        selected = set()
        root = self.tree.invisibleRootItem()
        self._collect_checked(root, selected)
        total = sum(1 for _ in self._all_packages())
        if len(selected) >= total * 0.95:
            return None
        return selected

    def _all_packages(self):
        scan_result = self.wizard().scan_result
        if scan_result:
            for pkg in scan_result["packages"]:
                yield pkg["name"]

    def _collect_checked(self, item, selected):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.childCount() > 0:
                self._collect_checked(child, selected)
            else:
                if child.checkState(0) == Qt.CheckState.Checked:
                    name = child.data(0, Qt.ItemDataRole.UserRole)
                    if name:
                        selected.add(name)


class DestinationBuildPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 4: Destination & Build")
        self.setSubTitle("Choose where to save the migration archive and build it.")

        layout = QVBoxLayout(self)

        dest_group = QGroupBox("Destination")
        dest_layout = QHBoxLayout(dest_group)
        self.dest_label = QLabel("No destination selected")
        self.dest_label.setStyleSheet("color: #666;")
        self.dest_button = QPushButton("Browse...")
        self.dest_button.clicked.connect(self.browse_destination)
        self.dest_path = None
        dest_layout.addWidget(self.dest_label, 1)
        dest_layout.addWidget(self.dest_button)
        layout.addWidget(dest_group)

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666; font-size: 10px;")
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        layout.addWidget(progress_group)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        self.result_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.result_text)

        self.build_button = QPushButton("Build Migration Archive")
        self.build_button.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        self.build_button.setMinimumHeight(40)
        self.build_button.clicked.connect(self.start_build)
        layout.addWidget(self.build_button)

        self.build_worker = None

    def browse_destination(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select destination for migration archive"
        )
        if path:
            self.dest_path = os.path.join(path, "omniport-archive")
            self.dest_label.setText(self.dest_path)
            self.dest_label.setStyleSheet("color: #000;")

    def isComplete(self):
        return self.dest_path is not None

    def start_build(self):
        if not self.dest_path:
            QMessageBox.warning(self, "No destination", "Please select a destination first.")
            return

        self.build_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.result_text.clear()
        self.result_text.append("Building archive...")

        file_page = self.wizard().page(1)
        pkg_page = self.wizard().page(2)
        selected_files = file_page.get_selected_files()
        selected_packages = pkg_page.get_selected_packages()

        self.build_worker = BuildWorker(self.dest_path, selected_files, selected_packages)
        self.build_worker.progress.connect(self.on_progress)
        self.build_worker.finished.connect(self.on_finished)
        self.build_worker.error.connect(self.on_error)
        self.build_worker.start()

    def on_progress(self, msg):
        self.progress_label.setText(msg)

    def on_finished(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append(msg)
        self.build_button.setEnabled(True)
        QMessageBox.information(self, "Build Complete", msg)

    def on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append(f"ERROR: {msg}")
        self.build_button.setEnabled(True)
        QMessageBox.critical(self, "Build Failed", msg)


class USBSetupPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 5: USB Setup (Ventoy)")
        self.setSubTitle("Set up a USB drive with Ventoy + Omniport for booting.")

        layout = QVBoxLayout(self)

        # Drive detection
        drive_group = QGroupBox("Detected USB Drives")
        drive_layout = QVBoxLayout(drive_group)
        self.drive_table = QTableWidget()
        self.drive_table.setColumnCount(4)
        self.drive_table.setHorizontalHeaderLabels(["Device", "Size", "Ventoy?", "Status"])
        self.drive_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.drive_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.drive_table.setMaximumHeight(150)
        drive_layout.addWidget(self.drive_table)

        self.refresh_btn = QPushButton("Refresh Drives")
        self.refresh_btn.clicked.connect(self.scan_drives)
        drive_layout.addWidget(self.refresh_btn)
        layout.addWidget(drive_group)

        # Setup info
        self.info_label = QLabel("Click 'Refresh Drives' to detect USB drives.")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #666;")
        layout.addWidget(self.info_label)

        # Progress
        progress_group = QGroupBox("Setup Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666; font-size: 10px;")
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        layout.addWidget(progress_group)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        self.result_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.result_text)

        self.setup_button = QPushButton("Set Up USB Drive")
        self.setup_button.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        self.setup_button.setMinimumHeight(40)
        self.setup_button.setEnabled(False)
        self.setup_button.clicked.connect(self.start_setup)
        layout.addWidget(self.setup_button)

        self.scan_worker = None
        self.setup_worker = None
        self.selected_device = None

    def initializePage(self):
        self.scan_drives()

    def scan_drives(self):
        self.info_label.setText("Scanning for USB drives...")
        self.drive_table.setRowCount(0)
        self.setup_button.setEnabled(False)
        self.scan_worker = USBScanWorker()
        self.scan_worker.finished.connect(self.on_drives_found)
        self.scan_worker.error.connect(self.on_scan_error)
        self.scan_worker.start()

    def on_drives_found(self, drives):
        self.drive_table.setRowCount(len(drives))
        for i, drive in enumerate(drives):
            self.drive_table.setItem(i, 0, QTableWidgetItem(drive["device"]))
            self.drive_table.setItem(i, 1, QTableWidgetItem(drive["size"]))
            ventoy_status = "✅ Yes" if drive["has_ventoy"] else "❌ No"
            self.drive_table.setItem(i, 2, QTableWidgetItem(ventoy_status))
            if drive["has_ventoy"]:
                status = "Ready (Ventoy installed)"
            else:
                status = "Needs setup"
            self.drive_table.setItem(i, 3, QTableWidgetItem(status))

        if drives:
            self.info_label.setText(
                f"Found {len(drives)} USB drive(s). Select one and click 'Set Up USB Drive'.\n"
                "Note: This will ERASE all data on the selected drive!"
            )
        else:
            self.info_label.setText("No USB drives detected. Plug in a USB drive and click Refresh.")

    def on_scan_error(self, msg):
        self.info_label.setText(f"Scan error: {msg}")

    def start_setup(self):
        rows = self.drive_table.selectedItems()
        if not rows:
            QMessageBox.warning(self, "No drive selected", "Please select a USB drive first.")
            return

        row = self.drive_table.currentRow()
        device = self.drive_table.item(row, 0).text()

        reply = QMessageBox.question(
            self, "Confirm",
            f"This will ERASE ALL DATA on {device} and install Ventoy + Omniport.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.setup_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.result_text.clear()
        self.result_text.append(f"Setting up {device}...")

        self.setup_worker = VentoySetupWorker(device)
        self.setup_worker.progress.connect(self.on_setup_progress)
        self.setup_worker.finished.connect(self.on_setup_finished)
        self.setup_worker.error.connect(self.on_setup_error)
        self.setup_worker.start()

    def on_setup_progress(self, msg):
        self.progress_label.setText(msg)
        self.result_text.append(msg)

    def on_setup_finished(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append("\n" + msg)
        self.setup_button.setEnabled(True)
        QMessageBox.information(self, "Setup Complete", "USB drive is ready!")

    def on_setup_error(self, msg):
        self.progress_bar.setVisible(False)
        self.result_text.append(f"\nERROR: {msg}")
        self.setup_button.setEnabled(True)
        QMessageBox.critical(self, "Setup Failed", msg)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class OmniportWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omniport — Prepare Migration")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)

        self.scan_result = None

        self.system_page = SystemInfoPage()
        self.file_page = FileSelectPage()
        self.package_page = PackageSelectPage()
        self.dest_page = DestinationBuildPage()
        self.usb_page = USBSetupPage()

        self.addPage(self.system_page)
        self.addPage(self.file_page)
        self.addPage(self.package_page)
        self.addPage(self.dest_page)
        self.addPage(self.usb_page)

    @property
    def scan_result(self):
        return self._scan_result

    @scan_result.setter
    def scan_result(self, value):
        self._scan_result = value


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    wizard = OmniportWizard()
    wizard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
