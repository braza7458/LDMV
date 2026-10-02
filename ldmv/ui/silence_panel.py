"""Panneau « Supprimer les silences », affiché au-dessus de la timeline.

Le seuil se règle en faisant glisser la barre orange sur la piste audio (ou
avec le curseur du panneau) : les zones rouges, recalculées en direct,
sont celles qui seront supprimées. L'analyse lourde (niveaux RMS) est faite
une fois à l'import ; ici on ne fait que rejouer le seuillage.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QWidget

from ldmv.core.silence import DB_FLOOR, Interval, SilenceParams, silences_from_levels

THRESHOLD_MAX = -5


class SilencePanel(QWidget):
    previewChanged = Signal(dict)      # {source_id: [intervalles]}
    thresholdChanged = Signal(float)   # dB, pour redessiner la barre
    applyRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyses: dict[str, tuple] = {}
        self.measure = None  # callable(silences) -> texte de résumé
        self.result_silences: dict[str, list[Interval]] = {}

        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(int(DB_FLOOR) * 2, THRESHOLD_MAX * 2)  # pas de 0,5 dB
        self.threshold.setValue(-80)
        self.threshold.setFixedWidth(160)
        self.threshold_label = QLabel()
        self.threshold_label.setMinimumWidth(60)

        def spin(value, lo, hi, step=10):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setValue(value)
            s.setSuffix(" ms")
            return s

        self.min_silence = spin(500, 50, 10_000, step=50)
        self.min_silence.setToolTip("Un silence plus court que cette durée est conservé")
        self.padding = spin(100, 0, 2_000)
        self.padding.setToolTip("Marge gardée avant et après chaque passage parlé")
        self.ripple = QCheckBox("Recoller")
        self.ripple.setToolTip("Recolle les clips restants (aimant)")
        self.info = QLabel()
        self.info.setStyleSheet("color: #ff8a70; font-weight: bold;")
        apply_btn = QPushButton("Couper tous les silences")
        apply_btn.setStyleSheet("background: #c0392b; color: white; padding: 4px 12px; font-weight: bold;")
        apply_btn.clicked.connect(self.applyRequested)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.closeRequested)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        title = QLabel("Silences")
        title.setStyleSheet("font-weight: bold;")
        row.addWidget(title)
        row.addWidget(QLabel("Seuil"))
        row.addWidget(self.threshold)
        row.addWidget(self.threshold_label)
        row.addSpacing(10)
        row.addWidget(QLabel("Durée min."))
        row.addWidget(self.min_silence)
        row.addWidget(QLabel("Marge"))
        row.addWidget(self.padding)
        row.addWidget(self.ripple)
        row.addSpacing(10)
        row.addWidget(self.info, 1)
        row.addWidget(apply_btn)
        row.addWidget(close_btn)
        self.setStyleSheet("SilencePanel { background: #2b1f1d; }")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.threshold.valueChanged.connect(self.recompute)
        self.min_silence.valueChanged.connect(self.recompute)
        self.padding.valueChanged.connect(self.recompute)

    def open(self, analyses: dict, ripple: bool, measure) -> None:
        self.analyses = analyses
        self.measure = measure
        self.ripple.setChecked(ripple)
        self.show()
        self.recompute()

    @property
    def threshold_db(self) -> float:
        return self.threshold.value() / 2

    def set_threshold(self, db: float) -> None:
        """Appelé quand l'utilisateur fait glisser la barre sur la piste."""
        self.threshold.setValue(round(db * 2))

    def params(self) -> SilenceParams:
        return SilenceParams(
            threshold_db=self.threshold_db,
            min_silence_ms=self.min_silence.value(),
            padding_ms=self.padding.value(),
        )

    def recompute(self) -> None:
        self.threshold_label.setText(f"{self.threshold_db:.1f} dB")
        params = self.params()
        self.result_silences = {
            sid: silences_from_levels(db, hop, rate, n, params)
            for sid, (db, hop, rate, n) in self.analyses.items()
        }
        if self.measure:
            self.info.setText(self.measure(self.result_silences))
        self.thresholdChanged.emit(self.threshold_db)
        self.previewChanged.emit(self.result_silences)
