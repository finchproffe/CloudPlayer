

from PySide6.QtCore import QEvent, QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QSlider, QVBoxLayout, QWidget,
)

from config import ACCENT_COLOR, BG_COLOR, BUTTON_BORDER, PANEL_BG, TEXT_COLOR, TEXT_MUTED
from smooth_scroll import SmoothScrollArea
from utils import rounded_cover_pixmap

class BoundedSongList(QListWidget):


    reorder_started = Signal(list)
    reorder_finished = Signal(list, list)
    EDGE_ZONE = 72
    MAX_SCROLL_SPEED = 22.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_item = None
        self._drag_source_row = -1
        self._drag_hotspot_y = 0
        self._last_cursor_y = 0
        self._scroll_speed = 0.0
        self._target_scroll_speed = 0.0
        self._drag_preview = QLabel(self.viewport())
        self._drag_preview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag_preview.setStyleSheet("background:transparent;border:none")
        self._drag_preview.hide()
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(16)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)

    def order(self):
        return [
            self.item(row).data(Qt.UserRole)
            for row in range(self.count())
            if self.item(row) is not None
        ]

    def startDrag(self, _supported_actions):
        item = self.currentItem()
        if item is None:
            return
        item_rect = self.visualItemRect(item)
        if not item_rect.isValid():
            return
        self.reorder_started.emit(self.order())
        cursor = self.viewport().mapFromGlobal(self.cursor().pos())
        self._drag_item = item
        self._drag_source_row = self.row(item)
        self._last_cursor_y = cursor.y()
        self._drag_hotspot_y = max(
            0, min(item_rect.height() - 1, cursor.y() - item_rect.top())
        )
        preview = self.viewport().grab(item_rect)
        self._drag_preview.setPixmap(preview)
        self._drag_preview.setFixedHeight(item_rect.height())
        self._move_preview(cursor.y())
        self._drag_preview.show()
        self._drag_preview.raise_()
        self._auto_scroll_timer.start()

        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
        transparent = QPixmap(1, 1)
        transparent.fill(Qt.transparent)
        drag.setPixmap(transparent)
        drag.exec(Qt.MoveAction)
        self._finish_drag_preview()

    def dragEnterEvent(self, event):
        if event.source() is self and self._drag_item is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self._drag_preview.show()
            self._drag_preview.raise_()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() is not self or self._drag_item is None:
            super().dragMoveEvent(event)
            return
        y = event.position().toPoint().y()
        self._last_cursor_y = y
        self._update_auto_scroll_target(y)
        self._move_preview(y)
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._target_scroll_speed = 0.0
        event.accept()

    def dropEvent(self, event):
        if event.source() is not self or self._drag_item is None:
            super().dropEvent(event)
            return

        before = self.order()
        source_row = self._drag_source_row
        target_row = self._row_for_y(event.position().toPoint().y())
        target_row = max(0, min(target_row, len(before)))
        after = list(before)
        if 0 <= source_row < len(after):
            moved = after.pop(source_row)
            if source_row < target_row:
                target_row -= 1
            target_row = max(0, min(target_row, len(after)))
            after.insert(target_row, moved)

        event.setDropAction(Qt.MoveAction)
        event.accept()
        self._finish_drag_preview()
        if before != after:
            self.reorder_finished.emit(before, after)

    def _row_for_y(self, y):
        if not self.count():
            return 0
        point = self.viewport().rect().topLeft()
        point.setY(max(0, min(y, self.viewport().height() - 1)))
        item = self.itemAt(point)
        if item is None:
            return self.count() if y >= self.viewport().height() / 2 else 0
        row = self.row(item)
        rect = self.visualItemRect(item)
        return row + (1 if y >= rect.center().y() else 0)

    def _update_auto_scroll_target(self, y):
        height = self.viewport().height()
        if y < self.EDGE_ZONE:
            strength = 1.0 - max(0, y) / self.EDGE_ZONE
            self._target_scroll_speed = -self.MAX_SCROLL_SPEED * strength
        elif y > height - self.EDGE_ZONE:
            strength = 1.0 - max(0, height - y) / self.EDGE_ZONE
            self._target_scroll_speed = self.MAX_SCROLL_SPEED * strength
        else:
            self._target_scroll_speed = 0.0

    def _auto_scroll_tick(self):
        if self._drag_item is None:
            self._auto_scroll_timer.stop()
            return
        self._scroll_speed += (
            self._target_scroll_speed - self._scroll_speed
        ) * 0.22
        if abs(self._scroll_speed) < 0.15 and self._target_scroll_speed == 0:
            self._scroll_speed = 0.0
            return
        bar = self.verticalScrollBar()
        old_value = bar.value()
        bar.setValue(round(old_value + self._scroll_speed))
        if bar.value() != old_value:
            self._move_preview(self._last_cursor_y)

    def _move_preview(self, cursor_y):
        if self._drag_item is None:
            return
        height = self._drag_preview.height()
        max_y = max(0, self.viewport().height() - height)
        y = max(0, min(cursor_y - self._drag_hotspot_y, max_y))
        self._drag_preview.setGeometry(0, y, self.viewport().width(), height)

    def _finish_drag_preview(self):
        self._auto_scroll_timer.stop()
        self._scroll_speed = 0.0
        self._target_scroll_speed = 0.0
        self._drag_preview.hide()
        self._drag_preview.clear()
        self._drag_item = None
        self._drag_source_row = -1


class TrackListItemWidget(QWidget):
    ROW_HEIGHT = 87
    COVER_SIZE = 66

    def __init__(self, index, title, artist, cover_pixmap=None, parent=None):
        super().__init__(parent)
        self.title = str(title)
        self.artist = str(artist)
        self.cover_pixmap = (
            QPixmap(cover_pixmap) if cover_pixmap and not cover_pixmap.isNull()
            else QPixmap()
        )
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 12, 0)
        layout.setSpacing(10)
        cover = QLabel()
        cover.setFixedSize(self.COVER_SIZE, self.COVER_SIZE)
        cover.setAlignment(Qt.AlignCenter)
        rendered = rounded_cover_pixmap(
            self.cover_pixmap, self.COVER_SIZE, 6
        )
        if rendered:
            cover.setPixmap(rendered)
        else:
            cover.setText("♪")
            cover.setStyleSheet(
                f"background:{PANEL_BG};border-radius:6px;"
                f"color:{TEXT_MUTED};font-size:22px"
            )

        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            f"background:transparent;font-size:14px;font-weight:700;"
            f"color:{TEXT_COLOR}"
        )
        artist_label = QLabel(self.artist)
        artist_label.setStyleSheet(
            f"background:transparent;font-size:11px;color:{TEXT_MUTED}"
        )



        cover_column = QVBoxLayout()
        cover_column.setContentsMargins(0, 5, 0, 0)
        cover_column.setSpacing(0)
        cover_column.addWidget(cover, 0, Qt.AlignTop)
        cover_column.addStretch(1)

        text_block = QVBoxLayout()
        text_block.setContentsMargins(0, 0, 0, 0)
        text_block.setSpacing(4)
        text_block.addWidget(self.title_label)
        text_block.addWidget(artist_label)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        labels.addStretch(1)
        labels.addLayout(text_block)
        labels.addStretch(1)

        layout.addLayout(cover_column)
        layout.addLayout(labels, 1)
        self.set_index(index)

    def set_index(self, index):
        self.title_label.setText(f"{index}. {self.title}")

    def snapshot(self):
        return {
            "title": self.title,
            "artist": self.artist,
            "cover": QPixmap(self.cover_pixmap),
        }


class CoverPreviewDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cover: Full Size")
        self.resize(720, 720)
        self.original_pixmap = pixmap
        self.scale_factor = 1.0
        self.dragging = False
        self.last_pos = QPoint()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = SmoothScrollArea(self, duration=300, wheel_step=100)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet(f"background:{BG_COLOR};border:none")
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.image)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll)
        hint = QLabel("Wheel to zoom • Hold left mouse button and drag to pan")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color:{TEXT_MUTED};padding:10px")
        layout.addWidget(hint)
        self._render()

    def _render(self):
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        scaled = self.original_pixmap.scaled(
            self.original_pixmap.size() * self.scale_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image.setPixmap(scaled)
        self.image.resize(scaled.size())

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport():
            if event.type() == QEvent.Wheel:
                factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
                self.scale_factor = max(
                    0.1, min(8, self.scale_factor * factor)
                )
                self._render()
                return True
            if (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                self.dragging = True
                self.last_pos = event.position().toPoint()
                self.scroll.viewport().setCursor(Qt.ClosedHandCursor)
                return True
            if event.type() == QEvent.MouseMove and self.dragging:
                delta = event.position().toPoint() - self.last_pos
                self.last_pos = event.position().toPoint()
                horizontal = self.scroll.horizontalScrollBar()
                vertical = self.scroll.verticalScrollBar()
                horizontal.setValue(horizontal.value() - delta.x())
                vertical.setValue(vertical.value() - delta.y())
                return True
            if (
                event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                self.dragging = False
                self.scroll.viewport().setCursor(Qt.ArrowCursor)
                return True
        return super().eventFilter(obj, event)


class DirectJumpSlider(QSlider):
    value_committed = Signal(int)

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._direct_drag = False

    def _event_value(self, event):
        if self.orientation() == Qt.Horizontal:
            length = max(1, self.width() - 1)
            ratio = event.position().x() / length
        else:
            length = max(1, self.height() - 1)
            ratio = 1.0 - event.position().y() / length
        ratio = max(0.0, min(1.0, ratio))
        if self.invertedAppearance():
            ratio = 1.0 - ratio
        return self.minimum() + round(
            ratio * (self.maximum() - self.minimum())
        )

    def _move_to_event(self, event):
        value = self._event_value(event)
        self.setValue(value)
        self.sliderMoved.emit(value)
        return value

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._direct_drag = True
        self.setSliderDown(True)
        value = self._move_to_event(event)
        self.value_committed.emit(value)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._direct_drag:
            super().mouseMoveEvent(event)
            return
        self._move_to_event(event)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._direct_drag:
            super().mouseReleaseEvent(event)
            return
        value = self._move_to_event(event)
        self._direct_drag = False
        self.setSliderDown(False)
        self.sliderReleased.emit()
        self.value_committed.emit(value)
        event.accept()


class BufferedPositionSlider(DirectJumpSlider):

    GROOVE_HEIGHT = 4.0
    HANDLE_SIZE = 12.0
    BUFFER_COLOR = "#5f7188"

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._buffered_ratio = 0.0
        self._buffer_visible = False
        self.setMinimumHeight(round(self.HANDLE_SIZE + 4))

    def set_buffered_progress(self, received_bytes, total_bytes):

        try:
            received = max(0, int(received_bytes))
            total = max(0, int(total_bytes))
        except (TypeError, ValueError):
            received, total = 0, 0
        self._buffered_ratio = (
            min(1.0, received / total) if total else 0.0
        )
        self._buffer_visible = total > 0
        self.update()

    def set_buffered_position(self, position_ms):

        span = self.maximum() - self.minimum()
        if span <= 0:
            self.clear_buffered_progress()
            return
        try:
            position = int(position_ms)
        except (TypeError, ValueError):
            position = self.minimum()
        self._buffered_ratio = max(
            0.0,
            min(1.0, (position - self.minimum()) / span),
        )
        self._buffer_visible = True
        self.update()

    def clear_buffered_progress(self):
        self._buffered_ratio = 0.0
        self._buffer_visible = False
        self.update()

    @property
    def buffered_ratio(self):
        return self._buffered_ratio if self._buffer_visible else 0.0

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        half_handle = self.HANDLE_SIZE / 2.0
        available = max(0.0, self.width() - self.HANDLE_SIZE)
        groove_top = (self.height() - self.GROOVE_HEIGHT) / 2.0
        groove = QRectF(
            half_handle,
            groove_top,
            available,
            self.GROOVE_HEIGHT,
        )
        radius = self.GROOVE_HEIGHT / 2.0
        painter.setBrush(QColor(BUTTON_BORDER))
        painter.drawRoundedRect(groove, radius, radius)

        span = self.maximum() - self.minimum()
        played_ratio = (
            max(
                0.0,
                min(1.0, (self.value() - self.minimum()) / span),
            )
            if span > 0
            else 0.0
        )
        if self._buffer_visible:
            buffered_ratio = max(played_ratio, self._buffered_ratio)
            buffered = QRectF(
                half_handle,
                groove_top,
                available * buffered_ratio,
                self.GROOVE_HEIGHT,
            )
            painter.setBrush(QColor(self.BUFFER_COLOR))
            painter.drawRoundedRect(buffered, radius, radius)

        played = QRectF(
            half_handle,
            groove_top,
            available * played_ratio,
            self.GROOVE_HEIGHT,
        )
        painter.setBrush(QColor(ACCENT_COLOR))
        painter.drawRoundedRect(played, radius, radius)

        handle_center = half_handle + available * played_ratio
        handle = QRectF(
            handle_center - half_handle,
            (self.height() - self.HANDLE_SIZE) / 2.0,
            self.HANDLE_SIZE,
            self.HANDLE_SIZE,
        )
        painter.drawEllipse(handle)