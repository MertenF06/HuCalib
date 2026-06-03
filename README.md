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

PhysioMotionTracker is a student project that provides a streamlined workflow for calibrating camera systems used in physiotherapy research. The program does not analyse movement itself — calibration ensures that recordings are accurate enough to be processed by external software.

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
conda create -n HUmocap-env python=3.12
```

**2. Activate the environment**
```bash
conda activate HUmocap-env
```

**3. Clone the repository**
```bash
git clone https://github.com/MaxUntersalmberger/PhysioMotionTracker
```

**4. Install the required libraries**
```bash
pip install -r "PhysioMotionTracker/Calibratie Programma/requirements.txt"
```

**5. Launch the GUI**
```bash
python PhysioMotionTracker/GUI/guiMain.py
```

The GUI will open automatically in a new window.

---

## Usage

### Main screen
On the main screen you can create a new project or open an existing one.

### Selecting cameras
Select the desired cameras via the Camera's/calibration menu. Make sure the cameras are properly connected and recognised by the system.

### Calibration
There are two different sequences when calibrating the camera's. First you calibrate the intrinsic parameters. This means that you calibrate each camera one at a time. Then you calibrate the extrinsic parameters. In this step you calibrate multiple camera's at the same time.
Follow the calibration steps in the GUI. Afterwards, the camera parameters are saved in the project and ready for use in external software. 

---

## Contributing

This project was developed by students at Hogeschool Utrecht. Contributions are welcome! Open an [issue](https://github.com/MaxUntersalmberger/PhysioMotionTracker/issues) or submit a pull request.

---

> 💡 *This project was built by and for students as part of a degree programme at Hogeschool Utrecht.*
