from __future__ import annotations

from mocap_app.models.types import Pose3D, Pose3DKeypoint


class ExponentialPoseSmoother:
    def __init__(self, alpha: float = 0.35) -> None:
        self._alpha = alpha
        self._state: dict[str, tuple[float, float, float, float]] = {}

    def reset(self) -> None:
        self._state.clear()

    def apply(self, pose: Pose3D) -> Pose3D:
        smoothed_keypoints: list[Pose3DKeypoint] = []
        for keypoint in pose.keypoints:
            previous = self._state.get(keypoint.name)
            if previous is None:
                x, y, z, confidence = keypoint.x, keypoint.y, keypoint.z, keypoint.confidence
            else:
                x = self._alpha * keypoint.x + (1.0 - self._alpha) * previous[0]
                y = self._alpha * keypoint.y + (1.0 - self._alpha) * previous[1]
                z = self._alpha * keypoint.z + (1.0 - self._alpha) * previous[2]
                confidence = self._alpha * keypoint.confidence + (1.0 - self._alpha) * previous[3]

            self._state[keypoint.name] = (x, y, z, confidence)
            smoothed_keypoints.append(
                Pose3DKeypoint(
                    name=keypoint.name,
                    x=x,
                    y=y,
                    z=z,
                    confidence=confidence,
                )
            )

        return Pose3D(
            frame_index=pose.frame_index,
            timestamp_sec=pose.timestamp_sec,
            keypoints=smoothed_keypoints,
        )

