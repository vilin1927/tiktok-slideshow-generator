"""
Saliency Detection Module

Identifies the main subject/focal point of an image.
Main subjects should be avoided when placing text.
"""

import cv2
import numpy as np
from typing import Dict, Optional, Tuple


def detect_saliency(image_path: str) -> Dict:
    """
    Detect the main salient region in an image.

    Uses OpenCV's Spectral Residual saliency detection.

    Args:
        image_path: Path to the image file

    Returns:
        Dictionary with:
        - main_subject: Bounding box of main subject {x, y, w, h} or None
        - confidence: Confidence score (0-1)
        - saliency_map: 2D array of saliency values (optional, for debug)
    """
    # Load image
    image = cv2.imread(image_path)
    if image is None:
        return {
            'main_subject': None,
            'confidence': 0,
            'saliency_map': None
        }

    h, w = image.shape[:2]

    # Create saliency detector
    try:
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
    except AttributeError:
        # Fallback if saliency module not available
        return {
            'main_subject': None,
            'confidence': 0,
            'saliency_map': None
        }

    # Compute saliency map
    success, saliency_map = saliency.computeSaliency(image)

    if not success or saliency_map is None:
        return {
            'main_subject': None,
            'confidence': 0,
            'saliency_map': None
        }

    # Convert to 8-bit for processing
    saliency_map = (saliency_map * 255).astype(np.uint8)

    # Threshold to get binary map
    _, binary_map = cv2.threshold(
        saliency_map, 0, 255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )

    # Find contours
    contours, _ = cv2.findContours(
        binary_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        return {
            'main_subject': None,
            'confidence': 0,
            'saliency_map': saliency_map
        }

    # Find the largest contour (main subject)
    largest_contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest_contour)

    # Minimum area threshold (at least 5% of image)
    min_area = w * h * 0.05

    if area < min_area:
        return {
            'main_subject': None,
            'confidence': 0,
            'saliency_map': saliency_map
        }

    # Get bounding box
    x, y, box_w, box_h = cv2.boundingRect(largest_contour)

    # Calculate confidence based on area relative to image
    # Larger salient regions = higher confidence
    confidence = min(1.0, area / (w * h * 0.3))

    return {
        'main_subject': {'x': x, 'y': y, 'w': box_w, 'h': box_h},
        'confidence': round(confidence, 3),
        'saliency_map': saliency_map
    }


def get_saliency_avoid_zone(image_path: str) -> Optional[Dict]:
    """
    Get main subject as an avoid zone for text placement.

    Args:
        image_path: Path to the image file

    Returns:
        Avoid zone dictionary or None if no significant subject detected
    """
    result = detect_saliency(image_path)

    if result['main_subject'] is None:
        return None

    return {
        'type': 'main_subject',
        'bounds': result['main_subject'],
        'confidence': result['confidence']
    }
