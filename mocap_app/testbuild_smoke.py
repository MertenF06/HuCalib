"""Exercise the frozen GUI without webcams, updates or an analysis install."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer
from PySide6.QtWidgets import QApplication

from mocap_app.core.config import AppConfig
from mocap_app.core.updater import updates_supported
from mocap_app.ui.designed_main_window import DesignedMainWindow
from mocap_app.ui.analysis_dialog import MotionResultsDialog
from mocap_app.ui.gaitdynamics_dialog import GaitDynamicsDialog
from mocap_app.ui.gaitdynamics_setup_dialog import GaitDynamicsSetupDialog
from mocap_app.ui.gaitdynamics_run_dialog import GaitDynamicsRunDialog
from mocap_app.analysis.gaitdynamics_wsl import payload_files, payload_id


def run_smoke(report_path: str) -> int:
    report = Path(report_path).absolute()
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    result = {'status': 'running', 'frozen': bool(getattr(sys, 'frozen', False))}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = AppConfig(app_root=root, calibration_dir=root / 'Projecten',
                           results_dir=root / 'Resultaten', logs_dir=root / 'logs', sessions_dir=root / 'sessions')
        config.ensure_directories()
        with patch.object(DesignedMainWindow, '_start_initial_camera_probe'), patch.object(AppConfig, 'save'):
            window = DesignedMainWindow(config)
            window.show()
            def check():
                try:
                    assert not updates_supported(), 'Test package must not contact the production update feed'
                    assert 'HU3D kinematics' in window.windowTitle()
                    for width, height in ((640, 480), (864, 500)):
                        window.resize(width, height)
                        app.processEvents()
                        assert window.width() <= width and window.height() <= height
                    window.actionAnalyzeRecording.trigger()
                    dialog = window._analysis_dialog
                    assert dialog.isVisible()
                    assert not dialog.start_button.isEnabled()
                    for width, height in ((640, 480), (864, 500)):
                        dialog.resize(width, height)
                        app.processEvents()
                        app.processEvents()
                        assert dialog.width() <= width and dialog.height() <= height
                        for button in dialog._action_buttons:
                            position = button.mapTo(dialog, QPoint(0, 0))
                            assert dialog.rect().contains(position + button.rect().bottomRight())
                    runtime = (Path(sys._MEIPASS) / 'analysis_runtime' if result['frozen']
                               else Path(__file__).resolve().parents[1])
                    assert (runtime / 'mocap_app/analysis/pose2sim_worker.py').is_file()
                    assert (runtime / 'mocap_app/analysis/model_conversion.py').is_file()
                    assert len(payload_id()) == 64
                    assert all(path.is_file() for path in payload_files()[1:])
                    assert hasattr(window, 'actionInstallGaitDynamics')
                    window.actionGaitDynamicsFiles.trigger()
                    app.processEvents()
                    existing_files = window._gait_files_dialog
                    assert existing_files.isVisible()
                    assert not existing_files.convert_button.isEnabled()
                    assert not existing_files.motion.text() and not existing_files.model.text()
                    existing_files.close()
                    sample = root / 'example.mot'
                    sample.write_text('nRows=2\nnColumns=2\ninDegrees=yes\nendheader\ntime knee_angle_r\n0 10\n.1 20\n')
                    view = MotionResultsDialog(sample, dialog)
                    view.show()
                    view.resize(640, 480)
                    app.processEvents()
                    assert view.coordinate.currentData() == 'knee_angle_r'
                    view.close()
                    sample_model = root / 'example.osim'
                    sample_model.write_text('<OpenSimDocument><Model><JointSet/></Model></OpenSimDocument>')
                    gait = GaitDynamicsDialog(sample, sample_model, dialog)
                    gait.show()
                    gait._check()
                    assert gait.report is not None and not gait.report['compatible']
                    assert gait.save_button.isEnabled()
                    for width, height in ((640, 480), (864, 500)):
                        gait.resize(width, height)
                        app.processEvents()
                        assert gait.width() <= width and gait.height() <= height
                        for button in (gait.convert_button, gait.check_button, gait.save_button, gait.close_button):
                            position = button.mapTo(gait, QPoint(0, 0))
                            assert gait.rect().contains(position + button.rect().bottomRight())
                    gait.close()
                    for view, buttons in (
                        (GaitDynamicsSetupDialog(dialog), ('check', 'install', 'update_button', 'close_button')),
                        (GaitDynamicsRunDialog(sample, sample_model, dialog),
                         ('install_button', 'start_button', 'view_button', 'folder_button', 'close_button'))):
                        view.show()
                        for point_size in (10, 15):
                            font = view.font()
                            font.setPointSize(point_size)
                            view.setFont(font)
                            view.resize(640, 480)
                            app.processEvents()
                            assert view.width() <= 640 and view.height() <= 480
                            for name in buttons:
                                button = getattr(view, name)
                                position = button.mapTo(view, QPoint(0, 0))
                                assert view.rect().contains(position + button.rect().bottomRight())
                        view.close()
                    result.update(status='passed', analysis_dialog_opens=True,
                                  analysis_runtime_present=True, automatic_updates_disabled=True, graph_viewer_opens=True,
                                  small_screen_actions_visible=True, product_name='HU3D kinematics',
                                  gaitdynamics_input_dialog_opens=True, model_conversion_runtime_present=True,
                                  model_conversion_controls_fit_small_screen=True,
                                  wsl_setup_payload_present=True, wsl_dialogs_fit_small_screen=True,
                                  existing_gait_files_menu_opens=True,
                                  wsl_installed_during_smoke=False)
                except Exception as error:
                    result.update(status='failed', error=repr(error))
                finally:
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
                    if window._analysis_dialog is not None:
                        window._analysis_dialog.close()
                    window.close()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.exit(0 if result['status'] == 'passed' else 1)
            QTimer.singleShot(500, check)
            return app.exec()
