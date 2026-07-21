from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QEventLoop, QRect, QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog as QtColorDialog,
    QComboBox,
    QDialog as QtDialog,
    QFileDialog as QtFileDialog,
    QGraphicsOpacityEffect,
    QInputDialog as QtInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox as QtMessageBox,
    QProgressDialog as QtProgressDialog,
    QToolButton,
    QWidget,
)

from config import ACCENT_COLOR, BG_COLOR, BUTTON_BORDER, PANEL_BG, TEXT_COLOR
from smooth_scroll import enable_smooth_scrolling
from utils import colored_icon


def _main_host(widget):
    candidate = widget
    if candidate is None:
        app = QApplication.instance()
        candidate = app.activeWindow() if app else None
    if candidate is None:
        return None
    window = candidate.window()
    if isinstance(window, QMainWindow) and window.centralWidget() is not None:
        return window.centralWidget()
    return window if isinstance(window, QWidget) else candidate


class _DropdownBackdrop(QWidget):
    def __init__(self, panel, parent):
        super().__init__(parent)
        self.panel = panel
        self.setObjectName("dropdownBackdrop")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#dropdownBackdrop{background:rgba(0,0,0,118)}")
        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity)

    def mousePressEvent(self, event):
        self.panel._dismiss_dropdown()
        event.accept()


class _DropdownHeader(QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self.panel._begin_dropdown_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.panel._move_dropdown_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
            self.panel._end_dropdown_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _DropdownMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dropdown_host = None
        self._dropdown_target = QRect()
        self._dropdown_header = None
        self._dropdown_title = None
        self._dropdown_close = None
        self._dropdown_backdrop = None
        self._dropdown_drag_offset = None
        self._dropdown_frame_timer = QTimer(self)
        self._dropdown_frame_timer.setTimerType(Qt.PreciseTimer)
        self._dropdown_frame_timer.setInterval(0)
        self._dropdown_frame_timer.timeout.connect(self._animate_dropdown_frame)
        self._dropdown_elapsed = QElapsedTimer()
        self._dropdown_opacity = QGraphicsOpacityEffect(self)
        self._dropdown_opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._dropdown_opacity)
        self.setWindowFlags(Qt.Widget | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def _prepare_dropdown(self):
        host = _main_host(self.parentWidget())
        if host is None:
            return False
        if self._dropdown_host is not host:
            if self._dropdown_host is not None:
                self._dropdown_host.removeEventFilter(self)
            self.setParent(host, Qt.Widget | Qt.FramelessWindowHint)
            self._dropdown_host = host
            host.installEventFilter(self)
            self._dropdown_backdrop = _DropdownBackdrop(self, host)
        if self._dropdown_header is None:
            header = _DropdownHeader(self)
            header.setObjectName("cloudDropdownHeader")
            header.setAttribute(Qt.WA_StyledBackground, True)
            header.setFixedHeight(56)
            header.setStyleSheet(
                f"QWidget#cloudDropdownHeader {{"
                f"background-color: {PANEL_BG};"
                f"border: 0px;"
                f"border-bottom: 1px solid {BUTTON_BORDER};"
                f"border-top-left-radius: 10px;"
                f"border-top-right-radius: 10px;"
                f"}}"
            )
            title = QLabel(header)
            title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            title.setStyleSheet(
                f"color:{TEXT_COLOR};font-size:14px;font-weight:700;"
                "background:transparent"
            )
            self._dropdown_header = header
            self._dropdown_title = title
        if self._dropdown_close is None:
            close_button = QToolButton(self._dropdown_header)
            close_button.setIcon(colored_icon("exit.svg", TEXT_COLOR, 22))
            close_button.setIconSize(QSize(22, 22))
            close_button.setToolTip("Close")
            close_button.setAccessibleName("Close dropdown")
            close_button.setCursor(Qt.PointingHandCursor)
            close_button.setFixedSize(34, 34)
            close_button.setStyleSheet(
                f"QToolButton{{background:rgba(255,255,255,10);border:1px solid "
                f"{BUTTON_BORDER};border-radius:17px;padding:5px}}"
                f"QToolButton:hover{{background:{ACCENT_COLOR};border-color:{ACCENT_COLOR}}}"
                "QToolButton:pressed{background:#9F2635}"
            )
            close_button.clicked.connect(self._dismiss_dropdown)
            self._dropdown_close = close_button
        self._dropdown_title.setText(self.windowTitle())
        self._dropdown_title.adjustSize()
        self._dropdown_close.setVisible(
            not isinstance(self, QtProgressDialog)
            or getattr(self, "_dropdown_cancel_allowed", True)
        )
        self._reserve_header_space()
        self.setObjectName("cloudDropdownPanel")
        current_style = self.styleSheet()
        marker = "QDialog#cloudDropdownPanel"
        if marker not in current_style:
            self.setStyleSheet(
                current_style
                + f"QDialog#cloudDropdownPanel {{"
                f"background-color: {BG_COLOR};"
                f"border: 1px solid {BUTTON_BORDER};"
                f"border-radius: 10px;"
                f"}}"
            )
        self._dropdown_backdrop.setGeometry(host.rect())
        self._layout_dropdown(False)
        return True

    def _begin_dropdown_drag(self, global_position):
        self._dropdown_drag_offset = (
            global_position - self.mapToGlobal(self.rect().topLeft())
        )

    def _move_dropdown_drag(self, global_position):
        host = self._dropdown_host
        if host is None or self._dropdown_drag_offset is None:
            return
        host_origin = host.mapToGlobal(host.rect().topLeft())
        position = global_position - self._dropdown_drag_offset - host_origin
        margin = 8
        maximum_x = max(margin, host.width() - self.width() - margin)
        maximum_y = max(margin, host.height() - self.height() - margin)
        position.setX(max(margin, min(position.x(), maximum_x)))
        position.setY(max(margin, min(position.y(), maximum_y)))
        self.move(position)
        self._dropdown_target = self.geometry()

    def _end_dropdown_drag(self):
        self._dropdown_drag_offset = None

    def _reserve_header_space(self):
        layout = self.layout()
        if layout is None:
            return
        left, top, right, bottom = layout.getContentsMargins()
        layout.setContentsMargins(left, max(top, 68), right, bottom)

    def _preferred_size(self):
        if self.layout() is not None:
            self.layout().activate()
        hint = self.sizeHint().expandedTo(self.minimumSize())
        width = max(hint.width(), 340)
        height = max(hint.height(), 150)
        if self.testAttribute(Qt.WA_Resized):
            width = max(width, self.width())
            height = max(height, self.height())
        return width, height

    def _layout_dropdown(self, animate):
        host = self._dropdown_host
        if host is None:
            return
        margin = 16
        available_width = max(1, host.width() - margin * 2)
        available_height = max(1, host.height() - margin * 2)
        preferred_width, preferred_height = self._preferred_size()
        width = min(preferred_width, available_width)
        height = min(preferred_height, available_height)
        x = max(margin, (host.width() - width) // 2)
        y = max(margin, (host.height() - height) // 2)
        target = QRect(x, y, width, height)
        self._dropdown_target = target
        if self._dropdown_header is not None:
            self._dropdown_header.setGeometry(0, 0, width, 56)
            self._dropdown_title.setGeometry(18, 0, max(0, width - 78), 56)
            self._dropdown_header.raise_()
        if self._dropdown_close is not None:
            self._dropdown_close.move(max(0, width - 44), 10)
            self._dropdown_close.raise_()
        if not animate:
            self.setGeometry(target)
            return
        self.setGeometry(target)
        self._dropdown_opacity.setOpacity(0.0)
        if self._dropdown_backdrop is not None:
            self._dropdown_backdrop.opacity.setOpacity(0.0)
        self._dropdown_elapsed.start()
        self._dropdown_frame_timer.start()

    def _animate_dropdown_frame(self):
        duration = 420.0
        progress = min(1.0, self._dropdown_elapsed.elapsed() / duration)
        eased = progress * progress * progress * (
            progress * (progress * 6.0 - 15.0) + 10.0
        )
        self._dropdown_opacity.setOpacity(eased)
        if self._dropdown_backdrop is not None:
            self._dropdown_backdrop.opacity.setOpacity(eased)
        self.update()
        if progress >= 1.0:
            self._dropdown_frame_timer.stop()
            self._dropdown_opacity.setOpacity(1.0)
            if self._dropdown_backdrop is not None:
                self._dropdown_backdrop.opacity.setOpacity(1.0)

    def _dismiss_dropdown(self):
        if isinstance(self, QtProgressDialog):
            if getattr(self, "_dropdown_cancel_allowed", True):
                self.cancel()
        else:
            self.reject()

    def show(self):
        prepared = self._prepare_dropdown()
        if prepared:
            self._dropdown_opacity.setOpacity(0.0)
            self._dropdown_backdrop.opacity.setOpacity(0.0)
            self._dropdown_backdrop.show()
            self._dropdown_backdrop.raise_()
        super().show()
        if prepared:
            self._reserve_header_space()
            self.raise_()
            self._dropdown_header.show()
            self._dropdown_header.raise_()
            self._dropdown_close.raise_()
            self._layout_dropdown(True)

    def open(self):
        self.show()

    def exec(self):
        self.setResult(QtDialog.Rejected)
        loop = QEventLoop(self)
        self.finished.connect(loop.quit)
        self.show()
        loop.exec()
        try:
            self.finished.disconnect(loop.quit)
        except RuntimeError:
            pass
        return self.result()

    def eventFilter(self, watched, event):
        if watched is self._dropdown_host and event.type() == QEvent.Resize:
            self._dropdown_frame_timer.stop()
            self._dropdown_backdrop.setGeometry(self._dropdown_host.rect())
            QTimer.singleShot(0, lambda: self._layout_dropdown(False))
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._dropdown_header is not None:
            self._dropdown_header.setGeometry(0, 0, self.width(), 56)
            self._dropdown_title.setGeometry(
                18,
                0,
                max(0, self.width() - 78),
                56,
            )
            self._dropdown_header.raise_()
        if self._dropdown_close is not None:
            self._dropdown_close.move(max(0, self.width() - 44), 10)
            self._dropdown_close.raise_()

    def hideEvent(self, event):
        self._dropdown_frame_timer.stop()
        self._dropdown_drag_offset = None
        if self._dropdown_header is not None:
            self._dropdown_header.setCursor(Qt.OpenHandCursor)
        if self._dropdown_backdrop is not None:
            self._dropdown_backdrop.hide()
        super().hideEvent(event)


class DropdownDialog(_DropdownMixin, QtDialog):
    pass


class DropdownMessageBox(_DropdownMixin, QtMessageBox):
    @classmethod
    def _display(cls, icon, parent, title, text, buttons, default_button):
        box = cls(icon, title, text, buttons, parent)
        if default_button != cls.NoButton:
            box.setDefaultButton(default_button)
        return cls.StandardButton(box.exec())

    @classmethod
    def information(cls, parent, title, text, buttons=None, defaultButton=None):
        buttons = cls.Ok if buttons is None else buttons
        defaultButton = cls.NoButton if defaultButton is None else defaultButton
        return cls._display(cls.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(cls, parent, title, text, buttons=None, defaultButton=None):
        buttons = cls.Ok if buttons is None else buttons
        defaultButton = cls.NoButton if defaultButton is None else defaultButton
        return cls._display(cls.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(cls, parent, title, text, buttons=None, defaultButton=None):
        buttons = cls.Ok if buttons is None else buttons
        defaultButton = cls.NoButton if defaultButton is None else defaultButton
        return cls._display(cls.Critical, parent, title, text, buttons, defaultButton)

    @classmethod
    def question(cls, parent, title, text, buttons=None, defaultButton=None):
        buttons = cls.Yes | cls.No if buttons is None else buttons
        defaultButton = cls.NoButton if defaultButton is None else defaultButton
        return cls._display(cls.Question, parent, title, text, buttons, defaultButton)


class DropdownInputDialog(_DropdownMixin, QtInputDialog):
    @classmethod
    def getText(
        cls,
        parent,
        title,
        label,
        echo=QLineEdit.Normal,
        text="",
        flags=None,
        inputMethodHints=Qt.ImhNone,
    ):
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextEchoMode(echo)
        dialog.setTextValue(text)
        dialog.setInputMethodHints(inputMethodHints)
        accepted = dialog.exec() == cls.Accepted
        return dialog.textValue(), accepted

    @classmethod
    def getItem(
        cls,
        parent,
        title,
        label,
        items,
        current=0,
        editable=True,
        flags=None,
        inputMethodHints=Qt.ImhNone,
    ):
        dialog = cls(parent)
        values = list(items)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setComboBoxItems(values)
        dialog.setComboBoxEditable(editable)
        dialog.setInputMethodHints(inputMethodHints)
        if values:
            dialog.setTextValue(str(values[max(0, min(current, len(values) - 1))]))
        combo = dialog.findChild(QComboBox)
        if combo is not None:
            enable_smooth_scrolling(
                combo.view(),
                duration=360,
                wheel_step=80,
                acceleration=0.14,
                max_boost=2.0,
            )
        accepted = dialog.exec() == cls.Accepted
        return dialog.textValue(), accepted

    @classmethod
    def getInt(
        cls,
        parent,
        title,
        label,
        value=0,
        minValue=-2147483647,
        maxValue=2147483647,
        step=1,
        flags=None,
    ):
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setInputMode(QtInputDialog.InputMode.IntInput)
        dialog.setIntRange(minValue, maxValue)
        dialog.setIntStep(step)
        dialog.setIntValue(value)
        accepted = dialog.exec() == cls.Accepted
        return dialog.intValue(), accepted


class DropdownFileDialog(_DropdownMixin, QtFileDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setOption(
            QtFileDialog.Option.DontUseNativeDialog, True
        )

    @classmethod
    def _create(cls, parent, caption, directory, filter_text, options):
        dialog = cls(parent, caption, directory, filter_text)
        if options is not None:
            dialog.setOptions(options)
            dialog.setOption(
                QtFileDialog.Option.DontUseNativeDialog, True
            )
        return dialog

    @classmethod
    def getOpenFileName(
        cls, parent=None, caption="", directory="", filter="", selectedFilter="", options=None
    ):
        dialog = cls._create(parent, caption, directory, filter, options)
        dialog.setFileMode(QtFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QtFileDialog.AcceptMode.AcceptOpen)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        accepted = dialog.exec() == cls.Accepted
        files = dialog.selectedFiles()
        return (files[0] if accepted and files else ""), dialog.selectedNameFilter()

    @classmethod
    def getOpenFileNames(
        cls, parent=None, caption="", directory="", filter="", selectedFilter="", options=None
    ):
        dialog = cls._create(parent, caption, directory, filter, options)
        dialog.setFileMode(QtFileDialog.FileMode.ExistingFiles)
        dialog.setAcceptMode(QtFileDialog.AcceptMode.AcceptOpen)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        accepted = dialog.exec() == cls.Accepted
        return (dialog.selectedFiles() if accepted else []), dialog.selectedNameFilter()

    @classmethod
    def getSaveFileName(
        cls, parent=None, caption="", directory="", filter="", selectedFilter="", options=None
    ):
        dialog = cls._create(parent, caption, directory, filter, options)
        dialog.setFileMode(QtFileDialog.FileMode.AnyFile)
        dialog.setAcceptMode(QtFileDialog.AcceptMode.AcceptSave)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        accepted = dialog.exec() == cls.Accepted
        files = dialog.selectedFiles()
        return (files[0] if accepted and files else ""), dialog.selectedNameFilter()

    @classmethod
    def getExistingDirectory(
        cls, parent=None, caption="", directory="", options=None
    ):
        dialog = cls._create(parent, caption, directory, "", options)
        dialog.setFileMode(QtFileDialog.FileMode.Directory)
        dialog.setAcceptMode(QtFileDialog.AcceptMode.AcceptOpen)
        dialog.setOption(QtFileDialog.Option.ShowDirsOnly, True)
        accepted = dialog.exec() == cls.Accepted
        files = dialog.selectedFiles()
        return files[0] if accepted and files else ""


class DropdownColorDialog(_DropdownMixin, QtColorDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setOption(
            QtColorDialog.ColorDialogOption.DontUseNativeDialog, True
        )


class DropdownProgressDialog(_DropdownMixin, QtProgressDialog):
    def __init__(self, *args, **kwargs):
        self._dropdown_cancel_allowed = bool(args[1]) if len(args) > 1 else True
        super().__init__(*args, **kwargs)

    def setCancelButton(self, button):
        self._dropdown_cancel_allowed = button is not None
        super().setCancelButton(button)


QDialog = DropdownDialog
QMessageBox = DropdownMessageBox
QInputDialog = DropdownInputDialog
# File selection deliberately stays native.  On Windows this opens Explorer's
# standard file picker instead of embedding Qt's picker in our custom overlay.
QFileDialog = QtFileDialog
QColorDialog = DropdownColorDialog
QProgressDialog = DropdownProgressDialog
