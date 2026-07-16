from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from config import ACCENT_COLOR, BUTTON_HOVER, PANEL_BG, TEXT_COLOR, TEXT_MUTED
from utils import rounded_cover_pixmap


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=12):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, index): return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index): return self._items.pop(index) if 0 <= index < len(self._items) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self._layout(QRect(0, 0, width, 0), True)
    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)
    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect, test):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self.spacing()
            if next_x - self.spacing() > area.right() and row_height:
                x, y, row_height = area.x(), y + row_height + self.spacing(), 0
                next_x = x + hint.width() + self.spacing()
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


class RecommendationCard(QWidget):
    COVER_SIZE = 88
    play_requested = Signal(dict)
    add_requested = Signal(dict, object)

    def __init__(self, rec, parent=None):
        super().__init__(parent)
        self.rec = self.track = rec
        self.loading = False
        self.setObjectName("recommendationCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"#recommendationCard{{background:{PANEL_BG};border-radius:4px}}"
            f"#recommendationCard:hover{{background:{BUTTON_HOVER}}}"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        self.cover = QLabel()
        self.cover.setFixedSize(self.COVER_SIZE, self.COVER_SIZE)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet(f"background:{PANEL_BG};border-radius:4px;color:{TEXT_MUTED};font-size:22px")
        labels = QVBoxLayout()
        title = QLabel(rec.get("title") or "Unknown Title")
        title.setMaximumWidth(150)
        title.setWordWrap(True)
        title.setStyleSheet(f"color:{TEXT_COLOR};font-size:13px;font-weight:700")
        artist = QLabel(rec.get("artist") or "Unknown Artist")
        artist.setMaximumWidth(150)
        artist.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px")
        labels.addStretch()
        labels.addWidget(title)
        labels.addWidget(artist)
        labels.addStretch()
        self.add_btn = QPushButton("+")
        self.add_btn.setToolTip("Add to playlist")
        self.add_btn.setFixedSize(30, 30)
        self.add_btn.setStyleSheet(
            f"background:{ACCENT_COLOR};color:#FFFFFF;border:none;border-radius:15px;"
            "font-size:19px;font-weight:700;padding:0"
        )
        self.add_btn.clicked.connect(lambda: self.add_requested.emit(self.rec, self.add_btn))
        root.addWidget(self.cover)
        root.addLayout(labels, 1)
        root.addWidget(self.add_btn, 0, Qt.AlignBottom)
        self._render()

    def _render(self):
        pixmap = QPixmap()
        pixmap.loadFromData(self.rec.get("cover_bytes") or b"")
        rendered = rounded_cover_pixmap(pixmap, self.COVER_SIZE, 4)
        if rendered:
            self.cover.setPixmap(rendered)
            self.cover.setText("")
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("♪")

    def set_loading(self, value):
        self.loading = value
        self.setEnabled(not value)
        self.cover.setText("..." if value else "")
        if not value:
            self._render()

    def sizeHint(self): return QSize(300, 104)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.loading:
            self.play_requested.emit(self.rec)
        super().mousePressEvent(event)