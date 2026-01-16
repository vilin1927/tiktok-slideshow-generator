"""
Face Detection Module

Uses multiple Haar Cascades for comprehensive face detection.
Returns bounding boxes with 30% padding for safe text placement.
"""

import cv2
import numpy as np
from typing import List, Dict, Tuple


def detect_faces(image_path: str, padding_percent: float = 0.30) -> List[Dict]:
    """
    Detect faces in an image using multiple Haar Cascades.

    Uses both frontal and profile face detectors for better coverage.
    More sensitive detection settings to catch faces at various angles/sizes.

    Args:
        image_path: Path to the image file
        padding_percent: Padding to add around detected faces (default 30%)

    Returns:
        List of face dictionaries with:
        - bounds: {x, y, w, h} bounding box
        - confidence: Detection confidence (0-1)
        - padded_bounds: {x, y, w, h} with padding applied
    """
    # Load image
    image = cv2.imread(image_path)
    if image is None:
        return []

    h, w = image.shape[:2]
    faces = []
    seen_regions = []  # Track detected regions to avoid duplicates

    def is_duplicate(x, y, face_w, face_h, threshold=0.5):
        """Check if this detection overlaps significantly with existing ones."""
        for (sx, sy, sw, sh) in seen_regions:
            # Calculate overlap
            overlap_x = max(0, min(x + face_w, sx + sw) - max(x, sx))
            overlap_y = max(0, min(y + face_h, sy + sh) - max(y, sy))
            overlap_area = overlap_x * overlap_y
            min_area = min(face_w * face_h, sw * sh)
            if min_area > 0 and overlap_area / min_area > threshold:
                return True
        return False

    def add_detection(x, y, face_w, face_h, confidence):
        """Add a face detection if not duplicate."""
        if is_duplicate(x, y, face_w, face_h):
            return

        seen_regions.append((x, y, face_w, face_h))

        # Calculate padded bounds (30% padding for safety)
        pad_x = int(face_w * padding_percent)
        pad_y = int(face_h * padding_percent)

        padded_x = max(0, x - pad_x)
        padded_y = max(0, y - pad_y)
        padded_w = min(w - padded_x, face_w + 2 * pad_x)
        padded_h = min(h - padded_y, face_h + 2 * pad_y)

        faces.append({
            'bounds': {'x': int(x), 'y': int(y), 'w': int(face_w), 'h': int(face_h)},
            'confidence': confidence,
            'padded_bounds': {
                'x': padded_x,
                'y': padded_y,
                'w': padded_w,
                'h': padded_h
            }
        })

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Detector 1: Frontal face (default - most reliable)
        frontal_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        frontal_detections = frontal_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,  # More sensitive (was 1.1)
            minNeighbors=3,    # More sensitive (was 5)
            minSize=(20, 20)   # Smaller faces (was 30, 30)
        )
        for (x, y, face_w, face_h) in frontal_detections:
            add_detection(x, y, face_w, face_h, 0.85)

        # Detector 2: Frontal face alt (catches different face types)
        frontal_alt_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml'
        )
        frontal_alt_detections = frontal_alt_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(20, 20)
        )
        for (x, y, face_w, face_h) in frontal_alt_detections:
            add_detection(x, y, face_w, face_h, 0.80)

        # Detector 3: Profile face (side views)
        profile_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_profileface.xml'
        )
        profile_detections = profile_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(20, 20)
        )
        for (x, y, face_w, face_h) in profile_detections:
            add_detection(x, y, face_w, face_h, 0.75)

        # Detector 4: Flipped image for opposite profile
        gray_flipped = cv2.flip(gray, 1)
        profile_flipped_detections = profile_cascade.detectMultiScale(
            gray_flipped,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(20, 20)
        )
        for (x, y, face_w, face_h) in profile_flipped_detections:
            # Flip x coordinate back
            flipped_x = w - x - face_w
            add_detection(flipped_x, y, face_w, face_h, 0.75)

    except Exception as e:
        # Face detection failed, continue without it
        print(f"Warning: Face detection failed ({e}), continuing without face avoidance")
        return []

    return faces


def get_face_avoid_zones(image_path: str) -> List[Dict]:
    """
    Get face regions as avoid zones for text placement.

    Args:
        image_path: Path to the image file

    Returns:
        List of avoid zone dictionaries with:
        - type: "face"
        - bounds: {x, y, w, h} padded bounding box
        - confidence: Detection confidence
    """
    faces = detect_faces(image_path)

    avoid_zones = []
    for face in faces:
        avoid_zones.append({
            'type': 'face',
            'bounds': face['padded_bounds'],
            'confidence': face['confidence']
        })

    return avoid_zones
