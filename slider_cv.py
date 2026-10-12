#!/usr/bin/env python3
"""Local Image Processing Slider CAPTCHA Solver.

Uses OpenCV computer vision techniques (Canny edge detection, template matching,
and contour filtering) to accurately locate sliding puzzle cutout coordinates locally.

Usage:
  1. CLI tool on saved images:
     python slider_cv.py --bg background.png --piece piece.png
     python slider_cv.py --bg background.png --detect-hole

  2. Imported as a module:
     from slider_cv import find_slider_displacement
     offset = find_slider_displacement(bg_bytes, piece_bytes, scale=0.72)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


def decode_image_bytes(data: bytes, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Decode raw image bytes into an OpenCV numpy array."""
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, flags)
    if img is None:
        raise ValueError("Could not decode image bytes into an OpenCV array.")
    return img


def match_template_edges(
    bg_img: np.ndarray,
    piece_img: np.ndarray,
    min_x: int = 50,
) -> Tuple[int, int, float]:
    """Find cutout coordinates using edge-based template matching.

    Args:
        bg_img: Background image (BGR or Grayscale).
        piece_img: Cutout piece image (RGBA or BGR).
        min_x: Minimum X coordinate to consider (ignores piece's start area).

    Returns:
        (best_x, best_y, confidence_score)
    """
    # 1. Background edge map enhanced via LAB color space (L* channel + CLAHE)
    # The L* channel isolates pure perceptual lightness, which handles all color tints
    # (purple, blue, warm gradients) and makes dark cutout holes pop out clearly.
    if len(bg_img.shape) == 3:
        lab = cv2.cvtColor(bg_img, cv2.COLOR_BGR2LAB)
        l_channel, _, _ = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l_channel)
        bg_blurred = cv2.GaussianBlur(l_enhanced, (3, 3), 0)
        bg_edges = cv2.Canny(bg_blurred, 70, 160)
    else:
        bg_blurred = cv2.GaussianBlur(bg_img, (3, 3), 0)
        bg_edges = cv2.Canny(bg_blurred, 70, 160)

    # 2. Piece edge map (leveraging alpha mask if present)
    if len(piece_img.shape) == 3 and piece_img.shape[2] == 4:
        alpha_channel = piece_img[:, :, 3]
        _, alpha_thresh = cv2.threshold(alpha_channel, 100, 255, cv2.THRESH_BINARY)
        piece_edges = cv2.Canny(alpha_thresh, 100, 200)
    else:
        if len(piece_img.shape) == 3:
            piece_gray = cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)
        else:
            piece_gray = piece_img
        piece_edges = cv2.Canny(piece_gray, 80, 180)

    # 3. Normalized cross-correlation on edges
    res = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)

    # Invalidate starting zone where the piece sits initially
    if min_x > 0 and min_x < res.shape[1]:
        res[:, :min_x] = -1.0

    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    best_x, best_y = max_loc

    return int(best_x), int(best_y), float(max_val)


def detect_hole_by_contours(
    bg_img: np.ndarray,
    target_y: Optional[int] = None,
    y_tolerance: int = 25,
    min_x: int = 50,
    expected_size: Tuple[int, int] = (42, 58),
) -> Tuple[int, int, float]:
    """Detect cutout hole using morphological contours and shadow detection.

    Useful when no separate piece image is available or when the piece is circular.

    Returns:
        (best_x, best_y, confidence)
    """
    if len(bg_img.shape) == 3:
        lab = cv2.cvtColor(bg_img, cv2.COLOR_BGR2LAB)
        l_channel, _, _ = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        bg_gray = clahe.apply(l_channel)
    else:
        bg_gray = bg_img

    h_img, w_img = bg_gray.shape
    blurred = cv2.GaussianBlur(bg_gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    # Morphological dilation to connect outline segments of the cutout
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    min_w, max_w = expected_size[0] - 12, expected_size[1] + 15
    min_h, max_h = expected_size[0] - 12, expected_size[1] + 15

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        # Basic geometry checks
        if x < min_x:
            continue
        if not (min_w <= w <= max_w and min_h <= h <= max_h):
            continue

        # Aspect ratio check (slider cutouts are roughly square/circular: 0.7 - 1.4)
        aspect = float(w) / float(h)
        if aspect < 0.7 or aspect > 1.4:
            continue

        # If a target Y coordinate is known, filter out candidates outside band
        if target_y is not None:
            if abs(y - target_y) > y_tolerance:
                continue

        # Cutouts typically have lower average brightness than the surrounding area
        patch = bg_gray[y : y + h, x : x + w]
        mean_brightness = float(np.mean(patch))

        # Score candidate based on dark intensity and proximity to target Y
        y_dist = abs(y - target_y) if target_y is not None else 0
        score = 255.0 - mean_brightness - (y_dist * 2.0)
        candidates.append((x, y, w, h, score))

    if candidates:
        candidates.sort(key=lambda c: c[4], reverse=True)
        best_x, best_y, _, _, best_score = candidates[0]
        return best_x, best_y, best_score

    # Fallback to middle-right if no contour matches
    return int(w_img * 0.55), target_y or int(h_img // 2), 0.0


def find_slider_displacement(
    bg_data: bytes,
    piece_data: Optional[bytes] = None,
    viewport_scale: float = 0.72,
    starting_left_px: float = 21.0,
    target_y_288: Optional[float] = None,
    piece_width_px: float = 37.0,
    fine_tune_offset_px: float = 0.0,
) -> int:
    """High-level function to calculate slider drag distance in browser coordinates.

    Args:
        bg_data: Raw bytes of the background canvas.
        piece_data: Optional raw bytes of the cutout piece image.
        viewport_scale: CSS scale factor (e.g. 288px / 400px = 0.72).
        starting_left_px: Initial X position of the slider button/piece in CSS px.
        target_y_288: Optional Y coordinate in CSS px (from sub-slider margin-top).
        piece_width_px: Width calibration (default 37.0px nudges slightly right into dead-center).
        fine_tune_offset_px: Optional pixel adjustment (+ nudges right, - nudges left).

    Returns:
        drag_distance: Exact integer pixels to slide in the browser.
    """
    bg_img = decode_image_bytes(bg_data, cv2.IMREAD_COLOR)

    if piece_data:
        piece_img = decode_image_bytes(piece_data, cv2.IMREAD_UNCHANGED)
        x_400, _, _ = match_template_edges(bg_img, piece_img, min_x=int(starting_left_px / viewport_scale + 20))
    else:
        target_y_400 = int(round(target_y_288 / viewport_scale)) if target_y_288 else None
        x_400, _, _ = detect_hole_by_contours(
            bg_img,
            target_y=target_y_400,
            min_x=int(starting_left_px / viewport_scale + 20),
        )

    # Convert 400px image space into 288px CSS browser space
    hole_x_css = x_400 * viewport_scale
    # Calibrated shift: centers the piece accurately into the hole
    drag_distance = int(round(hole_x_css - starting_left_px - piece_width_px + fine_tune_offset_px))
    return max(0, drag_distance)


def annotate_and_save(
    bg_path: Path,
    x: int,
    y: int,
    w: int = 50,
    h: int = 50,
    output_path: Path = Path("slider_detected.png"),
) -> None:
    """Save an annotated visual confirmation image for debugging."""
    img = cv2.imread(str(bg_path))
    if img is None:
        return
    # Green bounding box around detected cutout
    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.circle(img, (x + w // 2, y + h // 2), 4, (0, 0, 255), -1)
    cv2.putText(
        img,
        f"Hole: ({x}, {y})",
        (x, max(15, y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(output_path), img)
    print(f"Annotated visualization saved to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local OpenCV Slider CAPTCHA Processor.")
    parser.add_argument("--bg", type=Path, required=True, help="Path to background image (e.g. bg.png)")
    parser.add_argument("--piece", type=Path, default=None, help="Path to cutout piece image (e.g. piece.png)")
    parser.add_argument("--target-y", type=int, default=None, help="Target Y coordinate in 400 space (optional)")
    parser.add_argument("--output", type=Path, default=Path("detected_hole.png"), help="Path to save visual detection output")
    args = parser.parse_args()

    if not args.bg.exists():
        print(f"Error: Background file not found: {args.bg}")
        return 1

    bg_img = cv2.imread(str(args.bg), cv2.IMREAD_COLOR)

    if args.piece and args.piece.exists():
        piece_img = cv2.imread(str(args.piece), cv2.IMREAD_UNCHANGED)
        x, y, score = match_template_edges(bg_img, piece_img)
        print(f"[Template Match] Found cutout at: X={x}, Y={y} (Confidence: {score:.3f})")
        pw, ph = piece_img.shape[1], piece_img.shape[0]
    else:
        x, y, score = detect_hole_by_contours(bg_img, target_y=args.target_y)
        print(f"[Contour Detect] Found cutout at: X={x}, Y={y} (Score: {score:.1f})")
        pw, ph = 50, 50

    annotate_and_save(args.bg, x, y, pw, ph, args.output)

    # In 288 coordinate space (scale 0.72)
    x_288 = x * 0.72
    dist_288 = int(round(x_288 - 21.0))
    print(f"[Browser Coordinate] 288-space X: {x_288:.1f}px | Drag Distance: {dist_288}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
