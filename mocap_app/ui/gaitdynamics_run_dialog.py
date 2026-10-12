"""Dutch controls for the Windows-to-WSL CPU prediction transport."""
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit, QFormLayout, QVBoxLayout,
    QGridLayout, QPushButton, QPlainTextEdit, QFileDialog, QScrollArea, QWidget)

from mocap_app.ui.gaitdynamics_setup_dialog import GaitDynamicsSetupDialog, GaitTask
from mocap_app.ui.window_geometry import fit_window_to_screen


def participant_values(height, mass, speed):
    values = tuple(float(text.strip().replace(',', '.')) for text in (height, mass, speed))
    if not (1 <= values[0] <= 2.5 and 30 <= values[1] <= 200 and 0 <= values[2] <= 10):
        raise ValueError('Vul lengte in meters (1–2,5), massa in kg (30–200) en bandsnelheid in m/s (0–10) in.')
    return values


class GaitDynamicsRunDialog(QDialog):
    def __init__(self, motion, model, parent=None):
        super().__init__(parent)
        self.motion, self.model = Path(motion), Path(model)
        self.task = None
        self.setup_dialog = None
        self.output = None
        self.setModal(True)
        self.setWindowTitle('Grondreactiekrachten voorspellen — HU3D kinematics')
        self.setMinimumSize(440, 340)
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        note = QLabel('Voorspelling voor beide voeten op CPU, via WSL2. Vul de gegevens van deze persoon in. '
            'Bij wandelen over de grond is de bandsnelheid 0. Voorspelde krachten zijn geen gemeten krachten; '
            'de nauwkeurigheid voor jouw opname is niet gevalideerd.')
        note.setWordWrap(True)
        layout.addWidget(note)
        inputs = QLabel('Beweging: ' + self.motion.name + '\nModel: ' + self.model.name)
        inputs.setWordWrap(True)
        inputs.setToolTip(str(self.motion) + '\n' + str(self.model))
        layout.addWidget(inputs)
        form = QFormLayout()
        self.height_field, self.mass, self.speed = QLineEdit(), QLineEdit(), QLineEdit('0')
        self.height_field.setPlaceholderText('Bijvoorbeeld 1,75 — vul de werkelijke lengte in')
        self.mass.setPlaceholderText('Vul de werkelijke lichaamsmassa in')
        for label, field in (('Lengte (meter)', self.height_field), ('Massa (kg)', self.mass), ('Bandsnelheid (m/s)', self.speed)):
            form.addRow(label, field)
        layout.addLayout(form)
        self.details = QPlainTextEdit()
        self.details.setMinimumHeight(100)
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('Begin met Rekenomgeving installeren / controleren als je GaitDynamics nog niet hebt geïnstalleerd.')
        layout.addWidget(self.details, 1)
        buttons = QGridLayout()
        self.install_button = QPushButton('Rekenomgeving installeren…')
        self.start_button = QPushButton('Krachten voorspellen…')
        self.view_button = QPushButton('Krachten bekijken')
        self.folder_button = QPushButton('Resultaatmap openen')
        self.close_button = QPushButton('Sluiten')
        for i, button in enumerate((self.install_button, self.start_button, self.view_button, self.folder_button, self.close_button)):
            buttons.addWidget(button, i // 2, i % 2)
        outer.addLayout(buttons)
        self.view_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.install_button.clicked.connect(self._install)
        self.start_button.clicked.connect(self._start)
        self.view_button.clicked.connect(self._view)
        self.folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output))))
        self.close_button.clicked.connect(self.close)
        fit_window_to_screen(self, QSize(780, 580))

    @property
    def busy(self):
        return ((self.task is not None and self.task.isRunning()) or
                (self.setup_dialog is not None and self.setup_dialog.busy))

    def _install(self):
        if self.busy:
            return
        self.setup_dialog = GaitDynamicsSetupDialog(self)
        self.setup_dialog.exec()

    def _start(self):
        if self.busy:
            return
        try:
            height, mass, speed = participant_values(self.height_field.text(), self.mass.text(), self.speed.text())
        except ValueError as error:
            self.details.setPlainText('Controleer de persoonsgegevens: ' + str(error))
            return
        base = QFileDialog.getExistingDirectory(self, 'Kies waar de nieuwe krachtresultaten komen', str(self.motion.parent))
        if not base:
            return
        output = Path(base) / ('GaitDynamics-krachten-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
        self.output = output
        self.task = GaitTask('analyze', (self.motion, self.model, output, height, mass, speed), self)
        self.task.progress.connect(self.details.appendPlainText)
        self.task.result.connect(self._result)
        self.task.finished.connect(self._finished)
        self.view_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        for widget in (self.install_button, self.start_button, self.close_button, self.height_field, self.mass, self.speed):
            widget.setEnabled(False)
        self.details.setPlainText('WSL2 en invoer controleren…')
        self.task.start()

    def _result(self, result):
        self.details.appendPlainText(result.get('message', result['status']))
        self.view_button.setEnabled(result['status'] == 'completed')
        self.folder_button.setEnabled(self.output is not None and self.output.is_dir())
        if result.get('log'):
            self.details.appendPlainText('Log: ' + result['log'])

    def _finished(self):
        for widget in (self.install_button, self.start_button, self.close_button, self.height_field, self.mass, self.speed):
            widget.setEnabled(True)

    def _view(self):
        from mocap_app.ui.analysis_dialog import MotionResultsDialog
        self.viewer = MotionResultsDialog(self.output / 'ground_reaction_forces.mot', self, forces=True)
        self.viewer.show()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
