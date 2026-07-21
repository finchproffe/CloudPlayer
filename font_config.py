import os
from pathlib import Path
from threading import Lock

_HIDPI_READY = False
_CONFIGURED_APP = None
_CACHED_FONT = None
_LOCK = Lock()

GLOBAL_FONT_QSS = """
QWidget {
    font-family: "Segoe UI", "Inter", sans-serif;
}
"""


def setup_hidpi_scaling():
    global _HIDPI_READY

    with _LOCK:
        if _HIDPI_READY:
            return

        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
        os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
        quiet_rules = (
            "qt.multimedia.ffmpeg.info=false\n"
            "qt.gui.icc.warning=false"
        )
        existing_rules = os.environ.get("QT_LOGGING_RULES", "").strip()
        os.environ["QT_LOGGING_RULES"] = (
            f"{existing_rules}\n{quiet_rules}"
            if existing_rules
            else quiet_rules
        )
        _HIDPI_READY = True


def setup_application_fonts(app):
    global _CONFIGURED_APP
    global _CACHED_FONT

    if app is None:
        raise TypeError("setup_application_fonts() requires QApplication")

    with _LOCK:
        if _CONFIGURED_APP is app:
            return _CACHED_FONT

        from PySide6.QtGui import QFont, QFontDatabase

        project_directory = Path(__file__).resolve().parent

        for filename in (
            "Inter-Regular.ttf",
            "Inter-Medium.ttf",
            "Inter-SemiBold.ttf",
            "Inter-Bold.ttf",
        ):
            font_path = project_directory / filename
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))

        installed_families = set(QFontDatabase.families())

        if "Segoe UI" in installed_families:
            family = "Segoe UI"
        elif "Inter" in installed_families:
            family = "Inter"
        else:
            family = QFontDatabase.systemFont(
                QFontDatabase.SystemFont.GeneralFont
            ).family()

        source_font = app.font()
        font = QFont(family)
        font.setPointSizeF(
            source_font.pointSizeF() if source_font.pointSizeF() > 0 else 10.0
        )
        font.setWeight(QFont.Weight.Normal)
        font.setKerning(True)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.PreferQuality
        )

        app.setFont(font)

        existing_qss = app.styleSheet().strip()
        application_qss = GLOBAL_FONT_QSS.strip()
        if existing_qss:
            application_qss = f"{application_qss}\n{existing_qss}"
        app.setStyleSheet(application_qss)

        _CONFIGURED_APP = app
        _CACHED_FONT = QFont(font)
        return _CACHED_FONT
