import asyncio
import json
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path

import mss
from platformdirs import user_config_dir
from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QColor, QFont, QLinearGradient, QPainter, QPen, QBrush, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QSystemTrayIcon, QMenu, QVBoxLayout, QWidget, QStyle, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
    QStackedWidget, QSlider, QProgressBar, QFileDialog, QButtonGroup, QSizePolicy, QSpacerItem, QScrollArea, QGridLayout
)
from pywizlight import discovery, wizlight, PilotBuilder

APP_NAME = "WiZ Ambient Sync Beta"
APP_ID = "WiZAmbientSyncBeta"


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def config_path():
    folder = Path(user_config_dir(APP_ID, appauthor=False))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "config.json"
    if not target.exists():
        shutil.copy2(resource_path("config.json"), target)
    return target


def set_autostart(enabled):
    if sys.platform.startswith("win"):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                if getattr(sys, "frozen", False):
                    command = f'"{sys.executable}" --background'
                else:
                    script = str(Path(__file__).resolve())
                    command = f'"{sys.executable}" "{script}" --background'
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
    elif sys.platform.startswith("linux"):
        autostart_dir = Path.home() / ".config" / "autostart"
        desktop_file = autostart_dir / "wiz-ambient-sync.desktop"
        if enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            executable = str(Path(sys.executable).resolve())
            desktop_file.write_text(
                "[Desktop Entry]\nType=Application\nName=WiZ Ambient Sync\n"
                f"Exec={executable} {Path(__file__).resolve()} --background\n"
                "X-GNOME-Autostart-enabled=true\nTerminal=false\n",
                encoding="utf-8",
            )
        else:
            desktop_file.unlink(missing_ok=True)


class DiscoveryWorker(QThread):
    found = Signal(list)
    failed = Signal(str)

    @staticmethod
    def classify_module(module_name):
        name = (module_name or "").lower()
        strip_markers = (
            "strip", "lightstrip", "light_strip", "tape", "neon",
            "gradient", "flex", "linear", "rope",
        )
        bulb_markers = (
            "bulb", "lamp", "a60", "a19", "candle", "globe",
            "gu10", "mr16", "br30", "par",
        )
        if any(token in name for token in strip_markers):
            return "Likely strip", "strip"
        if any(token in name for token in bulb_markers):
            return "Likely bulb", "primary"
        return "WiZ light — form factor unknown", ""

    def run(self):
        try:
            async def scan_and_identify():
                lights = await asyncio.wait_for(
                    discovery.discover_lights(), timeout=8.0
                )
                results = []
                for light in lights:
                    ip = str(getattr(light, "ip", "") or "")
                    if not ip:
                        continue

                    module_name = "Unavailable"
                    capabilities = []
                    try:
                        bulb_type = await asyncio.wait_for(
                            light.get_bulbtype(), timeout=4.0
                        )
                        module_name = str(
                            getattr(bulb_type, "name", None) or "Unknown model"
                        )
                        features = getattr(bulb_type, "features", None)
                        if features is not None:
                            for attr, label in (
                                ("color", "Colour"),
                                ("brightness", "Dimming"),
                                ("color_tmp", "Colour temperature"),
                                ("effect", "Effects"),
                            ):
                                try:
                                    if bool(getattr(features, attr, False)):
                                        capabilities.append(label)
                                except Exception:
                                    pass
                    except Exception as exc:
                        module_name = f"Model query unavailable ({type(exc).__name__})"

                    detected_type, suggested_role = self.classify_module(module_name)
                    results.append({
                        "ip": ip,
                        "model": module_name,
                        "detected_type": detected_type,
                        "suggested_role": suggested_role,
                        "capabilities": ", ".join(capabilities) or "Not reported",
                    })
                return results

            self.found.emit(asyncio.run(scan_and_identify()))
        except Exception as exc:
            self.failed.emit(str(exc))


class DeviceControlWorker(QThread):
    """Runs WiZ UDP operations away from the GUI thread."""
    completed = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(self, ip, action="poll", value=None, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.action = action
        self.value = value

    def run(self):
        async def execute():
            light = wizlight(self.ip)
            try:
                # pywizlight's UDP retry window can be long on an unreachable
                # address. Bound every optional UI operation so a worker cannot
                # remain alive and suppress all subsequent polls indefinitely.
                if self.action == "on":
                    await asyncio.wait_for(light.turn_on(PilotBuilder()), timeout=4.0)
                elif self.action == "off":
                    await asyncio.wait_for(light.turn_off(), timeout=4.0)
                elif self.action == "brightness":
                    level = max(1, min(100, int(self.value)))
                    await asyncio.wait_for(
                        light.turn_on(PilotBuilder(brightness=round(level * 255 / 100))),
                        timeout=4.0,
                    )
                # Query the WiZ device directly rather than relying on the
                # pywizlight PilotParser. getPilot returns state and dimming
                # as explicit fields; dimming is already 0..100 percent.
                request = json.dumps({"method": "getPilot", "params": {}}).encode("utf-8")
                def query_pilot():
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(3.0)
                    try:
                        sock.sendto(request, (self.ip, 38899))
                        payload, _ = sock.recvfrom(4096)
                        response = json.loads(payload.decode("utf-8"))
                        result = response.get("result")
                        if not isinstance(result, dict) or "state" not in result:
                            raise RuntimeError(f"Invalid getPilot response: {response!r}")
                        if "dimming" not in result:
                            raise RuntimeError(f"getPilot response has no dimming field: {response!r}")
                        return result
                    finally:
                        sock.close()
                result = await asyncio.wait_for(asyncio.to_thread(query_pilot), timeout=4.0)
                on = bool(result["state"])
                brightness = max(0, min(100, int(round(float(result["dimming"])))) )
                return {"ip": self.ip, "on": on, "brightness": brightness,
                        "reported_brightness": brightness, "mac": result.get("mac"),
                        "rssi": result.get("rssi")}
            finally:
                close = getattr(light, "async_close", None)
                if close:
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await asyncio.wait_for(result, timeout=1.0)
                    except Exception:
                        # Cleanup is best-effort. A stuck UDP close must not
                        # prevent the worker's finished signal from reaching
                        # the GUI and restarting the next poll.
                        pass
        try:
            result = asyncio.run(execute())
            self.completed.emit(self.ip, result)
        except Exception as exc:
            self.failed.emit(self.ip, f"{type(exc).__name__}: {exc}")


class DeviceDiscoveryDialog(QDialog):
    def __init__(self, parent, existing_ips):
        super().__init__(parent)
        self.setWindowTitle("Find WiZ devices")
        self.resize(850, 470)
        self.setModal(True)
        self.setObjectName("DiscoveryDialog")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.worker = None
        self.selected_assignments = {}
        self.existing_ips = {ip for ip in existing_ips if ip}

        theme = getattr(parent, "_last_applied_theme", "Light")
        light_theme = theme == "Light"
        dialog_bg = "#F7F5FC" if light_theme else "#17161d"
        panel_bg = "#FFFFFF" if light_theme else "#302c39"
        text_fg = "#29243A" if light_theme else "#f1eff6"
        muted_fg = "#716A82" if light_theme else "#aaa5b7"
        border = "#E8E1F2" if light_theme else "#494454"
        accent = "#7652B5" if light_theme else "#b39bdd"
        arrow_path = resource_path("chevron-light.png" if light_theme else "chevron-dark.png").as_posix()
        self.setStyleSheet(f"""

            QDialog#DiscoveryDialog {{ background:{dialog_bg}; color:{text_fg}; }}
            QLabel {{ background:transparent; color:{text_fg}; }}
            QTableWidget {{ background:{panel_bg}; color:{text_fg}; alternate-background-color:{dialog_bg};
                border:1px solid {border}; border-radius:10px; gridline-color:{border}; selection-background-color:{accent}; }}
            QTableWidget::item {{ padding:7px; color:{text_fg}; }}
            QTableWidget::item:selected {{ color:white; background:{accent}; }}
            QHeaderView::section {{ background:{dialog_bg}; color:{muted_fg}; border:0; border-bottom:1px solid {border}; padding:8px; }}
            QComboBox {{ background:{panel_bg}; color:{text_fg}; border:1px solid {border}; border-radius:8px; padding:6px 28px 6px 8px; min-height:24px; }}
            QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right; width:26px; border:0; }}
            QComboBox::down-arrow {{ image:url("{arrow_path}"); width:10px; height:7px; }}
            QComboBox QAbstractItemView {{ background:{panel_bg}; color:{text_fg}; selection-background-color:{accent}; border:1px solid {border}; outline:0; }}
            QPushButton {{ background:{panel_bg}; color:{text_fg}; border:1px solid {border}; border-radius:9px; padding:8px 13px; min-height:28px; }}
            QPushButton:hover {{ border-color:{accent}; background:{dialog_bg}; }}
            QPushButton:pressed {{ background:{accent}; color:white; }}
        """)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(dialog_bg))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(text_fg))
        palette.setColor(QPalette.ColorRole.Base, QColor(panel_bg))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(dialog_bg))
        palette.setColor(QPalette.ColorRole.Text, QColor(text_fg))
        palette.setColor(QPalette.ColorRole.Button, QColor(panel_bg))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(text_fg))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(palette)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        explanation = QLabel(
            "Scan the local network, query each device's WiZ model/capabilities, "
            "and suggest whether it is a bulb or strip. Model-name matching is "
            "best-effort; you can override the suggested role before adding."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.status = QLabel("Ready to scan your local network.")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "IP address", "WiZ model", "Detected type", "Capabilities", "Add as"
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.viewport().setAutoFillBackground(True)
        self.table.viewport().setStyleSheet(f"background:{panel_bg}; color:{text_fg};")
        layout.addWidget(self.table, 1)

        self.controls = QHBoxLayout()
        self.scan_button = QPushButton("Scan network")
        self.add_button = QPushButton("Add selected devices")
        self.cancel_button = QPushButton("Cancel")
        self.add_button.setEnabled(False)
        self.scan_button.clicked.connect(self.start_scan)
        self.add_button.clicked.connect(self.accept_assignments)
        self.cancel_button.clicked.connect(self.reject)
        self.controls.addWidget(self.scan_button)
        self.controls.addStretch(1)
        self.controls.addWidget(self.add_button)
        self.controls.addWidget(self.cancel_button)
        layout.addLayout(self.controls)

    def start_scan(self):
        self.scan_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.status.setText(
            "Discovering devices and checking their model information…"
        )
        self.table.setRowCount(0)
        self.worker = DiscoveryWorker(self)
        self.worker.found.connect(self.scan_succeeded)
        self.worker.failed.connect(self.scan_failed)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def scan_finished(self):
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def scan_succeeded(self, devices):
        self.table.setRowCount(0)
        for device in devices:
            ip = device["ip"]
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(ip))
            self.table.setItem(row, 1, QTableWidgetItem(device["model"]))
            self.table.setItem(row, 2, QTableWidgetItem(device["detected_type"]))
            self.table.setItem(row, 3, QTableWidgetItem(device["capabilities"]))

            role = QComboBox()
            role.addItems([
                "Do not add",
                "Primary bulb",
                "Secondary bulb",
                "WiZ strip",
            ])
            if ip in self.existing_ips:
                role.clear()
                role.addItem("Already configured")
                role.setEnabled(False)
            elif device["suggested_role"] == "strip":
                role.setCurrentText("WiZ strip")
            elif device["suggested_role"] == "primary":
                role.setCurrentText("Primary bulb")
            else:
                role.setCurrentText("Do not add")
                role.setToolTip(
                    "The model name did not clearly identify the form factor. "
                    "Choose a role manually if you know the device."
                )
            self.table.setCellWidget(row, 4, role)

        if devices:
            self.status.setText(
                f"Found and inspected {len(devices)} WiZ device(s). "
                "Review the suggested roles before adding."
            )
            self.add_button.setEnabled(True)
        else:
            self.status.setText(
                "No WiZ devices found. Check that the computer and lights share "
                "the same reachable LAN and that UDP discovery is not blocked."
            )

    def scan_failed(self, message):
        self.status.setText(f"Discovery failed: {message}")

    def accept_assignments(self):
        role_map = {
            "Primary bulb": "primary",
            "Secondary bulb": "secondary",
            "WiZ strip": "strip",
        }
        assignments = {}
        for row in range(self.table.rowCount()):
            ip = self.table.item(row, 0).text()
            role = role_map.get(self.table.cellWidget(row, 4).currentText())
            if role:
                if role in assignments:
                    QMessageBox.warning(
                        self, "Duplicate role",
                        f"Only one device can be assigned as {role} in this setup."
                    )
                    return
                assignments[role] = ip
        if not assignments:
            QMessageBox.information(
                self, "Nothing selected", "Choose at least one device role."
            )
            return
        self.selected_assignments = assignments
        self.accept()


class PowerSwitch(QPushButton):
    """Small native-painted WiZ-style switch using the current accent colour."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Light power")
        self.setToolTip("Power state is confirmed by the device")
        self.setStyleSheet("QPushButton { background: transparent; border: 0; padding: 0; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 2, -1, -2)
        on = self.isChecked()
        track = self.palette().color(QPalette.ColorRole.Highlight) if on else QColor("#C9CED8")
        if not self.isEnabled():
            track.setAlpha(145)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        knob = QColor("#FFFFFF")
        painter.setBrush(knob)
        painter.drawEllipse(int(x), int(r.top() + 3), int(d), int(d))
        painter.end()


class MetricCard(QFrame):
    def __init__(self, label, value="--", unit="", parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(4)
        self.label = QLabel(label.upper())
        self.label.setObjectName("MetricLabel")
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        self.unit = QLabel(unit)
        self.unit.setObjectName("Muted")
        layout.addWidget(self.label)
        row = QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(self.value, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addStretch(1)
        layout.addLayout(row)

    def set_value(self, value, unit=None):
        self.value.setText(str(value))
        if unit is not None:
            self.unit.setText(unit)


class BandMeter(QFrame):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        top = QHBoxLayout()
        self.name = QLabel(label)
        self.name.setObjectName("MetricLabel")
        self.value = QLabel("0%")
        self.value.setObjectName("MeterValue")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        layout.addLayout(top)
        layout.addWidget(self.bar)

    def set_value(self, value):
        pct = max(0, min(100, round(float(value) * 100)))
        self.value.setText(f"{pct}%")
        self.bar.setValue(pct)


class SpectrumWidget(QWidget):
    """Lightweight custom-painted music visualizer driven by engine telemetry."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.levels = [0.08] * 36
        self.history = [0.0] * 90
        self.metrics = {"bass": 0, "mids": 0, "highs": 0, "energy": 0}
        self.phase = 0

    def set_metrics(self, metrics):
        self.metrics = metrics
        target = max(0.02, min(1.0, float(metrics.get("energy", 0))))
        bands = [
            float(metrics.get("bass", 0)),
            float(metrics.get("mids", 0)),
            float(metrics.get("highs", 0)),
        ]
        for i in range(len(self.levels)):
            band = bands[(i // 12) % 3]
            wobble = ((i * 17 + self.phase * 7) % 13) / 100.0
            wanted = max(0.025, min(1.0, 0.18 + band * 0.66 + target * 0.22 + wobble))
            self.levels[i] = self.levels[i] * 0.58 + wanted * 0.42
        self.history.append(target)
        self.history = self.history[-90:]
        self.phase += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 3, -2, -3)
        base = self.palette().color(QPalette.ColorRole.Base)
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        painter.fillRect(rect, base)
        width = rect.width()
        height = rect.height()
        gap = 4
        bar_w = max(3, (width - gap * (len(self.levels) - 1)) / len(self.levels))
        baseline = rect.bottom() - 12
        for i, level in enumerate(self.levels):
            bar_h = max(5, (height - 26) * level)
            x = rect.left() + i * (bar_w + gap)
            y = baseline - bar_h
            gradient = QLinearGradient(x, y, x, baseline)
            top_colour = QColor(highlight).lighter(128)
            bottom_colour = QColor(highlight).darker(112)
            gradient.setColorAt(0, top_colour)
            gradient.setColorAt(1, bottom_colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 3, 3)
        # A fine energy trace below the spectrum gives the page a studio feel.
        if len(self.history) > 1:
            points = []
            for i, value in enumerate(self.history):
                x = rect.left() + i * width / max(1, len(self.history) - 1)
                y = rect.bottom() - 4 - value * 7
                points.append((x, y))
            trace = QColor(highlight).lighter(135 if self.palette().color(QPalette.ColorRole.Base).lightness() < 128 else 115)
            pen = QPen(trace, 1.4)
            painter.setPen(pen)
            for a, b in zip(points, points[1:]):
                painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        painter.end()


class MainWindow(QMainWindow):
    NAV_ITEMS = [
        ("Overview", "◈"),
        ("Visualizer", "≋"),
        ("Music Tuning", "⌁"),
        ("Devices", "▣"),
        ("Settings", "⚙"),
        ("Logs", "≡"),
    ]
    PRESETS = {
        "Subtle": {"bass_sensitivity": 0.90, "mid_sensitivity": 0.90, "high_sensitivity": 0.85, "motion_intensity": 0.80, "transition_speed": 0.85, "transient_strength": 0.80},
        "Balanced": {"bass_sensitivity": 1.00, "mid_sensitivity": 1.00, "high_sensitivity": 1.00, "motion_intensity": 1.00, "transition_speed": 1.00, "transient_strength": 1.00},
        "Dynamic": {"bass_sensitivity": 1.15, "mid_sensitivity": 1.12, "high_sensitivity": 1.25, "motion_intensity": 1.20, "transition_speed": 1.20, "transient_strength": 1.20},
    }
    TUNING_LABELS = [
        ("bass_sensitivity", "Bass sensitivity"),
        ("mid_sensitivity", "Mid sensitivity"),
        ("high_sensitivity", "High sensitivity"),
        ("motion_intensity", "Colour movement"),
        ("transition_speed", "Transition speed"),
        ("transient_strength", "Beat / transient response"),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon_file = resource_path("app_icon.png")
        if icon_file.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_file)))
        self.resize(1120, 760)
        self.setMinimumSize(900, 650)
        self.config_file = config_path()
        self.config = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.tuning_file = self.config_file.with_name("tuning.json")
        self.device_control_file = self.config_file.with_name("device_controls.json")
        self.tuning = self.load_tuning()
        self.theme_mode = self.config.get("app", {}).get("theme", "System")
        self.background_launch = "--background" in sys.argv
        self.device_widgets = {}
        self.overview_device_widgets = {}
        self.device_workers = []
        self.pending_device_actions = {}
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.engine_finished)
        self.stdout_buffer = ""
        self.stderr_buffer = ""
        self.log_entries = []
        self.metrics = {
            "bass": 0.0, "mids": 0.0, "highs": 0.0, "energy": 0.0,
            "phrase": 0.0, "brightness": 0, "target_brightness": 0,
            "devices_active": 0, "devices_total": 0, "flow": 0.0,
            "momentum": 0.0, "motion_depth": 0.0, "pulse": 0.0,
            "impact": "----", "audio_device": "Waiting for engine…",
        }
        self.current_page = 0
        self.build_ui()
        self.create_tray()
        # Engine telemetry can arrive faster than the GUI needs to repaint.
        # Coalesce updates to at most 10 UI refreshes per second.
        self.metrics_update_pending = False
        self.metrics_ui_timer = QTimer(self)
        self.metrics_ui_timer.setInterval(100)
        self.metrics_ui_timer.timeout.connect(self.flush_metric_update)
        self.metrics_ui_timer.start()
        self.autostart.toggled.connect(self.update_autostart)
        self.device_poll_timer = QTimer(self)
        self.device_poll_timer.setInterval(3500)
        self.device_poll_timer.timeout.connect(self.refresh_device_states)
        self.device_poll_queue = []
        self.device_polling_active = False
        self.device_error_log_times = {}
        self.device_poll_timer.start()
        QTimer.singleShot(900, self.refresh_device_states)
        self.theme_poll_timer = QTimer(self)
        self.theme_poll_timer.setInterval(5000)
        self.theme_poll_timer.timeout.connect(self.refresh_system_theme)
        self.theme_poll_timer.start()
        # A normal manual launch must always open in the foreground.
        # Only the explicit --background autostart argument starts hidden.
        if self.background_launch and self.autostart.isChecked():
            QTimer.singleShot(900, self.start_engine)
            QTimer.singleShot(1200, self.hide)

    def load_tuning(self):
        defaults = self.PRESETS["Balanced"].copy()
        if self.tuning_file.exists():
            try:
                data = json.loads(self.tuning_file.read_text(encoding="utf-8"))
                defaults.update({k: float(v) for k, v in data.items() if k in defaults})
            except Exception:
                pass
        return defaults

    def build_ui(self):
        self.apply_theme(self.theme_mode)
        root = QWidget()
        root.setObjectName("AppRoot")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(7)
        brand = QHBoxLayout()
        logo = QLabel("✦")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(34, 34)
        logo.setObjectName("AppLogo")
        brand.addWidget(logo)
        brand_text = QVBoxLayout()
        title = QLabel("WiZ Ambient Sync")
        title.setObjectName("AppTitle")
        title.setMinimumWidth(0)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subtitle = QLabel("MUSIC-REACTIVE LIGHTING")
        subtitle.setObjectName("Eyebrow")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand.addLayout(brand_text, 1)
        side.addLayout(brand)
        side.addSpacing(15)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (name, glyph) in enumerate(self.NAV_ITEMS):
            button = QPushButton(f"{glyph}   {name}")
            button.setObjectName("Nav")
            button.setMinimumHeight(42)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav_group.addButton(button, index)
            side.addWidget(button)
            if index == 1 or index == 3:
                side.addSpacing(7)
        self.nav_group.button(0).setChecked(True)
        side.addStretch(1)
        self.sidebar_status = QFrame()
        self.sidebar_status.setObjectName("Panel")
        status_layout = QVBoxLayout(self.sidebar_status)
        status_layout.setContentsMargins(12, 11, 12, 11)
        self.sidebar_status_title = QLabel("SYNC STOPPED")
        self.sidebar_status_title.setObjectName("StatusText")
        self.sidebar_audio = QLabel("Audio not connected")
        self.sidebar_audio.setObjectName("Muted")
        self.sidebar_devices = QLabel("0 devices connected")
        self.sidebar_devices.setObjectName("Muted")
        status_layout.addWidget(self.sidebar_status_title)
        status_layout.addWidget(self.sidebar_audio)
        status_layout.addWidget(self.sidebar_devices)
        side.addWidget(self.sidebar_status)
        outer.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("ContentArea")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 16, 22, 12)
        content_layout.setSpacing(10)
        header = QHBoxLayout()
        heading_box = QVBoxLayout()
        self.page_title = QLabel("Overview")
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel("Your lights, in rhythm.")
        self.page_subtitle.setObjectName("Subtitle")
        heading_box.addWidget(self.page_title)
        heading_box.addWidget(self.page_subtitle)
        header.addLayout(heading_box)
        header.addStretch(1)
        self.header_status = QLabel("●  STOPPED")
        self.header_status.setObjectName("StatusPill")
        self.header_status.setProperty("status", "stopped")
        self.header_status.setFixedHeight(26)
        self.header_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.header_status)
        content_layout.addLayout(header)

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        self.stack.addWidget(self.build_overview_page())
        self.stack.addWidget(self.build_visualizer_page())
        self.stack.addWidget(self.build_tuning_page())
        self.stack.addWidget(self.build_devices_page())
        self.stack.addWidget(self.build_settings_page())
        self.stack.addWidget(self.build_logs_page())
        self.stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        for page_index in range(self.stack.count()):
            page_widget = self.stack.widget(page_index)
            page_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            page_widget.setAutoFillBackground(True)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setObjectName("PageScroll")
        self.page_scroll.viewport().setAutoFillBackground(True)
        self.page_scroll.viewport().setObjectName("PageViewport")
        self.page_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.page_scroll.setWidget(self.stack)
        content_layout.addWidget(self.page_scroll, 1)
        footer = QHBoxLayout()
        self.footer_status = QLabel("Ready")
        self.footer_status.setObjectName("Muted")
        footer.addWidget(self.footer_status)
        footer.addStretch(1)
        content_layout.addLayout(footer)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)

    def panel(self, title, description=None):
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(9)
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
        if description:
            desc = QLabel(description)
            desc.setObjectName("Muted")
            desc.setWordWrap(True)
            layout.addWidget(desc)
        return frame, layout

    def build_overview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        hero = QFrame()
        hero.setObjectName("Panel")
        hero_l = QHBoxLayout(hero)
        hero_l.setContentsMargins(14, 10, 14, 10)
        hero_l.setSpacing(12)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(2)
        hero_title = QLabel("Music is driving your lights")
        hero_title.setObjectName("PanelTitle")
        hero_desc = QLabel("Screen colours move with the music.")
        hero_desc.setObjectName("Muted")
        hero_copy.addWidget(hero_title)
        hero_copy.addWidget(hero_desc)
        hero_l.addLayout(hero_copy, 1)
        self.start_button = QPushButton("Start")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.setIconSize(QSize(13, 13))
        self.start_button.setFixedHeight(20)
        self.start_button.setObjectName("Primary")
        self.start_button.setStyleSheet("QPushButton { border-radius:10px; }")
        self.start_button.setCheckable(True)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.setIconSize(QSize(13, 13))
        self.stop_button.setFixedHeight(20)
        self.stop_button.setObjectName("Danger")
        self.stop_button.setStyleSheet("QPushButton { border-radius:10px; }")
        self.stop_button.setCheckable(True)
        self.stop_button.setChecked(True)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self.start_engine)
        self.stop_button.clicked.connect(self.stop_engine)
        hero_l.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignVCenter)
        hero_l.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignVCenter)
        hero.setFixedHeight(70)
        layout.addWidget(hero)
        cards = QHBoxLayout()
        cards.setSpacing(5)
        self.overview_brightness = MetricCard("Brightness", "--", "%")
        self.overview_devices = MetricCard("Devices", "0 / 0", "connected")
        self.overview_energy = MetricCard("Music energy", "0", "%")
        self.overview_pulse = MetricCard("Pulse", "0.0", "impact")
        for card in (self.overview_brightness, self.overview_devices, self.overview_energy, self.overview_pulse):
            card.layout().setContentsMargins(12, 8, 12, 8)
            card.layout().setSpacing(2)
            card.setFixedHeight(112)
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        device_panel, device_panel_l = self.panel("Your lights")
        self.overview_device_panel = device_panel
        device_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        device_panel_l.setContentsMargins(13, 8, 13, 11)
        device_panel_l.setSpacing(3)
        self.overview_device_cards_layout = QGridLayout()
        self._overview_grid_columns = 3
        self.overview_device_cards_layout.setHorizontalSpacing(5)
        self.overview_device_cards_layout.setVerticalSpacing(6)
        for column in range(3):
            self.overview_device_cards_layout.setColumnStretch(column, 1)
        device_panel_l.addLayout(self.overview_device_cards_layout)
        layout.addWidget(device_panel)
        self.rebuild_overview_device_cards()
        visual, visual_l = self.panel("Live music activity")
        visual_l.setContentsMargins(12, 8, 12, 8)
        visual_l.setSpacing(4)
        self.overview_spectrum = SpectrumWidget()
        self.overview_spectrum.setMinimumHeight(76)
        self.overview_spectrum.setMaximumHeight(80)
        self.overview_spectrum.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        visual_l.addWidget(self.overview_spectrum)
        visual.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        visual.setFixedHeight(124)
        layout.addWidget(visual)
        bottom = QHBoxLayout()
        bottom.setSpacing(5)
        self.overview_audio = MetricCard("Audio source", "Waiting", "")
        self.overview_flow = MetricCard("Flow momentum", "0.00", "")
        for compact_card in (self.overview_audio, self.overview_flow):
            compact_card.layout().setContentsMargins(12, 8, 12, 8)
            compact_card.layout().setSpacing(2)
            compact_card.setFixedHeight(96)
        bottom.addWidget(self.overview_audio, 2)
        bottom.addWidget(self.overview_flow, 1)
        layout.addLayout(bottom)
        layout.addStretch(1)
        return page

    def build_visualizer_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(13)
        spectrum, spectrum_l = self.panel("Live spectrum", "Bass, mids and highs are analysed by the engine. The visualizer only displays those metrics; it does not capture audio itself.")
        self.visualizer_spectrum_panel = spectrum
        self.spectrum = SpectrumWidget()
        self.spectrum.setMinimumHeight(180)
        spectrum_l.addWidget(self.spectrum)
        layout.addWidget(spectrum, 3)
        meters = QHBoxLayout()
        self.band_meters = {
            "bass": BandMeter("Bass · 35–180 Hz"),
            "mids": BandMeter("Mids · 180–2.5 kHz"),
            "highs": BandMeter("Highs · 2.5–10 kHz"),
        }
        for meter in self.band_meters.values():
            meters.addWidget(meter, 1)
        layout.addLayout(meters, 1)
        cards = QHBoxLayout()
        self.visual_energy = MetricCard("Energy", "0%")
        self.visual_phrase = MetricCard("Phrase", "0%")
        self.visual_flow = MetricCard("Flow", "0.00")
        self.visual_motion = MetricCard("Motion depth", "0.00")
        self.visual_pulse = MetricCard("Pulse", "0.0")
        for card in (self.visual_energy, self.visual_phrase, self.visual_flow, self.visual_motion, self.visual_pulse):
            cards.addWidget(card, 1)
        layout.addLayout(cards, 1)
        self.impact_label = QLabel("Waiting for music metrics…")
        self.impact_label.setObjectName("Muted")
        layout.addWidget(self.impact_label)
        return page

    def resizeEvent(self, event):
        self.reflow_overview_device_grid()
        super().resizeEvent(event)

    def build_tuning_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(13)
        intro, intro_l = self.panel("Shape the way your lights react", "Changes are sent to the running engine through a small tuning file. Audio capture and device sender loops remain untouched.")
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Response preset"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(self.PRESETS) + ["Custom"])
        self.preset_combo.setCurrentText("Balanced")
        self.preset_combo.currentTextChanged.connect(self.apply_preset_to_sliders)
        preset_row.addWidget(self.preset_combo)
        preset_row.addStretch(1)
        intro_l.addLayout(preset_row)
        layout.addWidget(intro)
        controls, controls_l = self.panel("Fine tuning", "100% preserves the current Stage 4.0 behaviour. Adjust gently; values are clamped to a safe range.")
        self.tuning_sliders = {}
        self.tuning_values = {}
        for key, label in self.TUNING_LABELS:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(205)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(50, 150)
            slider.setValue(round(self.tuning.get(key, 1.0) * 100))
            value = QLabel(f"{slider.value()}%")
            value.setMinimumWidth(48)
            slider.valueChanged.connect(lambda v, k=key, lab=value: self.on_tuning_changed(k, v, lab))
            row.addWidget(name)
            row.addWidget(slider, 1)
            row.addWidget(value)
            controls_l.addLayout(row)
            self.tuning_sliders[key] = slider
            self.tuning_values[key] = value
        if any(abs(self.tuning.get(key, 1.0) - 1.0) > 0.001 for key, _ in self.TUNING_LABELS):
            self.preset_combo.setCurrentText("Custom")
        buttons = QHBoxLayout()
        self.reset_tuning_button = QPushButton("Reset to Balanced")
        self.apply_tuning_button = QPushButton("✓   Apply live")
        self.apply_tuning_button.setObjectName("Primary")
        self.reset_tuning_button.clicked.connect(lambda: self.set_preset("Balanced"))
        self.apply_tuning_button.clicked.connect(self.apply_tuning)
        buttons.addWidget(self.reset_tuning_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_tuning_button)
        controls_l.addLayout(buttons)
        layout.addWidget(controls, 1)
        note, note_l = self.panel("What these controls change")
        note_l.addWidget(QLabel("• Sensitivity controls the strength of each frequency band's contribution.\n• Colour movement and transition speed affect flow only, not audio capture.\n• Beat / transient response changes the momentum injected by detected impacts.\n• Existing bulb and strip-specific multipliers remain in place."))
        layout.addWidget(note)
        return page

    def build_devices_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(13)
        quick, quick_l = self.panel("Light controls", "Power and brightness are read from each WiZ device. Status refreshes in a background worker while music sync is running; polling never runs in the audio engine.")
        actions = QHBoxLayout()
        all_on = QPushButton("All lights on")
        all_off = QPushButton("All lights off")
        all_on.clicked.connect(lambda: self.control_all_devices("on"))
        all_off.clicked.connect(lambda: self.control_all_devices("off"))
        actions.addWidget(all_on)
        actions.addWidget(all_off)
        actions.addStretch(1)
        discover_now = QPushButton("Discover devices")
        discover_now.clicked.connect(self.find_devices)
        actions.addWidget(discover_now)
        refresh = QPushButton("Refresh status")
        refresh.clicked.connect(self.refresh_device_states)
        actions.addWidget(refresh)
        quick_l.addLayout(actions)
        self.device_status = QLabel("Loading configured WiZ devices…")
        self.device_status.setObjectName("Muted")
        self.device_status.setWordWrap(True)
        quick_l.addWidget(self.device_status)
        self.device_cards_layout = QVBoxLayout()
        self.device_cards_layout.setSpacing(9)
        quick_l.addLayout(self.device_cards_layout)
        layout.addWidget(quick)

        box, form = self.panel("Device addresses", "Configure devices here or use discovery. Device controls use these saved addresses.")
        device = self.config.setdefault("device", {})
        self.primary_ip = QLineEdit(device.get("bulb_ip", ""))
        self.secondary_ip = QLineEdit(device.get("secondary_bulb_ip", ""))
        self.strip_ip = QLineEdit(device.get("strip_ip", ""))
        for field in (self.primary_ip, self.secondary_ip, self.strip_ip):
            field.setPlaceholderText("e.g. 192.168.1.25 · leave blank if unused")
        form.addWidget(self.field_row("Primary bulb", self.primary_ip))
        form.addWidget(self.field_row("Secondary bulb", self.secondary_ip))
        form.addWidget(self.field_row("WiZ strip", self.strip_ip))
        device_buttons = QHBoxLayout()
        self.find_button = QPushButton("Discover devices")
        self.find_button.clicked.connect(self.find_devices)
        save_devices = QPushButton("Save device settings")
        save_devices.setObjectName("Primary")
        save_devices.clicked.connect(self.save_settings)
        device_buttons.addWidget(self.find_button)
        device_buttons.addStretch(1)
        device_buttons.addWidget(save_devices)
        form.addLayout(device_buttons)
        layout.addWidget(box)
        display, display_l = self.panel("Screen palette source", "The engine samples the selected display to build the colour vocabulary.")
        self.monitor = QComboBox()
        self.refresh_monitors()
        display_l.addWidget(self.field_row("Capture display", self.monitor))
        layout.addWidget(display)
        layout.addStretch(1)
        self.rebuild_device_cards()
        return page

    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(13)
        appearance, appearance_l = self.panel("Appearance", "Choose a light or dark Liquid Glass appearance, or follow Windows automatically.")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.setCurrentText(self.theme_mode if self.theme_mode in ("System", "Light", "Dark") else "System")
        self.theme_combo.currentTextChanged.connect(self.change_theme)
        appearance_l.addWidget(self.field_row("Theme", self.theme_combo))
        layout.addWidget(appearance)
        background, bg_l = self.panel("Startup and background behaviour", "Choose how WiZ Ambient Sync behaves when you sign in or close the window.")
        self.autostart = QCheckBox("Launch WiZ Ambient Sync when I sign in")
        self.autostart.setChecked(bool(self.config.get("app", {}).get("launch_at_login", False)))
        bg_l.addWidget(self.autostart)
        self.minimise_to_tray = QCheckBox("Keep syncing in the system tray when the window is closed")
        self.minimise_to_tray.setChecked(bool(self.config.get("app", {}).get("minimise_to_tray", True)))
        bg_l.addWidget(self.minimise_to_tray)
        layout.addWidget(background)
        audio, audio_l = self.panel("Audio analysis", "Audio capture currently uses the Windows default output loopback selected by SoundCard.")
        self.audio_device_label = QLabel(self.metrics["audio_device"])
        self.audio_device_label.setObjectName("Muted")
        audio_l.addWidget(self.field_row("Current audio source", self.audio_device_label))
        layout.addWidget(audio)
        advanced, advanced_l = self.panel("Application information")
        advanced_l.addWidget(QLabel("Engine: Stage 4.0 Musical Flow\nGUI: PySide6 desktop shell\nTelemetry: local process pipe\nConfiguration: per-user AppData folder"))
        layout.addWidget(advanced)
        save = QPushButton("Save preferences")
        save.setObjectName("Primary")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)
        layout.addStretch(1)
        return page

    def build_logs_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(12)
        top, top_l = self.panel("Engine logs", "Audio metrics are shown in the Visualizer. This view is reserved for meaningful events and diagnostics.")
        controls = QHBoxLayout()
        self.log_filter = QComboBox()
        self.log_filter.addItems(["All messages", "Info", "Warnings", "Errors"])
        self.log_filter.currentTextChanged.connect(self.refresh_logs)
        controls.addWidget(self.log_filter)
        controls.addStretch(1)
        self.copy_logs_button = QPushButton("Copy")
        self.copy_logs_button.clicked.connect(self.copy_logs)
        self.export_logs_button = QPushButton("Export")
        self.export_logs_button.clicked.connect(self.export_logs)
        self.clear_logs_button = QPushButton("Clear view")
        self.clear_logs_button.clicked.connect(self.clear_logs)
        controls.addWidget(self.copy_logs_button)
        controls.addWidget(self.export_logs_button)
        controls.addWidget(self.clear_logs_button)
        top_l.addLayout(controls)
        layout.addWidget(top)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Engine startup, device connections, warnings and errors will appear here.")
        self.log_view.setMaximumBlockCount(1000)
        layout.addWidget(self.log_view, 1)
        return page

    def field_row(self, label, widget):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 3, 0, 3)
        title = QLabel(label)
        title.setMinimumWidth(155)
        title.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(widget, 1)
        return row

    def navigate(self, index):
        self.current_page = index
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.NAV_ITEMS[index][0])
        subtitles = [
            "Your lights, in rhythm.",
            "See the music that is moving your lights.",
            "Shape how the engine responds to music.",
            "Discover and manage your WiZ devices.",
            "Make the application work the way you prefer.",
            "Technical events, without the metric noise.",
        ]
        self.page_subtitle.setText(subtitles[index])
        if index == 5:
            self.refresh_logs()

    def on_tuning_changed(self, key, value, label):
        label.setText(f"{value}%")
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText("Custom") if self.preset_combo.findText("Custom") >= 0 else None
        self.preset_combo.blockSignals(False)
        self.tuning[key] = value / 100.0

    def apply_preset_to_sliders(self, name):
        if name not in self.PRESETS:
            return
        values = self.PRESETS[name]
        for key, value in values.items():
            slider = self.tuning_sliders[key]
            slider.blockSignals(True)
            slider.setValue(round(value * 100))
            slider.blockSignals(False)
            self.tuning[key] = value
            self.tuning_values[key].setText(f"{round(value * 100)}%")

    def set_preset(self, name):
        self.preset_combo.setCurrentText(name)
        self.apply_preset_to_sliders(name)
        self.apply_tuning()

    def apply_tuning(self):
        self.tuning = {key: slider.value() / 100.0 for key, slider in self.tuning_sliders.items()}
        try:
            temp = self.tuning_file.with_suffix(".tmp")
            temp.write_text(json.dumps(self.tuning, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.tuning_file)
            self.append_log("Live musical tuning updated.", "Info")
            self.footer_status.setText("Tuning applied live · no engine restart required")
        except Exception as exc:
            QMessageBox.warning(self, "Could not apply tuning", str(exc))

    def refresh_monitors(self):
        self.monitor.clear()
        try:
            with mss.MSS() as capture:
                for index, mon in enumerate(capture.monitors[1:], start=1):
                    self.monitor.addItem(f"Display {index} — {mon['width']} × {mon['height']}", index)
        except Exception as exc:
            self.monitor.addItem(f"Display detection unavailable: {exc}", 1)
        current = int(self.config.get("device", {}).get("monitor", 1))
        idx = self.monitor.findData(current)
        if idx >= 0:
            self.monitor.setCurrentIndex(idx)

    def configured_control_devices(self):
        cfg = self.config.get("device", {})
        entries = [
            ("Primary bulb", cfg.get("bulb_ip", "")),
            ("Secondary bulb", cfg.get("secondary_bulb_ip", "")),
            ("WiZ strip", cfg.get("strip_ip", "")),
        ]
        unique = {}
        for name, ip in entries:
            ip = str(ip or "").strip()
            if ip and ip not in unique:
                unique[ip] = name
        return [(name, ip) for ip, name in unique.items()]

    def overview_grid_column_count(self):
        if not hasattr(self, "overview_device_cards_layout"):
            return 3
        panel_width = getattr(self, "overview_device_panel", None)
        available_width = panel_width.width() - 26 if panel_width else 0
        if available_width <= 0:
            return 3
        return 3

    def reflow_overview_device_grid(self):
        if not hasattr(self, "overview_device_cards_layout"):
            return
        columns = self.overview_grid_column_count()
        if columns != getattr(self, "_overview_grid_columns", 3):
            self._overview_grid_columns = columns
            self.rebuild_overview_device_cards()

    def rebuild_overview_device_cards(self):
        """Build compact, glanceable device tiles on the opening dashboard."""
        if not hasattr(self, "overview_device_cards_layout"):
            return
        while self.overview_device_cards_layout.count():
            item = self.overview_device_cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.overview_device_widgets.clear()
        devices = self.configured_control_devices()
        columns = self.overview_grid_column_count()
        if not devices:
            empty = QLabel("No lights configured")
            empty.setObjectName("Muted")
            self.overview_device_cards_layout.addWidget(empty, 0, 0, 1, columns)
            return
        for index, (name, ip) in enumerate(devices):
            tile = QFrame()
            tile.setObjectName("DeviceCard")
            tile.setMinimumWidth(0)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(11, 8, 11, 8)
            tile_layout.setSpacing(6)
            header = QHBoxLayout()
            title = QLabel(name)
            title.setObjectName("DeviceTitle")
            status = QLabel("Checking…")
            status.setObjectName("DeviceStatus")
            status.setProperty("status", "checking")
            switch = PowerSwitch()
            switch.setEnabled(False)
            switch.toggled.connect(lambda checked, target=ip: self.run_device_action(target, "on" if checked else "off"))
            header.addWidget(title, 1)
            header.addWidget(status)
            header.addWidget(switch)
            tile_layout.addLayout(header)
            brightness_row = QHBoxLayout()
            brightness_label = QLabel("Brightness")
            brightness_label.setObjectName("Muted")
            value = QLabel("--%")
            value.setMinimumWidth(36)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 100)
            slider.setValue(60)
            slider.setEnabled(False)
            slider.setFixedHeight(18)
            slider.setToolTip("Device-reported brightness. Available when music sync is stopped.")
            slider.sliderReleased.connect(lambda target=ip, control=slider: self.run_device_action(target, "brightness", control.value()))
            brightness_row.addWidget(brightness_label)
            brightness_row.addWidget(slider, 1)
            brightness_row.addWidget(value)
            tile_layout.addLayout(brightness_row)
            tile.setToolTip(ip)
            row, column = divmod(index, columns)
            self.overview_device_cards_layout.addWidget(tile, row, column)
            self.overview_device_widgets[ip] = {
                "status": status, "power": switch, "slider": slider, "value": value,
                "last_known_on": None, "last_known_brightness": None,
            }

    def update_overview_device(self, ip, state=None, error=None):
        controls = self.overview_device_widgets.get(ip)
        if not controls:
            return
        if state is not None:
            controls["status"].setText("Online")
            controls["status"].setProperty("status", "online")
            controls["status"].style().unpolish(controls["status"])
            controls["status"].style().polish(controls["status"])
            controls["last_known_on"] = bool(state["on"])
            controls["last_known_brightness"] = int(state["brightness"])
            switch = controls["power"]
            switch.blockSignals(True)
            switch.setChecked(bool(state["on"]))
            switch.setEnabled(True)
            switch.setToolTip("Device reports ON · click to turn off" if state["on"] else "Device reports OFF · click to turn on")
            switch.blockSignals(False)
            slider = controls["slider"]
            slider.blockSignals(True)
            slider.setValue(max(1, int(state["brightness"])))
            sync_running = self.process.state() != QProcess.ProcessState.NotRunning
            slider.setEnabled(bool(state["on"]) and not sync_running)
            slider.blockSignals(False)
            slider.setToolTip("Brightness control is available while sync is stopped." if sync_running else "Drag to set brightness; release to send it to the bulb.")
            controls["value"].setText(f"{int(state['brightness'])}%")
        elif error is not None:
            controls["status"].setText("Offline")
            controls["status"].setProperty("status", "offline")
            controls["status"].style().unpolish(controls["status"])
            controls["status"].style().polish(controls["status"])
            switch = controls["power"]
            controls["slider"].setEnabled(False)
            switch.setEnabled(controls["last_known_on"] is not None)
            switch.setToolTip("No recent device response · click to retry")
            if controls["last_known_on"] is not None:
                switch.blockSignals(True)
                switch.setChecked(bool(controls["last_known_on"]))
                switch.blockSignals(False)

    def rebuild_device_cards(self):
        if not hasattr(self, "device_cards_layout"):
            return
        while self.device_cards_layout.count():
            item = self.device_cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.device_widgets.clear()
        devices = self.configured_control_devices()
        if not devices:
            empty = QLabel("No devices configured yet. Add an IP address below or discover devices on your network.")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            self.device_cards_layout.addWidget(empty)
            if hasattr(self, "device_status"):
                self.device_status.setText("No WiZ devices configured. Add a bulb or strip address, or use Discover devices.")
            return
        if hasattr(self, "device_status"):
            self.device_status.setText(f"{len(devices)} configured device(s) · checking their live status…")
        for name, ip in devices:
            card = QFrame()
            card.setObjectName("DeviceCard")
            row = QVBoxLayout(card)
            row.setContentsMargins(13, 10, 13, 10)
            top = QHBoxLayout()
            title = QLabel(name)
            title.setObjectName("DeviceTitle")
            status = QLabel("Checking…")
            status.setObjectName("DeviceStatus")
            status.setProperty("status", "checking")
            power = PowerSwitch()
            power.setChecked(False)
            power.setEnabled(False)
            power.toggled.connect(lambda checked, target=ip: self.run_device_action(target, "on" if checked else "off"))
            top.addWidget(title)
            top.addWidget(status, 1)
            top.addWidget(power)
            row.addLayout(top)
            address = QLabel(ip)
            address.setObjectName("Muted")
            row.addWidget(address)
            bottom = QHBoxLayout()
            bottom.addWidget(QLabel("Brightness"))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 100)
            slider.setEnabled(False)
            value_label = QLabel("--%")
            value_label.setMinimumWidth(42)
            slider.sliderReleased.connect(lambda target=ip, control=slider: self.run_device_action(target, "brightness", control.value()))
            bottom.addWidget(slider, 1)
            bottom.addWidget(value_label)
            row.addLayout(bottom)
            self.device_cards_layout.addWidget(card)
            self.device_widgets[ip] = {
                "status": status, "power": power, "slider": slider,
                "value": value_label, "name": name, "last_known_on": None,
                "last_known_brightness": None,
            }

    def _device_worker_done(self, worker):
        if worker in self.device_workers:
            self.device_workers.remove(worker)
        worker.deleteLater()
        controls = self.device_widgets.get(getattr(worker, "ip", ""))
        if controls:
            controls["power"].setEnabled(True)
        overview_controls = self.overview_device_widgets.get(getattr(worker, "ip", ""))
        if overview_controls:
            overview_controls["power"].setEnabled(True)
        pending = self.pending_device_actions.pop(getattr(worker, "ip", ""), None)
        if pending:
            QTimer.singleShot(0, lambda ip=worker.ip, op=pending[0], val=pending[1]: self.run_device_action(ip, op, val))
        elif self.device_polling_active:
            QTimer.singleShot(120, self._poll_next_device)

    def run_device_action(self, ip, action="poll", value=None):
        if any(getattr(w, "ip", None) == ip and w.isRunning() for w in self.device_workers):
            if action != "poll":
                self.pending_device_actions[ip] = (action, value)
            return
        if action in ("on", "off"):
            # Persist the requested engine state, but do not let the switch
            # pretend the physical bulb changed until getPilot confirms it.
            self.set_device_enabled(ip, action == "on")
            controls = self.device_widgets.get(ip)
            if controls and controls.get("last_known_on") is not None:
                power = controls["power"]
                power.blockSignals(True)
                power.setChecked(bool(controls["last_known_on"]))
                power.blockSignals(False)
        worker = DeviceControlWorker(ip, action, value, self)
        if action in ("on", "off"):
            controls = self.device_widgets.get(ip)
            if controls:
                controls["power"].setEnabled(False)
            overview_controls = self.overview_device_widgets.get(ip)
            if overview_controls:
                overview_controls["power"].setEnabled(False)
                if overview_controls.get("last_known_on") is not None:
                    overview_controls["power"].blockSignals(True)
                    overview_controls["power"].setChecked(bool(overview_controls["last_known_on"]))
                    overview_controls["power"].blockSignals(False)
        self.device_workers.append(worker)
        worker.completed.connect(self.on_device_state)
        worker.failed.connect(self.on_device_error)
        worker.finished.connect(lambda w=worker: self._device_worker_done(w))
        worker.start()

    def refresh_device_states(self):
        if not hasattr(self, "device_widgets") or self.device_polling_active:
            return
        self.device_poll_queue = [ip for _, ip in self.configured_control_devices()]
        self.device_polling_active = bool(self.device_poll_queue)
        self._poll_next_device()

    def _poll_next_device(self):
        if not self.device_polling_active:
            return
        if any(worker.isRunning() for worker in self.device_workers):
            return
        if not self.device_poll_queue:
            self.device_polling_active = False
            return
        ip = self.device_poll_queue.pop(0)
        self.run_device_action(ip, "poll")

    def on_device_state(self, ip, state):
        # Overview tiles and the Devices page are separate widget collections.
        # Always refresh the Overview tile even if the Devices page controls
        # have not been constructed (or their mapping is temporarily absent).
        self.update_overview_device(ip, state=state)
        controls = self.device_widgets.get(ip)
        if not controls:
            self.update_reported_brightness_metric()
            self.update_live_device_count()
            return
        controls["status"].setText("Online")
        controls["status"].setProperty("status", "online")
        controls["status"].style().unpolish(controls["status"])
        controls["status"].style().polish(controls["status"])
        power = controls["power"]
        controls["last_known_on"] = bool(state["on"])
        controls["last_known_brightness"] = int(state["brightness"])
        power.blockSignals(True)
        power.setChecked(bool(state["on"]))
        power.setEnabled(True)
        power.setToolTip("Device reports ON · click to turn off" if state["on"] else "Device reports OFF · click to turn on")
        power.blockSignals(False)
        slider = controls["slider"]
        slider.blockSignals(True)
        slider.setValue(max(1, int(state["brightness"])))
        slider.setEnabled(bool(state["on"]) and self.process.state() == QProcess.ProcessState.NotRunning)
        slider.setToolTip("Live device-reported brightness. Changes are read-only while music sync is active." if self.process.state() != QProcess.ProcessState.NotRunning else "Current brightness reported by the device. Drag and release to change it.")
        slider.blockSignals(False)
        controls["value"].setText(f"{state['brightness']}%")
        controls["value"].setToolTip(f"Brightness reported by the latest getPilot query: {state.get('reported_brightness', state['brightness'])}")
        self.update_reported_brightness_metric()
        self.update_live_device_count()
        if hasattr(self, "device_status"):
            online = sum(1 for item in self.device_widgets.values() if item["status"].text() == "Online")
            total = len(self.device_widgets)
            self.device_status.setText(f"{online} of {total} configured device(s) online.")

    def update_live_device_count(self):
        """Use device getPilot responses for the dashboard count, not engine telemetry."""
        devices = list(getattr(self, "overview_device_widgets", {}).values())
        total = len(devices)
        online = sum(1 for item in devices if item["status"].text() == "Online")
        if hasattr(self, "overview_devices"):
            self.overview_devices.set_value(f"{online} / {total}", "connected")
        if hasattr(self, "sidebar_devices"):
            self.sidebar_devices.setText(f"{online} / {total} devices connected")

    def update_reported_brightness_metric(self):
        """Average live brightness from all currently online overview tiles."""
        if not hasattr(self, "overview_brightness"):
            return
        readings = [
            item["last_known_brightness"]
            for item in self.overview_device_widgets.values()
            if item.get("last_known_brightness") is not None and item["status"].text() == "Online"
        ]
        if readings:
            value = round(sum(readings) / len(readings))
            self.overview_brightness.set_value(value, "%")
            self.overview_brightness.setToolTip(
                f"Average getPilot brightness from {len(readings)} online device(s)."
            )
        else:
            self.overview_brightness.set_value("--", "%")
            self.overview_brightness.setToolTip("Waiting for device-reported brightness.")

    def on_device_error(self, ip, error):
        controls = self.device_widgets.get(ip)
        if controls:
            controls["status"].setText("Offline · check IP / network")
            controls["status"].setProperty("status", "offline")
            controls["status"].style().unpolish(controls["status"])
            controls["status"].style().polish(controls["status"])
            # Keep the switch interactive even when status polling fails:
            # the user may still issue an ON/OFF command after an IP/network
            # change. Do not infer that an unreachable device is OFF.
            controls["power"].setEnabled(True)
            controls["power"].setToolTip(
                "Device is not responding. Click to retry an ON/OFF command."
            )
            if controls.get("last_known_on") is not None:
                self.set_device_enabled(ip, bool(controls["last_known_on"]))
                controls["power"].blockSignals(True)
                controls["power"].setChecked(bool(controls["last_known_on"]))
                controls["power"].blockSignals(False)
            controls["slider"].setEnabled(False)
            self.update_reported_brightness_metric()
        self.update_overview_device(ip, error=error)
        self.update_reported_brightness_metric()
        self.update_live_device_count()
        if hasattr(self, "device_status"):
            online = sum(1 for item in self.device_widgets.values() if item["status"].text() == "Online")
            total = len(self.device_widgets)
            self.device_status.setText(f"{online} of {total} configured device(s) online · one or more devices need attention.")
        now = time.monotonic()
        last_logged = self.device_error_log_times.get(ip, 0.0)
        if now - last_logged >= 60.0:
            self.device_error_log_times[ip] = now
            self.append_log(
                f"Device {ip} did not respond ({error}). Check its current IP and Wi-Fi/LAN reachability.",
                "Warning",
            )

    def set_device_enabled(self, ip, enabled):
        try:
            data = {}
            if self.device_control_file.exists():
                data = json.loads(self.device_control_file.read_text(encoding="utf-8"))
            data[str(ip)] = {"enabled": bool(enabled)}
            temp = self.device_control_file.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.device_control_file)
        except Exception as exc:
            self.append_log(f"Could not update device control state: {exc}", "Warning")

    def control_all_devices(self, action):
        for _, ip in self.configured_control_devices():
            self.run_device_action(ip, action)

    def resolved_theme(self):
        if self.theme_mode in ("Light", "Dark"):
            return self.theme_mode
        # Prefer the OS colour-scheme hint. QApplication's palette is
        # customised by this app, so reading it here would make System mode
        # inherit our own previous theme rather than Windows' current theme.
        try:
            scheme = QApplication.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "Dark"
            if scheme == Qt.ColorScheme.Light:
                return "Light"
        except Exception:
            pass
        palette = QApplication.palette()
        return "Light" if palette.color(QPalette.ColorRole.Window).lightness() > 128 else "Dark"

    def apply_theme(self, mode=None):
        """Apply a lightweight Liquid Glass-inspired palette.

        Deliberately uses translucent fills and fine borders instead of blur
        effects: this keeps theme changes presentation-only and avoids adding
        a rendering workload to the music-sync process.
        """
        if mode:
            self.theme_mode = mode
        theme = self.resolved_theme()
        self._last_applied_theme = theme
        if theme == "Light":
            # A true white + purple palette. Avoid translucent grey cards:
            # Windows/Qt can composite transparent child widgets over black
            # native surfaces, which produced the previous muddy appearance.
            bg = "#F7F5FC"
            sidebar = "#FFFFFF"
            panel = "#FFFFFF"
            panel_hover = "#FCFAFF"
            field = "#FFFFFF"
            text, muted = "#29243A", "#716A82"
            border = "#E8E1F2"
            accent, accent_soft = "#7652B5", "#F0EAF9"
            hover, selected = "#F5F0FB", "#EDE5F8"
            track, handle = "#E6E0EE", "#7652B5"
            shadow_edge = "#FFFFFF"
        else:
            bg = "#17161d"
            sidebar = "rgba(34,32,43,0.72)"
            panel = "rgba(48,45,59,0.64)"
            panel_hover = "rgba(56,52,68,0.78)"
            field = "rgba(29,27,38,0.72)"
            text, muted = "#f1eff6", "#aaa5b7"
            border = "rgba(231,224,250,0.12)"
            accent, accent_soft = "#b39bdd", "rgba(179,155,221,0.13)"
            hover, selected = "rgba(255,255,255,0.055)", "rgba(179,155,221,0.13)"
            track, handle = "#454150", "#c2a9ed"
            shadow_edge = "rgba(255,255,255,0.035)"

        # Set a real Qt palette as well as QSS. Custom-painted widgets and native
        # controls (menus, combo popups, dialogs) otherwise retain dark defaults.
        palette = QApplication.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(bg if not bg.startswith("rgba") else ("#eeedf3" if theme == "Light" else "#17161d")))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff" if theme == "Light" else "#211f29"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F8F6FC" if theme == "Light" else "#2a2734"))
        palette.setColor(QPalette.ColorRole.Text, QColor(text))
        palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff" if theme == "Light" else "#302c39"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(text))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff" if theme == "Light" else "#302c39"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(text))
        QApplication.setPalette(palette)
        self.setPalette(palette)
        arrow_file = resource_path("chevron-light.png" if theme == "Light" else "chevron-dark.png")
        arrow_path = arrow_file.as_posix()
        self.setStyleSheet(f"""
            QMainWindow {{ background:{bg}; color:{text}; }}
            QWidget#AppRoot, QWidget#ContentArea, QStackedWidget,
            QScrollArea#PageScroll, QWidget#PageViewport {{
                background:{bg}; color:{text};
            }}
            QScrollArea#PageScroll QWidget#qt_scrollarea_viewport,
            QWidget#PageViewport {{
                background:{bg}; color:{text};
            }}
            QStackedWidget > QWidget {{ background:{bg}; color:{text}; }}
            QWidget {{
                background:transparent;
                color:{text};
                font-family:'Segoe UI';
                font-size:10pt;
            }}
            QFrame#Sidebar {{
                background:{sidebar};
                border-right:1px solid {border};
            }}
            QFrame#Panel, QFrame#MetricCard, QFrame#DeviceCard {{
                background:{panel};
                border:1px solid {border};
                border-radius:15px;
            }}
            QFrame#DeviceCard:hover {{ background:{panel_hover}; }}
            QLabel#AppTitle {{ font-size:11.5pt; font-weight:700; }}
            QLabel#AppLogo {{ background:{accent}; border-radius:11px; color:white; font-size:21pt; font-weight:700; }}
            QLabel#Eyebrow {{ color:{muted}; font-size:7pt; letter-spacing:1px; }}
            QLabel#StatusText {{ color:{muted}; font-size:8pt; font-weight:700; }}
            QLabel#PanelTitle {{ font-size:11pt; font-weight:700; }}
            QLabel#DeviceTitle {{ font-weight:600; }}
            QLabel#DeviceStatus {{ padding-left:5px; }}
            QFrame#DeviceCard {{ min-width:0; }}
            QLabel#DeviceStatus[status="online"] {{ color:#21865f; }}
            QLabel#DeviceStatus[status="offline"] {{ color:#b46c42; }}
            QLabel#DeviceStatus[status="checking"] {{ color:{muted}; }}
            QLabel#StatusPill {{ color:{muted}; background:{accent_soft}; border:1px solid {border}; border-radius:10px; padding:5px 9px; font-size:8pt; font-weight:700; }}
            QLabel#StatusPill[status="active"] {{ color:#21865f; background:rgba(54,170,120,0.13); }}
            QLabel#StatusPill[status="starting"] {{ color:#a87932; background:rgba(220,170,70,0.15); }}
            QLabel#PageTitle {{ font-size:21pt; font-weight:650; }}
            QLabel#Subtitle, QLabel#Muted {{ color:{muted}; }}
            QLabel#MetricLabel {{ color:{muted}; font-size:8pt; font-weight:650; letter-spacing:0.6px; }}
            QLabel#MetricValue {{ color:{text}; font-size:22pt; font-weight:650; }}
            QLabel#MeterValue {{ color:{accent}; font-weight:650; }}
            QPushButton {{
                background:{field};
                border:1px solid {border};
                border-radius:16px;
                padding:5px 10px;
                min-height:28px;
                min-width:50px;
                color:{text};
                font-size:9pt;
                font-weight:500;
            }}
            QPushButton#Primary, QPushButton#Danger {{ border-radius:13px; min-height:0; padding:3px 10px; }}
            QPushButton:hover {{ background:{hover}; border-color:{accent}; }}
            QPushButton:pressed {{ background:{selected}; }}
            QPushButton:disabled {{ color:{muted}; background:{field}; border-color:{border}; }}
            QPushButton#Primary {{
                background:{accent}; border:1px solid {accent}; border-radius:20px;
                color:white; font-weight:650;
            }}
            QPushButton#Primary:hover {{ background:{handle}; border-color:{handle}; }}
            QPushButton#Primary:pressed {{ background:{selected}; border-color:{selected}; padding-top:10px; padding-bottom:8px; }}
            QPushButton#Primary:checked {{ background:#23845F; border-color:#23845F; color:white; }}
            QPushButton#Primary:checked:hover {{ background:#1D7352; border-color:#1D7352; }}
            QPushButton#Danger {{ background:{accent_soft}; border:1px solid {border}; border-radius:13px; }}
            QPushButton#Danger:hover {{ background:{hover}; border-color:{accent}; }}
            QPushButton#Danger:pressed {{ background:{selected}; padding-top:10px; padding-bottom:8px; }}
            QPushButton#Primary:pressed, QPushButton#Danger:pressed {{ padding:3px 10px; }}
            QPushButton#Danger:checked {{ background:#E8EDF5; border:1px solid #B9C5D8; color:#34445D; }}
            QPushButton#Danger:checked:hover {{ background:#DCE5F1; border-color:#9EADC3; }}
            QPushButton#Primary, QPushButton#Danger {{ border-radius:999px; }}
            QPushButton#Nav {{
                text-align:left; padding:9px 12px; min-height:40px; min-width:0;
                background:transparent; border:1px solid transparent;
                color:{muted}; border-radius:10px; font-weight:550;
            }}
            QPushButton#Nav:hover {{ background:{hover}; color:{text}; border-color:{border}; }}
            QPushButton#Nav:checked {{
                background:{selected}; color:{text};
                border:1px solid {border}; font-weight:700;
            }}
            QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {{
                background:{field}; border:1px solid {border};
                border-radius:10px; padding:7px;
                selection-background-color:{accent};
                gridline-color:{border};
            }}
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color:{accent}; }}
            QComboBox {{ padding-right:28px; }}
            QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right; width:26px; border:0; }}
            QComboBox::down-arrow {{ image:url("{arrow_path}"); width:10px; height:7px; }}
            QComboBox QAbstractItemView {{
                background:{panel_hover}; color:{text};
                border:1px solid {border}; selection-background-color:{selected};
                outline:0; padding:4px;
            }}
            QHeaderView::section {{
                background:{accent_soft}; color:{muted};
                border:0; border-bottom:1px solid {border};
                padding:8px; font-weight:600;
            }}
            QSlider::groove:horizontal {{ height:5px; background:{track}; border-radius:3px; }}
            QSlider::sub-page:horizontal {{ background:{accent}; border-radius:3px; }}
            QSlider::handle:horizontal {{
                background:{handle}; border:2px solid {bg};
                width:14px; height:14px; margin:-6px 0; border-radius:8px;
            }}
            QSlider::handle:horizontal:hover {{ background:{text}; }}
            QSlider::groove:horizontal:disabled {{ background:{border}; }}
            QSlider::sub-page:horizontal:disabled {{ background:{border}; }}
            QSlider::handle:horizontal:disabled {{ background:{muted}; border-color:{bg}; }}
            QCheckBox {{ spacing:8px; }}
            QCheckBox::indicator {{
                width:17px; height:17px; border-radius:5px;
                background:{field}; border:1px solid {border};
            }}
            QCheckBox::indicator:checked {{ background:{accent}; border-color:{accent}; }}
            QPlainTextEdit {{ font-family:Consolas,'Cascadia Mono'; font-size:9pt; }}
            QProgressBar {{ background:{track}; border:0; border-radius:4px; height:6px; text-align:center; }}
            QProgressBar::chunk {{ background:{accent}; border-radius:4px; }}
            QScrollBar:vertical {{ background:transparent; width:9px; margin:3px; }}
            QScrollBar::handle:vertical {{ background:{border}; min-height:28px; border-radius:4px; }}
            QScrollBar::handle:vertical:hover {{ background:{accent}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QToolTip {{ background:{panel_hover}; color:{text}; border:1px solid {border}; padding:6px 8px; border-radius:7px; }}
        """)

    def change_theme(self, mode):
        self.theme_mode = mode
        self.apply_theme(mode)
        self.style_tray_menu()
        self.config.setdefault("app", {})["theme"] = mode
        try:
            temp = self.config_file.with_suffix(".tmp")
            temp.write_text(json.dumps(self.config, indent=4) + "\n", encoding="utf-8")
            temp.replace(self.config_file)
        except Exception as exc:
            self.append_log(f"Could not save theme preference: {exc}", "Warning")

    def refresh_system_theme(self):
        if self.theme_mode == "System":
            resolved = self.resolved_theme()
            if getattr(self, "_last_applied_theme", None) != resolved:
                self.apply_theme("System")

    def find_devices(self):
        existing = [self.primary_ip.text().strip(), self.secondary_ip.text().strip(), self.strip_ip.text().strip()]
        dialog = DeviceDiscoveryDialog(self, existing)
        dialog.start_scan()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            assignments = dialog.selected_assignments
            if "primary" in assignments: self.primary_ip.setText(assignments["primary"])
            if "secondary" in assignments: self.secondary_ip.setText(assignments["secondary"])
            if "strip" in assignments: self.strip_ip.setText(assignments["strip"])
            self.save_settings()
            self.append_log("Added discovered device(s): " + ", ".join(f"{role}={ip}" for role, ip in assignments.items()), "Info")

    def save_settings(self):
        device = self.config.setdefault("device", {})
        device["bulb_ip"] = self.primary_ip.text().strip()
        device["secondary_bulb_ip"] = self.secondary_ip.text().strip()
        device["strip_ip"] = self.strip_ip.text().strip()
        device["monitor"] = int(self.monitor.currentData() or 1)
        app_cfg = self.config.setdefault("app", {})
        app_cfg["launch_at_login"] = self.autostart.isChecked()
        app_cfg["minimise_to_tray"] = self.minimise_to_tray.isChecked()
        app_cfg["theme"] = self.theme_mode
        temp = self.config_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.config, indent=4) + "\n", encoding="utf-8")
        temp.replace(self.config_file)
        try:
            set_autostart(self.autostart.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, "Startup setting", str(exc))
        self.append_log("Application settings saved.", "Info")
        self.rebuild_device_cards()
        self.rebuild_overview_device_cards()
        QTimer.singleShot(300, self.refresh_device_states)

    def update_autostart(self, enabled):
        self.config.setdefault("app", {})["launch_at_login"] = enabled
        try:
            set_autostart(enabled)
        except Exception as exc:
            QMessageBox.warning(self, "Startup setting", str(exc))

    def engine_command(self):
        base = Path(sys.executable).resolve().parent
        candidates = [base / ("wiz-engine.exe" if sys.platform.startswith("win") else "wiz-engine"), base / "engine" / ("wiz-engine.exe" if sys.platform.startswith("win") else "wiz-engine")]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate), []
        source = Path(__file__).resolve().parent.parent / "engine" / "WiZ_Ambient_Sync.py"
        if source.exists():
            return sys.executable, [str(source)]
        raise FileNotFoundError("wiz-engine sidecar was not found beside the desktop app.")

    def start_engine(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.save_settings()
        self.apply_tuning()
        try:
            program, args = self.engine_command()
            env = QProcessEnvironment.systemEnvironment()
            env.insert("WIZ_AMBIENT_CONFIG", str(self.config_file))
            env.insert("WIZ_AMBIENT_TUNING", str(self.tuning_file))
            env.insert("WIZ_AMBIENT_TELEMETRY", "1")
            env.insert("WIZ_AMBIENT_DEVICE_CONTROL", str(self.device_control_file))
            self.process.setProcessEnvironment(env)
            self.process.setProgram(program)
            self.process.setArguments(args)
            self.process.setWorkingDirectory(str(self.config_file.parent))
            self.process.start()
            if not self.process.waitForStarted(5000):
                raise RuntimeError(self.process.errorString())
            self.set_running(True)
            self.append_log("WiZ engine process started.", "Info")
            self.footer_status.setText("Starting audio analysis and device senders…")
        except Exception as exc:
            QMessageBox.critical(self, "Could not start sync", str(exc))
            self.set_running(False)

    def set_status_pill(self, text, status):
        self.header_status.setText(text)
        self.header_status.setProperty("status", status)
        self.header_status.style().unpolish(self.header_status)
        self.header_status.style().polish(self.header_status)

    def set_running(self, running):
        # Device reads are isolated in QThreads and deliberately throttled.
        # Keep low-frequency polling active so the UI reflects real bulb
        # brightness without blocking the audio/sender process.
        if not self.device_poll_timer.isActive():
            self.device_poll_timer.start()
        if not running:
            QTimer.singleShot(500, self.refresh_device_states)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.start_button.blockSignals(True)
        self.stop_button.blockSignals(True)
        self.start_button.setChecked(bool(running))
        self.stop_button.setChecked(not bool(running))
        self.start_button.blockSignals(False)
        self.stop_button.blockSignals(False)
        self.tray_start.setEnabled(not running)
        self.tray_stop.setEnabled(running)
        for controls in getattr(self, "device_widgets", {}).values():
            controls["slider"].setEnabled((not running) and controls["power"].isChecked())
        for controls in getattr(self, "overview_device_widgets", {}).values():
            controls["slider"].setEnabled((not running) and controls["status"].text() == "Online" and bool(controls.get("last_known_on")))
        if running:
            self.set_status_pill("●  STARTING", "starting")
            self.sidebar_status_title.setText("SYNC STARTING")
        else:
            self.set_status_pill("●  STOPPED", "stopped")
            self.sidebar_status_title.setText("SYNC STOPPED")

    def stop_engine(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.process.terminate()
        QTimer.singleShot(2500, self.kill_engine_if_running)
        self.append_log("Stop requested.", "Info")

    def kill_engine_if_running(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def read_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.consume_text(data, is_error=False)

    def read_error(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self.consume_text(data, is_error=True)

    def consume_text(self, data, is_error=False):
        attr = "stderr_buffer" if is_error else "stdout_buffer"
        buffer = getattr(self, attr) + data
        buffer = buffer.replace("\r", "\n")
        lines = buffer.split("\n")
        setattr(self, attr, lines.pop())
        for line in lines:
            self.process_line(line.strip(), is_error)

    def process_line(self, line, is_error=False):
        if not line:
            return
        if line.startswith("WIZ_METRIC:"):
            try:
                payload = json.loads(line[len("WIZ_METRIC:"):])
                if not isinstance(payload, dict):
                    raise ValueError("Telemetry payload must be an object")
                self.metrics.update(payload)
                self.metrics_update_pending = True
            except Exception as exc:
                # A presentation/telemetry issue must never terminate the Qt event loop.
                self.append_log(f"Telemetry display update failed: {type(exc).__name__}: {exc}", "Warning")
            return
        if line.startswith("Audio:"):
            self.metrics["audio_device"] = line.split(":", 1)[1].strip()
            self.sidebar_audio.setText(self.metrics["audio_device"])
            self.overview_audio.set_value(self.metrics["audio_device"])
            self.audio_device_label.setText(self.metrics["audio_device"])
        if "SYNC ACTIVE" in line:
            self.set_status_pill("●  SYNC ACTIVE", "active")
            self.sidebar_status_title.setText("SYNC ACTIVE")
            self.footer_status.setText("Audio analysis active · lighting updates running")
        level = "Error" if is_error or any(x in line.lower() for x in ("error", "failed", "traceback")) else "Warning" if any(x in line.lower() for x in ("warning", "unavailable", "skipping")) else "Info"
        self.append_log(line, level)

    def flush_metric_update(self):
        if not getattr(self, "metrics_update_pending", False):
            return
        self.metrics_update_pending = False
        self.update_metric_views()

    def update_metric_views(self):
        m = self.metrics
        # Device count and brightness come from direct getPilot responses; engine
        # telemetry can be stale or zero before its first sender cycle.
        self.update_live_device_count()
        # The overview brightness card is device-reported state, not the
        # engine's requested/target brightness from setPilot telemetry.
        self.update_reported_brightness_metric()
        self.overview_energy.set_value(round(m.get("energy", 0) * 100), "%")
        self.overview_pulse.set_value(f"{m.get('pulse', 0):.1f}", "impact")
        self.overview_flow.set_value(f"{m.get('momentum', 0):.2f}")
        self.visual_energy.set_value(f"{round(m.get('energy', 0) * 100)}%")
        self.visual_phrase.set_value(f"{round(m.get('phrase', 0) * 100)}%")
        self.visual_flow.set_value(f"{m.get('flow', 0):.2f}")
        self.visual_motion.set_value(f"{m.get('motion_depth', 0):.2f}")
        self.visual_pulse.set_value(f"{m.get('pulse', 0):.1f}")
        for key, meter in self.band_meters.items():
            meter.set_value(m.get(key, 0))
        self.impact_label.setText(f"Impact: {m.get('impact', '----')}     ·     Brightness: {m.get('brightness', 0)}% → {m.get('target_brightness', 0)}% target")
        self.overview_spectrum.set_metrics(m)
        self.spectrum.set_metrics(m)

    def append_log(self, message, level="Info"):
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_entries.append((stamp, level, str(message)))
        self.log_entries = self.log_entries[-1000:]
        if self.current_page == 5:
            self.refresh_logs()

    def refresh_logs(self):
        if not hasattr(self, "log_view"):
            return
        selected = self.log_filter.currentText()
        mapping = {"Warnings": "Warning", "Errors": "Error"}
        target = mapping.get(selected, selected)
        rows = [f"[{t}] [{lvl.upper():7}] {msg}" for t, lvl, msg in self.log_entries if target == "All messages" or lvl == target]
        self.log_view.setPlainText("\n".join(rows[-1000:]))
        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)

    def copy_logs(self):
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.footer_status.setText("Visible logs copied to clipboard")

    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export WiZ Ambient Sync logs", "WiZ-Ambient-Sync-logs.txt", "Text files (*.txt)")
        if path:
            Path(path).write_text(self.log_view.toPlainText() + "\n", encoding="utf-8")
            self.footer_status.setText("Logs exported")

    def clear_logs(self):
        self.log_entries.clear()
        self.refresh_logs()

    def engine_finished(self, code, status):
        self.set_running(False)
        self.append_log(f"WiZ engine exited with code {code}.", "Warning" if code else "Info")
        self.footer_status.setText(f"Engine stopped · exit code {code}")

    def create_tray(self):
        icon_file = resource_path("app_icon.png")
        tray_icon = QIcon(str(icon_file)) if icon_file.exists() else self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(tray_icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.tray_menu = menu
        show_action = QAction("Open WiZ Ambient Sync", self)
        show_action.triggered.connect(self.show_window)
        self.tray_start = QAction("Start syncing", self)
        self.tray_start.triggered.connect(self.start_engine)
        self.tray_stop = QAction("Stop syncing", self)
        self.tray_stop.triggered.connect(self.stop_engine)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(self.tray_start)
        menu.addAction(self.tray_stop)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.style_tray_menu()
        self.tray_stop.setEnabled(False)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def style_tray_menu(self):
        """Use explicit readable colours; native Windows menu defaults can clash with app theme."""
        if not hasattr(self, "tray_menu"):
            return
        if self.resolved_theme() == "Light":
            bg, fg, hover, border = "#FFFFFF", "#29243A", "#F0EAF9", "#E8E1F2"
        else:
            bg, fg, hover, border = "#292633", "#F4F1FA", "#40394E", "#51495F"
        self.tray_menu.setStyleSheet(
            f"QMenu {{ background:{bg}; color:{fg}; border:1px solid {border}; padding:6px; }}"
            f"QMenu::item {{ color:{fg}; padding:7px 28px 7px 22px; border-radius:5px; }}"
            f"QMenu::item:selected {{ background:{hover}; color:{fg}; }}"
            f"QMenu::item:disabled {{ color:#9993A5; }}"
            f"QMenu::separator {{ height:1px; background:{border}; margin:5px 8px; }}"
        )

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.minimise_to_tray.isChecked() and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "WiZ Ambient Sync continues running in the system tray.")
        else:
            event.accept()
            self.stop_engine()
            for worker in list(self.device_workers):
                if worker.isRunning():
                    worker.wait(1200)
            self.tray.hide()
            QApplication.quit()

    def quit_app(self):
        self.stop_engine()
        self.tray.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_file = resource_path("app_icon.png")
    if icon_file.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_file)))
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    # Do not hide simply because "launch at login" is enabled.
    # The registry startup entry passes --background explicitly.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
