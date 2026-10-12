"""Dutch analysis controls; QProcess keeps validation/analysis off the UI thread."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import uuid

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from mocap_app.analysis.pose2sim import STAGES, STAGE_LABELS
from mocap_app.analysis.results import MotionResult, coordinate_label
from mocap_app.core.branding import APP_NAME
from mocap_app.ui.window_geometry import fit_window_to_screen


def warning_text(warning: dict) -> str:
    if warning.get('issue') == 'resolution_mismatch':
        return f"Camera {warning.get('camera_id')}: video en kalibratie hebben verschillende resoluties."
    if warning.get('issue') == 'result_header_frame_count_mismatch':
        return f"Trajectbestand: {warning['rows']} meetregels, terwijl de header {warning['declared_rows']} vermeldt."
    return 'Een resultaat vraagt controle: ' + str(warning.get('issue', 'onbekende waarschuwing'))


class CoordinatePlot(QWidget):
    """Small native Qt plot; no plotting dependencies in the Windows app."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 200)
        self.result = None
        self.name = None

    def set_coordinate(self, result: MotionResult, name: str):
        self.result, self.name = result, name
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#ffffff'))
        if self.result is None:
            return
        x = self.result.values[:, 0]
        y = self.result.values[:, self.result.names.index(self.name) + 1]
        xmin, xmax = float(x.min()), float(x.max())
        ymin, ymax = float(y.min()), float(y.max())
        xspan = max(xmax - xmin, .001)
        padding = max((ymax - ymin) * .12, .01)
        ymin, ymax = ymin - padding, ymax + padding
        left, top, width, height = 72, 52, self.width() - 100, self.height() - 112
        painter.setPen(QColor('#172b44'))
        painter.drawText(22, 26, coordinate_label(self.name) + ' (' + self.result.unit(self.name) + ')')
        for i in range(6):
            yy = top + height * i / 5
            xx = left + width * i / 5
            painter.setPen(QPen(QColor('#e5eaf1'), 1))
            painter.drawLine(left, int(yy), left + width, int(yy))
            painter.drawLine(int(xx), top, int(xx), top + height)
            painter.setPen(QColor('#52647b'))
            painter.drawText(4, int(yy) + 4, f'{ymax - (ymax - ymin) * i / 5:.2f}')
            painter.drawText(int(xx) - 10, top + height + 24, f'{xmin + xspan * i / 5:.2f}')
        painter.drawText(left + width // 2 - 50, self.height() - 12, 'Tijd (seconden)')
        path = QPainterPath()
        for i, (time, value) in enumerate(zip(x, y)):
            xx = left + (time - xmin) / xspan * width
            yy = top + (ymax - value) / (ymax - ymin) * height
            if i == 0:
                path.moveTo(xx, yy)
            else:
                path.lineTo(xx, yy)
        painter.setPen(QPen(QColor('#1769c2'), 2.5))
        painter.drawPath(path)


class MotionResultsDialog(QDialog):
    def __init__(self, path: Path, parent=None, forces=False):
        super().__init__(parent)
        self.result = MotionResult.read(path)
        self.setWindowTitle(f'{"Grondreactiekrachten" if forces else "Bewegingsresultaten"} — {APP_NAME}')
        self.setMinimumSize(420, 340)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        title = QLabel('Voorspelde grondreactiekrachten' if forces else 'Gewrichtshoeken en verplaatsingen')
        title.setWordWrap(True)
        title.setStyleSheet('font-size: 22px; font-weight: bold; color: #172b44;')
        layout.addWidget(title)
        layout.addWidget(QLabel(f'{len(self.result.names)} {"uitvoerkolommen" if forces else "modelcoördinaten"} · {len(self.result.values)} tijdstappen'))
        self.coordinate = QComboBox()
        self.coordinate.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.coordinate.setMinimumContentsLength(12)
        self.coordinate.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for name in self.result.names:
            if forces and name not in ('force1_vx', 'force1_vy', 'force1_vz', 'force2_vx', 'force2_vy', 'force2_vz'):
                continue
            self.coordinate.addItem(coordinate_label(name), name)
        layout.addWidget(self.coordinate)
        self.plot = CoordinatePlot()
        layout.addWidget(self.plot, 1)
        note = QLabel('Voorspelde krachten in newton; Y is verticaal in het OpenSim-coördinatenstelsel.\n'
                      'De voorspelling is geen krachtplaatmeting en is niet klinisch gevalideerd.' if forces else
                      'Berekende modelcoördinaten. De meetnauwkeurigheid is niet gevalideerd.\n'
                      'Gekoppelde coördinaten zijn geen onafhankelijke metingen.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.coordinate.currentIndexChanged.connect(self._update_plot)
        preferred = self.coordinate.findData('force1_vy' if forces else 'knee_angle_r')
        self.coordinate.setCurrentIndex(max(0, preferred))
        self._update_plot()
        self.setStyleSheet('QDialog { background: #f4f7fb; } QComboBox { padding: 8px; }')
        fit_window_to_screen(self, QSize(960, 600))

    def _update_plot(self):
        self.plot.set_coordinate(self.result, self.coordinate.currentData())


class AnalysisDialog(QDialog):
    def __init__(self, settings_path: Path, initial_session: Path | None,
                 output_base: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Bewegingsanalyse — {APP_NAME}')
        self.setMinimumSize(480, 340)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        font = self.font()
        font.setPointSize(10)
        self.setFont(font)
        self.settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._refresh_report)
        self.phase = 'idle'
        self.output_dir = None
        self.report = None
        self.checked = False
        self._buffer = ''
        self._results_dialog = None
        self._gaitdynamics_dialog = None
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        title = QLabel('Bewegingsanalyse')
        title.setStyleSheet('font-size: 25px; font-weight: bold; color: #172b44;')
        layout.addWidget(title)
        subtitle = QLabel('Kies een opname, controleer de cameragegevens en bereken de beweging met Pose2Sim.')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        inputs = QGroupBox('1. Opname en opslag')
        form = QFormLayout(inputs)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.session = self._path_row(form, 'Opnamemap', str(initial_session or ''), 'directory')
        self.output_base = self._path_row(form, 'Resultaten bewaren in', str(output_base), 'directory')
        layout.addWidget(inputs)
        settings = QGroupBox('2. Analyse-instellingen')
        form = QFormLayout(settings)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.python = self._path_row(form, 'Analyseomgeving', self.settings.value('python', '', str), 'python')
        self.config = self._path_row(form, 'Instellingenbestand', self.settings.value('config', '', str), 'config')
        help_text = QLabel('Kies eenmalig de Python uit je Pose2Sim-omgeving en een volledig Pose2Sim-instellingenbestand.\n'
                          'De analyse gebruikt CPU-verwerking. De originele opname blijft bewaard.')
        help_text.setWordWrap(True)
        form.addRow(help_text)
        self.diagnostic = QCheckBox('Diagnostische demoproef')
        self.diagnostic.setToolTip('Alleen voor een functionele demoproef. Framegaten blijven geblokkeerd.')
        form.addRow(self.diagnostic)
        diagnostic_help = QLabel('Staat een afwijkende kalibratieresolutie toe. Alleen voor de demoproef.')
        diagnostic_help.setWordWrap(True)
        form.addRow(diagnostic_help)
        layout.addWidget(settings)
        self.summary = QLabel('Nog geen opname gecontroleerd.')
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.status = QLabel('Klaar om een opname te kiezen')
        self.status.setWordWrap(True)
        self.status.setObjectName('analysisStatus')
        self.progress = QProgressBar()
        self.progress.setRange(0, len(STAGES))
        self.progress.setFormat('%v van %m stappen voltooid')
        self.stages = QListWidget()
        self.stages.setMinimumHeight(170)
        self.stages.setMaximumHeight(180)
        for stage in STAGES:
            self.stages.addItem('Wacht — ' + STAGE_LABELS[stage].capitalize())
        layout.addWidget(self.stages)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMinimumHeight(100)
        self.messages.setPlaceholderText('Invoercontroles, waarschuwingen en foutmeldingen verschijnen hier.')
        layout.addWidget(self.messages, 1)
        self.content_scroll.setWidget(content)
        outer_layout.addWidget(self.content_scroll, 1)
        outer_layout.addWidget(self.status)
        outer_layout.addWidget(self.progress)
        self.actions = QGridLayout()
        self.check_button = QPushButton('Opname controleren')
        self.start_button = QPushButton('Analyse starten')
        self.start_button.setObjectName('startAnalysis')
        self.folder_button = QPushButton('Resultatenmap openen')
        self.graph_button = QPushButton('Grafieken bekijken')
        self.gait_button = QPushButton('GaitDynamics voorbereiden')
        self.close_button = QPushButton('Sluiten')
        self._action_buttons = (self.check_button, self.start_button, self.gait_button, self.folder_button,
                                self.graph_button, self.close_button)
        self._action_columns = None
        outer_layout.addLayout(self.actions)
        self._arrange_actions()
        self.check_button.clicked.connect(self._check)
        self.start_button.clicked.connect(self._start)
        self.folder_button.clicked.connect(self._open_folder)
        self.graph_button.clicked.connect(self._open_graph)
        self.gait_button.clicked.connect(self._open_gaitdynamics)
        self.close_button.clicked.connect(self.close)
        for field in (self.session, self.output_base, self.python, self.config):
            field.textChanged.connect(self._invalidate)
        self.diagnostic.toggled.connect(self._invalidate)
        self._input_groups = (inputs, settings)
        self.setStyleSheet('''
            QDialog { background: #f4f7fb; color: #243449; }
            QGroupBox { border: 1px solid #d7e0ea; border-radius: 8px; margin-top: 12px; padding: 14px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QPlainTextEdit, QListWidget { background: white; border: 1px solid #d7e0ea; border-radius: 5px; padding: 6px; }
            QPushButton { padding: 9px 12px; border-radius: 5px; border: 1px solid #bdcbdc; background: white; }
            QPushButton#startAnalysis { background: #1769c2; color: white; font-weight: bold; }
            QPushButton:disabled { color: #8592a3; background: #e8edf3; }
            QPushButton#startAnalysis:disabled { color: #8592a3; background: #e8edf3; }
            QLabel#analysisStatus { font-weight: bold; color: #1769c2; }
        ''')
        self._update_buttons()
        fit_window_to_screen(self, QSize(960, 740))

    def _arrange_actions(self):
        columns = 3 if self.width() >= 760 else 2
        if columns == self._action_columns:
            return
        self._action_columns = columns
        while self.actions.count():
            self.actions.takeAt(0)
        for index, button in enumerate(self._action_buttons):
            # Primary actions first, close at the bottom right.
            row, col = divmod(index, columns)
            self.actions.addWidget(button, row, col)
        for col in range(3):
            self.actions.setColumnStretch(col, 1 if col < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_action_buttons'):
            self._arrange_actions()

    @property
    def busy(self):
        gait = getattr(self, '_gaitdynamics_dialog', None)
        converting = gait is not None and gait.busy
        return self.process.state() != QProcess.ProcessState.NotRunning or self.phase in ('checking', 'running') or converting

    def _path_row(self, form, label, value, kind):
        row = QHBoxLayout()
        field = QLineEdit(value)
        button = QPushButton('Kiezen…')
        row.addWidget(field, 1)
        row.addWidget(button)
        form.addRow(label, row)
        def choose():
            if kind == 'directory':
                value = QFileDialog.getExistingDirectory(self, label, field.text())
            else:
                file_filter = 'Instellingen (*.toml)' if kind == 'config' else 'Python (python.exe python);;Alle bestanden (*)'
                value = QFileDialog.getOpenFileName(self, label, field.text(), file_filter)[0]
            if value:
                field.setText(value)
        button.clicked.connect(choose)
        return field

    def _invalidate(self):
        self.checked = False
        self.report = None
        self.output_dir = None
        self.summary.setText('Instellingen gewijzigd. Controleer de opname opnieuw.')
        self._update_buttons()

    def _update_buttons(self):
        busy = self.busy
        for group in self._input_groups:
            group.setEnabled(not busy)
        ready = all(field.text().strip() for field in (self.session, self.python, self.config, self.output_base))
        self.check_button.setEnabled(not busy and ready)
        self.start_button.setEnabled(not busy and self.checked)
        self.close_button.setEnabled(not busy)
        self.folder_button.setEnabled(self.output_dir is not None and self.output_dir.is_dir())
        self.graph_button.setEnabled(not busy and bool(self.report and self.report.get('status') == 'completed'))
        self.gait_button.setEnabled(self.graph_button.isEnabled())

    def _launch(self, arguments):
        root = (Path(sys._MEIPASS) / 'analysis_runtime' if getattr(sys, 'frozen', False)
                else Path(__file__).resolve().parents[2])
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONPATH', str(root) + os.pathsep + env.value('PYTHONPATH', ''))
        env.insert('MPLBACKEND', 'Agg')
        env.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(root))
        self.process.setProgram(str(Path(self.python.text()).expanduser().absolute()))
        self.process.setArguments(['-m', 'mocap_app.analysis.pose2sim'] + arguments)
        self._buffer = ''
        self.process.start()
        self._update_buttons()

    def _arguments(self):
        args = ['--session', str(Path(self.session.text()).expanduser().resolve()),
                '--config', str(Path(self.config.text()).expanduser().resolve())]
        if self.diagnostic.isChecked():
            args.append('--diagnostic-resolution-mismatch')
        return args

    def _check(self):
        if self.busy:
            return
        session = Path(self.session.text()).expanduser().resolve()
        base = Path(self.output_base.text()).expanduser().resolve()
        if base == session or base.is_relative_to(session):
            self.status.setText('Kies een resultatenmap buiten de opnamemap')
            self.checked = False
            self._update_buttons()
            return
        self.checked = False
        self.phase = 'checking'
        self.status.setText('Opname en analyseomgeving worden gecontroleerd…')
        self.messages.clear()
        self.progress.setRange(0, 0)
        self._launch(self._arguments() + ['--check-only'])

    def _start(self):
        if self.busy or not self.checked:
            return
        self.output_dir = Path(self.output_base.text()).expanduser().resolve() / (
            'analyse_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
        self.phase = 'running'
        self.report = None
        self.messages.clear()
        self.status.setText('Analyse gestart — invoer wordt opnieuw gecontroleerd…')
        self.progress.setRange(0, len(STAGES))
        self.progress.setValue(0)
        for i, stage in enumerate(STAGES):
            self.stages.item(i).setText('Wacht — ' + STAGE_LABELS[stage].capitalize())
        self.settings.setValue('python', self.python.text())
        self.settings.setValue('config', self.config.text())
        self._launch(self._arguments() + ['--output', str(self.output_dir), '--python',
                                        str(Path(self.python.text()).expanduser().absolute())])
        self.timer.start()

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.phase = 'idle'
            self.checked = False
            self.timer.stop()
            self.progress.setRange(0, len(STAGES))
            self.status.setText('Analyseomgeving kan niet worden gestart')
            self.messages.appendPlainText('Controleer het gekozen Python-bestand. ' + self.process.errorString())
            self._update_buttons()

    def _refresh_report(self):
        if self.output_dir is None:
            return
        try:
            report = json.loads((self.output_dir / 'analysis.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        self.report = report
        states = {s['name']: s['status'] for s in report.get('stages', [])}
        completed = 0
        for i, stage in enumerate(STAGES):
            state = states.get(stage, 'waiting')
            label = {'completed': 'Klaar', 'running': 'Bezig', 'failed': 'Mislukt', 'skipped': 'Overgeslagen'}.get(state, 'Wacht')
            self.stages.item(i).setText(label + ' — ' + STAGE_LABELS[stage].capitalize())
            if state == 'completed':
                completed += 1
            elif state == 'running':
                self.status.setText('Bezig met ' + STAGE_LABELS[stage] + '…')
        self.progress.setValue(completed)

    def _process_finished(self, code, exit_status):
        self._read_output()
        phase = self.phase
        self.phase = 'idle'
        self.timer.stop()
        self.progress.setRange(0, len(STAGES))
        if phase == 'checking':
            if code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
                try:
                    checked = json.loads(self._buffer.strip().splitlines()[-1])
                    videos = checked['videos']
                    self.summary.setText(f"{len(videos)} camera’s · {videos[0]['decoded_frames']} frames per camera · {checked['frame_rate']:g} beelden/seconde\n"
                                         'Camerakoppeling, kalibratie en videobestanden gecontroleerd.')
                    self.checked = True
                    self.status.setText('Opname gecontroleerd — klaar om te analyseren')
                    for warning in checked['warnings']:
                        self.messages.appendPlainText(warning_text(warning))
                    if self.diagnostic.isChecked():
                        self.messages.appendPlainText('Diagnostische demoproef: de uitkomst valideert geen meetnauwkeurigheid.')
                except (ValueError, KeyError, IndexError):
                    self.checked = False
                    self.status.setText('Controle kon niet worden afgerond')
                    self.messages.appendPlainText(self._buffer)
            else:
                self.checked = False
                self.status.setText('Opname niet geschikt — analyse geblokkeerd')
                self.messages.appendPlainText(self._buffer or 'De analyseomgeving is onverwacht gestopt.')
        elif phase == 'running':
            self._refresh_report()
            if code == 0 and self.report and self.report.get('status') == 'completed':
                self.status.setText('Analyse voltooid — resultaten beschikbaar')
                motions = [s for s in self.report.get('result_summary', []) if 'coordinates' in s]
                if motions:
                    self.summary.setText(f"{motions[0]['rows']} tijdstappen · {motions[0]['coordinates']} modelcoördinaten\n"
                                         'Bekijk de grafieken of open de resultatenmap.')
                for warning in self.report.get('warnings', []):
                    self.messages.appendPlainText(warning_text(warning))
            else:
                self.status.setText('Analyse gestopt — bekijk de foutmelding')
                self.checked = False
                self.messages.appendPlainText((self.report or {}).get('error') or self._buffer or 'De analyseomgeving is onverwacht gestopt.')
        self._update_buttons()

    def _open_folder(self):
        if self.output_dir and self.output_dir.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def _open_graph(self):
        if not self.report or self.report.get('status') != 'completed':
            return
        entries = [s for s in self.report.get('result_summary', []) if 'coordinates' in s]
        if not entries:
            return
        path = (self.output_dir / entries[0]['path']).resolve()
        if not path.is_relative_to(self.output_dir.resolve()):
            self.messages.appendPlainText('Resultaatbestand ligt buiten de analysemap.')
            return
        try:
            self._results_dialog = MotionResultsDialog(path, self)
            self._results_dialog.show()
        except (OSError, ValueError, KeyError) as error:
            self.messages.appendPlainText('Grafiek kan niet worden geopend: ' + str(error))

    def _open_gaitdynamics(self):
        if self.busy or not self.report or self.report.get('status') != 'completed' or self.output_dir is None:
            return
        from mocap_app.ui.gaitdynamics_dialog import GaitDynamicsDialog
        motions = [entry['path'] for entry in self.report.get('result_summary', []) if 'coordinates' in entry]
        models = [entry['path'] for entry in self.report.get('artifacts', []) if entry['path'].lower().endswith('.osim')]
        def candidate(paths):
            if len(paths) != 1:
                return None  # Let the user choose when there are multiple models/trials.
            path = (self.output_dir / paths[0]).resolve()
            return path if path.is_relative_to(self.output_dir.resolve()) else None
        self._gaitdynamics_dialog = GaitDynamicsDialog(candidate(motions), candidate(models), self,
                                                     python=Path(self.python.text()) if self.python.text().strip() else None)
        self._gaitdynamics_dialog.show()

    def closeEvent(self, event):
        if self.busy:
            self.status.setText('Wacht tot de controle of analyse is afgerond voordat je dit venster sluit.')
            event.ignore()
            return
        super().closeEvent(event)
