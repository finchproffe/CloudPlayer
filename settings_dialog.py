import re

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRectF, Signal, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QColorDialog as QtColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
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
from dropdown_ui import QDialog
from hotkeys import BINDS_PATH
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


class ColorPickerDialog(QDialog):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Accent Color")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 18)
        root.setSpacing(14)
        self.picker = QtColorDialog(QColor(color), self)
        self.picker.setWindowFlags(Qt.Widget)
        self.picker.setOption(
            QtColorDialog.ColorDialogOption.DontUseNativeDialog, True
        )
        self.picker.setOption(
            QtColorDialog.ColorDialogOption.NoButtons, True
        )
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(apply_button)
        root.addWidget(self.picker, 1)
        root.addLayout(actions)
        self.setStyleSheet(
            f"QDialog{{background:{BG_COLOR};color:{TEXT_COLOR}}}"
            f"QPushButton{{background:{BUTTON_BG};color:{TEXT_COLOR};"
            f"border:1px solid {BUTTON_BORDER};border-radius:5px;"
            "padding:9px 15px;font-size:13px;font-weight:700}"
            f"QPushButton:hover{{background:{BUTTON_HOVER};"
            f"border-color:{ACCENT_COLOR}}}"
        )

    def selected_color(self):
        return self.picker.currentColor()


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
        self.reset_keyboard_bindings = False
        self.setWindowTitle("Settings")
        self.setMinimumSize(460, 500)
        self.resize(520, 680)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(12)

        self.settings_scroll = QScrollArea(self)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.settings_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.settings_scroll.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        self.settings_scroll.viewport().setAutoFillBackground(False)

        content = QWidget(self.settings_scroll)
        content.setObjectName("settingsContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(0, 0, 8, 0)
        content_root.setSpacing(13)
        self.settings_scroll.setWidget(content)

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

        keyboard_title = QLabel("Keyboard")
        keyboard_title.setStyleSheet(
            "font-size:18px;font-weight:700;background:transparent"
        )
        keyboard_description = QLabel(
            "Edit keyboard shortcuts in binds.cfg or restore the defaults."
        )
        keyboard_description.setWordWrap(True)
        keyboard_description.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;background:transparent"
        )

        self.keyboard_path = QLineEdit(str(BINDS_PATH))
        self.keyboard_path.setReadOnly(True)
        self.keyboard_path.setToolTip(str(BINDS_PATH))
        self.keyboard_path.setCursorPosition(0)
        self.keyboard_path.setMinimumHeight(40)
        self.keyboard_path.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )

        self.reset_keyboard_button = QPushButton("Reset Bindings")
        self.reset_keyboard_button.setMinimumHeight(40)
        self.reset_keyboard_button.setMinimumWidth(150)
        self.reset_keyboard_button.setSizePolicy(
            QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )
        self.reset_keyboard_button.clicked.connect(
            self._request_keyboard_reset
        )

        keyboard_row = QHBoxLayout()
        keyboard_row.setContentsMargins(0, 0, 0, 0)
        keyboard_row.setSpacing(10)
        keyboard_row.addWidget(self.keyboard_path, 1)
        keyboard_row.addWidget(self.reset_keyboard_button)

        self.keyboard_status = QLabel("")
        self.keyboard_status.setWordWrap(True)
        self.keyboard_status.setVisible(False)
        self.keyboard_status.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:11px;background:transparent"
        )

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

        content_root.addWidget(title)
        content_root.addWidget(description)
        content_root.addSpacing(4)
        content_root.addWidget(self.picker_button)
        content_root.addWidget(hex_label)
        content_root.addWidget(self.hex_input)
        content_root.addWidget(self.status)
        content_root.addSpacing(10)
        content_root.addWidget(developer_title)
        content_root.addWidget(developer_description)
        content_root.addWidget(self.debug_checkbox)
        content_root.addSpacing(10)
        content_root.addWidget(keyboard_title)
        content_root.addWidget(keyboard_description)
        content_root.addLayout(keyboard_row)
        content_root.addWidget(self.keyboard_status)
        content_root.addSpacing(10)
        content_root.addWidget(account_title)
        content_root.addWidget(account_description)
        content_root.addWidget(self.delete_account_button)
        content_root.addStretch(1)

        root.addWidget(self.settings_scroll, 1)
        root.addLayout(actions)

        self.setStyleSheet(
            f"QDialog{{background:{BG_COLOR};color:{TEXT_COLOR}}}"
            "QWidget#settingsContent{background:transparent}"
            "QScrollArea{background:transparent;border:0}"
            "QScrollArea>QWidget>QWidget{background:transparent}"
            f"QLabel{{color:{TEXT_COLOR}}}"
            f"QCheckBox{{color:{TEXT_COLOR};spacing:10px;padding:6px 0}}"
            f"QCheckBox::indicator{{width:20px;height:20px;"
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
        picker = ColorPickerDialog(self.selected_color, self)
        if picker.exec() == QDialog.Accepted:
            self.selected_color = picker.selected_color().name().upper()
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

    def _request_keyboard_reset(self):
        self.reset_keyboard_bindings = True
        self.reset_keyboard_button.setEnabled(False)
        self.reset_keyboard_button.setText("Defaults Selected")
        self.keyboard_status.setText(
            "Defaults will be restored when you save."
        )
        self.keyboard_status.setVisible(True)

    def _reset(self):
        self.selected_color = DEFAULT_ACCENT_COLOR
        self.debug_enabled = DEFAULT_DEBUG
        self.debug_checkbox.setChecked(self.debug_enabled)
        self._update_preview()

    def _save(self):
        if self._apply_hex_input():
            self.accept()
