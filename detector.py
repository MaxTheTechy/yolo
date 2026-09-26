import logging
import os
import queue
import threading
import time

from ultralytics import YOLO

import config

log = logging.getLogger(__name__)


def resolve_device():
    """config.DETECTOR_DEVICE: "auto" picks the first CUDA GPU if one is available, else the CPU."""
    if config.DETECTOR_DEVICE != "auto":
        return config.DETECTOR_DEVICE
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


class PersonDetector:
    def __init__(self, model_path=None, confidence=None, device="cpu"):
        self.model = YOLO(model_path or config.YOLO_MODEL_PATH)
        self.confidence = confidence or config.CONFIDENCE_THRESHOLD
        self.device = device
        self.half = device.startswith("cuda")  # FP16 on GPU: ~2x faster, same detections in practice

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

    def detect_batch(self, images, imgsz):
        """Person detections for several images at one model input size, as a list of sv.Detections."""
        import supervision as sv

        results = self.model(images, conf=self.confidence, classes=[config.PERSON_CLASS_ID], imgsz=imgsz,
                             device=self.device, half=self.half, verbose=False)
        return [sv.Detections.from_ultralytics(r) for r in results]

    def detect_sv(self, frame, roi=None, imgsz=None):
        """Person detections as sv.Detections (for ByteTrack). roi=(x1, y1, x2, y2) limits detection to
        that part of the frame; boxes are returned in full-frame coordinates."""
        crop, offset = crop_roi(frame, roi)
        return shift(self.detect_batch([crop], imgsz or config.YOLO_IMGSZ)[0], offset)


def crop_roi(frame, roi):
    if not roi:
        return frame, (0, 0)
    x0, y0, x1, y1 = roi
    return frame[y0:y1, x0:x1], (x0, y0)


def shift(detections, offset):
    if offset != (0, 0) and len(detections):
        detections.xyxy += [offset[0], offset[1], offset[0], offset[1]]
    return detections


class _Request:
    __slots__ = ("image", "offset", "imgsz", "done", "result", "error")

    def __init__(self, image, offset, imgsz):
        self.image, self.offset, self.imgsz = image, offset, imgsz
        self.done = threading.Event()
        self.result = self.error = None


class DetectorPool:
    """Person detection shared by all cameras, instead of one model per camera.

    Camera threads call detect_sv() exactly like PersonDetector.detect_sv(); requests go into one
    queue served by a few detector threads, each with its own model copy.
    - CPU: several workers (DETECTOR_WORKERS, default one per TORCH_THREADS cores, leaving cores for
      video decoding), batch size 1 - batching gains little on CPU, parallel workers do.
    - GPU: one worker that batches up to DETECTOR_BATCH frames from different cameras per model
      call (waiting at most DETECTOR_BATCH_WAIT_MS to fill a batch), which is where GPUs are fast.
    Each camera has at most one frame in flight (it waits for its result), so the queue can't grow
    beyond the number of cameras.
    """

    def __init__(self):
        self.device = resolve_device()
        gpu = self.device.startswith("cuda")
        cores = len(os.sched_getaffinity(0))
        self.workers = config.DETECTOR_WORKERS or (1 if gpu else max(1, cores // config.TORCH_THREADS - 1))
        self.batch_size = config.DETECTOR_BATCH or (8 if gpu else 1)
        self.batch_wait = config.DETECTOR_BATCH_WAIT_MS / 1000
        self.queue = queue.Queue()
        self.threads = []
        for i in range(self.workers):
            detector = PersonDetector(device=self.device)
            t = threading.Thread(target=self._serve, args=(detector,), name=f"detector{i}", daemon=True)
            t.start()
            self.threads.append(t)
        log.info("Detector pool: device=%s workers=%d batch=%d model=%s",
                 self.device, self.workers, self.batch_size, config.YOLO_MODEL_PATH)

    def detect_sv(self, frame, roi=None, imgsz=None, timeout=60):
        image, offset = crop_roi(frame, roi)
        req = _Request(image, offset, imgsz or config.YOLO_IMGSZ)
        self.queue.put(req)
        if not req.done.wait(timeout):
            raise TimeoutError("detector pool did not answer in time")
        if req.error:
            raise req.error
        return req.result

    def stop(self, timeout=30):
        """Stop and wait for the workers: exiting while a thread is inside torch aborts the process."""
        for _ in self.threads:
            self.queue.put(None)
        for t in self.threads:
            t.join(timeout)

    def _next_batch(self):
        first = self.queue.get()
        if first is None:
            return None
        batch = [first]
        deadline = time.monotonic() + self.batch_wait
        while len(batch) < self.batch_size:
            try:
                req = self.queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if req is None:  # stop signal: finish this batch, then let the loop see it again
                self.queue.put(None)
                break
            batch.append(req)
        return batch

    def _serve(self, detector):
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            by_size = {}
            for req in batch:  # entrance (640) and zone (1280) cameras use different input sizes
                by_size.setdefault(req.imgsz, []).append(req)
            for imgsz, reqs in by_size.items():
                try:
                    results = detector.detect_batch([r.image for r in reqs], imgsz)
                    for req, det in zip(reqs, results):
                        req.result = shift(det, req.offset)
                except Exception as exc:  # hand the error to the camera thread that asked
                    for req in reqs:
                        req.error = exc
                for req in reqs:
                    req.done.set()
