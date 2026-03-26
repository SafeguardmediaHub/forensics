"""
Forensic Primitives
-------------------
Shared low-level image analysis functions used by both frame_analysis.py
and visual_forens.py. Neither module should import from the other; both
import from here instead, keeping them fully decoupled.
"""

import os
from datetime import datetime

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage


# ============================================================================
# ERROR LEVEL ANALYSIS
# ============================================================================

def error_level_analysis(image_path, output_path, quality=95):
    """
    Perform Error Level Analysis on an image file.

    Saves a JET-colourmap heatmap to *output_path* and returns a dict with:
      ela_score        – mean absolute pixel difference (higher ⟹ more suspect)
      ela_image        – path to the saved heatmap
      interpretation   – 'High' | 'Medium' | 'Low'
    """
    try:
        original = Image.open(image_path)

        if original.mode != 'RGB':
            original = original.convert('RGB')

        temp_path = output_path.replace('.png', '_temp.jpg')
        original.save(temp_path, 'JPEG', quality=quality)

        compressed = Image.open(temp_path)

        original_arr  = np.array(original).astype(float)
        compressed_arr = np.array(compressed).astype(float)

        diff = np.abs(original_arr - compressed_arr)
        diff_enhanced = (diff * 10).clip(0, 255).astype(np.uint8)

        heatmap = cv2.applyColorMap(
            cv2.cvtColor(diff_enhanced, cv2.COLOR_RGB2GRAY),
            cv2.COLORMAP_JET,
        )

        timestamp_text = f"ELA Analysis - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.putText(heatmap, timestamp_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imwrite(output_path, heatmap)

        manipulation_score = np.mean(diff)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        return {
            'ela_score': round(manipulation_score, 2),
            'ela_image': output_path,
            'interpretation': (
                'High'   if manipulation_score > 15 else
                'Medium' if manipulation_score > 8  else
                'Low'
            ),
        }

    except Exception as e:
        return {'error': f'ELA failed: {str(e)}'}


# ============================================================================
# NOISE ANALYSIS
# ============================================================================

def noise_analysis(image_path, output_path):
    """
    Analyse local noise variance across an image.

    Saves a HOT-colourmap heatmap to *output_path* and returns a dict with:
      noise_inconsistency_score – std-dev of local variance (higher ⟹ more suspect)
      noise_heatmap             – path to saved heatmap
      interpretation            – 'High' | 'Medium' | 'Low'
    """
    try:
        img  = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        noise   = cv2.subtract(gray, blurred)

        noise_float    = noise.astype(float)
        local_variance = ndimage.generic_filter(noise_float, np.var, size=15)

        normalized = cv2.normalize(local_variance, None, 0, 255,
                                   cv2.NORM_MINMAX).astype(np.uint8)
        heatmap = cv2.applyColorMap(normalized, cv2.COLORMAP_HOT)

        timestamp_text = f"Noise Analysis - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.putText(heatmap, timestamp_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imwrite(output_path, heatmap)

        noise_std = np.std(local_variance)

        return {
            'noise_inconsistency_score': round(noise_std, 2),
            'noise_heatmap': output_path,
            'interpretation': (
                'High'   if noise_std > 50 else
                'Medium' if noise_std > 25 else
                'Low'
            ),
        }

    except Exception as e:
        return {'error': f'Noise analysis failed: {str(e)}'}


# ============================================================================
# COPY-MOVE DETECTION  (SIFT primary, DCT fallback)
# ============================================================================

def copy_move_detection_sift(image_path, output_path):
    """
    Copy-Move Detection using SIFT (Scale-Invariant Feature Transform).

    More robust than DCT block matching — detects rotation/scaling-invariant
    clones.  Falls back to :func:`copy_move_detection_dct_fallback` if SIFT
    fails or too few keypoints are found.

    Returns a dict with:
      clone_score        – percentage of image area flagged as suspicious
      matches_found      – number of spatially-distinct clone matches
      clone_heatmap      – path to saved overlay image
      method             – detector used
      interpretation     – 'High' | 'Medium' | 'Low'
    """
    try:
        img  = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Resize if too large
        max_dim = 1200
        height, width = gray.shape
        if max(height, width) > max_dim:
            scale = max_dim / max(height, width)
            gray = cv2.resize(gray, None, fx=scale, fy=scale)
            img  = cv2.resize(img,  None, fx=scale, fy=scale)
            height, width = gray.shape

        sift = cv2.SIFT_create(nfeatures=1000)
        keypoints, descriptors = sift.detectAndCompute(gray, None)

        if descriptors is None or len(keypoints) < 10:
            return {
                'clone_score':    0,
                'matches_found':  0,
                'clone_heatmap':  output_path,
                'interpretation': 'Low',
                'message':        'Insufficient keypoints detected',
            }

        FLANN_INDEX_KDTREE = 1
        index_params  = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        flann = cv2.FlannBasedMatcher(index_params, search_params)

        # k=3: index-0 is always the self-match; use indices 1 & 2 for ratio test
        matches = flann.knnMatch(descriptors, descriptors, k=3)

        suspicious_regions = np.zeros(gray.shape, dtype=np.uint8)
        good_matches  = []
        match_count   = 0
        min_spatial_distance = 50

        for match_group in matches:
            if len(match_group) < 3:
                continue

            m = match_group[1]
            n = match_group[2]

            if m.distance < 0.7 * n.distance:
                pt1 = keypoints[m.queryIdx].pt
                pt2 = keypoints[m.trainIdx].pt

                spatial_dist = np.sqrt(
                    (pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2
                )

                if spatial_dist > min_spatial_distance:
                    good_matches.append(m)
                    match_count += 1

                    x1, y1 = int(pt1[0]), int(pt1[1])
                    x2, y2 = int(pt2[0]), int(pt2[1])
                    cv2.circle(suspicious_regions, (x1, y1), 20, 255, -1)
                    cv2.circle(suspicious_regions, (x2, y2), 20, 255, -1)

        heatmap = cv2.applyColorMap(suspicious_regions, cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(img, 0.6, heatmap, 0.4, 0)

        timestamp_text = (
            f"SIFT Clone Detection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        cv2.putText(overlay, timestamp_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(overlay, f"Matches Found: {match_count}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imwrite(output_path, overlay)

        clone_score = (np.sum(suspicious_regions > 0) / suspicious_regions.size) * 100

        return {
            'clone_score':       round(clone_score, 2),
            'matches_found':     match_count,
            'clone_heatmap':     output_path,
            'method':            'SIFT (Scale-Invariant)',
            'interpretation':    (
                'High'   if clone_score > 5 else
                'Medium' if clone_score > 2 else
                'Low'
            ),
            'keypoints_detected': len(keypoints),
        }

    except Exception:
        return copy_move_detection_dct_fallback(image_path, output_path)


def copy_move_detection_dct_fallback(image_path, output_path, block_size=32):
    """DCT-based copy-move detection (fallback when SIFT is unavailable)."""
    try:
        img    = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        height, width = img.shape

        max_dim = 800
        if max(height, width) > max_dim:
            scale  = max_dim / max(height, width)
            img    = cv2.resize(img, None, fx=scale, fy=scale)
            height, width = img.shape

        blocks    = []
        positions = []
        step      = block_size

        for i in range(0, height - block_size, step):
            for j in range(0, width - block_size, step):
                block = img[i:i + block_size, j:j + block_size]
                if block.shape == (block_size, block_size):
                    dct_block = cv2.dct(np.float32(block))
                    blocks.append(dct_block.flatten()[:10])
                    positions.append((i, j))

        blocks             = np.array(blocks)
        suspicious_regions = np.zeros_like(img)
        match_count        = 0
        max_comparisons    = min(len(blocks), 1000)

        for i in range(max_comparisons):
            for j in range(i + 1, max_comparisons):
                distance         = np.linalg.norm(blocks[i] - blocks[j])
                spatial_distance = np.linalg.norm(
                    np.array(positions[i]) - np.array(positions[j])
                )

                if distance < 3 and spatial_distance > block_size * 2:
                    match_count += 1
                    y1, x1 = positions[i]
                    y2, x2 = positions[j]
                    suspicious_regions[y1:y1 + block_size, x1:x1 + block_size] = 255
                    suspicious_regions[y2:y2 + block_size, x2:x2 + block_size] = 255

        heatmap        = cv2.applyColorMap(suspicious_regions, cv2.COLORMAP_JET)
        original_color = cv2.imread(image_path)

        if original_color.shape[:2] != heatmap.shape[:2]:
            heatmap = cv2.resize(
                heatmap, (original_color.shape[1], original_color.shape[0])
            )

        overlay = cv2.addWeighted(original_color, 0.6, heatmap, 0.4, 0)

        timestamp_text = (
            f"DCT Clone Detection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        cv2.putText(overlay, timestamp_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imwrite(output_path, overlay)

        clone_score = (np.sum(suspicious_regions > 0) / suspicious_regions.size) * 100

        return {
            'clone_score':    round(clone_score, 2),
            'matches_found':  match_count,
            'clone_heatmap':  output_path,
            'method':         'DCT (Discrete Cosine Transform)',
            'interpretation': (
                'High'   if clone_score > 5 else
                'Medium' if clone_score > 2 else
                'Low'
            ),
        }

    except Exception as e:
        return {'error': f'Copy-move detection failed: {str(e)}'}
