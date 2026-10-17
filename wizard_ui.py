#!/usr/bin/env python3
"""Omniport Wizard — step-by-step UI for the Capture → Prepare stage.

A multi-page wizard that guides the user through:
  1. Gather System Info  — scan and display system summary
  2. Select Files         — browse home tree, pick what to include, preview contents
  3. Select Packages      — choose which installed apps to carry over
  4. Destination & Build  — pick where to save, build the archive

Usage:
    python3 wizard_ui.py
"""

import os
import sys
import shutil
import threading
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QGroupBox, QTextEdit, QSplitter,
    QScrollArea, QWidget, QSizePolicy, QRadioButton, QButtonGroup,
    QFileDialog as QFD, QMessageBox, QFrame, QHeaderView,
    QAbstractItemView, QLineEdit, QComboBox,
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
        self.selected_files = selected_files  # set of relative paths (or None = all)
        self.selected_packages = selected_packages  # set of package names (or None = all)

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
                # Check if this top-level entry is selected
                if self.selected_files is not None:
                    # Only copy if at least one file under this entry is selected
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
        self.info_label.setFont(__import__("PyQt6.QtGui", fromlist=["QFont"]).QFont("Monospace", 10))
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

        # Splitter: tree on left, preview on right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # File tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / Folder", "Size", "Type"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 80)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_file_selected)
        splitter.addWidget(self.tree)

        # Preview panel
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

        # Selection buttons
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

        self.selected_files = None  # None = all selected
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
                # Check if it's a cache dir
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
            # Show file info
            try:
                sz = path.stat().st_size
                if sz > 1024 * 1024:
                    self.preview_text.setPlainText(f"[File too large to preview: {sz / 1024 / 1024:.1f} MB]")
                    self.preview_info.setText(f"{path.name} — {sz:,} bytes")
                    return
                # Try to read as text
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
        """Return set of selected relative paths, or None if all selected."""
        selected = set()
        root = self.tree.invisibleRootItem()
        # Walk the tree and collect checked items
        self._collect_checked(root, selected, "")
        # If all top-level items are checked, return None (all)
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

        # Group by source
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
        # If all selected, return None
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

        # Destination
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

        # Progress
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

        # Result
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

        # Get selections from previous pages
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

        # Pages
        self.system_page = SystemInfoPage()
        self.file_page = FileSelectPage()
        self.package_page = PackageSelectPage()
        self.dest_page = DestinationBuildPage()

        self.addPage(self.system_page)
        self.addPage(self.file_page)
        self.addPage(self.package_page)
        self.addPage(self.dest_page)

    def _on_scan_done(self, result):
        self.scan_result = result
        # Propagate to pages
        self.file_page.wizard = lambda: self
        self.package_page.wizard = lambda: self

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
