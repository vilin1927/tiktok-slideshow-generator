"""
Main Safe Zone Detector - Combines all detection modules.
"""

import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .face_detector import detect_faces
from .edge_analyzer import analyze_edges, get_cell_bounds
from .color_analyzer import analyze_colors, get_dominant_text_color
from .saliency import detect_saliency
from .text_calculator import calculate_zone_capacity


# Configuration
DEFAULT_GRID_SIZE = 12
MIN_ZONE_WIDTH = 200
MIN_ZONE_HEIGHT = 100
EDGE_THRESHOLD = 0.5      # Above this = busy
VARIANCE_THRESHOLD = 0.4  # Above this = too varied


def analyze_image(
    image_path: str,
    font_name: str = "Inter",
    font_size: int = 56,
    grid_size: int = DEFAULT_GRID_SIZE
) -> Dict:
    """
    Analyze an image to detect safe zones for text placement.

    This is the main entry point that combines all detection methods.

    Args:
        image_path: Path to the image file
        font_name: Font name for text capacity calculation
        font_size: Font size for text capacity calculation
        grid_size: Grid resolution (12 = 12x12 grid)

    Returns:
        Dict with:
        - image_size: {"w": width, "h": height}
        - safe_zones: List of safe zone dicts
        - avoid_zones: List of avoid zone dicts
        - recommended_zone: Index of best safe zone
        - analysis_metadata: Additional info
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Step 1: Run all detection modules
    faces = detect_faces(str(image_path))
    edge_data = analyze_edges(str(image_path), grid_size)
    color_data = analyze_colors(str(image_path), grid_size)
    saliency_data = detect_saliency(str(image_path))

    img_w = edge_data["image_size"]["w"]
    img_h = edge_data["image_size"]["h"]
    cell_w = edge_data["cell_size"]["w"]
    cell_h = edge_data["cell_size"]["h"]

    # Step 2: Build avoid zones
    avoid_zones = []

    # Add faces as avoid zones
    for face in faces:
        avoid_zones.append(face)

    # Add main subject as partial avoid zone
    if saliency_data["main_subject"]:
        avoid_zones.append(saliency_data["main_subject"])

    # Step 3: Score each grid cell
    # A cell is "safe" if it has low edge density AND low color variance
    cell_scores = np.zeros((grid_size, grid_size), dtype=np.float32)

    for row in range(grid_size):
        for col in range(grid_size):
            # Get cell bounds
            bounds = get_cell_bounds(row, col, cell_w, cell_h, img_w, img_h, grid_size)

            # Check if cell overlaps with any avoid zone
            overlaps_avoid = False
            for zone in avoid_zones:
                if _rectangles_overlap(bounds, zone["bounds"]):
                    overlaps_avoid = True
                    break

            if overlaps_avoid:
                cell_scores[row, col] = 0.0
                continue

            # Score based on edge density (lower = better)
            edge_score = 1.0 - edge_data["density_map"][row, col]

            # Score based on color variance (lower variance = better)
            variance_score = 1.0 - color_data["variance_map"][row, col]

            # Combined score
            cell_scores[row, col] = (edge_score * 0.5) + (variance_score * 0.5)

    # Step 4: Find contiguous safe regions
    safe_regions = _find_safe_regions(cell_scores, grid_size, threshold=0.5)

    # Step 5: Convert regions to safe zones with bounds
    safe_zones = []
    for region in safe_regions:
        bounds = _region_to_bounds(region, cell_w, cell_h, img_w, img_h, grid_size)

        # Skip zones that are too small
        if bounds["w"] < MIN_ZONE_WIDTH or bounds["h"] < MIN_ZONE_HEIGHT:
            continue

        # Calculate average metrics for the region
        cells = region["cells"]
        avg_confidence = np.mean([cell_scores[r, c] for r, c in cells])

        # Get color info from first cell (representative)
        first_cell = cells[0]
        avg_color = color_data["color_map"][first_cell[0]][first_cell[1]]
        brightness = np.mean([color_data["brightness_map"][r, c] for r, c in cells])
        text_color = "white" if brightness < 0.5 else "black"

        # Calculate text capacity
        capacity = calculate_zone_capacity(bounds, font_name, "500", font_size)

        # Build reasons
        reasons = []
        avg_edge = np.mean([edge_data["density_map"][r, c] for r, c in cells])
        avg_variance = np.mean([color_data["variance_map"][r, c] for r, c in cells])
        if avg_edge < EDGE_THRESHOLD:
            reasons.append("low_edge_density")
        if avg_variance < VARIANCE_THRESHOLD:
            reasons.append("uniform_color")
        if not any(_rectangles_overlap(bounds, f["bounds"]) for f in faces):
            reasons.append("no_faces")

        safe_zones.append({
            "position": _get_position_name(bounds, img_w, img_h),
            "bounds": bounds,
            "confidence": float(avg_confidence),
            "avg_color": avg_color,
            "brightness": float(brightness),
            "text_color_suggestion": text_color,
            "max_chars_per_line": capacity["max_chars_per_line"],
            "max_lines": capacity["max_lines"],
            "reasons": reasons
        })

    # Sort by confidence (highest first)
    safe_zones.sort(key=lambda z: z["confidence"], reverse=True)

    # Step 6: Select recommended zone
    recommended_zone = 0 if safe_zones else None

    # Step 7: Build metadata
    total_cells = grid_size * grid_size
    safe_cell_count = np.sum(cell_scores > 0.5)
    complexity = "low" if safe_cell_count > total_cells * 0.5 else \
                 "medium" if safe_cell_count > total_cells * 0.2 else "high"

    # Add busy areas to avoid zones
    busy_cells = edge_data["busy_cells"]
    for row, col in busy_cells:
        bounds = get_cell_bounds(row, col, cell_w, cell_h, img_w, img_h, grid_size)
        # Only add if not already covered by another avoid zone
        already_covered = any(_rectangles_overlap(bounds, z["bounds"]) for z in avoid_zones)
        if not already_covered:
            avoid_zones.append({
                "type": "busy_area",
                "bounds": bounds,
                "confidence": float(edge_data["density_map"][row, col])
            })

    return {
        "image_size": {"w": img_w, "h": img_h},
        "safe_zones": safe_zones,
        "avoid_zones": avoid_zones,
        "recommended_zone": recommended_zone,
        "analysis_metadata": {
            "face_count": len(faces),
            "has_main_subject": saliency_data["has_main_subject"],
            "overall_complexity": complexity,
            "grid_size": grid_size,
            "safe_cell_ratio": float(safe_cell_count / total_cells)
        }
    }


def _rectangles_overlap(r1: Dict, r2: Dict) -> bool:
    """Check if two rectangles overlap."""
    return not (
        r1["x"] + r1["w"] <= r2["x"] or
        r2["x"] + r2["w"] <= r1["x"] or
        r1["y"] + r1["h"] <= r2["y"] or
        r2["y"] + r2["h"] <= r1["y"]
    )


def _find_safe_regions(scores: np.ndarray, grid_size: int, threshold: float = 0.5) -> List[Dict]:
    """
    Find contiguous regions of safe cells using flood fill.
    """
    visited = np.zeros((grid_size, grid_size), dtype=bool)
    regions = []

    def flood_fill(start_row: int, start_col: int) -> List[Tuple[int, int]]:
        """Flood fill to find connected safe cells."""
        cells = []
        stack = [(start_row, start_col)]

        while stack:
            row, col = stack.pop()

            if (row < 0 or row >= grid_size or
                col < 0 or col >= grid_size or
                visited[row, col] or
                scores[row, col] < threshold):
                continue

            visited[row, col] = True
            cells.append((row, col))

            # Check 4-connected neighbors
            stack.extend([
                (row - 1, col),
                (row + 1, col),
                (row, col - 1),
                (row, col + 1)
            ])

        return cells

    # Find all regions
    for row in range(grid_size):
        for col in range(grid_size):
            if not visited[row, col] and scores[row, col] >= threshold:
                cells = flood_fill(row, col)
                if cells:
                    regions.append({"cells": cells})

    return regions


def _region_to_bounds(region: Dict, cell_w: int, cell_h: int,
                      img_w: int, img_h: int, grid_size: int) -> Dict:
    """Convert a region of cells to pixel bounds."""
    cells = region["cells"]

    min_row = min(c[0] for c in cells)
    max_row = max(c[0] for c in cells)
    min_col = min(c[1] for c in cells)
    max_col = max(c[1] for c in cells)

    x = min_col * cell_w
    y = min_row * cell_h
    w = (max_col - min_col + 1) * cell_w
    h = (max_row - min_row + 1) * cell_h

    # Clamp to image bounds
    w = min(w, img_w - x)
    h = min(h, img_h - y)

    return {"x": x, "y": y, "w": w, "h": h}


def _get_position_name(bounds: Dict, img_w: int, img_h: int) -> str:
    """Get a human-readable position name for a zone."""
    cx = bounds["x"] + bounds["w"] / 2
    cy = bounds["y"] + bounds["h"] / 2

    # Vertical position
    if cy < img_h / 3:
        v = "top"
    elif cy > img_h * 2 / 3:
        v = "bottom"
    else:
        v = "middle"

    # Horizontal position
    if cx < img_w / 3:
        h = "left"
    elif cx > img_w * 2 / 3:
        h = "right"
    else:
        h = "center"

    if h == "center" and v == "middle":
        return "center"
    elif h == "center":
        return v
    elif v == "middle":
        return h

    return f"{v}-{h}"


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) > 1:
        result = analyze_image(sys.argv[1])
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Usage: python detector.py <image_path>")
