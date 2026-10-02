"""Boîte de dialogue « Supprimer les silences » avec aperçu en direct.

L'analyse lourde (niveaux RMS) est faite une seule fois à l'import ; ici on
ne fait que rejouer le seuillage, ce qui est instantané même sur 30 min.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QSlider, QSpinBox, QVBoxLayout)

from ldmv.core.silence import Interval, SilenceParams, silences_from_levels, total_duration
from ldmv.core.timecode import format_timecode


class SilenceDialog(QDialog):
    previewChanged = Signal(dict)   # {source_id: [intervalles]}

    def __init__(self, analyses: dict, magnet: bool, total: int, fps: float, parent=None):
        """`analyses` : {source_id: (niveaux_db, hop, sample_rate, n_samples)}."""
        super().__init__(parent)
        self.setWindowTitle("Supprimer les silences")
        self.analyses = analyses
        self.total = total
        self.fps = fps
        self.result_silences: dict[str, list[Interval]] = {}

        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(-80, -10)
        self.threshold.setValue(-40)
        self.threshold_label = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.threshold, 1)
        row.addWidget(self.threshold_label)

        def spin(value, lo, hi, suffix=" ms", step=10):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setValue(value)
            s.setSuffix(suffix)
            return s

        self.min_silence = spin(500, 50, 10_000, step=50)
        self.padding = spin(100, 0, 2_000)
        self.min_sound = spin(60, 0, 1_000)
        self.ripple = QCheckBox("Recoller les clips restants (aimant)")
        self.ripple.setChecked(magnet)
        self.info = QLabel()
        self.info.setStyleSheet("color: #ff8a70; font-weight: bold;")

        form = QFormLayout()
        form.addRow("Seuil de bruit", row)
        form.addRow("Durée minimale du silence", self.min_silence)
        form.addRow("Marge autour de la parole", self.padding)
        form.addRow("Ignorer les sons plus courts que", self.min_sound)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Couper tous les silences")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.ripple)
        layout.addWidget(QLabel("Les zones rouges de la timeline seront supprimées."))
        layout.addWidget(self.info)
        layout.addWidget(buttons)

        for w in (self.threshold, self.min_silence, self.padding, self.min_sound):
            w.valueChanged.connect(self.recompute)
        self.recompute()

    def params(self) -> SilenceParams:
        return SilenceParams(
            threshold_db=float(self.threshold.value()),
            min_silence_ms=self.min_silence.value(),
            padding_ms=self.padding.value(),
            min_sound_ms=self.min_sound.value(),
        )

    def recompute(self) -> None:
        self.threshold_label.setText(f"{self.threshold.value()} dB")
        params = self.params()
        self.result_silences = {
            sid: silences_from_levels(db, hop, rate, n, params)
            for sid, (db, hop, rate, n) in self.analyses.items()
        }
        count = sum(len(v) for v in self.result_silences.values())
        removed = sum(total_duration(v) for v in self.result_silences.values())
        pct = 100 * removed / self.total if self.total else 0
        self.info.setText(f"{count} silences — {format_timecode(removed, self.fps)} à retirer ({pct:.0f} %)")
        self.previewChanged.emit(self.result_silences)
