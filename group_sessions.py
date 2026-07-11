from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from config import ACCENT_COLOR, TEXT_MUTED
from p2p_sync_manager import P2PSyncManager


class GroupSessionWidget(QWidget):
    back_requested = Signal()

    def __init__(self, manager: P2PSyncManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.tasks = set()
        self._build()
        manager.connection_state_changed.connect(lambda state: self.status.setText(f"WebRTC: {state}"))
        manager.connected.connect(self._connected)
        manager.disconnected.connect(lambda: self.status.setText("Disconnected"))
        manager.error_occurred.connect(self.status.setText)

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
            "Host saves an Offer TXT, guest loads it and saves an Answer TXT, "
            "then host loads the Answer TXT. TURN relay is used when direct P2P is blocked."
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{TEXT_MUTED}")
        root.addWidget(description)

        roles = QHBoxLayout()
        host = QPushButton("Host")
        guest = QPushButton("Guest")
        roles.addWidget(host)
        roles.addWidget(guest)
        roles.addStretch()
        root.addLayout(roles)

        self.pages = QStackedWidget()
        host.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        guest.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        self.pages.addWidget(self._host_page())
        self.pages.addWidget(self._guest_page())
        root.addWidget(self.pages, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Not connected")
        disconnect = QPushButton("Disconnect")
        disconnect.clicked.connect(lambda: self._spawn(self.manager.close()))
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(disconnect)
        root.addLayout(footer)

    def _host_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("1. Create and save Offer TXT. 2. Send it to the guest. 3. Load their Answer TXT."))
        self.offer = QTextEdit()
        self.offer.setReadOnly(True)
        self.offer.setPlaceholderText("Offer data")
        self.answer_input = QTextEdit()
        self.answer_input.setPlaceholderText("Answer data")
        buttons = QHBoxLayout()
        create = QPushButton("Create Offer TXT")
        save = QPushButton("Save Offer TXT")
        copy = QPushButton("Copy Offer")
        load_answer = QPushButton("Load Answer TXT")
        apply = QPushButton("Apply Answer")
        create.clicked.connect(lambda: self._spawn(self._create_offer()))
        save.clicked.connect(lambda: self._save_txt(self.offer.toPlainText(), "CloudPlayer-Offer.txt"))
        copy.clicked.connect(lambda: self._copy(self.offer.toPlainText()))
        load_answer.clicked.connect(lambda: self._load_txt(self.answer_input))
        apply.clicked.connect(lambda: self._spawn(self._apply_answer()))
        for button in (create, save, copy, load_answer, apply):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addWidget(self.offer)
        layout.addWidget(self.answer_input)
        layout.addLayout(buttons)
        return page

    def _guest_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("1. Load the host's Offer TXT. 2. Create and save Answer TXT. 3. Send it to the host."))
        self.offer_input = QTextEdit()
        self.offer_input.setPlaceholderText("Offer data")
        self.answer = QTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setPlaceholderText("Answer data")
        buttons = QHBoxLayout()
        load_offer = QPushButton("Load Offer TXT")
        create = QPushButton("Create Answer TXT")
        save = QPushButton("Save Answer TXT")
        copy = QPushButton("Copy Answer")
        load_offer.clicked.connect(lambda: self._load_txt(self.offer_input))
        create.clicked.connect(lambda: self._spawn(self._create_answer()))
        save.clicked.connect(lambda: self._save_txt(self.answer.toPlainText(), "CloudPlayer-Answer.txt"))
        copy.clicked.connect(lambda: self._copy(self.answer.toPlainText()))
        for button in (load_offer, create, save, copy):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addWidget(self.offer_input)
        layout.addWidget(self.answer)
        layout.addLayout(buttons)
        return page

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.status.setText(str(task.exception()))

    async def _create_offer(self):
        self.status.setText("Gathering direct and relay candidates...")
        self.offer.setPlainText(await self.manager.create_host_offer())
        self.status.setText("Offer ready. Save the TXT and send it to the guest.")

    async def _create_answer(self):
        value = self.offer_input.toPlainText().strip()
        if not value:
            raise ValueError("Load the host Offer TXT first.")
        self.status.setText("Creating answer with relay candidates...")
        self.answer.setPlainText(await self.manager.accept_host_offer(value))
        self.status.setText("Answer ready. Save the TXT and send it to the host.")

    async def _apply_answer(self):
        value = self.answer_input.toPlainText().strip()
        if not value:
            raise ValueError("Load the guest Answer TXT first.")
        await self.manager.accept_guest_answer(value)
        self.status.setText("Connecting...")

    def _connected(self):
        self.status.setText("P2P connected")
        self.status.setStyleSheet(f"color:{ACCENT_COLOR};font-weight:700")

    def _save_txt(self, text, default_name):
        if not text.strip():
            QMessageBox.warning(self, "Nothing to Save", "Create the connection data first.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save Connection TXT", default_name, "Text files (*.txt)")
        if filename:
            Path(filename).write_text(text.strip(), encoding="utf-8")
            self.status.setText(f"Saved {Path(filename).name}")

    def _load_txt(self, target):
        filename, _ = QFileDialog.getOpenFileName(self, "Load Connection TXT", "", "Text files (*.txt)")
        if filename:
            target.setPlainText(Path(filename).read_text(encoding="utf-8").strip())
            self.status.setText(f"Loaded {Path(filename).name}")

    @staticmethod
    def _copy(text):
        if text:
            QGuiApplication.clipboard().setText(text)
