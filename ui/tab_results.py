"""Results tab - shows the active calibration bundle and exports TOML."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets


class TabResults:
    def __init__(self, logic_instance) -> None:
        self.logic = logic_instance
        self.window = logic_instance.window
        self._toml_text: str = ""

    def setup(self) -> None:
        self.window.btn_res_show_tmol.clicked.connect(self.go_to_preview)
        self.window.pushButton.clicked.connect(self.go_to_results_tab)
        self.window.export_toml.clicked.connect(self.export_toml_file)
        self.refresh()

    # ----- navigation between results stacked pages -----------------------

    def go_to_preview(self) -> None:
        self.refresh()
        self.window.stackedWidget_2.setCurrentIndex(1)

    def go_to_results_tab(self) -> None:
        self.window.stackedWidget_2.setCurrentIndex(0)

    # ----- rendering ------------------------------------------------------

    def refresh(self) -> None:
        bundle = self._current_bundle()
        if bundle is None:
            self.window.text_res_intrinsics.setPlainText("Nog geen intrinsics opgelost.")
            self.window.text_res_extrinsics.setPlainText("Nog geen extrinsics opgelost.")
            self.window.text_res_camera_info.setPlainText("Voeg camera's toe op de kalibratiepagina.")
            self.window.text_res_frames.setPlainText("Geen samples verzameld.")
            self.window.text_res_error.setPlainText("-")
            self._toml_text = ""
            self.window.text_res_preview_tmol.setPlainText("Nog geen kalibratie om te exporteren.")
            return

        intrinsics_lines: list[str] = []
        extrinsics_lines: list[str] = []
        camera_lines: list[str] = []
        frame_lines: list[str] = []
        errors: list[float] = []

        manager = getattr(self.logic, "calibration_manager", None)
        sample_counts = (
            manager.observations_summary(include_sync_only=False) if manager else {}
        )

        for source_id, camera in bundle.cameras.items():
            camera_lines.append(
                f"{source_id}: status={camera.status}, image="
                f"{camera.image_size[0]}x{camera.image_size[1]}"
                if camera.image_size
                else f"{source_id}: status={camera.status}"
            )
            if camera.intrinsics is not None:
                fx = camera.intrinsics[0][0]
                fy = camera.intrinsics[1][1]
                cx = camera.intrinsics[0][2]
                cy = camera.intrinsics[1][2]
                intrinsics_lines.append(
                    f"{source_id}: fx={fx:.2f} fy={fy:.2f} cx={cx:.2f} cy={cy:.2f}"
                )
                if camera.distortion is not None:
                    dist_preview = ", ".join(f"{value:.4f}" for value in camera.distortion[:5])
                    intrinsics_lines.append(f"  distortion: [{dist_preview}]")
            else:
                intrinsics_lines.append(f"{source_id}: niet opgelost")

            if camera.rotation is not None and camera.translation is not None:
                trans = camera.translation
                extrinsics_lines.append(
                    f"{source_id}: t=({trans[0]:.3f}, {trans[1]:.3f}, {trans[2]:.3f}) m"
                )
            else:
                extrinsics_lines.append(f"{source_id}: geen extrinsics")

            if camera.reprojection_error is not None:
                errors.append(float(camera.reprojection_error))

            frame_lines.append(f"{source_id}: {sample_counts.get(source_id, 0)} samples")

        self.window.text_res_intrinsics.setPlainText("\n".join(intrinsics_lines) or "-")
        self.window.text_res_extrinsics.setPlainText("\n".join(extrinsics_lines) or "-")
        self.window.text_res_camera_info.setPlainText("\n".join(camera_lines) or "-")
        self.window.text_res_frames.setPlainText("\n".join(frame_lines) or "Geen samples")
        if errors:
            mean_err = sum(errors) / len(errors)
            self.window.text_res_error.setPlainText(
                f"Gemiddelde reprojection error: {mean_err:.4f} px\n"
                + "\n".join(
                    f"{source_id}: {camera.reprojection_error:.4f} px"
                    for source_id, camera in bundle.cameras.items()
                    if camera.reprojection_error is not None
                )
            )
        else:
            self.window.text_res_error.setPlainText("-")

        self._toml_text = self._bundle_to_toml(bundle)
        self.window.text_res_preview_tmol.setPlainText(self._toml_text)

    # ----- TOML export ----------------------------------------------------

    def export_toml_file(self) -> None:
        if not self._toml_text:
            QtWidgets.QMessageBox.information(
                self.window,
                "Export",
                "Er is nog geen kalibratie om te exporteren. Solve eerst.",
            )
            return

        config = getattr(self.logic, "config", None)
        default_dir = (
            str(config.calibration_dir)
            if config is not None
            else str(Path.cwd())
        )
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.window,
            "Export calibration as TOML",
            f"{default_dir}/calibration.toml",
            "TOML files (*.toml);;All files (*.*)",
        )
        if not selected:
            return
        try:
            Path(selected).write_text(self._toml_text, encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self.window, "Export mislukt", str(exc))
            return
        self.logic.log_to_console(f"Systeem: TOML geëxporteerd naar {selected}")

    # ----- helpers --------------------------------------------------------

    def _current_bundle(self):
        return getattr(self.logic, "current_bundle", None)

    @staticmethod
    def _bundle_to_toml(bundle) -> str:
        lines: list[str] = []
        lines.append('[metadata]')
        lines.append(f'app = "PhysioMotionTracker"')
        for key, value in bundle.metadata.items():
            if isinstance(value, (int, float)):
                lines.append(f"{key} = {value}")
            elif isinstance(value, str):
                lines.append(f'{key} = "{value}"')
        lines.append("")
        for source_id, camera in bundle.cameras.items():
            lines.append(f'[camera."{source_id}"]')
            lines.append(f'status = "{camera.status}"')
            if camera.image_size:
                lines.append(
                    f"image_size = [{camera.image_size[0]}, {camera.image_size[1]}]"
                )
            if camera.reprojection_error is not None:
                lines.append(f"reprojection_error = {camera.reprojection_error:.6f}")
            if camera.intrinsics is not None:
                lines.append("intrinsics = [")
                for row in camera.intrinsics:
                    lines.append(
                        "  [" + ", ".join(f"{value:.6f}" for value in row) + "],"
                    )
                lines.append("]")
            if camera.distortion is not None:
                lines.append(
                    "distortion = ["
                    + ", ".join(f"{value:.6f}" for value in camera.distortion)
                    + "]"
                )
            if camera.rotation is not None:
                lines.append(
                    "rotation = ["
                    + ", ".join(f"{value:.6f}" for value in camera.rotation)
                    + "]"
                )
            if camera.translation is not None:
                lines.append(
                    "translation = ["
                    + ", ".join(f"{value:.6f}" for value in camera.translation)
                    + "]"
                )
            lines.append("")
        return "\n".join(lines)
