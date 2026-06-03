"""Serialize a calibration payload to JSON or TOML for preview/export.

The TOML writer is self-contained (no third-party dependency) and only needs to
handle the calibration payload shape: nested dicts (tables), lists of dicts
(arrays of tables), lists of scalars/lists (inline arrays) and scalars. ``None``
values are dropped because TOML has no null.
"""
from __future__ import annotations

import json
import re
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)


def to_toml(payload: dict[str, Any]) -> str:
    cleaned = _strip_none(payload)
    if not isinstance(cleaned, dict):
        raise TypeError("TOML export expects a mapping at the top level.")
    return "\n".join(_dump_table(cleaned, [])).strip() + "\n"


def build_motion_capture_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Build an aniposelib/Anipose-compatible calibration mapping.

    Each usable camera becomes a ``[cam_N]`` table with ``name``, ``size``,
    ``matrix``, ``distortions``, ``rotation`` (a 3-element Rodrigues vector) and
    ``translation``. Cameras without solved intrinsics and extrinsics are
    skipped, since they cannot be used for triangulation/motion analysis.
    """
    import numpy as np
    import cv2

    cameras = payload.get("cameras", {})
    result: dict[str, Any] = {}
    skipped: list[str] = []
    errors: list[float] = []
    index = 0
    for source_id in sorted(cameras.keys()):
        camera = cameras[source_id]
        intrinsics = camera.get("intrinsics")
        distortion = camera.get("distortion")
        rotation = camera.get("rotation")
        translation = camera.get("translation")
        size = camera.get("image_size")
        if not (intrinsics and distortion and rotation and translation and size):
            skipped.append(str(source_id))
            continue
        rotation_array = np.asarray(rotation, dtype=float).reshape(-1)
        if rotation_array.size == 9:
            rvec, _ = cv2.Rodrigues(rotation_array.reshape(3, 3))
            rotation_vec = [float(value) for value in rvec.reshape(3)]
        elif rotation_array.size == 3:
            rotation_vec = [float(value) for value in rotation_array]
        else:
            skipped.append(str(source_id))
            continue
        result[f"cam_{index}"] = {
            "name": str(camera.get("source_id", source_id)),
            "size": [int(size[0]), int(size[1])],
            "matrix": [[float(value) for value in row] for row in intrinsics],
            "distortions": [float(value) for value in distortion],
            "rotation": rotation_vec,
            "translation": [float(value) for value in translation],
        }
        error = camera.get("reprojection_error")
        if isinstance(error, (int, float)):
            errors.append(float(error))
        index += 1

    metadata: dict[str, Any] = {"adjusted": False}
    if errors:
        metadata["error"] = float(sum(errors) / len(errors))
    result["metadata"] = metadata
    return result, skipped


def to_motion_capture_toml(payload: dict[str, Any]) -> tuple[str, list[str], int]:
    """Return (toml_text, skipped_source_ids, included_camera_count)."""
    mapping, skipped = build_motion_capture_payload(payload)
    included = sum(1 for key in mapping if key != "metadata")
    header = [
        "# Calibration exported by HuCalib for motion analysis "
        "(aniposelib/Anipose-compatible).",
        "# rotation = Rodrigues vector (3 elements); translation in calibration board "
        "units (meters); size = [width, height].",
    ]
    if skipped:
        header.append("# Skipped cameras without solved extrinsics: " + ", ".join(skipped))
    text = "\n".join(header) + "\n\n" + to_toml(mapping)
    return text, skipped, included


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value if item is not None]
    return value


def _format_key(key: Any) -> str:
    text = str(key)
    return text if _BARE_KEY.match(text) else _format_string(text)


def _format_string(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf are invalid in TOML
            return _format_string(str(value))
        return repr(value)
    if isinstance(value, str):
        return _format_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_scalar(item) for item in value) + "]"
    raise TypeError(f"Cannot serialize value of type {type(value).__name__} to TOML.")


def _is_array_of_tables(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(item, dict) for item in value)


def _dump_table(table: dict[str, Any], prefix: list[str]) -> list[str]:
    lines: list[str] = []
    simple: list[tuple[str, Any]] = []
    sub_tables: list[tuple[str, Any]] = []
    array_tables: list[tuple[str, Any]] = []

    for key, value in table.items():
        if isinstance(value, dict):
            sub_tables.append((key, value))
        elif _is_array_of_tables(value):
            array_tables.append((key, value))
        else:
            simple.append((key, value))

    if prefix and (simple or (not sub_tables and not array_tables)):
        lines.append(f"[{'.'.join(prefix)}]")
    for key, value in simple:
        lines.append(f"{_format_key(key)} = {_format_scalar(value)}")

    for key, value in sub_tables:
        path = prefix + [_format_key(key)]
        if simple or lines:
            lines.append("")
        lines.extend(_dump_table(value, path))

    for key, value in array_tables:
        path = prefix + [_format_key(key)]
        for item in value:
            lines.append("")
            lines.append(f"[[{'.'.join(path)}]]")
            inner = _dump_table(item, path)
            # Drop the duplicate header line that _dump_table would add for the item.
            lines.extend(line for line in inner if not line.startswith(f"[{'.'.join(path)}]"))
    return lines
