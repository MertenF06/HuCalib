from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from mocap_app.models.types import Pose3D, Pose3DKeypoint


Point3D = tuple[float, float, float]


@dataclass(slots=True)
class MetricSeries:
    key: str
    label: str
    unit: str
    values: list[float | None] = field(default_factory=list)


@dataclass(slots=True)
class JointMetricSummary:
    label: str
    mean_deg: float | None
    min_deg: float | None
    max_deg: float | None
    range_deg: float | None


@dataclass(slots=True)
class SessionAnalysisReport:
    session_id: str
    total_frames: int
    frames_with_pose: int
    duration_sec: float
    pose_coverage_ratio: float
    mean_visible_joints: float
    mean_confidence: float
    center_path_length_m: float | None
    movement_volume_m3: float | None
    metric_series: dict[str, MetricSeries] = field(default_factory=dict)
    joint_summaries: list[JointMetricSummary] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_export_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "total_frames": self.total_frames,
            "frames_with_pose": self.frames_with_pose,
            "duration_sec": self.duration_sec,
            "pose_coverage_ratio": self.pose_coverage_ratio,
            "mean_visible_joints": self.mean_visible_joints,
            "mean_confidence": self.mean_confidence,
            "center_path_length_m": self.center_path_length_m,
            "movement_volume_m3": self.movement_volume_m3,
            "joint_summaries": [
                {
                    "label": summary.label,
                    "mean_deg": summary.mean_deg,
                    "min_deg": summary.min_deg,
                    "max_deg": summary.max_deg,
                    "range_deg": summary.range_deg,
                }
                for summary in self.joint_summaries
            ],
            "metric_series": {
                key: {
                    "label": series.label,
                    "unit": series.unit,
                    "values": series.values,
                }
                for key, series in self.metric_series.items()
            },
            "notes": list(self.notes),
        }

    def to_text(self) -> str:
        lines = [
            "Session Analysis",
            "",
            f"Session: {self.session_id}",
            f"Frames with 3D pose: {self.frames_with_pose} / {self.total_frames} ({self.pose_coverage_ratio * 100:.1f}%)",
            f"Duration: {self.duration_sec:.2f} s",
            f"Average visible joints: {self.mean_visible_joints:.2f}",
            f"Average joint confidence: {self.mean_confidence:.3f}",
            (
                f"Body-center path length: {self.center_path_length_m:.3f} m"
                if self.center_path_length_m is not None
                else "Body-center path length: n/a"
            ),
            (
                f"Movement volume: {self.movement_volume_m3:.4f} m^3"
                if self.movement_volume_m3 is not None
                else "Movement volume: n/a"
            ),
            "",
            "Joint Angle Summaries",
        ]
        if not self.joint_summaries:
            lines.append("- No joint-angle series available.")
        else:
            for summary in self.joint_summaries:
                if summary.mean_deg is None:
                    lines.append(f"- {summary.label}: n/a")
                    continue
                lines.append(
                    f"- {summary.label}: mean {summary.mean_deg:.1f} deg, "
                    f"min {summary.min_deg:.1f}, max {summary.max_deg:.1f}, range {summary.range_deg:.1f}"
                )
        if self.notes:
            lines.extend(["", "Notes"])
            for note in self.notes:
                lines.append(f"- {note}")
        return "\n".join(lines)


def build_session_analysis(
    session_id: str,
    poses: list[Pose3D],
    total_frames: int,
    fps: float,
) -> SessionAnalysisReport | None:
    frame_count = max(int(total_frames), len(poses))
    if frame_count <= 0:
        return None

    series_map = {
        "visible_joints": MetricSeries("visible_joints", "Visible joints", "count", [None] * frame_count),
        "mean_confidence": MetricSeries("mean_confidence", "Mean joint confidence", "score", [None] * frame_count),
        "left_knee_angle_deg": MetricSeries("left_knee_angle_deg", "Left knee angle", "deg", [None] * frame_count),
        "right_knee_angle_deg": MetricSeries("right_knee_angle_deg", "Right knee angle", "deg", [None] * frame_count),
        "left_elbow_angle_deg": MetricSeries("left_elbow_angle_deg", "Left elbow angle", "deg", [None] * frame_count),
        "right_elbow_angle_deg": MetricSeries("right_elbow_angle_deg", "Right elbow angle", "deg", [None] * frame_count),
        "shoulder_width_m": MetricSeries("shoulder_width_m", "Shoulder width", "m", [None] * frame_count),
        "hip_width_m": MetricSeries("hip_width_m", "Hip width", "m", [None] * frame_count),
        "body_center_depth_m": MetricSeries("body_center_depth_m", "Body-center depth", "m", [None] * frame_count),
    }

    frames_with_pose = 0
    visible_joint_values: list[float] = []
    confidence_values: list[float] = []
    all_points: list[Point3D] = []
    previous_center: Point3D | None = None
    center_path_length_m = 0.0

    sorted_poses = sorted(poses, key=lambda pose: pose.frame_index)
    raw_indices = [pose.frame_index for pose in sorted_poses]
    frame_index_offset = 1 if raw_indices and min(raw_indices) >= 1 and 0 not in raw_indices else 0
    for pose_offset, pose in enumerate(sorted_poses):
        normalized_index = pose.frame_index - frame_index_offset
        frame_index = normalized_index if 0 <= normalized_index < frame_count else pose_offset
        keypoints_by_name = {keypoint.name: keypoint for keypoint in pose.keypoints}
        if not keypoints_by_name:
            continue

        frames_with_pose += 1
        visible_joint_count = float(len(keypoints_by_name))
        visible_joint_values.append(visible_joint_count)
        series_map["visible_joints"].values[frame_index] = visible_joint_count

        confidences = [float(keypoint.confidence) for keypoint in keypoints_by_name.values()]
        mean_confidence = _mean(confidences)
        confidence_values.append(mean_confidence)
        series_map["mean_confidence"].values[frame_index] = mean_confidence

        body_center = _mean_point(
            [
                (float(keypoint.x), float(keypoint.y), float(keypoint.z))
                for keypoint in keypoints_by_name.values()
            ]
        )
        if body_center is not None:
            if previous_center is not None:
                center_path_length_m += _distance(previous_center, body_center)
            previous_center = body_center
            series_map["body_center_depth_m"].values[frame_index] = body_center[2]

        for keypoint in keypoints_by_name.values():
            all_points.append((float(keypoint.x), float(keypoint.y), float(keypoint.z)))

        _set_angle_metric(
            series_map["left_knee_angle_deg"].values,
            frame_index,
            _joint_angle(
                keypoints_by_name.get("left_hip"),
                keypoints_by_name.get("left_knee"),
                keypoints_by_name.get("left_ankle"),
            ),
        )
        _set_angle_metric(
            series_map["right_knee_angle_deg"].values,
            frame_index,
            _joint_angle(
                keypoints_by_name.get("right_hip"),
                keypoints_by_name.get("right_knee"),
                keypoints_by_name.get("right_ankle"),
            ),
        )
        _set_angle_metric(
            series_map["left_elbow_angle_deg"].values,
            frame_index,
            _joint_angle(
                keypoints_by_name.get("left_shoulder"),
                keypoints_by_name.get("left_elbow"),
                keypoints_by_name.get("left_wrist"),
            ),
        )
        _set_angle_metric(
            series_map["right_elbow_angle_deg"].values,
            frame_index,
            _joint_angle(
                keypoints_by_name.get("right_shoulder"),
                keypoints_by_name.get("right_elbow"),
                keypoints_by_name.get("right_wrist"),
            ),
        )

        left_shoulder = keypoints_by_name.get("left_shoulder")
        right_shoulder = keypoints_by_name.get("right_shoulder")
        if left_shoulder is not None and right_shoulder is not None:
            series_map["shoulder_width_m"].values[frame_index] = _distance(
                _point_of(left_shoulder),
                _point_of(right_shoulder),
            )

        left_hip = keypoints_by_name.get("left_hip")
        right_hip = keypoints_by_name.get("right_hip")
        if left_hip is not None and right_hip is not None:
            series_map["hip_width_m"].values[frame_index] = _distance(
                _point_of(left_hip),
                _point_of(right_hip),
            )

    if frames_with_pose == 0:
        return SessionAnalysisReport(
            session_id=session_id,
            total_frames=frame_count,
            frames_with_pose=0,
            duration_sec=(frame_count / fps) if fps > 0 else 0.0,
            pose_coverage_ratio=0.0,
            mean_visible_joints=0.0,
            mean_confidence=0.0,
            center_path_length_m=None,
            movement_volume_m3=None,
            metric_series=series_map,
            joint_summaries=[],
            notes=["No stored 3D pose frames were available in this session."],
        )

    timestamps = [pose.timestamp_sec for pose in sorted_poses if pose.keypoints]
    if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
        duration_sec = float(timestamps[-1] - timestamps[0])
    else:
        duration_sec = float(frame_count / fps) if fps > 0 else 0.0

    movement_volume = _movement_volume(all_points)
    joint_summaries = [
        _build_joint_summary("Left knee angle", series_map["left_knee_angle_deg"].values),
        _build_joint_summary("Right knee angle", series_map["right_knee_angle_deg"].values),
        _build_joint_summary("Left elbow angle", series_map["left_elbow_angle_deg"].values),
        _build_joint_summary("Right elbow angle", series_map["right_elbow_angle_deg"].values),
    ]

    notes: list[str] = []
    if frames_with_pose < frame_count:
        notes.append(
            f"3D pose coverage is {frames_with_pose}/{frame_count} frames; missing frames are excluded from summaries."
        )
    if center_path_length_m <= 1e-9:
        notes.append("Body-center path length is near zero; this may indicate a largely stationary movement.")

    return SessionAnalysisReport(
        session_id=session_id,
        total_frames=frame_count,
        frames_with_pose=frames_with_pose,
        duration_sec=duration_sec,
        pose_coverage_ratio=frames_with_pose / max(frame_count, 1),
        mean_visible_joints=_mean(visible_joint_values),
        mean_confidence=_mean(confidence_values),
        center_path_length_m=center_path_length_m,
        movement_volume_m3=movement_volume,
        metric_series=series_map,
        joint_summaries=[summary for summary in joint_summaries if summary.mean_deg is not None],
        notes=notes,
    )


def _build_joint_summary(label: str, values: list[float | None]) -> JointMetricSummary:
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return JointMetricSummary(label=label, mean_deg=None, min_deg=None, max_deg=None, range_deg=None)
    min_value = min(numeric_values)
    max_value = max(numeric_values)
    return JointMetricSummary(
        label=label,
        mean_deg=_mean(numeric_values),
        min_deg=min_value,
        max_deg=max_value,
        range_deg=max_value - min_value,
    )


def _joint_angle(
    point_a: Pose3DKeypoint | None,
    pivot: Pose3DKeypoint | None,
    point_c: Pose3DKeypoint | None,
) -> float | None:
    if point_a is None or pivot is None or point_c is None:
        return None
    ba = (
        float(point_a.x - pivot.x),
        float(point_a.y - pivot.y),
        float(point_a.z - pivot.z),
    )
    bc = (
        float(point_c.x - pivot.x),
        float(point_c.y - pivot.y),
        float(point_c.z - pivot.z),
    )
    norm_ba = math.sqrt(sum(component * component for component in ba))
    norm_bc = math.sqrt(sum(component * component for component in bc))
    if norm_ba <= 1e-8 or norm_bc <= 1e-8:
        return None
    dot_product = sum(left * right for left, right in zip(ba, bc))
    cosine = max(-1.0, min(1.0, dot_product / (norm_ba * norm_bc)))
    return math.degrees(math.acos(cosine))


def _point_of(keypoint: Pose3DKeypoint) -> Point3D:
    return float(keypoint.x), float(keypoint.y), float(keypoint.z)


def _mean(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return float(sum(values_list) / len(values_list))


def _mean_point(points: list[Point3D]) -> Point3D | None:
    if not points:
        return None
    count = float(len(points))
    sx = sum(point[0] for point in points)
    sy = sum(point[1] for point in points)
    sz = sum(point[2] for point in points)
    return (sx / count, sy / count, sz / count)


def _distance(point_a: Point3D, point_b: Point3D) -> float:
    return math.sqrt(
        (point_a[0] - point_b[0]) ** 2
        + (point_a[1] - point_b[1]) ** 2
        + (point_a[2] - point_b[2]) ** 2
    )


def _movement_volume(points: list[Point3D]) -> float | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    return float((max(xs) - min(xs)) * (max(ys) - min(ys)) * (max(zs) - min(zs)))


def _set_angle_metric(target: list[float | None], frame_index: int, value: float | None) -> None:
    if 0 <= frame_index < len(target):
        target[frame_index] = value
