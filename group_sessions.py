from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from config import ACCENT_COLOR, BUTTON_BG, BUTTON_BORDER, ELEVATED_BG, TEXT_COLOR, TEXT_MUTED
from network_sync_manager import NetworkSyncManager

FIELD_STYLE = f"""
QLineEdit {{
    background-color:{ELEVATED_BG};
    color:{TEXT_COLOR};
    border:1px solid {BUTTON_BORDER};
    border-radius:6px;
    padding:8px 10px;
    font-size:13px;
}}
QLineEdit:focus {{ border:1px solid {ACCENT_COLOR}; }}
"""

PRIMARY_BUTTON_STYLE = f"""
QPushButton {{
    background-color:{ACCENT_COLOR};
    color:#ffffff;
    border:none;
    border-radius:6px;
    padding:10px;
    font-weight:700;
    font-size:13px;
}}
QPushButton:hover {{ background-color:#1560D4; }}
"""

MODE_BUTTON_STYLE = f"""
QPushButton {{
    background-color:{BUTTON_BG};
    color:{TEXT_COLOR};
    border:1px solid {BUTTON_BORDER};
    border-radius:6px;
    padding:9px 16px;
    font-weight:600;
}}
QPushButton:checked {{ background-color:{ACCENT_COLOR}; color:#ffffff; border:1px solid {ACCENT_COLOR}; }}
"""


def _labeled_field(label_text, placeholder=""):
    box = QVBoxLayout()
    box.setSpacing(4)
    label = QLabel(label_text)
    label.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px;font-weight:600")
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setStyleSheet(FIELD_STYLE)
    box.addWidget(label)
    box.addWidget(field)
    return box, field


def _country_display(country_code):

    code = str(country_code or "").strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        flag = "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)
        return flag, f"Location: {code}"
    return "🌐", "Location: Unknown"


class GroupSessionWidget(QWidget):
    back_requested = Signal()

    def __init__(self, manager: NetworkSyncManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.tasks = set()
        self._build()
        manager.connection_state_changed.connect(self.status.setText)
        manager.connected.connect(self._connected)
        manager.disconnected.connect(self._disconnected)
        manager.error_occurred.connect(self._on_error)
        manager.roster_updated.connect(self._update_roster)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 34, 34, 34)
        root.setSpacing(18)

        top = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested)
        title = QLabel("Listen Together")
        title.setStyleSheet("font-size:28px;font-weight:700")
        top.addWidget(back)
        top.addSpacing(14)
        top.addWidget(title)
        top.addStretch()
        root.addLayout(top)

        description = QLabel(
            "Create a server to host a room, or join one with a Host and Port. Everyone in the "
            "room stays in sync automatically. The buffer adapts to connection quality, "
            "the loaded part is shown on the timeline, and interrupted transfers resume "
            "automatically after reconnecting."
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{TEXT_MUTED}")
        root.addWidget(description)

        self.mode_bar = QWidget()
        modes = QHBoxLayout(self.mode_bar)
        modes.setContentsMargins(0, 0, 0, 0)
        self.btn_host = QPushButton("Create Server")
        self.btn_join = QPushButton("Join Room")
        for btn in (self.btn_host, self.btn_join):
            btn.setCheckable(True)
            btn.setStyleSheet(MODE_BUTTON_STYLE)
            modes.addWidget(btn)
        modes.addStretch()
        self.btn_host.setChecked(True)
        self.btn_host.clicked.connect(lambda: self._select_mode(0))
        self.btn_join.clicked.connect(lambda: self._select_mode(1))
        root.addWidget(self.mode_bar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._host_page())
        self.pages.addWidget(self._join_page())
        self.pages.addWidget(self._roster_page())
        root.addWidget(self.pages, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Not connected")
        self.status.setStyleSheet(f"color:{TEXT_MUTED}")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setStyleSheet(MODE_BUTTON_STYLE)
        self.disconnect_btn.clicked.connect(lambda: self._spawn(self.manager.close()))
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(self.disconnect_btn)
        root.addLayout(footer)

    def _select_mode(self, index):
        self.btn_host.setChecked(index == 0)
        self.btn_join.setChecked(index == 1)
        self.pages.setCurrentIndex(index)



    def _host_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)
        layout.addWidget(QLabel(
            "Enter the public host or IP that other people will use. Full URLs, IDN domains, "
            "IPv4, and IPv6 are supported. Forward the selected port to Local Port when needed."
        ))

        fields = QHBoxLayout()
        fields.setSpacing(14)
        host_box, self.host_url_input = _labeled_field("Public Host or IP", "e.g. myserver.example.com")
        port_box, self.host_port_input = _labeled_field("Port", "e.g. 2916")
        local_box, self.host_local_port_input = _labeled_field("Local Port (optional)", "defaults to Port")
        self.host_port_input.setValidator(QIntValidator(1, 65535, self))
        self.host_local_port_input.setValidator(QIntValidator(1, 65535, self))
        fields.addLayout(host_box, 2)
        fields.addLayout(port_box, 1)
        fields.addLayout(local_box, 1)
        layout.addLayout(fields)

        create = QPushButton("Create Server")
        create.setStyleSheet(PRIMARY_BUTTON_STYLE)
        create.clicked.connect(self._create_server)
        layout.addWidget(create)
        layout.addStretch()
        return page

    def _create_server(self):
        host_url = self.host_url_input.text().strip()
        port_text = self.host_port_input.text().strip()
        local_port_text = self.host_local_port_input.text().strip()

        if not host_url or not port_text:
            QMessageBox.warning(self, "Missing Information", "Enter both Public Host and Port.")
            return
        try:
            port = int(port_text)
            local_port = int(local_port_text) if local_port_text else None
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port and Local Port must be numbers.")
            return

        self.status.setText("Starting server...")
        self._spawn(self.manager.host(host_url, port, local_port))



    def _join_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)
        layout.addWidget(QLabel("Enter the room host or full URL and its port."))

        fields = QHBoxLayout()
        fields.setSpacing(14)
        host_box, self.join_url_input = _labeled_field("Host or IP", "e.g. myserver.example.com")
        port_box, self.join_port_input = _labeled_field("Port", "e.g. 2916")
        self.join_port_input.setValidator(QIntValidator(1, 65535, self))
        fields.addLayout(host_box, 2)
        fields.addLayout(port_box, 1)
        layout.addLayout(fields)

        join = QPushButton("Join Room")
        join.setStyleSheet(PRIMARY_BUTTON_STYLE)
        join.clicked.connect(self._join_room)
        layout.addWidget(join)
        layout.addStretch()
        return page

    def _join_room(self):
        host_url = self.join_url_input.text().strip()
        port_text = self.join_port_input.text().strip()

        if not host_url or not port_text:
            QMessageBox.warning(self, "Missing Information", "Enter both Host and Port.")
            return
        try:
            port = int(port_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number.")
            return

        self.status.setText("Connecting...")
        self._spawn(self.manager.join(host_url, port))



    def _roster_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        self.roster_title = QLabel("Room Members (0)")
        self.roster_title.setStyleSheet("font-size:15px;font-weight:700")
        layout.addWidget(self.roster_title)
        self.roster_list = QListWidget()
        self.roster_list.setStyleSheet(f"""
            QListWidget {{ background-color:{ELEVATED_BG}; border:1px solid {BUTTON_BORDER}; border-radius:8px; }}
            QListWidget::item {{ border-bottom:1px solid {BUTTON_BORDER}; }}
            QListWidget::item:last {{ border-bottom:none; }}
        """)
        self.roster_list.setSelectionMode(QListWidget.NoSelection)
        self.roster_list.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.roster_list, 1)
        return page

    @staticmethod
    def _member_row(member: dict) -> QWidget:
        row = QWidget()
        outer = QHBoxLayout(row)
        outer.setContentsMargins(12, 10, 12, 10)

        flag, location = _country_display(member.get("country"))
        flag_label = QLabel(flag)
        flag_label.setFixedWidth(34)
        flag_label.setAlignment(Qt.AlignCenter)
        flag_label.setStyleSheet("font-size:21px;background:transparent")
        flag_label.setToolTip(location)
        outer.addWidget(flag_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name = member.get("name") or "Unknown"
        if member.get("is_self"):
            name += "  (You)"
        name_label = QLabel(name)
        name_label.setStyleSheet(f"color:{TEXT_COLOR};font-weight:700;font-size:13px")
        country_label = QLabel(location)
        country_label.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px")
        text_box.addWidget(name_label)
        text_box.addWidget(country_label)
        outer.addLayout(text_box, 1)

        ping = member.get("ping")
        ping_text = f"{ping} ms" if isinstance(ping, (int, float)) else "-- ms"
        ping_label = QLabel(ping_text)
        ping_label.setStyleSheet(f"color:{ACCENT_COLOR};font-weight:700;font-size:13px")
        ping_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        outer.addWidget(ping_label)
        return row

    def _update_roster(self, members):
        self.roster_list.clear()
        self.roster_title.setText(f"Room Members ({len(members)})")
        for member in members:
            item = QListWidgetItem()
            row = self._member_row(member)
            item.setSizeHint(row.sizeHint())
            self.roster_list.addItem(item)
            self.roster_list.setItemWidget(item, row)



    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.status.setText(str(task.exception()))

    def _connected(self):
        self.mode_bar.setVisible(False)
        self.pages.setCurrentIndex(2)
        self.status.setText("Connected")
        self.status.setStyleSheet(f"color:{ACCENT_COLOR};font-weight:700")

    def _disconnected(self):
        self.mode_bar.setVisible(True)
        self.pages.setCurrentIndex(0 if self.btn_join.isChecked() is False else 1)
        self.roster_list.clear()
        self.roster_title.setText("Room Members (0)")
        self.status.setStyleSheet(f"color:{TEXT_MUTED}")
        self.status.setText("Disconnected")

    def _on_error(self, message):
        self.status.setStyleSheet(f"color:#E57373")
        self.status.setText(message)