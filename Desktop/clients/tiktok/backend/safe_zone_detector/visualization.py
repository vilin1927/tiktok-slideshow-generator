"""
Visualization Module - Generates debug images with zone overlays.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional


def draw_zones(
    image_path: str,
    analysis: Dict,
    output_path: str,
    show_labels: bool = True
) -> str:
    """
    Draw safe and avoid zones on an image for debugging.

    Args:
        image_path: Path to the original image
        analysis: Analysis result from detector.analyze_image()
        output_path: Path to save the debug image
        show_labels: Whether to show text labels

    Returns:
        Path to saved debug image
    """
    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]

    # Create overlay for transparency
    overlay = image.copy()

    # Colors (BGR format for OpenCV)
    SAFE_COLOR = (0, 255, 0)      # Green
    AVOID_COLOR = (0, 0, 255)     # Red
    RECOMMENDED_COLOR = (255, 200, 0)  # Cyan/Blue
    FACE_COLOR = (255, 0, 255)    # Magenta

    # Draw avoid zones first (red)
    for zone in analysis.get("avoid_zones", []):
        bounds = zone["bounds"]
        x, y, w, h = bounds["x"], bounds["y"], bounds["w"], bounds["h"]

        # Choose color based on type
        if zone.get("type") == "face":
            color = FACE_COLOR
        else:
            color = AVOID_COLOR

        # Semi-transparent fill
        cv2.rectangle(overlay, (x, y), (x + w, y + h), color, -1)

        # Solid border
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)

        # Label
        if show_labels:
            label = f"{zone.get('type', 'avoid')} ({zone.get('confidence', 0):.0%})"
            cv2.putText(image, label, (x + 5, y + 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    # Draw safe zones (green)
    recommended_idx = analysis.get("recommended_zone", 0)
    for i, zone in enumerate(analysis.get("safe_zones", [])):
        bounds = zone["bounds"]
        x, y, w, h = bounds["x"], bounds["y"], bounds["w"], bounds["h"]

        # Recommended zone gets special treatment
        if i == recommended_idx:
            color = RECOMMENDED_COLOR
            thickness = 3
        else:
            color = SAFE_COLOR
            thickness = 2

        # Semi-transparent fill
        cv2.rectangle(overlay, (x, y), (x + w, y + h), color, -1)

        # Solid border
        cv2.rectangle(image, (x, y), (x + w, y + h), color, thickness)

        # Label
        if show_labels:
            conf = zone.get("confidence", 0)
            max_chars = zone.get("max_chars_per_line", "?")
            max_lines = zone.get("max_lines", "?")
            label = f"#{i} {conf:.0%} ({max_chars}c x {max_lines}L)"

            # Position label
            label_y = y + 20 if y > 30 else y + h - 10
            cv2.putText(image, label, (x + 5, label_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            # Show text color suggestion
            text_color = zone.get("text_color_suggestion", "white")
            cv2.putText(image, f"text: {text_color}", (x + 5, label_y + 15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # Blend overlay with original (30% opacity for fills)
    alpha = 0.3
    cv2.addWeighted(overlay, alpha, image, 1 - alpha, 0, image)

    # Add legend
    legend_y = height - 80
    cv2.putText(image, "Legend:", (10, legend_y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.rectangle(image, (10, legend_y + 10), (25, legend_y + 25), SAFE_COLOR, -1)
    cv2.putText(image, "Safe Zone", (30, legend_y + 22),
               cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    cv2.rectangle(image, (120, legend_y + 10), (135, legend_y + 25), RECOMMENDED_COLOR, -1)
    cv2.putText(image, "Recommended", (140, legend_y + 22),
               cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    cv2.rectangle(image, (10, legend_y + 35), (25, legend_y + 50), AVOID_COLOR, -1)
    cv2.putText(image, "Avoid", (30, legend_y + 47),
               cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    cv2.rectangle(image, (80, legend_y + 35), (95, legend_y + 50), FACE_COLOR, -1)
    cv2.putText(image, "Face", (100, legend_y + 47),
               cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

    # Add metadata
    meta = analysis.get("analysis_metadata", {})
    face_count = meta.get("face_count", 0)
    complexity = meta.get("overall_complexity", "unknown")
    cv2.putText(image, f"Faces: {face_count} | Complexity: {complexity}",
               (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # Save output
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), image)

    return str(output_path)


def draw_grid(
    image_path: str,
    grid_data: Dict,
    output_path: str,
    value_map: np.ndarray,
    title: str = "Grid Analysis"
) -> str:
    """
    Draw a grid overlay showing cell values (edge density, color variance, etc.)

    Args:
        image_path: Path to the original image
        grid_data: Dict with grid_size, cell_size, image_size
        output_path: Path to save the debug image
        value_map: 2D array of values (0-1) per cell
        title: Title for the visualization

    Returns:
        Path to saved debug image
    """
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    height, width = image.shape[:2]
    grid_size = grid_data["grid_size"]
    cell_w = grid_data["cell_size"]["w"]
    cell_h = grid_data["cell_size"]["h"]

    # Draw grid with color-coded cells
    for row in range(grid_size):
        for col in range(grid_size):
            value = value_map[row, col]

            x1 = col * cell_w
            y1 = row * cell_h
            x2 = min(x1 + cell_w, width)
            y2 = min(y1 + cell_h, height)

            # Color based on value (red = high, green = low)
            r = int(255 * value)
            g = int(255 * (1 - value))
            color = (0, g, r)  # BGR

            # Draw cell border
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 1)

            # Show value
            cv2.putText(image, f"{value:.2f}", (x1 + 2, y1 + 12),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1)

    # Add title
    cv2.putText(image, title, (10, 25),
               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    # Save output
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), image)

    return str(output_path)


def create_combined_visualization(
    image_path: str,
    analysis: Dict,
    edge_data: Dict,
    color_data: Dict,
    output_path: str
) -> str:
    """
    Create a 2x2 grid showing original, zones, edge density, and color variance.
    """
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")

    h, w = image.shape[:2]

    # Resize to fit in grid
    target_w, target_h = w // 2, h // 2
    small = cv2.resize(image, (target_w, target_h))

    # Create output canvas
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    # Top-left: Original
    canvas[0:target_h, 0:target_w] = small
    cv2.putText(canvas, "Original", (10, 25),
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    # Other panels would require drawing zones on copies
    # For now, just save the zones visualization
    return draw_zones(image_path, analysis, output_path)


if __name__ == "__main__":
    print("Visualization module loaded.")
    print("Use draw_zones() to create debug images.")
