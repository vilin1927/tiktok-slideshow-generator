"""
Edge Density Analyzer - Detects busy areas using Canny edge detection.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict, Tuple


def analyze_edges(image_path: str, grid_size: int = 12) -> Dict:
    """
    Analyze edge density across a grid of cells.

    High edge density = busy area = avoid zone
    Low edge density = calm area = potential safe zone

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension (12 = 12x12 grid = 144 cells)

    Returns:
        Dict with:
        - density_map: 2D numpy array of edge density values (0-1)
        - busy_cells: List of (row, col) tuples for high-density cells
        - image_size: {"w": width, "h": height}
        - cell_size: {"w": cell_width, "h": cell_height}
    """
    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]

    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply Gaussian blur to reduce noise
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Canny edge detection
    edges = cv2.Canny(blurred, 50, 150)

    # Calculate cell dimensions
    cell_h = height // grid_size
    cell_w = width // grid_size

    # Create density map
    density_map = np.zeros((grid_size, grid_size), dtype=np.float32)

    for row in range(grid_size):
        for col in range(grid_size):
            # Extract cell region
            y1 = row * cell_h
            y2 = (row + 1) * cell_h if row < grid_size - 1 else height
            x1 = col * cell_w
            x2 = (col + 1) * cell_w if col < grid_size - 1 else width

            cell = edges[y1:y2, x1:x2]

            # Calculate edge density (ratio of edge pixels to total pixels)
            edge_pixels = np.count_nonzero(cell)
            total_pixels = cell.size
            density_map[row, col] = edge_pixels / total_pixels if total_pixels > 0 else 0

    # Normalize to 0-1 range (relative to max in this image)
    max_density = density_map.max()
    if max_density > 0:
        density_map = density_map / max_density

    # Identify busy cells (threshold: 0.5 = top 50% of edge density)
    busy_threshold = 0.5
    busy_cells = []
    for row in range(grid_size):
        for col in range(grid_size):
            if density_map[row, col] > busy_threshold:
                busy_cells.append((row, col))

    return {
        "density_map": density_map,
        "busy_cells": busy_cells,
        "image_size": {"w": width, "h": height},
        "cell_size": {"w": cell_w, "h": cell_h},
        "grid_size": grid_size
    }


def get_cell_bounds(row: int, col: int, cell_w: int, cell_h: int,
                    img_w: int, img_h: int, grid_size: int) -> Dict:
    """Convert grid cell coordinates to pixel bounds."""
    x = col * cell_w
    y = row * cell_h
    w = cell_w if col < grid_size - 1 else img_w - x
    h = cell_h if row < grid_size - 1 else img_h - y
    return {"x": x, "y": y, "w": w, "h": h}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = analyze_edges(sys.argv[1])
        print(f"Image size: {result['image_size']}")
        print(f"Cell size: {result['cell_size']}")
        print(f"Busy cells: {len(result['busy_cells'])} / {result['grid_size']**2}")
        print(f"Density map shape: {result['density_map'].shape}")
