"""Read OpenSim motion results without importing Pose2Sim or OpenSim."""
from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np

from mocap_app.analysis.pose2sim import summarize_result

LABELS = {
    'pelvis_tilt': 'Bekkenkanteling', 'pelvis_list': 'Bekkenzijwaartse kanteling',
    'pelvis_rotation': 'Bekkenrotatie', 'pelvis_tx': 'Bekkenverplaatsing X',
    'pelvis_ty': 'Bekkenverplaatsing Y', 'pelvis_tz': 'Bekkenverplaatsing Z',
    'hip_flexion': 'Heupflexie', 'hip_adduction': 'Heupadductie', 'hip_rotation': 'Heuprotatie',
    'knee_angle': 'Kniehoek', 'knee_angle_beta': 'Gekoppelde kniecoördinaat',
    'ankle_angle': 'Enkelhoek', 'subtalar_angle': 'Onderste spronggewricht', 'mtp_angle': 'Teenbuiging',
    'neck_flexion': 'Nekflexie', 'neck_bending': 'Nekzijbuiging', 'neck_rotation': 'Nekrotatie',
    'arm_flex': 'Schouderflexie', 'arm_add': 'Schouderadductie', 'arm_rot': 'Schouderrotatie',
    'elbow_flex': 'Elleboogflexie', 'pro_sup': 'Onderarmrotatie',
    'wrist_flex': 'Polsflexie', 'wrist_dev': 'Polszijwaartse beweging',
}


def coordinate_label(name: str) -> str:
    force = re.fullmatch(r'force([12])_v([xyz])', name)
    if force:
        axis = {'x': 'X', 'y': 'Y (verticaal)', 'z': 'Z'}[force[2]]
        return f'Grondreactiekracht {"rechts" if force[1] == "1" else "links"} — {axis}'
    side = ' links' if name.endswith('_l') else ' rechts' if name.endswith('_r') else ''
    base = name[:-2] if side else name
    if base in LABELS:
        return LABELS[base] + side
    match = re.fullmatch(r'(L\d|T\d+)_(S\d|L\d|T\d+)_(Flex_Ext|Lat_Bending|axial_rotation)', base)
    if match:
        movement = {'Flex_Ext': 'buiging', 'Lat_Bending': 'zijbuiging', 'axial_rotation': 'rotatie'}[match[3]]
        return f'Rug {match[1]}–{match[2]}: {movement}'
    if base.startswith('Abs_'):
        return 'Gekoppelde rompcoördinaat ' + base[4:]
    return 'Modelcoördinaat ' + name


@dataclass
class MotionResult:
    names: list[str]
    values: np.ndarray
    in_degrees: bool

    @classmethod
    def read(cls, path: Path) -> 'MotionResult':
        summary = summarize_result(path)
        lines = path.read_text(encoding='utf-8').splitlines()
        end = next(i for i, line in enumerate(lines) if line.strip() == 'endheader')
        return cls(lines[end + 1].split()[1:],
                   np.array([[float(v) for v in row.split()] for row in lines[end + 2:] if row.strip()]),
                   summary['in_degrees'])

    def unit(self, name: str) -> str:
        if re.fullmatch(r'force[12]_v[xyz]', name):
            return 'N'
        if re.fullmatch(r'force[12]_p[xyz]', name):
            return 'meter'
        if re.fullmatch(r'torque[12]_[xyz]', name):
            return 'N·m'
        if name in ('pelvis_tx', 'pelvis_ty', 'pelvis_tz', 'Abs_t1', 'Abs_t2'):
            return 'meter'
        return 'graden' if self.in_degrees else 'radialen'
