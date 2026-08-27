from __future__ import annotations

from PySide6 import QtWidgets


class PatternGuide(QtWidgets.QTextBrowser):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHtml(
            "<b>Pattern guide</b><br><br>"
            "<b>Waveform:</b> amplitude and timing; silence is near the center line.<br><br>"
            "<b>Spectrogram:</b> horizontal bands often indicate harmonics or resonances; "
            "fricatives appear as diffuse high-frequency energy.<br><br>"
            "<b>F0:</b> an estimate of vocal-fold repetition rate. Missing points are unvoiced, "
            "not zero pitch.<br><br>"
            "<b>F1-F3:</b> experimental LPC estimates of vocal-tract resonances. They are "
            "different from F0 harmonics and are most meaningful on steady voiced vowels.<br><br>"
            "<b>Fourier:</b> peaks show strong periodic components in the exact labeled region. "
            "Recording conditions can change every view, so comparisons are descriptive."
        )
        self.setOpenExternalLinks(False)
