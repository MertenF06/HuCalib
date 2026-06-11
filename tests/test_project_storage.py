from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from mocap_app.ui.main_window import (
    CURRENT_CALIBRATION_FILENAME,
    MainWindow,
    PROJECT_FILES_DIRNAME,
    PROJECT_RESULTS_DIRNAME,
    PROJECT_VIDEOS_DIRNAME,
)


class ProjectStorageTests(TestCase):
    def _window_for_paths(self, root: Path, project: Path | None):
        window = MainWindow.__new__(MainWindow)
        window._config = SimpleNamespace(
            app_root=root,
            calibration_dir=root / "Projecten",
            results_dir=root / "Resultaten",
        )
        window._active_project_dir = project
        return window

    def test_project_directories_are_created_inside_project(self) -> None:
        with TemporaryDirectory() as temporary:
            project = Path(temporary) / "Project A"
            project.mkdir()

            MainWindow._ensure_project_directories(project)

            self.assertTrue((project / PROJECT_FILES_DIRNAME).is_dir())
            self.assertTrue((project / PROJECT_RESULTS_DIRNAME).is_dir())
            self.assertTrue((project / PROJECT_VIDEOS_DIRNAME).is_dir())

    def test_active_project_scopes_all_default_output_paths(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "Project A"
            window = self._window_for_paths(root, project)

            self.assertEqual(window._project_files_dir(), project / PROJECT_FILES_DIRNAME)
            self.assertEqual(window._project_results_dir(), project / PROJECT_RESULTS_DIRNAME)
            self.assertEqual(window._project_videos_dir(), project / PROJECT_VIDEOS_DIRNAME)
            self.assertEqual(
                window._active_calibration_path(),
                project / PROJECT_FILES_DIRNAME / CURRENT_CALIBRATION_FILENAME,
            )

    def test_recording_folder_name_does_not_reuse_existing_folder(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "rec_20260611_120000").mkdir()
            (base / "rec_20260611_120000_2").mkdir()

            result = MainWindow._next_recording_output_dir(base, "20260611_120000")

            self.assertEqual(result, base / "rec_20260611_120000_3")

    def test_project_name_sanitization_removes_invalid_and_trailing_characters(self) -> None:
        self.assertEqual(MainWindow._sanitize_project_name('  Project<>:"/\\|?*...  '), "Project")

    def test_existing_empty_project_directory_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            existing = parent / "Nieuw Project"
            existing.mkdir()
            window = MainWindow.__new__(MainWindow)
            warnings: list[str] = []
            window._video_recorder = None
            window._prompt_new_project = lambda: ("Nieuw Project", parent)
            window._show_warning = warnings.append

            MainWindow._on_new_project(window)

            self.assertEqual(len(warnings), 1)
            self.assertIn("bestaat al", warnings[0])
            self.assertEqual(list(existing.iterdir()), [])
