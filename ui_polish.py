

import re

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractScrollArea, QMenu, QWidget

from config import ACCENT_COLOR, BUTTON_BORDER, PANEL_BG, TEXT_MUTED
from smooth_scroll import enable_smooth_scrolling

FONT_WEIGHT = QFont.Weight.Bold
SCROLLBAR_STYLE = f"""
QScrollBar:vertical {{
    background:{PANEL_BG};
    width:12px;
    margin:3px 2px;
    border:none;
    border-radius:5px;
}}
QScrollBar::handle:vertical {{
    background:{BUTTON_BORDER};
    min-height:34px;
    border:none;
    border-radius:4px;
}}
QScrollBar::handle:vertical:hover {{
    background:{TEXT_MUTED};
    border:1px solid {TEXT_MUTED};
}}
QScrollBar::handle:vertical:pressed {{
    background:{ACCENT_COLOR};
    border:1px solid {ACCENT_COLOR};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height:0;
    background:transparent;
    border:none;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background:transparent;
}}
QScrollBar:horizontal {{
    background:{PANEL_BG};
    height:12px;
    margin:2px 3px;
    border:none;
    border-radius:5px;
}}
QScrollBar::handle:horizontal {{
    background:{BUTTON_BORDER};
    min-width:34px;
    border:none;
    border-radius:4px;
}}
QScrollBar::handle:horizontal:hover {{
    background:{TEXT_MUTED};
    border:1px solid {TEXT_MUTED};
}}
QScrollBar::handle:horizontal:pressed {{
    background:{ACCENT_COLOR};
    border:1px solid {ACCENT_COLOR};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width:0;
    background:transparent;
    border:none;
}}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background:transparent;
}}
QAbstractScrollArea::corner {{
    background:transparent;
    border:none;
}}
"""

def _polish(widget):


    if isinstance(widget, QMenu):
        return
    if isinstance(widget, QAbstractScrollArea):
        enable_smooth_scrolling(widget)
        if not getattr(widget, "_cloud_scrollbar_styled", False):
            widget.setStyleSheet(
                widget.styleSheet() + "\n" + SCROLLBAR_STYLE
            )
            widget._cloud_scrollbar_styled = True
    font = widget.font()
    font.setWeight(FONT_WEIGHT)
    font.setBold(True)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    widget.setFont(font)
    style = re.sub(r"font-weight\s*:\s*(?:bold|normal|[1-9]00)", "font-weight:700", widget.styleSheet(), flags=re.I)
    if widget.metaObject().className() in {"TrackListItemWidget", "TrackRow"}:
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setAutoFillBackground(False)
        style += ";background:transparent;border:none"
        for child in widget.findChildren(QWidget):
            child.setAutoFillBackground(False)
            child.setStyleSheet(child.styleSheet() + ";background:transparent;border:none")
    if style != widget.styleSheet():
        widget.setStyleSheet(style)


def _application_is_active():
    return (
        QCoreApplication.instance() is not None
        and not QCoreApplication.closingDown()
    )


def _polish_later(root):
    if not _application_is_active():
        return
    try:
        polish_tree(root)
    except RuntimeError:
        return


class UiFilter(QObject):
    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.ChildAdded
            and isinstance(watched, QWidget)
            and not isinstance(watched, QMenu)
            and _application_is_active()
        ):
            QTimer.singleShot(
                0,
                lambda root=watched: _polish_later(root),
            )
        return False


_ui_filter = UiFilter()


def polish_tree(root):
    if not isinstance(root, QWidget) or isinstance(root, QMenu):
        return
    _polish(root)
    root.installEventFilter(_ui_filter)
    for widget in root.findChildren(QWidget):
        if isinstance(widget, QMenu):
            continue
        _polish(widget)
        widget.installEventFilter(_ui_filter)
