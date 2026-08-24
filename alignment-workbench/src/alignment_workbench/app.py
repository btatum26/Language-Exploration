from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6 import QtWidgets
from registry_align.storage.database import redact_secrets

from alignment_workbench.services.registry import RegistryServices
from alignment_workbench.ui.main_window import MainWindow


def build_services() -> tuple[RegistryServices | None, str | None]:
    config_value = os.getenv("ALIGNMENT_WORKBENCH_CONFIG", "").strip()
    default_config = (
        Path(__file__).resolve().parents[3] / "registry-aligner" / "registry-align.example.toml"
    )
    config_path = (
        Path(config_value) if config_value else default_config if default_config.is_file() else None
    )
    try:
        return RegistryServices.from_environment(config_path), None
    except Exception as exc:
        return None, redact_secrets(str(exc))


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Alignment Workbench")
    style_path = Path(__file__).with_name("resources") / "styles.qss"
    if style_path.is_file():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))
    services, startup_error = build_services()
    window = MainWindow(services, startup_error=startup_error)
    window.show()
    return app.exec()
