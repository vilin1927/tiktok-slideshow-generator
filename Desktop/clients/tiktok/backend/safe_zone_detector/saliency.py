"""
Saliency Detection - Identifies main subject/focal point in images.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict, Optional, Tuple


def detect_saliency(image_path: str, min_area_ratio: float = 0.05) -> Dict:
    """
    Detect the main subject/focal point using saliency analysis.

    Uses spectral residual saliency detection to find visually prominent regions.

    Args:
        image_path: Path to the image file
        min_area_ratio: Minimum area ratio for a region to be considered (0.05 = 5% of image)

    Returns:
        Dict with:
        - main_subject: Bounding box of largest salient region (or None)
        - saliency_map: 2D array of saliency values (0-1)
        - salient_regions: List of all detected salient regions
        - has_main_subject: Boolean indicating if a main subject was found
    """
    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]
    total_pixels = height * width
    min_area = int(total_pixels * min_area_ratio)

    # Create saliency detector
    saliency = cv2.saliency.StaticSaliencySpectralResidual_create()

    # Compute saliency map
    success, saliency_map = saliency.computeSaliency(image)

    if not success:
        return {
            "main_subject": None,
            "saliency_map": np.zeros((height, width), dtype=np.float32),
            "salient_regions": [],
            "has_main_subject": False,
            "image_size": {"w": width, "h": height}
        }

    # Normalize saliency map to 0-1
    saliency_map = (saliency_map * 255).astype(np.uint8)

    # Threshold to get binary mask
    _, binary = cv2.threshold(saliency_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Find contours
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Extract salient regions
    salient_regions = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area >= min_area:
            x, y, w, h = cv2.boundingRect(contour)
            salient_regions.append({
                "bounds": {"x": x, "y": y, "w": w, "h": h},
                "area": area,
                "confidence": min(1.0, area / (total_pixels * 0.3)),  # Normalize confidence
                "type": "salient_region"
            })

    # Sort by area (largest first)
    salient_regions.sort(key=lambda r: r["area"], reverse=True)

    # Main subject is the largest salient region
    main_subject = None
    if salient_regions:
        main_subject = {
            "bounds": salient_regions[0]["bounds"],
            "confidence": salient_regions[0]["confidence"],
            "type": "main_subject"
        }

    return {
        "main_subject": main_subject,
        "saliency_map": saliency_map.astype(np.float32) / 255.0,
        "salient_regions": salient_regions,
        "has_main_subject": main_subject is not None,
        "image_size": {"w": width, "h": height}
    }


def detect_product_region(image_path: str) -> Optional[Dict]:
    """
    Attempt to detect product/object region using contrast and center-bias.

    Fallback method when saliency detection is not reliable.
    Products are often centered and have high contrast with background.
    """
    image = cv2.imread(str(image_path))
    if image is None:
        return None

    height, width = image.shape[:2]

    # Convert to grayscale and apply edge detection
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    # Apply center-weighted mask (products are often centered)
    center_mask = np.zeros((height, width), dtype=np.float32)
    cv2.ellipse(center_mask, (width // 2, height // 2),
                (width // 3, height // 3), 0, 0, 360, 1.0, -1)
    center_mask = cv2.GaussianBlur(center_mask, (51, 51), 0)

    # Combine edges with center bias
    weighted = edges.astype(np.float32) * center_mask

    # Find the region with highest weighted edge density
    _, binary = cv2.threshold(weighted.astype(np.uint8), 30, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    # Get largest contour
    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)

    return {
        "bounds": {"x": x, "y": y, "w": w, "h": h},
        "confidence": 0.6,  # Lower confidence for fallback method
        "type": "product"
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = detect_saliency(sys.argv[1])
        print(f"Has main subject: {result['has_main_subject']}")
        if result['main_subject']:
            print(f"Main subject: {result['main_subject']}")
        print(f"Salient regions: {len(result['salient_regions'])}")
