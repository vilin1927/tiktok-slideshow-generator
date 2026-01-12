"""
Color Variance Analyzer - Detects uniform color regions suitable for text.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict, Tuple


def analyze_colors(image_path: str, grid_size: int = 12) -> Dict:
    """
    Analyze color variance and brightness across a grid of cells.

    Low variance = uniform color = good for text overlay
    High variance = busy/textured area = avoid

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension (12 = 12x12 grid)

    Returns:
        Dict with:
        - variance_map: 2D array of color variance values (0-1, normalized)
        - brightness_map: 2D array of brightness values (0-1)
        - color_map: 2D array of average hex colors per cell
        - text_color_map: 2D array of suggested text colors ("white" or "black")
        - uniform_cells: List of (row, col) tuples for low-variance cells
    """
    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]

    # Convert to RGB and LAB for analysis
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)

    # Calculate cell dimensions
    cell_h = height // grid_size
    cell_w = width // grid_size

    # Create maps
    variance_map = np.zeros((grid_size, grid_size), dtype=np.float32)
    brightness_map = np.zeros((grid_size, grid_size), dtype=np.float32)
    color_map = [[None for _ in range(grid_size)] for _ in range(grid_size)]
    text_color_map = [[None for _ in range(grid_size)] for _ in range(grid_size)]

    for row in range(grid_size):
        for col in range(grid_size):
            # Extract cell region
            y1 = row * cell_h
            y2 = (row + 1) * cell_h if row < grid_size - 1 else height
            x1 = col * cell_w
            x2 = (col + 1) * cell_w if col < grid_size - 1 else width

            cell_rgb = rgb[y1:y2, x1:x2]
            cell_lab = lab[y1:y2, x1:x2]

            # Calculate color variance (standard deviation in LAB space)
            # LAB is more perceptually uniform than RGB
            l_std = np.std(cell_lab[:, :, 0])
            a_std = np.std(cell_lab[:, :, 1])
            b_std = np.std(cell_lab[:, :, 2])
            variance = (l_std + a_std + b_std) / 3.0
            variance_map[row, col] = variance

            # Calculate brightness (L channel in LAB, 0-255 -> 0-1)
            brightness = np.mean(cell_lab[:, :, 0]) / 255.0
            brightness_map[row, col] = brightness

            # Calculate average color
            avg_r = int(np.mean(cell_rgb[:, :, 0]))
            avg_g = int(np.mean(cell_rgb[:, :, 1]))
            avg_b = int(np.mean(cell_rgb[:, :, 2]))
            color_map[row][col] = f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}"

            # Suggest text color based on brightness
            # Dark background (< 0.5) -> white text
            # Light background (>= 0.5) -> black text
            text_color_map[row][col] = "white" if brightness < 0.5 else "black"

    # Normalize variance to 0-1 range
    max_variance = variance_map.max()
    if max_variance > 0:
        variance_map = variance_map / max_variance

    # Identify uniform cells (low variance threshold: 0.3)
    uniform_threshold = 0.3
    uniform_cells = []
    for row in range(grid_size):
        for col in range(grid_size):
            if variance_map[row, col] < uniform_threshold:
                uniform_cells.append((row, col))

    return {
        "variance_map": variance_map,
        "brightness_map": brightness_map,
        "color_map": color_map,
        "text_color_map": text_color_map,
        "uniform_cells": uniform_cells,
        "image_size": {"w": width, "h": height},
        "cell_size": {"w": cell_w, "h": cell_h},
        "grid_size": grid_size
    }


def get_dominant_text_color(brightness_map: np.ndarray, cells: list) -> str:
    """Get the most common text color suggestion for a set of cells."""
    if not cells:
        return "white"

    dark_count = sum(1 for r, c in cells if brightness_map[r, c] < 0.5)
    return "white" if dark_count > len(cells) / 2 else "black"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = analyze_colors(sys.argv[1])
        print(f"Image size: {result['image_size']}")
        print(f"Uniform cells: {len(result['uniform_cells'])} / {result['grid_size']**2}")
        print(f"Brightness range: {result['brightness_map'].min():.2f} - {result['brightness_map'].max():.2f}")
