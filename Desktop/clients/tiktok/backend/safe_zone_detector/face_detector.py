"""
Face Detection Module - Uses MediaPipe for accurate face detection.
"""

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict
import urllib.request
import os


# Model file path (downloaded on first use)
MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "blaze_face_short_range.tflite"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"


def _ensure_model():
    """Download the face detection model if not present."""
    if MODEL_PATH.exists():
        return

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading face detection model to {MODEL_PATH}...")
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print("Model downloaded successfully.")


def detect_faces(image_path: str, padding: float = 0.2) -> List[Dict]:
    """
    Detect faces in an image using MediaPipe Face Detection.

    Args:
        image_path: Path to the image file
        padding: Padding to add around detected faces (0.2 = 20% expansion)

    Returns:
        List of face bounding boxes with confidence scores:
        [
            {
                "bounds": {"x": int, "y": int, "w": int, "h": int},
                "confidence": float,
                "type": "face"
            }
        ]
    """
    _ensure_model()

    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]

    # Convert to MediaPipe Image format
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

    # Create face detector
    base_options = mp_python.BaseOptions(model_asset_path=str(MODEL_PATH))
    options = vision.FaceDetectorOptions(
        base_options=base_options,
        min_detection_confidence=0.5
    )

    faces = []

    with vision.FaceDetector.create_from_options(options) as detector:
        detection_result = detector.detect(mp_image)

        for detection in detection_result.detections:
            # Get bounding box
            bbox = detection.bounding_box

            x = bbox.origin_x
            y = bbox.origin_y
            w = bbox.width
            h = bbox.height

            # Add padding
            pad_w = int(w * padding)
            pad_h = int(h * padding)

            x = max(0, x - pad_w)
            y = max(0, y - pad_h)
            w = min(width - x, w + 2 * pad_w)
            h = min(height - y, h + 2 * pad_h)

            # Get confidence score
            confidence = detection.categories[0].score if detection.categories else 0.5

            faces.append({
                "bounds": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
                "confidence": float(confidence),
                "type": "face"
            })

    return faces


if __name__ == "__main__":
    # Test on a sample image
    import sys
    if len(sys.argv) > 1:
        result = detect_faces(sys.argv[1])
        print(f"Detected {len(result)} faces:")
        for face in result:
            print(f"  {face}")
