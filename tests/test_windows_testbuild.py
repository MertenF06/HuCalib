import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mocap_app.core.testbuild import is_test_build
from mocap_app.core.updater import updates_supported
from tools.prepare_windows_demo import HASHES, prepare_demo


class TestBuildTests(unittest.TestCase):
    def test_test_package_has_no_updates_but_production_keeps_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'HuCalib.exe'
            with patch('sys.frozen', True, create=True), patch('sys.executable', str(executable)):
                self.assertFalse(is_test_build())
                self.assertTrue(updates_supported())
                (executable.parent / 'TESTBUILD.json').write_text('{}')
                self.assertTrue(is_test_build())
                self.assertFalse(updates_supported())

    def test_source_checkout_does_not_become_a_test_package(self):
        with patch('sys.frozen', False, create=True):
            self.assertFalse(is_test_build())
            self.assertFalse(updates_supported())

    def test_demo_checksum_mismatch_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            (source / 'videos').mkdir(parents=True)
            (source / 'videos/cam01.mp4').write_bytes(b'wrong demo')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                prepare_demo(root / 'output', source)
            self.assertFalse((root / 'output/opname').exists())

    def test_previous_demo_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(FileExistsError):
                prepare_demo(root)

    def test_pinned_hashes_and_calibration_fixture(self):
        self.assertEqual(len(HASHES), 4)
        self.assertTrue(all(len(value) == 64 for value in HASHES.values()))
        fixture = Path(__file__).resolve().parents[1] / 'testdata/pose2sim-demo/Calib_qualisys.toml'
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         '4d2ac5ffc43224b6d8704038d73cc2ce576e4c2857375a2f0dc8535e508935f8')


if __name__ == '__main__':
    unittest.main()
