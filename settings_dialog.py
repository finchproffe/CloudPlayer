import re

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRectF, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
)

from config import (
    ACCENT_COLOR,
    BG_COLOR,
    BUTTON_BG,
    BUTTON_BORDER,
    BUTTON_HOVER,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_DEBUG,
    PANEL_BG,
    TEXT_COLOR,
    TEXT_MUTED,
    normalize_accent_color,
)
from dropdown_ui import QColorDialog, QDialog
from utils import colored_svg_renderer


class AnimatedCheckBox(QCheckBox):
    def __init__(self, text="", checked=False, parent=None):
        super().__init__(text, parent)
        super().setChecked(bool(checked))
        self._check_progress = 1.0 if checked else 0.0
        self._check_renderer = colored_svg_renderer("check.svg", "#FFFFFF")
        self.animation = QPropertyAnimation(self, b"checkProgress", self)
        self.animation.setDuration(380)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.toggled.connect(self._animate_check)

    def _get_check_progress(self):
        return self._check_progress

    def _set_check_progress(self, value):
        self._check_progress = max(0.0, min(1.0, float(value)))
        self.update()

    checkProgress = Property(
        float,
        _get_check_progress,
        _set_check_progress,
    )

    def _animate_check(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self._check_progress)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._check_progress <= 0.001:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator,
            option,
            self,
        )
        target = QRectF(indicator).adjusted(0.5, 0.5, -0.5, -0.5)
        visible_width = target.width() * self._check_progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(min(1.0, self._check_progress * 1.4))
        painter.setClipRect(
            QRectF(
                target.left(),
                target.top(),
                visible_width,
                target.height(),
            )
        )
        self._check_renderer.render(painter, target)
        painter.end()


class SettingsDialog(QDialog):
    delete_account_requested = Signal()

    def __init__(
        self,
        accent_color=ACCENT_COLOR,
        debug_enabled=DEFAULT_DEBUG,
        parent=None,
        account_username=None,
    ):
        super().__init__(parent)
        self.account_username = str(account_username or "").strip()
        self.selected_color = (
            normalize_accent_color(accent_color) or DEFAULT_ACCENT_COLOR
        )
        self.debug_enabled = bool(debug_enabled)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(13)

        title = QLabel("Appearance")
        title.setStyleSheet(
            "font-size:22px;font-weight:700;background:transparent"
        )
        description = QLabel(
            "Choose the primary accent color used across CloudPlayer."
        )
        description.setWordWrap(True)
        description.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;background:transparent"
        )

        self.picker_button = QPushButton("Open Color Picker")
        self.picker_button.setMinimumHeight(76)
        self.picker_button.clicked.connect(self._open_picker)

        hex_label = QLabel("HEX color (optional)")
        hex_label.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;background:transparent"
        )
        self.hex_input = QLineEdit(self.selected_color)
        self.hex_input.setPlaceholderText("#0D47A1")
        self.hex_input.setMaxLength(7)
        self.hex_input.editingFinished.connect(self._apply_hex_input)

        self.status = QLabel("")
        self.status.setStyleSheet(
            "color:#EF9A9A;font-size:11px;background:transparent"
        )

        developer_title = QLabel("Developer")
        developer_title.setStyleSheet(
            "font-size:18px;font-weight:700;background:transparent"
        )
        developer_description = QLabel(
            "Show a live console with stdout, stderr, warnings, Python/Qt "
            "logging and uncaught exceptions."
        )
        developer_description.setWordWrap(True)
        developer_description.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;background:transparent"
        )
        self.debug_checkbox = AnimatedCheckBox(
            "Enable Debug Console",
            self.debug_enabled,
        )
        self.debug_checkbox.toggled.connect(self._set_debug_enabled)

        account_title = QLabel("Account")
        account_title.setStyleSheet(
            "font-size:18px;font-weight:700;background:transparent"
        )
        account_description = QLabel(
            "Permanently delete your account and synchronized tracks."
            if self.account_username
            else "Sign in to manage or delete your account."
        )
        account_description.setWordWrap(True)
        account_description.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;background:transparent"
        )
        self.delete_account_button = QPushButton("Delete Account")
        self.delete_account_button.setObjectName("deleteAccountButton")
        self.delete_account_button.setEnabled(bool(self.account_username))
        self.delete_account_button.clicked.connect(
            lambda _checked=False: self.delete_account_requested.emit()
        )

        actions = QHBoxLayout()
        reset = QPushButton("Reset to Default")
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        reset.clicked.connect(self._reset)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        actions.addWidget(reset)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(save)

        root.addWidget(title)
        root.addWidget(description)
        root.addSpacing(4)
        root.addWidget(self.picker_button)
        root.addWidget(hex_label)
        root.addWidget(self.hex_input)
        root.addWidget(self.status)
        root.addSpacing(10)
        root.addWidget(developer_title)
        root.addWidget(developer_description)
        root.addWidget(self.debug_checkbox)
        root.addSpacing(10)
        root.addWidget(account_title)
        root.addWidget(account_description)
        root.addWidget(self.delete_account_button)
        root.addSpacing(4)
        root.addLayout(actions)

        self.setStyleSheet(
            f"QDialog{{background:{BG_COLOR};color:{TEXT_COLOR}}}"
            f"QLabel{{color:{TEXT_COLOR}}}"
            f"QCheckBox{{color:{TEXT_COLOR};spacing:10px;padding:6px 0}}"
            f"QCheckBox::indicator{{width:20px;height:20px;image:none;"
            f"background:{PANEL_BG};"
            f"border:1px solid {BUTTON_BORDER};border-radius:5px}}"
            f"QCheckBox::indicator:hover{{border-color:{self.selected_color}}}"
            f"QCheckBox::indicator:checked{{background:{self.selected_color};"
            f"border-color:{self.selected_color}}}"
            f"QLineEdit{{background:{PANEL_BG};color:{TEXT_COLOR};border:1px "
            f"solid {BUTTON_BORDER};border-radius:5px;padding:11px}}"
            f"QPushButton{{background:{BUTTON_BG};color:{TEXT_COLOR};border:1px "
            f"solid {BUTTON_BORDER};border-radius:5px;padding:10px 15px;"
            "font-size:13px;font-weight:700}"
            f"QPushButton:hover{{background:{BUTTON_HOVER};border-color:"
            f"{self.selected_color}}}"
            "QPushButton#deleteAccountButton{background:#2A1717;color:#FFB4AB;"
            "border-color:#7A3030}"
            "QPushButton#deleteAccountButton:hover{background:#3A1C1C;"
            "border-color:#EF5350}"
            "QPushButton#deleteAccountButton:disabled{background:#1E1E1E;"
            "color:#666666;border-color:#333333}"
        )
        self._update_preview()

    @staticmethod
    def _text_color(color):
        value = QColor(color)
        luminance = (
            0.299 * value.red()
            + 0.587 * value.green()
            + 0.114 * value.blue()
        )
        return "#111111" if luminance > 160 else "#FFFFFF"

    def _update_preview(self):
        foreground = self._text_color(self.selected_color)
        self.picker_button.setText(
            f"Open Color Picker  |  {self.selected_color}"
        )
        self.picker_button.setStyleSheet(
            f"QPushButton{{background:{self.selected_color};color:{foreground};"
            f"border:1px solid {self.selected_color};border-radius:7px;"
            "font-size:14px;font-weight:700}"
            "QPushButton:hover{border:2px solid #FFFFFF}"
        )
        self.hex_input.setText(self.selected_color)
        self.status.clear()

    def _open_picker(self):
        picker = QColorDialog(QColor(self.selected_color), self)
        picker.setWindowTitle("Choose Accent Color")
        picker.setOption(QColorDialog.DontUseNativeDialog, True)
        picker.setStyleSheet(self.styleSheet())
        if picker.exec() == QDialog.Accepted:
            self.selected_color = picker.selectedColor().name().upper()
            self._update_preview()

    def _apply_hex_input(self):
        value = self.hex_input.text().strip()
        if not value:
            self.hex_input.setText(self.selected_color)
            return True
        color = normalize_accent_color(value)
        if not color or not re.fullmatch(r"#[0-9A-F]{6}", color):
            self.status.setText("Enter a valid HEX color, for example #0D47A1.")
            return False
        self.selected_color = color
        self._update_preview()
        return True

    def _set_debug_enabled(self, enabled):
        self.debug_enabled = bool(enabled)

    def _reset(self):
        self.selected_color = DEFAULT_ACCENT_COLOR
        self.debug_enabled = DEFAULT_DEBUG
        self.debug_checkbox.setChecked(self.debug_enabled)
        self._update_preview()

    def _save(self):
        if self._apply_hex_input():
            self.accept()
