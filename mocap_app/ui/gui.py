# -*- coding: utf-8 -*-
"""HuCalib designed UI - ported to PySide6.

Widget object names and the page indices below are part of the public
contract with DesignedMainWindow (designed_main_window.py). Keep them
stable when editing.

Page index map (stackedWidget):
    0 -> page_home
    1 -> page_cameras
    2 -> page_results
    3 -> page_directory
    4 -> page_diagnostics
    5 -> page_advanced_settings
"""

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets


IMAGES_DIR = Path(__file__).resolve().parent / "imagesGUI"


def _load_pixmap(name: str) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(str(IMAGES_DIR / name))
    return pixmap


class Ui_MainWindow(object):
    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(1280, 800)
        MainWindow.setMinimumSize(QtCore.QSize(960, 600))

        icon = QtGui.QIcon()
        icon.addPixmap(_load_pixmap("hucalib_cube_icon.ico"), QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
        MainWindow.setWindowIcon(icon)

        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")

        self.gridLayout = QtWidgets.QGridLayout(self.centralwidget)
        self.gridLayout.setObjectName("gridLayout")
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.gridLayout.setSpacing(0)

        # ----- Left side: navigation (frame_menu) ------------------------------
        self.frame_menu = QtWidgets.QFrame(self.centralwidget)
        self.frame_menu.setObjectName("frame_menu")
        size_policy = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.frame_menu.setSizePolicy(size_policy)
        self.frame_menu.setMinimumWidth(190)
        self.frame_menu.setMaximumWidth(220)
        self.frame_menu.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.verticalLayout = QtWidgets.QVBoxLayout(self.frame_menu)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setContentsMargins(12, 18, 12, 18)
        self.verticalLayout.setSpacing(8)

        self.label_logo = QtWidgets.QLabel(self.frame_menu)
        self.label_logo.setObjectName("label_logo")
        self.label_logo.setMinimumHeight(110)
        self.label_logo.setMaximumHeight(150)
        self.label_logo.setText("")
        logo_pixmap = _load_pixmap("Nieuw Logo 4.png")
        if not logo_pixmap.isNull():
            self.label_logo.setPixmap(logo_pixmap)
        self.label_logo.setScaledContents(True)
        self.label_logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.verticalLayout.addWidget(self.label_logo)

        self.verticalLayout.addSpacing(8)

        nav_button_specs = [
            ("btn_home", "Home"),
            ("btn_cameras", "Camera's /\nKalibratie"),
            ("btn_results", "Resultaten /\nExport"),
            ("btn_directory", "Bestanden"),
            ("btn_diagnostics", "Diagnostiek"),
            ("btn_advanced_settings", "Geavanceerde\ninstellingen"),
        ]
        for object_name, text in nav_button_specs:
            button = QtWidgets.QPushButton(self.frame_menu)
            button.setObjectName(object_name)
            button.setText(text)
            button.setMinimumHeight(46)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setProperty("nav", True)
            setattr(self, object_name, button)
            self.verticalLayout.addWidget(button)

        self.verticalLayout.addStretch(1)

        self.gridLayout.addWidget(self.frame_menu, 0, 0, 2, 1)

        # ----- Right side: content (frame_pages) -------------------------------
        self.frame_pages = QtWidgets.QFrame(self.centralwidget)
        self.frame_pages.setObjectName("frame_pages")
        self.frame_pages.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        pages_layout = QtWidgets.QGridLayout(self.frame_pages)
        pages_layout.setObjectName("gridLayout_2")
        pages_layout.setContentsMargins(14, 14, 14, 8)
        pages_layout.setSpacing(0)

        self.stackedWidget = QtWidgets.QStackedWidget(self.frame_pages)
        self.stackedWidget.setObjectName("stackedWidget")

        # Page: Home
        self._build_page_home()
        # Page: Cameras
        self._build_page_cameras()
        # Page: Results
        self._build_page_results()
        # Page: Directory
        self._build_page_directory()
        # Page: Diagnostics
        self._build_page_diagnostics()
        # Page: Advanced settings
        self._build_page_advanced_settings()

        pages_layout.addWidget(self.stackedWidget, 0, 0, 1, 1)
        self.gridLayout.addWidget(self.frame_pages, 0, 1, 1, 1)

        # ----- Bottom: console panel ------------------------------------------
        self.frame_console = QtWidgets.QFrame(self.centralwidget)
        self.frame_console.setObjectName("frame_console")
        self.frame_console.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        console_layout = QtWidgets.QVBoxLayout(self.frame_console)
        console_layout.setContentsMargins(14, 0, 14, 12)
        console_layout.setSpacing(6)

        console_header = QtWidgets.QHBoxLayout()
        console_header.setContentsMargins(0, 0, 0, 0)
        self.lab_console_title = QtWidgets.QLabel("Console")
        self.lab_console_title.setObjectName("lab_console_title")
        self.lab_console_title.setProperty("section", True)
        console_header.addWidget(self.lab_console_title)
        console_header.addStretch(1)
        self.lab_console_hint = QtWidgets.QLabel(
            "Probeer:  help  |  capture intrinsics 0  |  capture extrinsics  |  home / cameras / results"
        )
        self.lab_console_hint.setObjectName("lab_console_hint")
        self.lab_console_hint.setProperty("hint", True)
        console_header.addWidget(self.lab_console_hint)
        console_layout.addLayout(console_header)

        self.plaintextedit_console = QtWidgets.QPlainTextEdit(self.frame_console)
        self.plaintextedit_console.setObjectName("plaintextedit_console")
        self.plaintextedit_console.setMinimumHeight(56)
        self.plaintextedit_console.setReadOnly(True)
        console_layout.addWidget(self.plaintextedit_console, 1)

        self.lineedit_console_input = QtWidgets.QLineEdit(self.frame_console)
        self.lineedit_console_input.setObjectName("lineedit_console_input")
        self.lineedit_console_input.setMinimumHeight(30)
        self.lineedit_console_input.setPlaceholderText(">  type een commando en druk op Enter")
        console_layout.addWidget(self.lineedit_console_input, 0)

        self.gridLayout.addWidget(self.frame_console, 1, 1, 1, 1)

        self.gridLayout.setColumnStretch(0, 0)
        self.gridLayout.setColumnStretch(1, 1)
        self.gridLayout.setRowStretch(0, 1)
        self.gridLayout.setRowStretch(1, 0)

        MainWindow.setCentralWidget(self.centralwidget)

        # ----- Menu bar -------------------------------------------------------
        self.menuBar = QtWidgets.QMenuBar(MainWindow)
        self.menuBar.setObjectName("menuBar")
        self.menuFile = QtWidgets.QMenu(self.menuBar)
        self.menuFile.setObjectName("menuFile")
        # Kept for compatibility with older glue code, but no longer shown in the menu bar.
        self.menuRun = QtWidgets.QMenu(self.menuBar)
        self.menuRun.setObjectName("menuRun")
        self.menuHelp = QtWidgets.QMenu(self.menuBar)
        self.menuHelp.setObjectName("menuHelp")
        MainWindow.setMenuBar(self.menuBar)

        self.actionRun = QtGui.QAction(MainWindow)
        self.actionRun.setObjectName("actionRun")
        self.actionNew_project = QtGui.QAction(MainWindow)
        self.actionNew_project.setObjectName("actionNew_project")
        self.actionOpen_project = QtGui.QAction(MainWindow)
        self.actionOpen_project.setObjectName("actionOpen_project")
        self.actionQuit = QtGui.QAction(MainWindow)
        self.actionQuit.setObjectName("actionQuit")
        self.actionOpen_documentation = QtGui.QAction(MainWindow)
        self.actionOpen_documentation.setObjectName("actionOpen_documentation")
        self.actionInfo = QtGui.QAction(MainWindow)
        self.actionInfo.setObjectName("actionInfo")

        self.menuFile.addAction(self.actionNew_project)
        self.menuFile.addAction(self.actionOpen_project)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionQuit)
        self.menuHelp.addAction(self.actionOpen_documentation)
        self.menuHelp.addAction(self.actionInfo)
        self.menuBar.addAction(self.menuFile.menuAction())
        self.menuBar.addAction(self.menuHelp.menuAction())

        self.statusBar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusBar)

        self.retranslateUi(MainWindow)
        self.stackedWidget.setCurrentIndex(0)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    # ------------------------------------------------------------------ pages

    def _build_page_home(self) -> None:
        self.page_home = QtWidgets.QWidget()
        self.page_home.setObjectName("page_home")

        outer = QtWidgets.QVBoxLayout(self.page_home)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.setSpacing(20)

        self.label_main_text = QtWidgets.QLabel(self.page_home)
        self.label_main_text.setObjectName("label_main_text")
        self.label_main_text.setText("HuCalib")
        self.label_main_text.setProperty("display", True)
        outer.addWidget(self.label_main_text)

        self.label_home_subtitle = QtWidgets.QLabel(self.page_home)
        self.label_home_subtitle.setObjectName("label_home_subtitle")
        self.label_home_subtitle.setText(
            "Start een nieuw kalibratieproject of laad een bestaand project om verder te gaan."
        )
        self.label_home_subtitle.setWordWrap(True)
        self.label_home_subtitle.setProperty("subtitle", True)
        outer.addWidget(self.label_home_subtitle)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(14)

        self.btn_newproject = QtWidgets.QPushButton(self.page_home)
        self.btn_newproject.setObjectName("btn_newproject")
        self.btn_newproject.setText("Nieuw Project")
        self.btn_newproject.setMinimumHeight(48)
        self.btn_newproject.setMinimumWidth(180)
        self.btn_newproject.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.btn_newproject.setProperty("accent", True)
        button_row.addWidget(self.btn_newproject)

        self.btn_loadproject = QtWidgets.QPushButton(self.page_home)
        self.btn_loadproject.setObjectName("btn_loadproject")
        self.btn_loadproject.setText("Project Openen")
        self.btn_loadproject.setMinimumHeight(48)
        self.btn_loadproject.setMinimumWidth(180)
        self.btn_loadproject.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self.btn_loadproject)

        button_row.addStretch(1)
        outer.addLayout(button_row)
        outer.addStretch(1)

        self.stackedWidget.addWidget(self.page_home)

    def _build_page_cameras(self) -> None:
        self.page_cameras = QtWidgets.QWidget()
        self.page_cameras.setObjectName("page_cameras")

        layout = QtWidgets.QVBoxLayout(self.page_cameras)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Top: settings card with FPS, pattern, intrinsics, extrinsics, reset.
        self.frame = QtWidgets.QFrame(self.page_cameras)
        self.frame.setObjectName("frame")
        self.frame.setProperty("card", True)
        self.frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        top_layout = QtWidgets.QGridLayout(self.frame)
        top_layout.setObjectName("gridLayout_9")
        top_layout.setContentsMargins(16, 14, 16, 14)
        top_layout.setHorizontalSpacing(12)
        top_layout.setVerticalSpacing(10)

        self.lab_cap_fps = QtWidgets.QLabel(self.frame)
        self.lab_cap_fps.setObjectName("lab_cap_fps")
        self.lab_cap_fps.setText("FPS")
        top_layout.addWidget(self.lab_cap_fps, 0, 0)

        self.spin_cap_fps = QtWidgets.QSpinBox(self.frame)
        self.spin_cap_fps.setObjectName("spin_cap_fps")
        self.spin_cap_fps.setRange(1, 120)
        self.spin_cap_fps.setValue(30)
        self.spin_cap_fps.setSuffix(" fps")
        top_layout.addWidget(self.spin_cap_fps, 0, 1)

        self.lab_cap_pattern = QtWidgets.QLabel(self.frame)
        self.lab_cap_pattern.setObjectName("lab_cap_pattern")
        self.lab_cap_pattern.setText("Patroon")
        top_layout.addWidget(self.lab_cap_pattern, 1, 0)

        self.combo_cap_pattern = QtWidgets.QComboBox(self.frame)
        self.combo_cap_pattern.setObjectName("combo_cap_pattern")
        self.combo_cap_pattern.addItem("Chessboard", "chessboard")
        self.combo_cap_pattern.addItem("Charuco", "charuco")
        top_layout.addWidget(self.combo_cap_pattern, 1, 1)

        # Intrinsics card
        self.frame_2 = QtWidgets.QFrame(self.frame)
        self.frame_2.setObjectName("frame_2")
        self.frame_2.setProperty("card", True)
        self.frame_2.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        intrinsics_layout = QtWidgets.QVBoxLayout(self.frame_2)
        intrinsics_layout.setContentsMargins(12, 10, 12, 10)
        intrinsics_layout.setSpacing(8)

        self.lab_cap_intrinsics = QtWidgets.QLabel(self.frame_2)
        self.lab_cap_intrinsics.setObjectName("lab_cap_intrinsics")
        self.lab_cap_intrinsics.setText("Intrinsics")
        self.lab_cap_intrinsics.setProperty("section", True)
        intrinsics_layout.addWidget(self.lab_cap_intrinsics)

        self.btn_cap_intrinsics_start = QtWidgets.QPushButton(self.frame_2)
        self.btn_cap_intrinsics_start.setObjectName("btn_cap_intrinsics_start")
        self.btn_cap_intrinsics_start.setText("Start")
        self.btn_cap_intrinsics_start.setCheckable(True)
        self.btn_cap_intrinsics_start.setMinimumHeight(36)
        intrinsics_layout.addWidget(self.btn_cap_intrinsics_start)

        self.btn_cap_calculate_intrinsics = QtWidgets.QPushButton(self.frame_2)
        self.btn_cap_calculate_intrinsics.setObjectName("btn_cap_calculate_intrinsics")
        self.btn_cap_calculate_intrinsics.setText("Berekenen")
        self.btn_cap_calculate_intrinsics.setMinimumHeight(36)
        self.btn_cap_calculate_intrinsics.setProperty("accent", True)
        intrinsics_layout.addWidget(self.btn_cap_calculate_intrinsics)

        top_layout.addWidget(self.frame_2, 2, 0, 1, 2)

        # Extrinsics card
        self.frame_3 = QtWidgets.QFrame(self.frame)
        self.frame_3.setObjectName("frame_3")
        self.frame_3.setProperty("card", True)
        self.frame_3.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        extrinsics_layout = QtWidgets.QVBoxLayout(self.frame_3)
        extrinsics_layout.setContentsMargins(12, 10, 12, 10)
        extrinsics_layout.setSpacing(8)

        self.lab_cap_extrinsics = QtWidgets.QLabel(self.frame_3)
        self.lab_cap_extrinsics.setObjectName("lab_cap_extrinsics")
        self.lab_cap_extrinsics.setText("Extrinsics")
        self.lab_cap_extrinsics.setProperty("section", True)
        extrinsics_layout.addWidget(self.lab_cap_extrinsics)

        self.btn_cap_extrinsics_start = QtWidgets.QPushButton(self.frame_3)
        self.btn_cap_extrinsics_start.setObjectName("btn_cap_extrinsics_start")
        self.btn_cap_extrinsics_start.setText("Start")
        self.btn_cap_extrinsics_start.setCheckable(True)
        self.btn_cap_extrinsics_start.setMinimumHeight(36)
        extrinsics_layout.addWidget(self.btn_cap_extrinsics_start)

        self.btn_cap_calculate_extrinsics = QtWidgets.QPushButton(self.frame_3)
        self.btn_cap_calculate_extrinsics.setObjectName("btn_cap_calculate_extrinsics")
        self.btn_cap_calculate_extrinsics.setText("Berekenen")
        self.btn_cap_calculate_extrinsics.setMinimumHeight(36)
        self.btn_cap_calculate_extrinsics.setProperty("accent", True)
        extrinsics_layout.addWidget(self.btn_cap_calculate_extrinsics)

        top_layout.addWidget(self.frame_3, 2, 2, 1, 2)

        self.btn_cap_reset_calibration = QtWidgets.QPushButton(self.frame)
        self.btn_cap_reset_calibration.setObjectName("btn_cap_reset_calibration")
        self.btn_cap_reset_calibration.setText("Kalibratie resetten")
        self.btn_cap_reset_calibration.setMinimumHeight(34)
        self.btn_cap_reset_calibration.setProperty("danger", True)
        top_layout.addWidget(self.btn_cap_reset_calibration, 3, 0, 1, 4)

        top_layout.setColumnStretch(1, 1)
        top_layout.setColumnStretch(3, 1)
        layout.addWidget(self.frame)

        # Camera grid container
        self.frame_cam = QtWidgets.QFrame(self.page_cameras)
        self.frame_cam.setObjectName("frame_cam")
        self.frame_cam.setProperty("card", True)
        self.frame_cam.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.gridLayout_6 = QtWidgets.QGridLayout(self.frame_cam)
        self.gridLayout_6.setObjectName("gridLayout_6")
        self.gridLayout_6.setContentsMargins(10, 10, 10, 10)
        self.gridLayout_6.setSpacing(8)
        layout.addWidget(self.frame_cam, stretch=1)

        self.stackedWidget.addWidget(self.page_cameras)

    def _build_page_results(self) -> None:
        self.page_results = QtWidgets.QWidget()
        self.page_results.setObjectName("page_results")

        outer = QtWidgets.QVBoxLayout(self.page_results)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stackedWidget_2 = QtWidgets.QStackedWidget(self.page_results)
        self.stackedWidget_2.setObjectName("stackedWidget_2")

        # ----- Page: results tab
        self.page_results_tab = QtWidgets.QWidget()
        self.page_results_tab.setObjectName("page_results_tab")
        page_layout = QtWidgets.QVBoxLayout(self.page_results_tab)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(12)

        # Export bar
        self.frame_4 = QtWidgets.QFrame(self.page_results_tab)
        self.frame_4.setObjectName("frame_4")
        self.frame_4.setProperty("card", True)
        self.frame_4.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        export_layout = QtWidgets.QHBoxLayout(self.frame_4)
        export_layout.setContentsMargins(14, 10, 14, 10)
        export_layout.setSpacing(10)

        self.lab_res_title = QtWidgets.QLabel("Kalibratie Resultaten")
        self.lab_res_title.setProperty("section", True)
        export_layout.addWidget(self.lab_res_title)
        export_layout.addStretch(1)

        self.btn_res_show_tmol = QtWidgets.QPushButton(self.frame_4)
        self.btn_res_show_tmol.setObjectName("btn_res_show_tmol")
        self.btn_res_show_tmol.setText("Preview TOML")
        self.btn_res_show_tmol.setMinimumHeight(32)
        export_layout.addWidget(self.btn_res_show_tmol)

        self.export_toml = QtWidgets.QPushButton(self.frame_4)
        self.export_toml.setObjectName("export_toml")
        self.export_toml.setText("Export TOML")
        self.export_toml.setMinimumHeight(32)
        self.export_toml.setProperty("accent", True)
        export_layout.addWidget(self.export_toml)
        page_layout.addWidget(self.frame_4)

        # Grid: labels + value frames
        grid_holder = QtWidgets.QFrame(self.page_results_tab)
        grid_holder.setObjectName("frame_results_grid")
        grid_holder.setProperty("card", True)
        grid_holder.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        grid = QtWidgets.QGridLayout(grid_holder)
        grid.setObjectName("gridLayout_13")
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        self.lab_res_intrinsics_results = QtWidgets.QLabel("Intrinsics resultaten")
        self.lab_res_intrinsics_results.setObjectName("lab_res_intrinsics_results")
        self.lab_res_intrinsics_results.setProperty("section", True)
        grid.addWidget(self.lab_res_intrinsics_results, 0, 0)

        self.frame_res_intrinsic_results = QtWidgets.QFrame(grid_holder)
        self.frame_res_intrinsic_results.setObjectName("frame_res_intrinsic_results")
        self.frame_res_intrinsic_results.setProperty("value", True)
        intr_layout = QtWidgets.QVBoxLayout(self.frame_res_intrinsic_results)
        intr_layout.setContentsMargins(10, 8, 10, 8)
        self.text_res_intrinsics = QtWidgets.QPlainTextEdit(self.frame_res_intrinsic_results)
        self.text_res_intrinsics.setObjectName("text_res_intrinsics")
        self.text_res_intrinsics.setReadOnly(True)
        self.text_res_intrinsics.setPlaceholderText("Nog geen intrinsics resultaten.")
        intr_layout.addWidget(self.text_res_intrinsics)
        grid.addWidget(self.frame_res_intrinsic_results, 0, 1)

        self.lab_res_extrinsics_results = QtWidgets.QLabel("Extrinsics resultaten")
        self.lab_res_extrinsics_results.setObjectName("lab_res_extrinsics_results")
        self.lab_res_extrinsics_results.setProperty("section", True)
        grid.addWidget(self.lab_res_extrinsics_results, 1, 0)

        self.frame_res_extrinsics_results = QtWidgets.QFrame(grid_holder)
        self.frame_res_extrinsics_results.setObjectName("frame_res_extrinsics_results")
        self.frame_res_extrinsics_results.setProperty("value", True)
        extr_layout = QtWidgets.QVBoxLayout(self.frame_res_extrinsics_results)
        extr_layout.setContentsMargins(10, 8, 10, 8)
        self.text_res_extrinsics = QtWidgets.QPlainTextEdit(self.frame_res_extrinsics_results)
        self.text_res_extrinsics.setObjectName("text_res_extrinsics")
        self.text_res_extrinsics.setReadOnly(True)
        self.text_res_extrinsics.setPlaceholderText("Nog geen extrinsics resultaten.")
        extr_layout.addWidget(self.text_res_extrinsics)
        grid.addWidget(self.frame_res_extrinsics_results, 1, 1)

        self.lab_res_cam_info = QtWidgets.QLabel("Camera info")
        self.lab_res_cam_info.setObjectName("lab_res_cam_info")
        self.lab_res_cam_info.setProperty("section", True)
        grid.addWidget(self.lab_res_cam_info, 2, 0)

        self.frame_res_camera_info = QtWidgets.QFrame(grid_holder)
        self.frame_res_camera_info.setObjectName("frame_res_camera_info")
        self.frame_res_camera_info.setProperty("value", True)
        cam_layout = QtWidgets.QVBoxLayout(self.frame_res_camera_info)
        cam_layout.setContentsMargins(10, 8, 10, 8)
        self.text_res_camera_info = QtWidgets.QPlainTextEdit(self.frame_res_camera_info)
        self.text_res_camera_info.setObjectName("text_res_camera_info")
        self.text_res_camera_info.setReadOnly(True)
        self.text_res_camera_info.setPlaceholderText("Voeg camera's toe om hier informatie te zien.")
        cam_layout.addWidget(self.text_res_camera_info)
        grid.addWidget(self.frame_res_camera_info, 2, 1)

        self.lab_res_frames = QtWidgets.QLabel("Frames")
        self.lab_res_frames.setObjectName("lab_res_frames")
        self.lab_res_frames.setProperty("section", True)
        grid.addWidget(self.lab_res_frames, 3, 0)

        self.frame_res_aantal_frames = QtWidgets.QFrame(grid_holder)
        self.frame_res_aantal_frames.setObjectName("frame_res_aantal_frames")
        self.frame_res_aantal_frames.setProperty("value", True)
        frames_layout = QtWidgets.QVBoxLayout(self.frame_res_aantal_frames)
        frames_layout.setContentsMargins(10, 8, 10, 8)
        self.text_res_frames = QtWidgets.QPlainTextEdit(self.frame_res_aantal_frames)
        self.text_res_frames.setObjectName("text_res_frames")
        self.text_res_frames.setReadOnly(True)
        self.text_res_frames.setPlaceholderText("Geen samples verzameld.")
        frames_layout.addWidget(self.text_res_frames)
        grid.addWidget(self.frame_res_aantal_frames, 3, 1)

        self.lab_res_error = QtWidgets.QLabel("Reprojection error")
        self.lab_res_error.setObjectName("lab_res_error")
        self.lab_res_error.setProperty("section", True)
        grid.addWidget(self.lab_res_error, 4, 0)

        self.frame_res_error = QtWidgets.QFrame(grid_holder)
        self.frame_res_error.setObjectName("frame_res_error")
        self.frame_res_error.setProperty("value", True)
        err_layout = QtWidgets.QVBoxLayout(self.frame_res_error)
        err_layout.setContentsMargins(10, 8, 10, 8)
        self.text_res_error = QtWidgets.QPlainTextEdit(self.frame_res_error)
        self.text_res_error.setObjectName("text_res_error")
        self.text_res_error.setReadOnly(True)
        self.text_res_error.setPlaceholderText("Nog niet beschikbaar.")
        err_layout.addWidget(self.text_res_error)
        grid.addWidget(self.frame_res_error, 4, 1)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 1)
        grid.setRowStretch(3, 1)
        grid.setRowStretch(4, 1)
        page_layout.addWidget(grid_holder, stretch=1)

        self.stackedWidget_2.addWidget(self.page_results_tab)

        # ----- Page: TOML preview
        self.page_preview_TMOL = QtWidgets.QWidget()
        self.page_preview_TMOL.setObjectName("page_preview_TMOL")
        preview_layout = QtWidgets.QVBoxLayout(self.page_preview_TMOL)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(10)

        preview_header = QtWidgets.QHBoxLayout()
        self.label = QtWidgets.QLabel("Preview TOML")
        self.label.setObjectName("label")
        self.label.setProperty("section", True)
        preview_header.addWidget(self.label)
        preview_header.addStretch(1)
        self.pushButton = QtWidgets.QPushButton("✕")
        self.pushButton.setObjectName("pushButton")
        self.pushButton.setMinimumSize(36, 32)
        self.pushButton.setMaximumWidth(60)
        self.pushButton.setToolTip("Terug naar resultaten")
        preview_header.addWidget(self.pushButton)
        preview_layout.addLayout(preview_header)

        self.frame_res_preview_tmol = QtWidgets.QFrame(self.page_preview_TMOL)
        self.frame_res_preview_tmol.setObjectName("frame_res_preview_tmol")
        self.frame_res_preview_tmol.setProperty("card", True)
        self.frame_res_preview_tmol.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        preview_inner = QtWidgets.QVBoxLayout(self.frame_res_preview_tmol)
        preview_inner.setContentsMargins(12, 12, 12, 12)
        self.text_res_preview_tmol = QtWidgets.QPlainTextEdit(self.frame_res_preview_tmol)
        self.text_res_preview_tmol.setObjectName("text_res_preview_tmol")
        self.text_res_preview_tmol.setReadOnly(True)
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        self.text_res_preview_tmol.setFont(font)
        preview_inner.addWidget(self.text_res_preview_tmol)
        preview_layout.addWidget(self.frame_res_preview_tmol, stretch=1)

        self.stackedWidget_2.addWidget(self.page_preview_TMOL)

        outer.addWidget(self.stackedWidget_2)
        self.stackedWidget.addWidget(self.page_results)

    def _build_page_directory(self) -> None:
        self.page_directory = QtWidgets.QWidget()
        self.page_directory.setObjectName("page_directory")
        layout = QtWidgets.QVBoxLayout(self.page_directory)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.frame_directory = QtWidgets.QFrame(self.page_directory)
        self.frame_directory.setObjectName("frame_directory")
        self.frame_directory.setProperty("card", True)
        self.frame_directory.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        layout.addWidget(self.frame_directory, stretch=1)

        self.stackedWidget.addWidget(self.page_directory)

    def _build_page_diagnostics(self) -> None:
        self.page_diagnostics = QtWidgets.QWidget()
        self.page_diagnostics.setObjectName("page_diagnostics")

        holder = QtWidgets.QFrame(self.page_diagnostics)
        holder.setObjectName("frame_diagnostics")
        holder.setProperty("card", True)
        holder.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)

        wrap = QtWidgets.QVBoxLayout(self.page_diagnostics)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(holder)

        grid = QtWidgets.QGridLayout(holder)
        grid.setObjectName("gridLayout_5")
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        diag_specs = [
            ("lab_diag_dropped_frames", "Dropped frames", "text_diag_dropped_frames"),
            ("lab_diag_current_fps", "Huidige FPS", "text_diag_current_fps"),
            ("lab_diag_used_cams", "Camera's in gebruik", "text_diag_used_cams"),
            ("lab_diag_intrinsics_mode_time", "Intrinsics tijd", "text_diag_intrinsics_mode_time"),
            ("lab_diag_extrinsics_mode_time", "Extrinsics tijd", "text_diag_extrinsics_mode_time"),
            ("lab_diag_intrinsics_time", "Intrinsics tijd", "text_diag_Intrinsics_time"),
            ("lab_diag_extrinsics_time", "Extrinsics tijd", "text_diag_extrinsics_time"),
            ("lab_diag_total_time", "Totale tijd", "text_diag_total_time"),
        ]
        for row, (label_name, label_text, text_name) in enumerate(diag_specs):
            label = QtWidgets.QLabel(label_text, holder)
            label.setObjectName(label_name)
            label.setProperty("section", True)
            setattr(self, label_name, label)
            grid.addWidget(label, row, 0)

            text = QtWidgets.QTextEdit(holder)
            text.setObjectName(text_name)
            text.setReadOnly(True)
            text.setMaximumHeight(60)
            text.setProperty("value", True)
            setattr(self, text_name, text)
            grid.addWidget(text, row, 1)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(len(diag_specs), 1)

        self.stackedWidget.addWidget(self.page_diagnostics)

    def _build_page_advanced_settings(self) -> None:
        # This page is only a container: DesignedMainWindow clears its layout
        # on startup and fills it with the advanced-settings forms. The one
        # control built here is ``doubleSpinBox`` (chessboard square size),
        # which DesignedMainWindow re-parents into its own chessboard form.
        self.page_advanced_settings = QtWidgets.QWidget()
        self.page_advanced_settings.setObjectName("page_advanced_settings")

        wrap = QtWidgets.QVBoxLayout(self.page_advanced_settings)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)

        self.doubleSpinBox = QtWidgets.QDoubleSpinBox(self.page_advanced_settings)
        self.doubleSpinBox.setObjectName("doubleSpinBox")
        self.doubleSpinBox.setRange(1.0, 500.0)
        self.doubleSpinBox.setDecimals(2)
        self.doubleSpinBox.setSingleStep(0.5)
        self.doubleSpinBox.setSuffix(" mm")
        self.doubleSpinBox.setValue(24.0)

        self.stackedWidget.addWidget(self.page_advanced_settings)

    def retranslateUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        _t = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_t("MainWindow", "HuCalib"))
        self.menuFile.setTitle(_t("MainWindow", "&Bestand"))
        self.menuRun.setTitle(_t("MainWindow", "Uitvoeren"))
        self.menuHelp.setTitle(_t("MainWindow", "Help"))
        self.actionRun.setText(_t("MainWindow", "Uitvoeren"))
        self.actionNew_project.setText(_t("MainWindow", "Nieuw project"))
        self.actionOpen_project.setText(_t("MainWindow", "Project openen"))
        self.actionQuit.setText(_t("MainWindow", "Afsluiten"))
        self.actionOpen_documentation.setText(_t("MainWindow", "Documentatie openen"))
        self.actionInfo.setText(_t("MainWindow", "Info"))
