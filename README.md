# HuCalib

A camera calibration tool for physiotherapy-related motion capture setups, built with Python. The system provides a user-friendly GUI for calibrating multiple cameras in preparation for movement recordings.

> **Planned feature:** A future version will support video recording of movements. These recordings will not be analysed by the program itself, but can be exported to external tools such as [Pose2Sim](https://github.com/perfanalytics/pose2sim) for further processing.

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Contributing](#contributing)

---

## Overview

HuCalib is a student project that provides a streamlined workflow for calibrating camera systems used in physiotherapy research. The program does not analyse movement itself — calibration ensures that recordings are accurate enough to be processed by external software.

---

## Features

- 🔧 **Calibration tool** for accurate camera setup
- 🖥️ **User-friendly GUI** for easy operation
- 📁 **Project management** – create new projects or open existing ones
- 🔜 **Video recording** *(planned)* – recordings exportable to tools like Pose2Sim

---

## Installation

Open an Anaconda-enabled command prompt and follow the steps below:

**1. Create a Python environment**
```bash
conda create -n HuCalib-env python=3.12
```

**2. Activate the environment**
```bash
conda activate HuCalib-env
```

**3. Clone the repository**
```bash
git clone https://github.com/MertenF06/HuCalib
```

**4. Install the required libraries**
```bash
pip install -r HuCalib/requirements.txt
```

**5. Launch the GUI**
```bash
python HuCalib/run.py
```

The GUI will open automatically in a new window.

---

## Usage

### 1. Start a project
On the **Home** screen, click **Nieuw Project** to start a new calibration, or **Project Openen** to continue an existing one.

### 2. Connect the cameras
Connect your webcams before or while the app is running. On startup HuCalib automatically scans for cameras, opens every camera it finds, and shows a live preview of each on the **Camera's / Kalibratie** tab. Use the **Camera's zoeken** button to rescan if you plug in a camera later.

### 3. Calibrate
You need a printed calibration board — a **chessboard** by default; **ChArUco** is also supported. The board dimensions and square size can be changed on the **Geavanceerde instellingen** tab.

With the default settings, a single **Start kalibratie** button runs the whole sequence automatically:

1. **Intrinsics** – each camera is calibrated on its own. Move the board slowly so it is seen across the whole image of every camera; the on-screen grid fills up as enough views are captured.
2. **Extrinsics** – the cameras are calibrated relative to each other. Hold the board so that **several cameras see it at the same time**, until every camera is connected to the reference camera.

The app then switches to the **Resultaten / Export** tab, which shows a plain-language pass/fail verdict for the calibration.

### 4. Export
On the **Resultaten / Export** tab, choose a format (**TOML** or **JSON**) and click **Export** to save the camera parameters for use in external tools such as [Pose2Sim](https://github.com/perfanalytics/pose2sim).

---

## Contributing

This project was developed by students at Hogeschool Utrecht. Contributions are welcome! Open an [issue](https://github.com/MertenF06/HuCalib/issues) or submit a pull request.

---

> 💡 *This project was built by and for students as part of a degree programme at Hogeschool Utrecht.*
