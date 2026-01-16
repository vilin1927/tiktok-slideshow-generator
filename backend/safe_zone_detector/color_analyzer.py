"""
Color Variance Analyzer

Analyzes color variance and brightness per grid cell.
Low variance = uniform color = good for text.
Determines optimal text color (white/black) based on brightness.
"""

import cv2
import numpy as np
from typing import Dict, Tuple


def analyze_colors(image_path: str, grid_size: int = 12) -> Dict:
    """
    Analyze color variance and brightness across a grid.

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension (default 12x12)

    Returns:
        Dictionary with:
        - variance_map: 2D array of color variance per cell (0-1 normalized)
        - brightness_map: 2D array of brightness per cell (0-1)
        - avg_colors: 2D array of average colors (hex strings)
        - text_color_map: 2D array of suggested text colors ("white" or "black")
        - cell_size: (width, height) of each grid cell
    """
    # Load image
    image = cv2.imread(image_path)
    if image is None:
        return {
            'variance_map': np.zeros((grid_size, grid_size)),
            'brightness_map': np.zeros((grid_size, grid_size)),
            'avg_colors': [['' for _ in range(grid_size)] for _ in range(grid_size)],
            'text_color_map': [['white' for _ in range(grid_size)] for _ in range(grid_size)],
            'cell_size': (0, 0)
        }

    h, w = image.shape[:2]

    # Convert to LAB color space for better perceptual analysis
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)

    # Calculate cell dimensions
    cell_w = w // grid_size
    cell_h = h // grid_size

    variance_map = np.zeros((grid_size, grid_size))
    brightness_map = np.zeros((grid_size, grid_size))
    avg_colors = [['' for _ in range(grid_size)] for _ in range(grid_size)]
    text_color_map = [['white' for _ in range(grid_size)] for _ in range(grid_size)]

    for row in range(grid_size):
        for col in range(grid_size):
            # Extract cell
            y1 = row * cell_h
            y2 = y1 + cell_h
            x1 = col * cell_w
            x2 = x1 + cell_w

            cell_bgr = image[y1:y2, x1:x2]
            cell_lab = lab[y1:y2, x1:x2]

            # Calculate color variance in LAB space
            # Higher variance = more color variation = harder to read text
            l_var = np.var(cell_lab[:, :, 0])
            a_var = np.var(cell_lab[:, :, 1])
            b_var = np.var(cell_lab[:, :, 2])

            # Combined variance (weighted)
            variance = (l_var + a_var + b_var) / 3

            # Calculate brightness (L channel in LAB, 0-255)
            brightness = np.mean(cell_lab[:, :, 0]) / 255.0

            # Calculate average color
            avg_bgr = np.mean(cell_bgr, axis=(0, 1)).astype(int)
            avg_hex = '#{:02x}{:02x}{:02x}'.format(
                avg_bgr[2], avg_bgr[1], avg_bgr[0]  # BGR to RGB
            )

            # Suggest text color based on brightness
            # Dark background (brightness < 0.5) = white text
            # Light background (brightness >= 0.5) = black text
            text_color = 'white' if brightness < 0.5 else 'black'

            variance_map[row, col] = variance
            brightness_map[row, col] = brightness
            avg_colors[row][col] = avg_hex
            text_color_map[row][col] = text_color

    # Normalize variance to 0-1 range
    max_variance = variance_map.max()
    if max_variance > 0:
        variance_map = variance_map / max_variance

    return {
        'variance_map': variance_map,
        'brightness_map': brightness_map,
        'avg_colors': avg_colors,
        'text_color_map': text_color_map,
        'cell_size': (cell_w, cell_h)
    }


def get_uniform_safe_zones(image_path: str, grid_size: int = 12, variance_threshold: float = 0.2) -> list:
    """
    Get uniform color areas as potential safe zones for text.

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension
        variance_threshold: Max variance to consider "uniform" (0-1)

    Returns:
        List of safe zone dictionaries for uniform color regions
    """
    result = analyze_colors(image_path, grid_size)

    # Load image to get dimensions
    image = cv2.imread(image_path)
    if image is None:
        return []

    h, w = image.shape[:2]
    cell_w, cell_h = result['cell_size']

    # Find cells with low variance (uniform color)
    safe_cells = []
    for row in range(grid_size):
        for col in range(grid_size):
            if result['variance_map'][row, col] < variance_threshold:
                safe_cells.append({
                    'row': row,
                    'col': col,
                    'variance': result['variance_map'][row, col],
                    'brightness': result['brightness_map'][row, col],
                    'avg_color': result['avg_colors'][row][col],
                    'text_color': result['text_color_map'][row][col]
                })

    return safe_cells


def get_text_color_for_region(image_path: str, x: int, y: int, w: int, h: int) -> str:
    """
    Determine optimal text color for a specific region.

    Args:
        image_path: Path to the image file
        x, y, w, h: Region coordinates

    Returns:
        "white" or "black" based on region brightness
    """
    image = cv2.imread(image_path)
    if image is None:
        return 'white'

    # Extract region
    region = image[y:y+h, x:x+w]

    # Convert to LAB and get brightness
    lab = cv2.cvtColor(region, cv2.COLOR_BGR2LAB)
    brightness = np.mean(lab[:, :, 0]) / 255.0

    return 'white' if brightness < 0.5 else 'black'
