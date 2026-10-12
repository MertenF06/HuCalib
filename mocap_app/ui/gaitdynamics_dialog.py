"""Inspect Pose2Sim/OpenSim inputs before optional GaitDynamics processing."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QGridLayout)

from mocap_app.analysis.gaitdynamics import inspect_inputs
from mocap_app.core.branding import APP_NAME
from mocap_app.ui.window_geometry import fit_window_to_screen


class GaitDynamicsDialog(QDialog):
    def __init__(self, motion: Path | None, model: Path | None, parent=None, python: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle(f'GaitDynamics — {APP_NAME}')
        self.setMinimumSize(480, 340)
        self.report = None
        self._file_buttons = []
        self.conversion_output = None
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_conversion)
        self.process.finished.connect(self._conversion_finished)
        self.process.errorOccurred.connect(self._conversion_error)
        self._conversion_log = ''
        layout = QVBoxLayout(self)
        note = QLabel('Experimentele koppeling voor het voorspellen van grondreactiekrachten. '
            'De rekenmodule draait apart op Linux of WSL2 (Linux op Windows), met CPU-verwerking. '
            'Controleer eerst of het bewegingsbestand en het bijbehorende lichaamsmodel geschikt zijn.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.motion = self._file(form, 'Bewegingsbestand', motion, '*.mot')
        self.model = self._file(form, 'OpenSim-model', model, '*.osim')
        self.python = self._file(form, 'Analyseomgeving', python, 'python.exe python')
        layout.addLayout(form)
        conversion_row = QHBoxLayout()
        self.convert_button = QPushButton('Pose2Sim-model converteren…')
        conversion_note = QLabel('Maakt nieuwe bestanden met 23 coördinaten. Gebruikt je bestaande Pose2Sim-omgeving.')
        conversion_note.setWordWrap(True)
        conversion_row.addWidget(self.convert_button)
        conversion_row.addWidget(conversion_note, 1)
        layout.addLayout(conversion_row)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('De controle verandert geen opname, model of analyseresultaat.')
        layout.addWidget(self.details, 1)
        actions = QGridLayout()
        self.check_button = QPushButton('Invoer controleren')
        self.save_button = QPushButton('Rapport bewaren')
        self.close_button = close = QPushButton('Sluiten')
        self.force_button = QPushButton('Krachten voorspellen…')
        self.run_dialog = None
        for i, button in enumerate((self.check_button, self.save_button, self.force_button, close)):
            actions.addWidget(button, i // 2, i % 2)
        layout.addLayout(actions)
        self.save_button.setEnabled(False)
        self.check_button.clicked.connect(self._check)
        self.convert_button.clicked.connect(self._convert)
        self.save_button.clicked.connect(self._save)
        self.force_button.clicked.connect(self._forces)
        close.clicked.connect(self.close)
        self.motion.textChanged.connect(self._invalidate)
        self.model.textChanged.connect(self._invalidate)
        self.python.textChanged.connect(self._invalidate)
        self._invalidate()
        fit_window_to_screen(self, QSize(820, 560))

    def _file(self, form, label, value, pattern):
        row = QHBoxLayout()
        field = QLineEdit(str(value or ''))
        button = QPushButton('Kiezen…')
        self._file_buttons.append(button)
        row.addWidget(field, 1)
        row.addWidget(button)
        form.addRow(label, row)
        def choose():
            kind = 'Python' if pattern == 'python.exe python' else 'OpenSim'
            path = QFileDialog.getOpenFileName(self, label, field.text(), f'{kind} ({pattern})')[0]
            if path:
                field.setText(path)
        button.clicked.connect(choose)
        return field

    def _invalidate(self):
        self.report = None
        self.save_button.setEnabled(False)
        self.convert_button.setEnabled(self.process.state() == QProcess.ProcessState.NotRunning and
            all(field.text().strip() for field in (self.motion, self.model, self.python)))

    def _set_busy(self, busy):
        for field in (self.motion, self.model, self.python):
            field.setEnabled(not busy)
        for button in self._file_buttons:
            button.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.convert_button.setEnabled(not busy and all(field.text().strip() for field in (self.motion, self.model, self.python)))
        self.save_button.setEnabled(not busy and self.report is not None)
        self.force_button.setEnabled(not busy)

    @property
    def busy(self):
        return (self.process.state() != QProcess.ProcessState.NotRunning or
                (self.run_dialog is not None and self.run_dialog.busy))

    def _forces(self):
        if self.busy:
            return
        self._check()
        if not self.report or not self.report['compatible']:
            self.details.appendPlainText('Converteer eerst het Pose2Sim-model en controleer de invoer.')
            return
        from mocap_app.ui.gaitdynamics_run_dialog import GaitDynamicsRunDialog
        self.run_dialog = GaitDynamicsRunDialog(Path(self.motion.text()), Path(self.model.text()), self)
        self.run_dialog.exec()

    def _convert(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        base = QFileDialog.getExistingDirectory(self, 'Kies de map voor nieuwe conversieresultaten', str(Path(self.motion.text()).parent))
        if not base:
            return
        self.conversion_output = Path(base) / ('GaitDynamics-conversie-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
        self._start_conversion(self.conversion_output)

    def _start_conversion(self, output):
        self.conversion_output = Path(output)
        root = (Path(sys._MEIPASS) / 'analysis_runtime' if getattr(sys, 'frozen', False)
                else Path(__file__).resolve().parents[2])
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONPATH', str(root) + os.pathsep + environment.value('PYTHONPATH', ''))
        environment.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(root))
        self.process.setProgram(str(Path(self.python.text()).expanduser().absolute()))
        self.process.setArguments(['-m', 'mocap_app.analysis.model_conversion',
            '--motion', str(Path(self.motion.text()).expanduser().absolute()),
            '--model', str(Path(self.model.text()).expanduser().absolute()),
            '--output', str(self.conversion_output.absolute())])
        self._conversion_log = ''
        self.details.setPlainText('Modelconversie loopt. Alle tijdstappen worden geometrisch gecontroleerd.\n'
            'Je oorspronkelijke Pose2Sim-resultaten blijven behouden.')
        self._set_busy(True)
        self.process.start()

    def _read_conversion(self):
        self._conversion_log = (self._conversion_log + bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace'))[-100000:]

    def _conversion_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._set_busy(False)
            self.details.setPlainText('Analyseomgeving kon niet starten. Kies het Python-programma van je Pose2Sim-omgeving.\n' + self.process.errorString())

    def _conversion_finished(self, code, status):
        self._read_conversion()
        self._set_busy(False)
        try:
            if self.conversion_output.is_dir():
                (self.conversion_output / 'conversion.log').write_text(self._conversion_log, encoding='utf-8')
            path = self.conversion_output / 'conversion.json'
            report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if code != 0 or status != QProcess.ExitStatus.NormalExit or report.get('status') != 'completed':
                raise ValueError(report.get('error') or self._conversion_log[-2000:] or 'Het proces is onverwacht gestopt.')
            self.motion.setText(str(self.conversion_output / 'motion.mot'))
            self.model.setText(str(self.conversion_output / 'model.osim'))
            self._check()
            self.details.appendPlainText(f"\nModelconversie gereed. Maximale rompafwijking: {report['validation']['max_trunk_position_error_m']*1000:.1f} mm.\n"
                f"Resultaten en conversierapport: {self.conversion_output}\n" + '\n'.join(report['warnings']))
        except Exception as error:
            self.details.setPlainText('Modelconversie niet geslaagd: ' + str(error) + f'\nUitvoermap: {self.conversion_output}')

    def closeEvent(self, event):
        if self.busy:
            self.details.appendPlainText('Wacht tot de conversie, installatie of krachtberekening is afgerond voordat je dit venster sluit.')
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if not self.busy:
            super().reject()

    def _check(self):
        try:
            self.report = inspect_inputs(Path(self.motion.text()), Path(self.model.text()))
            status = 'Invoer geschikt voor de afzonderlijke rekenmodule.' if self.report['compatible'] else 'Invoer nog niet geschikt voor GaitDynamics.'
            self.details.setPlainText(status + '\n\n' + '\n\n'.join(self.report['errors']) +
                f"\n\n{self.report['rows']} tijdstappen · {self.report['duration_seconds']:.2f} seconden\n" +
                'Voorspelde krachten zijn geen gemeten krachten. De meetnauwkeurigheid is niet gevalideerd.')
            self.save_button.setEnabled(True)
        except Exception as error:
            self.report = None
            self.save_button.setEnabled(False)
            self.details.setPlainText('Invoercontrole mislukt: ' + str(error))

    def _save(self):
        if self.report is None:
            return
        path = QFileDialog.getSaveFileName(self, 'Controlerapport bewaren', 'gaitdynamics-controle.json', 'JSON (*.json)')[0]
        if path:
            try:
                Path(path).write_text(json.dumps(self.report, indent=2), encoding='utf-8')
            except OSError as error:
                self.details.appendPlainText('Rapport kon niet worden bewaard: ' + str(error))
