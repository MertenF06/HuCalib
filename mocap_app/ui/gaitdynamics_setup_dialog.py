"""Guided WSL2 installation; blocking OS/package operations run in a thread."""
from pathlib import Path
import traceback

from PySide6.QtCore import QThread, Signal, QSize
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QGridLayout, QVBoxLayout, QScrollArea, QWidget

from mocap_app.analysis.gaitdynamics_wsl import WslBackend
from mocap_app.ui.window_geometry import fit_window_to_screen


class GaitTask(QThread):
    progress = Signal(str)
    result = Signal(object)

    def __init__(self, operation, arguments=(), parent=None):
        super().__init__(parent)
        self.operation, self.arguments = operation, arguments

    def run(self):
        log = []
        def notify(text):
            log.append(text)
            self.progress.emit(text)
        backend = None
        # Write errors alongside persistent setup state, including pre-inference failures.
        try:
            backend = WslBackend(notify)
            value = getattr(backend, self.operation)(*self.arguments)
        except Exception as error:
            value = dict(status='failed', message=str(error))
            if backend is not None:
                value['log'] = str(backend.state_dir / 'setup.log')
            log.append(traceback.format_exc())
        finally:
            if backend is not None and self.operation != 'probe':
                try:
                    (backend.state_dir / 'setup.log').write_text('\n'.join(log), encoding='utf-8')
                except OSError:
                    pass
        self.result.emit(value)


class GaitDynamicsSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('GaitDynamics installeren — HU3D kinematics')
        self.setMinimumSize(440, 320)
        self.task = None
        self.status = None
        self.setModal(True)
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        note = QLabel('Deze optionele installatie gebruikt WSL2: Linux binnen Windows, op deze computer. '
            'De app installeert Ubuntu 24.04, een afzonderlijke CPU-rekenomgeving en gecontroleerde AI-modellen. '
            'Je hebt internet, Windows 10/11 x64 met virtualisatie en voldoende schijfruimte nodig (reserveer 10 GB). '
            'Windows kan om beheerderstoestemming en een herstart vragen. Er wordt niet automatisch herstart. '
            'Een aanwezige Ubuntu 24.04 wordt gebruikt; daarin worden systeempakketten toegevoegd. '
            'Sluit lopende Docker- en WSL-taken voordat je WSL bijwerken kiest.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.summary = QLabel('Klik op Controleren om te zien wat al aanwezig is.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.details = QPlainTextEdit()
        self.details.setMinimumHeight(100)
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        row = QGridLayout()
        self.check = QPushButton('Controleren')
        self.install = QPushButton('Installeren / doorgaan')
        self.update_button = QPushButton('WSL bijwerken')
        self.update_button.setToolTip('Werk Microsoft WSL bij. Sluit vooraf lopende Docker- en WSL-taken.')
        self.close_button = QPushButton('Sluiten')
        for index, button in enumerate((self.check, self.install, self.update_button, self.close_button)):
            row.addWidget(button, index // 2, index % 2)
        outer.addLayout(row)
        self.check.clicked.connect(lambda: self.start('probe'))
        self.install.clicked.connect(lambda: self.start('install'))
        self.update_button.clicked.connect(lambda: self.start('update'))
        self.close_button.clicked.connect(self.close)
        fit_window_to_screen(self, QSize(760, 560))

    @property
    def busy(self):
        return self.task is not None and self.task.isRunning()

    def start(self, operation):
        if self.busy:
            return
        self.task = GaitTask(operation, parent=self)
        self.task.progress.connect(self.details.appendPlainText)
        self.task.result.connect(self._result)
        self.task.finished.connect(self._finished)
        for button in (self.check, self.install, self.update_button, self.close_button):
            button.setEnabled(False)
        self.summary.setText('Controleren…' if operation == 'probe' else 'Installatie loopt. Wacht tot deze stap klaar is.')
        self.task.start()

    def _result(self, value):
        self.status = value
        if value['status'] == 'failed':
            self.summary.setText('Installatie of controle niet voltooid. Bekijk de foutdetails hieronder.')
            self.details.appendPlainText(value.get('message', 'Onbekende fout'))
        else:
            self.summary.setText(value.get('message', value['status']))
        if value.get('log'):
            self.details.appendPlainText('Installatielog: ' + value['log'])

    def _finished(self):
        for button in (self.check, self.install, self.update_button, self.close_button):
            button.setEnabled(True)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
