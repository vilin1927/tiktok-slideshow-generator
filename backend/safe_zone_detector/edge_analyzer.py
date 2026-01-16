"""
Edge Density Analyzer

Uses Canny edge detection on a grid to identify busy vs calm areas.
High edge density = busy area = avoid zone.
"""

import cv2
import numpy as np
from typing import Dict, Tuple


def analyze_edges(image_path: str, grid_size: int = 12) -> Dict:
    """
    Analyze edge density across a grid.

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension (default 12x12)

    Returns:
        Dictionary with:
        - density_map: 2D numpy array of edge densities (0-1)
        - cell_size: (width, height) of each grid cell
        - busy_threshold: Threshold above which cells are considered busy
        - busy_cells: List of (row, col) tuples for busy cells
    """
    # Load image
    image = cv2.imread(image_path)
    if image is None:
        return {
            'density_map': np.zeros((grid_size, grid_size)),
            'cell_size': (0, 0),
            'busy_threshold': 0.3,
            'busy_cells': []
        }

    h, w = image.shape[:2]

    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply Gaussian blur to reduce noise
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Canny edge detection
    edges = cv2.Canny(blurred, 50, 150)

    # Calculate cell dimensions
    cell_w = w // grid_size
    cell_h = h // grid_size

    # Calculate edge density for each cell
    density_map = np.zeros((grid_size, grid_size))

    for row in range(grid_size):
        for col in range(grid_size):
            # Extract cell
            y1 = row * cell_h
            y2 = y1 + cell_h
            x1 = col * cell_w
            x2 = x1 + cell_w

            cell = edges[y1:y2, x1:x2]

            # Calculate density (ratio of edge pixels to total pixels)
            total_pixels = cell.size
            edge_pixels = np.count_nonzero(cell)
            density = edge_pixels / total_pixels if total_pixels > 0 else 0

            density_map[row, col] = density

    # Normalize density to 0-1 range
    max_density = density_map.max()
    if max_density > 0:
        density_map = density_map / max_density

    # Identify busy cells (density > threshold)
    busy_threshold = 0.3
    busy_cells = []
    for row in range(grid_size):
        for col in range(grid_size):
            if density_map[row, col] > busy_threshold:
                busy_cells.append((row, col))

    return {
        'density_map': density_map,
        'cell_size': (cell_w, cell_h),
        'busy_threshold': busy_threshold,
        'busy_cells': busy_cells
    }


def get_busy_avoid_zones(image_path: str, grid_size: int = 12) -> list:
    """
    Get busy areas as avoid zones for text placement.

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension

    Returns:
        List of avoid zone dictionaries for contiguous busy regions
    """
    result = analyze_edges(image_path, grid_size)

    if len(result['busy_cells']) == 0:
        return []

    # Load image to get dimensions
    image = cv2.imread(image_path)
    if image is None:
        return []

    h, w = image.shape[:2]
    cell_w, cell_h = result['cell_size']

    # Group contiguous busy cells into regions
    # For simplicity, return individual cells as avoid zones
    # Could be optimized to merge adjacent cells

    avoid_zones = []
    visited = set()

    def flood_fill(row, col, cells):
        """Find all connected busy cells."""
        if (row, col) in visited:
            return
        if (row, col) not in cells:
            return

        visited.add((row, col))

        # Check 4-connected neighbors
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = row + dr, col + dc
            if 0 <= nr < grid_size and 0 <= nc < grid_size:
                flood_fill(nr, nc, cells)

    busy_set = set(result['busy_cells'])

    for row, col in result['busy_cells']:
        if (row, col) not in visited:
            # Start new region
            region_cells = []
            region_visited_before = len(visited)
            flood_fill(row, col, busy_set)

            # Get cells that belong to this region
            region_cells = [(r, c) for r, c in visited
                          if (r, c) not in [(x, y) for x, y in list(visited)[:region_visited_before]]]

            if region_cells:
                # Calculate bounding box for region
                min_row = min(r for r, c in region_cells)
                max_row = max(r for r, c in region_cells)
                min_col = min(c for r, c in region_cells)
                max_col = max(c for r, c in region_cells)

                x = min_col * cell_w
                y = min_row * cell_h
                region_w = (max_col - min_col + 1) * cell_w
                region_h = (max_row - min_row + 1) * cell_h

                # Calculate average density for confidence
                avg_density = np.mean([result['density_map'][r, c] for r, c in region_cells])

                avoid_zones.append({
                    'type': 'busy_area',
                    'bounds': {'x': x, 'y': y, 'w': region_w, 'h': region_h},
                    'confidence': round(float(avg_density), 3)
                })

    return avoid_zones
