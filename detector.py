from ultralytics import YOLO

import config


class PersonDetector:
    def __init__(self, model_path=None, confidence=None):
        self.model = YOLO(model_path or config.YOLO_MODEL_PATH)
        self.confidence = confidence or config.CONFIDENCE_THRESHOLD

    def detect(self, frame):
        """Run detection on a frame, return list of person bounding boxes [(x1, y1, x2, y2, conf), ...]."""
        results = self.model(frame, conf=self.confidence, verbose=False)

        people = []
        for r in results:
            for box in r.boxes:
                cls = int(box.cls[0])
                if cls == config.PERSON_CLASS_ID:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    people.append((x1, y1, x2, y2, conf))

        return people

    def detect_sv(self, frame, roi=None, imgsz=None):
        """Person detections as sv.Detections (for ByteTrack). roi=(x1, y1, x2, y2) limits detection to
        that part of the frame; boxes are returned in full-frame coordinates."""
        import supervision as sv

        x0, y0 = 0, 0
        if roi:
            x0, y0, x1, y1 = roi
            frame = frame[y0:y1, x0:x1]
        result = self.model(frame, conf=self.confidence, classes=[config.PERSON_CLASS_ID],
                            imgsz=imgsz or config.YOLO_IMGSZ, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(result)
        if roi and len(detections):
            detections.xyxy += [x0, y0, x0, y0]
        return detections
