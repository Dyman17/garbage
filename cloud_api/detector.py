from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import cv2
import numpy as np
from ultralytics import YOLO


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    is_face: bool = False


def detection_overlaps_face(
    detection: Detection,
    face: Detection,
    minimum_detection_overlap: float = 0.25,
) -> bool:
    intersection_width = max(0.0, min(detection.x2, face.x2) - max(detection.x1, face.x1))
    intersection_height = max(0.0, min(detection.y2, face.y2) - max(detection.y1, face.y1))
    intersection_area = intersection_width * intersection_height
    detection_area = max(0.0, detection.x2 - detection.x1) * max(0.0, detection.y2 - detection.y1)
    if detection_area == 0.0:
        return False
    return (intersection_area / detection_area) >= minimum_detection_overlap


class WasteDetector:
    def __init__(
        self,
        model_path: str,
        device: str = "cpu",
        conf_threshold: float = 0.55,
        imgsz: int = 640,
        iou_threshold: float = 0.45,
        max_det: int = 10,
        face_model_path: Optional[str] = None,
        face_conf_threshold: float = 0.55,
        face_overlap_threshold: float = 0.25,
    ):
        self.model_path = model_path
        self.device = device
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self.iou_threshold = iou_threshold
        self.max_det = max_det

        self.model = YOLO(model_path)
        self.face_model = None
        self.face_conf_threshold = face_conf_threshold
        self.face_overlap_threshold = face_overlap_threshold

        if face_model_path and Path(face_model_path).is_file():
            self.face_model = YOLO(face_model_path)

    def predict_frame(self, frame: np.ndarray, conf_threshold: Optional[float] = None) -> List[Detection]:
        threshold = self.conf_threshold if conf_threshold is None else conf_threshold
        results = self.model(
            frame,
            imgsz=self.imgsz,
            conf=threshold,
            iou=self.iou_threshold,
            max_det=self.max_det,
            verbose=False,
            device=self.device,
        )

        detections: List[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].tolist()
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])
                class_name = self.model.names.get(cls_id, str(cls_id))
                detections.append(
                    Detection(
                        class_id=cls_id,
                        class_name=class_name,
                        confidence=confidence,
                        x1=float(xyxy[0]),
                        y1=float(xyxy[1]),
                        x2=float(xyxy[2]),
                        y2=float(xyxy[3]),
                    )
                )

        if self.face_model is not None:
            face_results = self.face_model(
                frame,
                imgsz=self.imgsz,
                conf=self.face_conf_threshold,
                iou=self.iou_threshold,
                max_det=self.max_det,
                verbose=False,
                device=self.device,
            )
            faces: List[Detection] = []
            for result in face_results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    xyxy = box.xyxy[0].tolist()
                    faces.append(
                        Detection(
                            class_id=int(box.cls[0]),
                            class_name="face (ignored)",
                            confidence=float(box.conf[0]),
                            x1=float(xyxy[0]),
                            y1=float(xyxy[1]),
                            x2=float(xyxy[2]),
                            y2=float(xyxy[3]),
                            is_face=True,
                        )
                    )

            detections = [
                d for d in detections
                if not any(detection_overlaps_face(d, face, self.face_overlap_threshold) for face in faces)
            ]
            detections.extend(faces)

        return sorted(detections, key=lambda item: item.confidence, reverse=True)

    def draw_detections(self, frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
        annotated = frame.copy()
        for detection in detections:
            color = (255, 0, 255) if detection.is_face else (0, 255, 0)
            cv2.rectangle(
                annotated,
                (int(detection.x1), int(detection.y1)),
                (int(detection.x2), int(detection.y2)),
                color,
                2,
            )
            label = f"{detection.class_name} {detection.confidence:.2f}"
            cv2.putText(
                annotated,
                label,
                (int(detection.x1), max(22, int(detection.y1) - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )
        return annotated

