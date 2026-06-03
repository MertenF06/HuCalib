from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

from mocap_app.analysis.session_analysis import SessionAnalysisReport, build_session_analysis
from mocap_app.models.types import (
    CameraSourceConfig,
    FramePacket,
    PipelineResult,
    Pose3D,
    Pose3DKeypoint,
    SessionManifest,
)


LOGGER = logging.getLogger(__name__)
MANIFEST_FILE = "session_manifest.json"
POSE_FILE = "pose3d.ndjson"


def _source_to_dict(source: CameraSourceConfig) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "kind": source.kind,
        "uri": source.uri,
        "label": source.label,
        "enabled": source.enabled,
    }


def _source_from_dict(data: dict[str, Any]) -> CameraSourceConfig:
    return CameraSourceConfig(
        source_id=str(data["source_id"]),
        kind=str(data["kind"]),
        uri=data["uri"],
        label=str(data.get("label", "")),
        enabled=bool(data.get("enabled", True)),
    )


def _manifest_to_dict(manifest: SessionManifest) -> dict[str, Any]:
    return {
        "version": manifest.version,
        "session_id": manifest.session_id,
        "created_at_iso": manifest.created_at_iso,
        "fps": manifest.fps,
        "sources": [_source_to_dict(source) for source in manifest.sources],
        "video_files": manifest.video_files,
        "total_frames": manifest.total_frames,
        "pose_file": manifest.pose_file,
        "calibration_file": manifest.calibration_file,
    }


def _manifest_from_dict(data: dict[str, Any]) -> SessionManifest:
    return SessionManifest(
        version=int(data["version"]),
        session_id=str(data["session_id"]),
        created_at_iso=str(data["created_at_iso"]),
        fps=float(data["fps"]),
        sources=[_source_from_dict(source) for source in data.get("sources", [])],
        video_files={str(key): str(value) for key, value in data.get("video_files", {}).items()},
        total_frames=int(data.get("total_frames", 0)),
        pose_file=str(data["pose_file"]) if data.get("pose_file") else None,
        calibration_file=str(data["calibration_file"]) if data.get("calibration_file") else None,
    )


def _pose3d_to_dict(pose: Pose3D) -> dict[str, Any]:
    return asdict(pose)


def _pose3d_from_dict(data: dict[str, Any]) -> Pose3D:
    payload = data.get("pose3d") if "pose3d" in data else data
    if not isinstance(payload, dict):
        raise ValueError("Invalid pose3d payload in session file.")
    keypoints_data = payload.get("keypoints", [])
    keypoints: list[Pose3DKeypoint] = []
    for item in keypoints_data:
        if not isinstance(item, dict):
            continue
        keypoints.append(
            Pose3DKeypoint(
                name=str(item.get("name", "")),
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                z=float(item.get("z", 0.0)),
                confidence=float(item.get("confidence", 0.0)),
            )
        )
    frame_index = int(payload.get("frame_index", data.get("frame_index", 0)))
    timestamp_sec = float(payload.get("timestamp_sec", data.get("timestamp_sec", 0.0)))
    return Pose3D(frame_index=frame_index, timestamp_sec=timestamp_sec, keypoints=keypoints)


class SessionRecorder:
    def __init__(self) -> None:
        self._session_dir: Path | None = None
        self._manifest: SessionManifest | None = None
        self._writers: dict[str, cv2.VideoWriter] = {}
        self._pose_file_handle = None

    @property
    def active(self) -> bool:
        return self._manifest is not None and self._session_dir is not None

    @property
    def session_dir(self) -> Path | None:
        return self._session_dir

    def start_session(
        self,
        root_dir: Path,
        fps: float,
        sources: list[CameraSourceConfig],
        calibration_file: str | None = None,
    ) -> Path:
        root_dir.mkdir(parents=True, exist_ok=True)
        session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self._session_dir = root_dir / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._manifest = SessionManifest(
            version=1,
            session_id=session_id,
            created_at_iso=datetime.now().isoformat(),
            fps=fps,
            sources=sources,
            video_files={},
            total_frames=0,
            pose_file=POSE_FILE,
            calibration_file=calibration_file,
        )
        pose_path = self._session_dir / POSE_FILE
        self._pose_file_handle = pose_path.open("w", encoding="utf-8")
        LOGGER.info("Recording started: %s", self._session_dir)
        return self._session_dir

    def append_batch(self, frames: dict[str, FramePacket], result: PipelineResult | None = None) -> None:
        if not self.active or self._manifest is None or self._session_dir is None:
            raise RuntimeError("SessionRecorder is not active.")

        for source_id, frame_packet in frames.items():
            writer = self._writers.get(source_id)
            if writer is None:
                height, width = frame_packet.frame_bgr.shape[:2]
                video_name = f"{source_id}.mp4"
                video_path = self._session_dir / video_name
                writer = cv2.VideoWriter(
                    str(video_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    self._manifest.fps,
                    (width, height),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"Could not open video writer for {video_path}")
                self._writers[source_id] = writer
                self._manifest.video_files[source_id] = video_name

            writer.write(frame_packet.frame_bgr)

        self._manifest.total_frames += 1

        if result and result.pose_3d and self._pose_file_handle is not None:
            payload = {
                "frame_index": result.frame_index,
                "timestamp_sec": result.timestamp_sec,
                "pose3d": _pose3d_to_dict(result.pose_3d),
            }
            self._pose_file_handle.write(json.dumps(payload) + "\n")

    def stop_session(self) -> Path | None:
        if not self.active or self._manifest is None or self._session_dir is None:
            return None

        for writer in self._writers.values():
            writer.release()
        self._writers.clear()

        if self._pose_file_handle is not None:
            self._pose_file_handle.close()
            self._pose_file_handle = None

        manifest_path = self._session_dir / MANIFEST_FILE
        manifest_path.write_text(
            json.dumps(_manifest_to_dict(self._manifest), indent=2),
            encoding="utf-8",
        )

        finished_dir = self._session_dir
        LOGGER.info("Recording stopped: %s", finished_dir)

        self._manifest = None
        self._session_dir = None
        return finished_dir


class SessionLoader:
    def load_manifest(self, session_dir: Path) -> SessionManifest:
        manifest_path = session_dir / MANIFEST_FILE
        if not manifest_path.exists():
            raise FileNotFoundError(f"Session manifest not found: {manifest_path}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _manifest_from_dict(data)

    def resolve_video_paths(self, session_dir: Path, manifest: SessionManifest) -> dict[str, Path]:
        video_paths: dict[str, Path] = {}
        for source_id, video_file in manifest.video_files.items():
            path = session_dir / video_file
            if not path.exists():
                raise FileNotFoundError(f"Session video missing: {path}")
            video_paths[source_id] = path
        return video_paths

    def load_pose_sequence(self, session_dir: Path, manifest: SessionManifest) -> list[Pose3D]:
        if not manifest.pose_file:
            return []
        pose_path = session_dir / manifest.pose_file
        if not pose_path.exists():
            return []

        poses: list[Pose3D] = []
        with pose_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    poses.append(_pose3d_from_dict(payload))
                except Exception as exc:
                    LOGGER.warning("Skipping malformed pose3d record in %s (%s).", pose_path, exc)
        return poses

    def load_analysis_report(self, session_dir: Path, manifest: SessionManifest) -> SessionAnalysisReport | None:
        poses = self.load_pose_sequence(session_dir, manifest)
        return build_session_analysis(
            session_id=manifest.session_id,
            poses=poses,
            total_frames=manifest.total_frames,
            fps=manifest.fps,
        )

    def list_sessions(self, sessions_root: Path) -> list[Path]:
        if not sessions_root.exists():
            return []
        candidates = [path for path in sessions_root.iterdir() if path.is_dir()]
        sessions = [path for path in candidates if (path / MANIFEST_FILE).exists()]
        sessions.sort(reverse=True)
        return sessions
