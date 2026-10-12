"""Exercise actual QProcess checks/analysis and capture the actual Qt interface."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from mocap_app.ui.analysis_dialog import AnalysisDialog


def wait_idle(app, dialog, timeout=600):
    deadline = time.monotonic() + timeout
    while dialog.busy:
        app.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError('GUI analysis did not finish')
        time.sleep(.01)
    app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output-base', type=Path, required=True)
    parser.add_argument('--screenshots', type=Path, required=True)
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    args.output_base.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    dialog = AnalysisDialog(args.output_base / 'gui-test-settings.ini', args.session, args.output_base)
    dialog.python.setText(str(args.python))
    dialog.config.setText(str(args.config))
    dialog.show()
    pulses = [0]
    heartbeat = QTimer()
    heartbeat.setInterval(25)
    heartbeat.timeout.connect(lambda: pulses.__setitem__(0, pulses[0] + 1))
    heartbeat.start()
    # Original demo mismatch must be blocked by the normal GUI path.
    dialog._check()
    wait_idle(app, dialog)
    assert not dialog.checked and not dialog.start_button.isEnabled()
    assert 'resoluties' in dialog.messages.toPlainText()
    dialog.diagnostic.setChecked(True)
    dialog._check()
    wait_idle(app, dialog)
    assert dialog.checked and dialog.start_button.isEnabled(), dialog.messages.toPlainText()
    assert pulses[0] > 10, 'UI event loop did not keep responding during validation'
    dialog.grab().save(str(args.screenshots / 'analyse-klaar.png'))
    dialog._start()
    assert not dialog.start_button.isEnabled() and not dialog.close_button.isEnabled()
    app.processEvents()
    dialog.grab().save(str(args.screenshots / 'analyse-bezig.png'))
    wait_idle(app, dialog)
    assert dialog.report['status'] == 'completed', dialog.messages.toPlainText()
    assert dialog.progress.value() == 7
    assert dialog.graph_button.isEnabled()
    dialog.grab().save(str(args.screenshots / 'analyse-voltooid.png'))
    dialog._open_graph()
    view = dialog._results_dialog
    assert view.coordinate.count() == 62
    view.show()
    app.processEvents()
    for index in range(view.coordinate.count()):
        view.coordinate.setCurrentIndex(index)
        app.processEvents()
        assert view.plot.name == view.result.names[index]
    view.coordinate.setCurrentIndex(view.coordinate.findData('knee_angle_r'))
    app.processEvents()
    view.grab().save(str(args.screenshots / 'bewegingsresultaten.png'))
    summary = {'status': 'passed', 'analysis_output': str(dialog.output_dir),
               'normal_demo_check_blocked': True, 'diagnostic_check_passed': True,
               'completed_stages': dialog.progress.value(), 'selectable_coordinates': view.coordinate.count(),
               'event_loop_heartbeat_count': pulses[0]}
    (args.screenshots / 'gui-test-result.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    heartbeat.stop()
    view.close()
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


if __name__ == '__main__':
    main()
