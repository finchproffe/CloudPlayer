from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtWidgets import QScrollArea


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

        self._duration = max(180, int(duration))
        self._wheel_step = float(wheel_step)
        self._acceleration = max(0.0, float(acceleration))
        self._max_boost = max(1.0, float(max_boost))

        self._target_position = 0.0
        self._velocity_boost = 1.0
        self._last_direction = 0

        self._wheel_clock = QElapsedTimer()
        self._wheel_clock.start()

        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._apply_scroll_value)
        self._animation.finished.connect(self._animation_finished)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            super().wheelEvent(event)
            return

        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()

        if pixel_delta:
            raw_delta = -float(pixel_delta)
        elif angle_delta:
            raw_delta = -(float(angle_delta) / 120.0) * self._wheel_step
        else:
            super().wheelEvent(event)
            return

        scrollbar = self.verticalScrollBar()
        current_position = float(scrollbar.value())
        direction = 1 if raw_delta > 0 else -1
        elapsed = self._wheel_clock.restart()

        if self._animation.state() != QVariantAnimation.Running:
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
            return



        adaptive_duration = min(
            680,
            max(260, self._duration + int(distance * 0.16)),
        )

        self._animation.stop()
        self._animation.setStartValue(current_position)
        self._animation.setEndValue(self._target_position)
        self._animation.setDuration(adaptive_duration)
        self._animation.start()
        event.accept()

    def _apply_scroll_value(self, value):
        self.verticalScrollBar().setValue(round(float(value)))

    def _animation_finished(self):
        self._target_position = float(self.verticalScrollBar().value())
        self._velocity_boost = 1.0