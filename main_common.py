from PySide6.QtWidgets import QMenu

from config import (
    ACCENT_COLOR,
    BUTTON_BORDER,
    PANEL_BG,
    TEXT_COLOR,
    TEXT_MUTED,
)

MENU_ICON_SIZE = 28
MENU_TEXT_SIZE = 14
MENU_STYLE = f"""
QMenu {{background-color:{PANEL_BG};color:{TEXT_COLOR};border:1px solid {BUTTON_BORDER};border-radius:4px;padding:4px;font-size:{MENU_TEXT_SIZE}px;font-weight:700}}
QMenu::item {{background-color:transparent;padding:3px 10px 3px 8px;margin:0;border-radius:3px;min-height:18px}}
QMenu::item:selected {{background-color:{ACCENT_COLOR};color:#ffffff}}
QMenu::item:disabled {{color:{TEXT_MUTED}}}
QMenu::separator {{height:1px;margin:4px 6px;background:{BUTTON_BORDER}}}
QMenu::icon {{width:{MENU_ICON_SIZE}px;height:{MENU_ICON_SIZE}px}}
"""


def make_menu(_parent=None):
    menu = QMenu()
    menu.setStyleSheet(MENU_STYLE)
    return menu
