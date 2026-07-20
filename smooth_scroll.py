from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea


class SmoothScrollController(QObject):
    def __init__(
        self,
        scroll_area,
        duration=320,
        wheel_step=110,
        acceleration=0.18,
        max_boost=2.25,
        install_filter=True,
    ):
        super().__init__(scroll_area)
        self.scroll_area = scroll_area
        self._duration = max(180, int(duration))
        self._wheel_step = float(wheel_step)
        self._acceleration = max(0.0, float(acceleration))
        self._max_boost = max(1.0, float(max_boost))
        self._target_position = 0.0
        self._velocity_boost = 1.0
        self._last_direction = 0
        self._scrollbar = None
        self._wheel_clock = QElapsedTimer()
        self._wheel_clock.start()
        self._animation_start = 0.0
        self._animation_end = 0.0
        self._animation_duration = float(self._duration)
        self._animation_clock = QElapsedTimer()
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.PreciseTimer)
        self._animation_timer.setInterval(0)
        self._animation_timer.timeout.connect(self._animate_scroll_frame)
        if install_filter:
            scroll_area.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and self.handle_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def _scroll_input(self, event):
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        vertical_delta = pixel.y() or angle.y()
        horizontal_delta = pixel.x() or angle.x()
        use_horizontal = bool(event.modifiers() & Qt.ShiftModifier)
        if use_horizontal and vertical_delta:
            delta = vertical_delta
            pixel_based = bool(pixel.y())
            horizontal = True
            scrollbar = self.scroll_area.horizontalScrollBar()
        elif vertical_delta:
            delta = vertical_delta
            pixel_based = bool(pixel.y())
            horizontal = False
            scrollbar = self.scroll_area.verticalScrollBar()
        elif horizontal_delta:
            delta = horizontal_delta
            pixel_based = bool(pixel.x())
            horizontal = True
            scrollbar = self.scroll_area.horizontalScrollBar()
        else:
            return None
        if scrollbar.minimum() == scrollbar.maximum():
            fallback = (
                self.scroll_area.verticalScrollBar()
                if horizontal
                else self.scroll_area.horizontalScrollBar()
            )
            if fallback.minimum() == fallback.maximum():
                return None
            scrollbar = fallback
        raw_delta = (
            -float(delta)
            if pixel_based
            else -(float(delta) / 120.0) * self._wheel_step
        )
        return scrollbar, raw_delta

    def handle_wheel(self, event):
        if event.modifiers() & Qt.ControlModifier:
            return False
        scroll_input = self._scroll_input(event)
        if scroll_input is None:
            return False
        scrollbar, raw_delta = scroll_input
        if not raw_delta:
            return False
        current_position = float(scrollbar.value())
        direction = 1 if raw_delta > 0 else -1
        elapsed = self._wheel_clock.restart()
        if self._scrollbar is not scrollbar:
            self._animation_timer.stop()
            self._scrollbar = scrollbar
            self._target_position = current_position
            self._velocity_boost = 1.0
            self._last_direction = 0
        if not self._animation_timer.isActive():
            self._target_position = current_position
            self._velocity_boost = 1.0
        if direction != self._last_direction:
            self._target_position = current_position
            self._velocity_boost = 1.0
        elif elapsed < 150:
            intensity = 1.0 - max(0, elapsed) / 150.0
            self._velocity_boost = min(
                self._max_boost,
                self._velocity_boost + self._acceleration * intensity,
            )
        else:
            self._velocity_boost = max(1.0, self._velocity_boost * 0.82)
        self._last_direction = direction
        self._target_position += raw_delta * self._velocity_boost
        self._target_position = max(
            float(scrollbar.minimum()),
            min(float(scrollbar.maximum()), self._target_position),
        )
        distance = abs(self._target_position - current_position)
        if distance < 0.5:
            event.accept()
            return True
        adaptive_duration = min(
            680,
            max(260, self._duration + int(distance * 0.16)),
        )
        self._animation_timer.stop()
        self._animation_start = current_position
        self._animation_end = self._target_position
        self._animation_duration = float(adaptive_duration)
        self._animation_clock.start()
        self._animation_timer.start()
        event.accept()
        return True

    def _animate_scroll_frame(self):
        if self._scrollbar is None:
            self._animation_timer.stop()
            return
        elapsed = self._animation_clock.nsecsElapsed() / 1_000_000.0
        progress = min(1.0, elapsed / self._animation_duration)
        eased = 1.0 - (1.0 - progress) ** 3
        value = self._animation_start + (
            self._animation_end - self._animation_start
        ) * eased
        self._scrollbar.setValue(round(value))
        if progress >= 1.0:
            self._animation_timer.stop()
            self._scrollbar.setValue(round(self._animation_end))
            self._target_position = float(self._scrollbar.value())
            self._velocity_boost = 1.0


class SmoothScrollArea(QScrollArea):
    def __init__(
        self,
        parent=None,
        duration=460,
        wheel_step=105,
        acceleration=0.18,
        max_boost=2.25,
    ):
        super().__init__(parent)
        self._smooth_scroll_controller = SmoothScrollController(
            self,
            duration,
            wheel_step,
            acceleration,
            max_boost,
            False,
        )

    def wheelEvent(self, event):
        if not self._smooth_scroll_controller.handle_wheel(event):
            super().wheelEvent(event)


def enable_smooth_scrolling(
    widget,
    duration=320,
    wheel_step=110,
    acceleration=0.18,
    max_boost=2.25,
):
    if not isinstance(widget, QAbstractScrollArea):
        return None
    controller = getattr(widget, "_smooth_scroll_controller", None)
    if controller is not None:
        return controller
    controller = SmoothScrollController(
        widget,
        duration,
        wheel_step,
        acceleration,
        max_boost,
        True,
    )
    widget._smooth_scroll_controller = controller
    return controller
