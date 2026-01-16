"""
Safe Zone Detector - Main Module

Combines all detection modules to find safe areas for text placement.
Outputs proposed zones that can be validated by Gemini.

Performance: Uses downsampling for faster CV analysis.
"""

import cv2

# Disable OpenCV threading to prevent SIGSEGV in forked processes (Celery)
cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)

import numpy as np
import os
import tempfile
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

from .face_detector import get_face_avoid_zones
from .edge_analyzer import analyze_edges
from .color_analyzer import analyze_colors, get_text_color_for_region
from .saliency import get_saliency_avoid_zone

# Analysis resolution (downsample large images for faster processing)
ANALYSIS_MAX_DIM = 1024


@dataclass
class SafeZoneResult:
    """Result of safe zone analysis."""
    image_size: Dict[str, int]
    safe_zones: List[Dict]
    avoid_zones: List[Dict]
    recommended_zone: Optional[int]
    analysis_metadata: Dict

    def to_dict(self) -> Dict:
        return asdict(self)


def _create_downsampled_image(image_path: str, max_dim: int = ANALYSIS_MAX_DIM) -> Tuple[str, float, bool]:
    """
    Create a downsampled version of the image for faster analysis.

    Returns:
        Tuple of (path_to_use, scale_factor, needs_cleanup)
        - If image is already small enough, returns original path with scale=1.0
        - Otherwise returns temp file path with scale factor
    """
    image = cv2.imread(image_path)
    if image is None:
        return image_path, 1.0, False

    h, w = image.shape[:2]

    # Check if downsampling needed
    if max(h, w) <= max_dim:
        return image_path, 1.0, False

    # Calculate scale factor
    scale = max_dim / max(h, w)
    new_w = int(w * scale)
    new_h = int(h * scale)

    # Resize image
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Save to temp file
    temp_fd, temp_path = tempfile.mkstemp(suffix='.jpg')
    os.close(temp_fd)
    cv2.imwrite(temp_path, resized, [cv2.IMWRITE_JPEG_QUALITY, 85])

    return temp_path, scale, True


def _scale_bounds(bounds: Dict, scale: float) -> Dict:
    """Scale bounds coordinates back to original image size."""
    return {
        'x': int(bounds['x'] / scale),
        'y': int(bounds['y'] / scale),
        'w': int(bounds['w'] / scale),
        'h': int(bounds['h'] / scale)
    }


def analyze_image(
    image_path: str,
    grid_size: int = 12,
    min_zone_width: int = 200,
    min_zone_height: int = 100
) -> SafeZoneResult:
    """
    Analyze image to find safe zones for text placement.

    This is the main entry point. Combines:
    - Face detection (avoid faces)
    - Edge density (avoid busy areas)
    - Color variance (prefer uniform areas)
    - Saliency (avoid main subject)

    Performance: Downsamples large images to 1024px for faster CV analysis.

    Args:
        image_path: Path to the image file
        grid_size: Number of cells per dimension for analysis
        min_zone_width: Minimum safe zone width in pixels
        min_zone_height: Minimum safe zone height in pixels

    Returns:
        SafeZoneResult with safe_zones, avoid_zones, and recommended zone
    """
    # Load image to get original dimensions
    image = cv2.imread(image_path)
    if image is None:
        return SafeZoneResult(
            image_size={'w': 0, 'h': 0},
            safe_zones=[],
            avoid_zones=[],
            recommended_zone=None,
            analysis_metadata={'error': 'Could not load image'}
        )

    h, w = image.shape[:2]

    # Create downsampled version for faster analysis
    analysis_path, scale, needs_cleanup = _create_downsampled_image(image_path)

    try:
        # Collect all avoid zones (using downsampled image)
        avoid_zones = []

        # 1. Face detection
        face_zones = get_face_avoid_zones(analysis_path)
        avoid_zones.extend(face_zones)

        # 2. Main subject detection
        subject_zone = get_saliency_avoid_zone(analysis_path)
        if subject_zone:
            avoid_zones.append(subject_zone)

        # 3. Analyze edges and colors
        edge_result = analyze_edges(analysis_path, grid_size)
        color_result = analyze_colors(analysis_path, grid_size)
    finally:
        # Clean up temp file
        if needs_cleanup and os.path.exists(analysis_path):
            os.remove(analysis_path)

    # Scale avoid zone bounds back to original resolution
    for zone in avoid_zones:
        zone['bounds'] = _scale_bounds(zone['bounds'], scale)

    cell_w = w // grid_size
    cell_h = h // grid_size

    # Build grid of safe/unsafe cells
    # A cell is safe if:
    # - Low edge density (< 0.3)
    # - Low color variance (< 0.3)
    # - Not overlapping with avoid zones

    safe_grid = np.ones((grid_size, grid_size), dtype=bool)

    # Mark cells as unsafe based on edge density
    for row in range(grid_size):
        for col in range(grid_size):
            if edge_result['density_map'][row, col] > 0.3:
                safe_grid[row, col] = False

    # Mark cells as unsafe based on color variance
    for row in range(grid_size):
        for col in range(grid_size):
            if color_result['variance_map'][row, col] > 0.3:
                safe_grid[row, col] = False

    # Mark cells as unsafe if they overlap with avoid zones
    for zone in avoid_zones:
        bounds = zone['bounds']
        # Convert bounds to grid coordinates
        start_col = max(0, bounds['x'] // cell_w)
        end_col = min(grid_size, (bounds['x'] + bounds['w']) // cell_w + 1)
        start_row = max(0, bounds['y'] // cell_h)
        end_row = min(grid_size, (bounds['y'] + bounds['h']) // cell_h + 1)

        for row in range(start_row, end_row):
            for col in range(start_col, end_col):
                safe_grid[row, col] = False

    # Find contiguous safe regions
    safe_zones = _find_safe_regions(
        safe_grid, w, h, cell_w, cell_h,
        edge_result, color_result,
        min_zone_width, min_zone_height,
        image_path
    )

    # Score and rank safe zones
    for zone in safe_zones:
        zone['confidence'] = _calculate_zone_confidence(
            zone, edge_result, color_result
        )

    # Sort by confidence
    safe_zones.sort(key=lambda z: z['confidence'], reverse=True)

    # Add position labels
    for zone in safe_zones:
        zone['position'] = _get_position_label(zone['bounds'], w, h)

    # Select recommended zone
    recommended_zone = 0 if safe_zones else None

    # Build metadata
    metadata = {
        'face_count': len(face_zones),
        'has_main_subject': subject_zone is not None,
        'grid_size': grid_size,
        'total_safe_cells': int(np.sum(safe_grid)),
        'total_cells': grid_size * grid_size
    }

    # Add busy areas to avoid_zones
    for row, col in edge_result['busy_cells']:
        # Only add if not already covered by another avoid zone
        cell_x = col * cell_w
        cell_y = row * cell_h

        # Check if this cell is already in an avoid zone
        already_covered = False
        for zone in avoid_zones:
            zb = zone['bounds']
            if (cell_x >= zb['x'] and cell_x < zb['x'] + zb['w'] and
                cell_y >= zb['y'] and cell_y < zb['y'] + zb['h']):
                already_covered = True
                break

        if not already_covered and edge_result['density_map'][row, col] > 0.5:
            avoid_zones.append({
                'type': 'busy_area',
                'bounds': {'x': cell_x, 'y': cell_y, 'w': cell_w, 'h': cell_h},
                'confidence': round(float(edge_result['density_map'][row, col]), 3)
            })

    return SafeZoneResult(
        image_size={'w': w, 'h': h},
        safe_zones=safe_zones,
        avoid_zones=avoid_zones,
        recommended_zone=recommended_zone,
        analysis_metadata=metadata
    )


def _find_safe_regions(
    safe_grid: np.ndarray,
    img_w: int, img_h: int,
    cell_w: int, cell_h: int,
    edge_result: Dict, color_result: Dict,
    min_width: int, min_height: int,
    image_path: str
) -> List[Dict]:
    """Find contiguous safe regions from the grid."""
    grid_size = safe_grid.shape[0]
    visited = np.zeros_like(safe_grid, dtype=bool)
    regions = []

    def flood_fill(start_row: int, start_col: int) -> List[Tuple[int, int]]:
        """Find all connected safe cells."""
        cells = []
        stack = [(start_row, start_col)]

        while stack:
            row, col = stack.pop()

            if (row < 0 or row >= grid_size or col < 0 or col >= grid_size):
                continue
            if visited[row, col] or not safe_grid[row, col]:
                continue

            visited[row, col] = True
            cells.append((row, col))

            # Add 4-connected neighbors
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
            if safe_grid[row, col] and not visited[row, col]:
                cells = flood_fill(row, col)

                if len(cells) >= 2:  # At least 2 cells for a valid region
                    # Calculate bounding box
                    min_row = min(r for r, c in cells)
                    max_row = max(r for r, c in cells)
                    min_col = min(c for r, c in cells)
                    max_col = max(c for r, c in cells)

                    x = min_col * cell_w
                    y = min_row * cell_h
                    w = (max_col - min_col + 1) * cell_w
                    h = (max_row - min_row + 1) * cell_h

                    # Check minimum size
                    if w >= min_width and h >= min_height:
                        # Get text color suggestion for this region
                        text_color = get_text_color_for_region(image_path, x, y, w, h)

                        # Calculate average brightness
                        brightness_values = [
                            color_result['brightness_map'][r, c]
                            for r, c in cells
                        ]
                        avg_brightness = sum(brightness_values) / len(brightness_values)

                        # Get average color
                        mid_row = (min_row + max_row) // 2
                        mid_col = (min_col + max_col) // 2
                        avg_color = color_result['avg_colors'][mid_row][mid_col]

                        regions.append({
                            'bounds': {'x': x, 'y': y, 'w': w, 'h': h},
                            'cell_count': len(cells),
                            'avg_brightness': round(avg_brightness, 3),
                            'avg_color': avg_color,
                            'text_color_suggestion': text_color
                        })

    return regions


def _calculate_zone_confidence(
    zone: Dict,
    edge_result: Dict,
    color_result: Dict
) -> float:
    """Calculate confidence score for a safe zone.

    CRITICAL: Avoid center of image where products/subjects are.
    Prefer top or bottom edges for TikTok-style text placement.
    """
    bounds = zone['bounds']
    cell_w, cell_h = edge_result['cell_size']
    grid_size = edge_result['density_map'].shape[0]

    # Get image dimensions from grid
    img_h = grid_size * cell_h
    img_w = grid_size * cell_w

    # Get cells in this zone
    start_col = bounds['x'] // cell_w
    end_col = min(grid_size, (bounds['x'] + bounds['w']) // cell_w)
    start_row = bounds['y'] // cell_h
    end_row = min(grid_size, (bounds['y'] + bounds['h']) // cell_h)

    # Average edge density (lower = better)
    edge_values = []
    variance_values = []

    for row in range(start_row, end_row):
        for col in range(start_col, end_col):
            edge_values.append(edge_result['density_map'][row, col])
            variance_values.append(color_result['variance_map'][row, col])

    avg_edge = sum(edge_values) / len(edge_values) if edge_values else 0.5
    avg_variance = sum(variance_values) / len(variance_values) if variance_values else 0.5

    # Size factor (normalize to 0-1 based on zone count)
    size_factor = min(1.0, zone['cell_count'] / 20)

    # Edge factor (lower density = higher score)
    edge_factor = 1.0 - avg_edge

    # Variance factor (lower variance = higher score)
    variance_factor = 1.0 - avg_variance

    # POSITION FACTOR - Critical for avoiding product/subject
    # Zone center position
    zone_center_y = bounds['y'] + bounds['h'] / 2
    zone_center_x = bounds['x'] + bounds['w'] / 2

    # Calculate vertical position score (0 = center, 1 = top/bottom edge)
    # Center 40% of image gets heavily penalized
    y_ratio = zone_center_y / img_h
    if 0.30 <= y_ratio <= 0.70:
        # Zone is in center 40% - heavy penalty
        position_factor = 0.1
    elif y_ratio < 0.25 or y_ratio > 0.75:
        # Zone is in top/bottom 25% - big boost
        position_factor = 1.0
    else:
        # Transition zones
        position_factor = 0.5

    # Also penalize horizontal center (products often centered)
    x_ratio = zone_center_x / img_w
    if 0.25 <= x_ratio <= 0.75:
        # Zone is horizontally centered - slight penalty
        position_factor *= 0.8

    # Combined confidence with position heavily weighted
    confidence = (
        size_factor * 0.15 +
        edge_factor * 0.20 +
        variance_factor * 0.20 +
        position_factor * 0.45  # Position is most important
    )

    return round(confidence, 3)


def _get_position_label(bounds: Dict, img_w: int, img_h: int) -> str:
    """Get a human-readable position label for a zone."""
    center_x = bounds['x'] + bounds['w'] / 2
    center_y = bounds['y'] + bounds['h'] / 2

    # Determine horizontal position
    if center_x < img_w / 3:
        h_pos = 'left'
    elif center_x > 2 * img_w / 3:
        h_pos = 'right'
    else:
        h_pos = 'center'

    # Determine vertical position
    if center_y < img_h / 3:
        v_pos = 'top'
    elif center_y > 2 * img_h / 3:
        v_pos = 'bottom'
    else:
        v_pos = 'middle'

    if h_pos == 'center' and v_pos == 'middle':
        return 'center'
    elif h_pos == 'center':
        return v_pos
    elif v_pos == 'middle':
        return h_pos

    return f"{v_pos}-{h_pos}"
