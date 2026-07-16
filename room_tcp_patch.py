

from ui_bootstrap import install as install_ui_bootstrap

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    install_ui_bootstrap()