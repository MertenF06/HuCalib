# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller HuCalib.spec --noconfirm
# Produces a one-folder build at dist/HuCalib/ (HuCalib.exe + its
# dependencies). This onedir layout is required by Velopack, which packages
# that folder into an installer and self-updating release (see UPDATING.md and
# build_release.ps1). On first launch the app creates its calibration/, logs/
# and sessions/ folders next to the executable.

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    # Bundle the GUI images/icons; ui/gui.py loads them relative to its module.
    datas=[('ui/imagesGUI', 'ui/imagesGUI')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The app only uses QtCore/QtGui/QtWidgets, so drop the heavy unused Qt
    # modules (and other libs PyInstaller pulls in by default) to shrink the exe.
    excludes=[
        'tkinter',
        'matplotlib',
        # Heavy scientific libs that get pulled in transitively but are unused
        # by HuCalib (it only needs numpy, scipy, cv2 and PySide6).
        'torch',
        'torchvision',
        'torchaudio',
        'pandas',
        'numba',
        'llvmlite',
        'IPython',
        'jedi',
        'sympy',
        'PIL',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngine',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQml',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DExtras',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtDesigner',
        'PySide6.QtTest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir: binaries/datas are collected below
    name='HuCalib',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='ui/imagesGUI/hucalib_cube_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='HuCalib',
)
