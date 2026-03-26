# ============================================================================
# FRAME ANALYSIS BACKEND - COMPLETE IMPLEMENTATION
# ============================================================================
# This is a production-ready implementation based on the PRD
# Components: Video Processing, Metric Calculation, Anomaly Detection, Job Queue
# ============================================================================

"""
INSTALLATION REQUIREMENTS:
--------------------------
pip install opencv-python-headless
pip install numpy
pip install scikit-image
pip install pillow
pip install celery
pip install redis
pip install ffmpeg-python
pip install scipy
pip install matplotlib

SYSTEM REQUIREMENTS:
-------------------
- FFmpeg (install via: apt-get install ffmpeg or brew install ffmpeg)
- Redis server (for Celery job queue)
"""

import os
import json
import uuid
import random
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from PIL import Image
from scipy.spatial.distance import euclidean
from scipy.stats import zscore
import ffmpeg
from forensic_config import (
    FA_TAMPERED_CONFIDENCE, FA_REVIEW_CONFIDENCE,
    FA_SEV_HIGH_PTS, FA_SEV_MEDIUM_PTS, FA_SEV_LOW_PTS,
    FA_ELA_HIGH_THRESHOLD, FA_ELA_MEDIUM_THRESHOLD,
    FA_NOISE_HIGH_THRESHOLD, FA_NOISE_MEDIUM_THRESHOLD,
    FA_CLONE_HIGH_THRESHOLD, FA_CLONE_MEDIUM_THRESHOLD,
)


# Duplicate detection: limit search window and report one finding per duplicate run
MAX_DUPLICATE_GAP_FRAMES = int(os.getenv('MAX_DUPLICATE_GAP_FRAMES', 90))   # ~3 sec at 30fps
IMAGEHASH_HAMMING_THRESHOLD = int(os.getenv('IMAGEHASH_HAMMING_THRESHOLD', 5))
MAX_DUPLICATE_CLUSTER_FINDINGS = int(os.getenv('MAX_DUPLICATE_CLUSTER_FINDINGS', 200))  # cap per run
MAX_TOTAL_FINDINGS = int(os.getenv('MAX_TOTAL_FINDINGS', 0))  # 0 = no cap

# ============================================================================
# DATA MODELS
# ============================================================================

class MediaType(Enum):
    VIDEO = "video"

class AnalysisMode(Enum):
    STANDARD = "standard"
    HIGH_SENSITIVITY = "high_sensitivity"
    DEEP_SCAN = "deep_scan"

class SamplingMode(Enum):
    SAMPLED = "sampled"  # 2-5 fps
    FULL = "full"        # All frames

class FindingType(Enum):
    ABRUPT_TRANSITION = "ABRUPT_TRANSITION"
    FREEZE_OR_DUPLICATION = "FREEZE_OR_DUPLICATION"
    QUALITY_DRIFT = "QUALITY_DRIFT"
    TIMING_IRREGULARITY = "TIMING_IRREGULARITY"
    MOTION_ANOMALY = "MOTION_ANOMALY"
    IRREGULAR_GOP = "IRREGULAR_GOP"
    MOTION_DISCONTINUITY = "MOTION_DISCONTINUITY"
    # Spatial (per-frame) finding types
    ELA_ANOMALY = "ELA_ANOMALY"
    NOISE_ANOMALY = "NOISE_ANOMALY"
    COPY_MOVE_ANOMALY = "COPY_MOVE_ANOMALY"
    # Audio-video sync
    AV_SYNC_ANOMALY = "AV_SYNC_ANOMALY"

class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass
class Finding:
    id: str
    type: FindingType
    severity: Severity
    location: Dict[str, Any]  # {start, end} or {frames: []}
    explanation: str
    metrics: Dict[str, float]
    evidence_artifacts: List[str]  # Paths to evidence files

@dataclass
class AnalysisResult:
    job_id: str
    media_type: MediaType
    media_info: Dict[str, Any]
    findings: List[Finding]          # temporal findings
    parameters: Dict[str, Any]
    created_at: str
    completed_at: str
    spatial_findings: List[Finding] = field(default_factory=list)
    # Unified verdict fields (populated by _compute_verdict)
    verdict: str = "LIKELY_AUTHENTIC"              # LIKELY_AUTHENTIC | POSSIBLY_TAMPERED | LIKELY_TAMPERED
    tampering_confidence: float = 0.0              # 0.0 – 100.0
    tampering_type: str = "NONE"                   # NONE | TEMPORAL_EDIT | CONTENT_MANIPULATION | BOTH
    verdict_explanation: str = ""                  # human-readable driver summary

# ============================================================================
# HELPERS: DUPLICATE CLUSTERING
# ============================================================================

def _union_find_components(n: int, pairs: List[Tuple[int, int]]) -> List[List[int]]:
    """Group frame indices into connected components given duplicate pairs (union-find)."""
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, j in pairs:
        if 0 <= i < n and 0 <= j < n:
            union(i, j)

    components: Dict[int, List[int]] = {}
    for i in range(n):
        root = find(i)
        if root not in components:
            components[root] = []
        components[root].append(i)
    return [sorted(c) for c in components.values() if len(c) >= 2]


# ============================================================================
# VIDEO PROCESSING MODULE
# ============================================================================

class VideoProcessor:
    """Handles video decoding and frame extraction"""
    
    @staticmethod
    def get_video_info(video_path: str) -> Dict[str, Any]:
        """Extract video metadata using ffmpeg"""
        try:
            probe = ffmpeg.probe(video_path)
            video_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            
            fps_parts = video_stream['r_frame_rate'].split('/')
            fps = float(fps_parts[0]) / float(fps_parts[1])
            
            duration = float(probe['format']['duration'])
            
            return {
                'duration': duration,
                'fps': fps,
                'width': video_stream['width'],
                'height': video_stream['height'],
                'codec': video_stream['codec_name'],
                'total_frames': int(duration * fps)
            }
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            raise
    
    @staticmethod
    def extract_frames(video_path: str, sampling_mode: SamplingMode, target_fps: float = 2.0) -> List[Tuple[float, np.ndarray]]:
        """
        Extract frames from video
        Returns: List of (timestamp, frame) tuples
        """
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        
        original_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if original_fps <= 0.0 or np.isnan(original_fps):
            cap.release()
            raise ValueError(
                f"Cannot determine FPS for video '{video_path}'. "
                "Ensure the file is a valid video with timebase metadata."
            )
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        frames = []
        
        if sampling_mode == SamplingMode.FULL:
            # Extract all frames
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                timestamp = frame_idx / original_fps
                frames.append((timestamp, frame))
                frame_idx += 1
        else:
            # Sampled mode: extract at target_fps
            if target_fps <= 0:
                target_fps = 1.0
            ratio = original_fps / target_fps
            frame_interval = max(1, int(round(ratio))) if ratio >= 1 else 1
            frame_idx = 0
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                if frame_idx % frame_interval == 0:
                    timestamp = frame_idx / original_fps
                    frames.append((timestamp, frame))
                
                frame_idx += 1
        
        cap.release()
        logger.info(f"Extracted {len(frames)} frames from video")
        return frames

# ============================================================================
# IMAGE SEQUENCE PROCESSING MODULE
# ============================================================================

# ============================================================================
# METRIC CALCULATION MODULE
# ============================================================================

class MetricCalculator:
    """Calculate frame-to-frame metrics"""
    
    @staticmethod
    def calculate_ssim(frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate Structural Similarity Index"""
        # Convert to grayscale
        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
        
        # Resize if needed (SSIM requires same dimensions)
        if gray1.shape != gray2.shape:
            h, w = gray1.shape
            gray2 = cv2.resize(gray2, (w, h))
        
        score, _ = ssim(gray1, gray2, full=True)
        return float(score)
    
    @staticmethod
    def calculate_histogram_distance(frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate histogram distance (normalized)"""
        hist1 = cv2.calcHist([frame1], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist2 = cv2.calcHist([frame2], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        
        hist1 = cv2.normalize(hist1, hist1).flatten()
        hist2 = cv2.normalize(hist2, hist2).flatten()
        
        distance = cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA)
        return float(distance)
    
    @staticmethod
    def calculate_sharpness(frame: np.ndarray) -> float:
        """Calculate frame sharpness using Laplacian variance"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        return float(laplacian_var)
    
    @staticmethod
    def calculate_brightness(frame: np.ndarray) -> float:
        """Calculate average brightness"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))
    
    @staticmethod
    def calculate_contrast(frame: np.ndarray) -> float:
        """Calculate contrast (standard deviation)"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.std(gray))
    
    @staticmethod
    def calculate_blockiness(frame: np.ndarray) -> float:
        """Estimate compression artifacts (blockiness)"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Calculate horizontal and vertical gradients
        grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        
        # Blockiness is estimated by edge strength
        blockiness = np.mean(np.abs(grad_x)) + np.mean(np.abs(grad_y))
        return float(blockiness)
    
    @staticmethod
    def calculate_noise_level(frame: np.ndarray) -> float:
        """Estimate noise level using high-frequency energy"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Apply high-pass filter
        kernel = np.array([[-1, -1, -1],
                          [-1,  8, -1],
                          [-1, -1, -1]])
        filtered = cv2.filter2D(gray, -1, kernel)
        
        noise = np.std(filtered)
        return float(noise)
    
    @staticmethod
    def calculate_all_metrics(frame1: np.ndarray, frame2: np.ndarray) -> Dict[str, float]:
        """Calculate all frame-to-frame metrics"""
        return {
            'ssim': MetricCalculator.calculate_ssim(frame1, frame2),
            'histogram_distance': MetricCalculator.calculate_histogram_distance(frame1, frame2),
            'sharpness_1': MetricCalculator.calculate_sharpness(frame1),
            'sharpness_2': MetricCalculator.calculate_sharpness(frame2),
            'sharpness_change': abs(MetricCalculator.calculate_sharpness(frame1) - MetricCalculator.calculate_sharpness(frame2)),
            'brightness_1': MetricCalculator.calculate_brightness(frame1),
            'brightness_2': MetricCalculator.calculate_brightness(frame2),
            'brightness_change': abs(MetricCalculator.calculate_brightness(frame1) - MetricCalculator.calculate_brightness(frame2)),
            'blockiness_1': MetricCalculator.calculate_blockiness(frame1),
            'blockiness_2': MetricCalculator.calculate_blockiness(frame2),
            'noise_1': MetricCalculator.calculate_noise_level(frame1),
            'noise_2': MetricCalculator.calculate_noise_level(frame2),
        }

# ============================================================================
# ANOMALY DETECTION MODULE
# ============================================================================

class AnomalyDetector:
    """Detect temporal anomalies in frame sequences"""
    
    def __init__(self, mode: AnalysisMode, adaptive_thresholds: Optional[Dict[str, float]] = None):
        self.mode = mode
        
        if adaptive_thresholds:
            # Use adaptive thresholds calculated from video statistics
            self.ssim_threshold = adaptive_thresholds.get('ssim_threshold', 0.65)
            self.histogram_threshold = adaptive_thresholds.get('histogram_threshold', 0.75)
            self.duplicate_threshold = adaptive_thresholds.get('duplicate_threshold', 0.98)
            self.zscore_threshold = adaptive_thresholds.get('zscore_threshold', 2.5)
        else:
            # Fallback to fixed thresholds based on analysis mode
            if mode == AnalysisMode.STANDARD:
                self.ssim_threshold = 0.65
                self.histogram_threshold = 0.75
                self.duplicate_threshold = 0.98
                self.zscore_threshold = 2.5
            elif mode == AnalysisMode.HIGH_SENSITIVITY:
                self.ssim_threshold = 0.75
                self.histogram_threshold = 0.65
                self.duplicate_threshold = 0.96
                self.zscore_threshold = 2.0
            else:  # DEEP_SCAN
                self.ssim_threshold = 0.80
                self.histogram_threshold = 0.60
                self.duplicate_threshold = 0.95
                self.zscore_threshold = 1.5
    
    def detect_abrupt_transition(self, metrics: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Detect abrupt visual transitions"""
        ssim_score = metrics['ssim']
        hist_dist = metrics['histogram_distance']
        
        if ssim_score < self.ssim_threshold and hist_dist > self.histogram_threshold:
            return {
                'type': FindingType.ABRUPT_TRANSITION,
                'severity': Severity.HIGH if ssim_score < 0.5 else Severity.MEDIUM,
                'metrics': {
                    'ssim_drop': 1.0 - ssim_score,
                    'histogram_distance': hist_dist,
                    'sharpness_change': metrics['sharpness_change']
                },
                'explanation': f"Detected abrupt visual transition with SSIM of {ssim_score:.2f}. Suggests possible cut or splice."
            }
        return None
    
    def detect_freeze_duplication(self, metrics: Dict[str, float], duration: float = 0) -> Optional[Dict[str, Any]]:
        """Detect frozen or duplicated frames"""
        ssim_score = metrics['ssim']
        
        if ssim_score > self.duplicate_threshold:
            return {
                'type': FindingType.FREEZE_OR_DUPLICATION,
                'severity': Severity.MEDIUM if duration > 1.0 else Severity.LOW,
                'metrics': {
                    'similarity_score': ssim_score,
                    'duplicate_duration': duration
                },
                'explanation': f"Near-identical frames detected (SSIM: {ssim_score:.2f}). May indicate freeze frame insertion."
            }
        return None
    
    def detect_quality_drift(self, metrics: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Detect quality changes between frames"""
        blockiness_change = abs(metrics['blockiness_2'] - metrics['blockiness_1'])
        noise_change = abs(metrics['noise_2'] - metrics['noise_1'])
        
        # Normalize changes
        blockiness_ratio = blockiness_change / max(metrics['blockiness_1'], 1.0)
        noise_ratio = noise_change / max(metrics['noise_1'], 1.0)
        
        if blockiness_ratio > 0.3 or noise_ratio > 0.3:
            return {
                'type': FindingType.QUALITY_DRIFT,
                'severity': Severity.HIGH if blockiness_ratio > 0.5 else Severity.MEDIUM,
                'metrics': {
                    'quality_drop': blockiness_ratio,
                    'noise_increase': noise_ratio,
                    'blockiness_change': blockiness_change
                },
                'explanation': f"Step change in compression quality detected. Blockiness changed by {blockiness_ratio*100:.0f}%."
            }
        return None
    
# ============================================================================
# CORE ANALYSIS ENGINE
# ============================================================================

class FrameAnalysisEngine:
    """Main analysis engine orchestrating all components"""
    
    def __init__(self, job_id: str, output_dir: str):
        self.job_id = job_id
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def _calculate_adaptive_thresholds(self, all_metrics: List[Dict[str, float]], mode: AnalysisMode) -> Dict[str, float]:
        """Calculate adaptive thresholds based on video statistics"""
        if not all_metrics:
            # Fallback to fixed thresholds if no metrics
            if mode == AnalysisMode.STANDARD:
                return {'ssim_threshold': 0.65, 'histogram_threshold': 0.75, 'duplicate_threshold': 0.98, 'zscore_threshold': 2.5}
            elif mode == AnalysisMode.HIGH_SENSITIVITY:
                return {'ssim_threshold': 0.75, 'histogram_threshold': 0.65, 'duplicate_threshold': 0.96, 'zscore_threshold': 2.0}
            else:
                return {'ssim_threshold': 0.80, 'histogram_threshold': 0.60, 'duplicate_threshold': 0.95, 'zscore_threshold': 1.5}
        
        # Extract metric arrays
        ssim_values = [m['ssim'] for m in all_metrics]
        hist_dist_values = [m['histogram_distance'] for m in all_metrics]
        
        # Calculate statistics
        ssim_mean = np.mean(ssim_values)
        ssim_std = np.std(ssim_values)
        hist_mean = np.mean(hist_dist_values)
        hist_std = np.std(hist_dist_values)
        
        # Adaptive thresholds: use mean - N*std for SSIM (lower SSIM = more different)
        # and mean + N*std for histogram distance (higher distance = more different)
        # Mode determines sensitivity multiplier
        if mode == AnalysisMode.STANDARD:
            ssim_multiplier = 1.5
            hist_multiplier = 1.5
            duplicate_threshold = 0.98
            zscore_threshold = 2.5
        elif mode == AnalysisMode.HIGH_SENSITIVITY:
            ssim_multiplier = 1.0
            hist_multiplier = 1.0
            duplicate_threshold = 0.96
            zscore_threshold = 2.0
        else:  # DEEP_SCAN
            ssim_multiplier = 0.8
            hist_multiplier = 0.8
            duplicate_threshold = 0.95
            zscore_threshold = 1.5
        
        # Calculate adaptive thresholds
        # SSIM threshold: mean - multiplier*std (lower SSIM = anomaly)
        ssim_threshold = max(0.3, min(0.9, ssim_mean - ssim_multiplier * ssim_std))
        
        # Histogram threshold: mean + multiplier*std (higher distance = anomaly)
        histogram_threshold = max(0.3, min(0.95, hist_mean + hist_multiplier * hist_std))
        
        logger.info(f"Adaptive thresholds - SSIM: {ssim_threshold:.3f} (mean: {ssim_mean:.3f}, std: {ssim_std:.3f}), "
                   f"Histogram: {histogram_threshold:.3f} (mean: {hist_mean:.3f}, std: {hist_std:.3f})")
        
        return {
            'ssim_threshold': float(ssim_threshold),
            'histogram_threshold': float(histogram_threshold),
            'duplicate_threshold': duplicate_threshold,
            'zscore_threshold': zscore_threshold
        }
    
    def analyze_video(self, video_path: str, mode: AnalysisMode, sampling_mode: SamplingMode) -> AnalysisResult:
        """Analyze video for temporal anomalies"""
        logger.info(f"Starting video analysis: {video_path}")
        
        start_time = datetime.utcnow()
        
        # 1. Get video info
        video_info = VideoProcessor.get_video_info(video_path)
        
        # 2. Extract frames
        frames = VideoProcessor.extract_frames(video_path, sampling_mode, 2.0)
        frame_list = [f for _, f in frames]
        timestamp_list = [t for t, _ in frames]
        
        findings = []
        
        # 3. Calculate ALL metrics first to establish baseline statistics
        all_metrics = []
        for i in range(len(frames) - 1):
            timestamp1, frame1 = frames[i]
            timestamp2, frame2 = frames[i + 1]
            metrics = MetricCalculator.calculate_all_metrics(frame1, frame2)
            all_metrics.append(metrics)
        
        # 4. Calculate adaptive thresholds based on video statistics
        adaptive_thresholds = self._calculate_adaptive_thresholds(all_metrics, mode)
        detector = AnomalyDetector(mode, adaptive_thresholds)
        
        # 5. PySceneDetect with dynamic confidence calculation
        try:
            from scenedetect import detect, ContentDetector
            scene_list = detect(video_path, ContentDetector(threshold=27.0))
            
            for start_time_obj, end_time_obj in scene_list:
                scene_start = start_time_obj.get_seconds()
                scene_end = end_time_obj.get_seconds()
                
                # Find closest frames to scene boundary for confidence calculation
                closest_idx = min(range(len(timestamp_list)), key=lambda i: abs(timestamp_list[i] - scene_start))
                boundary_metrics = None
                confidence = 0.85  # Default confidence
                
                if closest_idx < len(frame_list) - 1:
                    # Calculate actual metrics at scene boundary
                    boundary_metrics = MetricCalculator.calculate_all_metrics(
                        frame_list[closest_idx], 
                        frame_list[min(closest_idx + 1, len(frame_list) - 1)]
                    )
                    
                    # Calculate confidence based on how anomalous the transition is
                    # Lower SSIM and higher histogram distance = higher confidence
                    ssim_score = boundary_metrics['ssim']
                    hist_dist = boundary_metrics['histogram_distance']
                    
                    # Normalize to 0.7-0.99 range based on how extreme the change is
                    # Very low SSIM (< 0.3) or very high hist distance (> 0.8) = high confidence
                    confidence = 0.7 + min(0.29, (1.0 - ssim_score) * 0.15 + hist_dist * 0.15)
                    confidence = min(0.99, max(0.7, confidence))
                
                # Build metrics dict
                metrics_dict = {
                    'confidence': round(confidence, 3), 
                    'method': 'PySceneDetect'
                }
                if boundary_metrics:
                    metrics_dict['ssim'] = boundary_metrics.get('ssim', 0)
                    metrics_dict['histogram_distance'] = boundary_metrics.get('histogram_distance', 0)
                
                finding = Finding(
                    id=str(uuid.uuid4()),
                    type=FindingType.ABRUPT_TRANSITION,
                    severity=Severity.HIGH,
                    location={'start': scene_start, 'end': scene_end},
                    explanation=f"Scene change detected using PySceneDetect at {scene_start:.1f}s",
                    metrics=metrics_dict,
                    evidence_artifacts=[]
                )
                findings.append(finding)
        except Exception as e:
            logger.error(f"PySceneDetect failed: {e}")
        
        # 6. GOP / I-frame distribution analysis
        try:
            gop_findings = self._analyze_gop_distribution(video_path)
            findings.extend(gop_findings)
        except Exception as e:
            logger.error(f"GOP analysis failed (non-fatal): {e}")

        # 7. Optical flow motion vector consistency analysis
        try:
            flow_findings = self._analyze_optical_flow(frames, all_metrics)
            findings.extend(flow_findings)
        except Exception as e:
            logger.error(f"Optical flow analysis failed (non-fatal): {e}")

        # 8. ImageHash duplicate detection (windowed + clustered: one finding per duplicate run)
        try:
            import imagehash
            from PIL import Image as PILImage

            hashes = []
            for frame in frame_list:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = PILImage.fromarray(frame_rgb)
                phash = imagehash.phash(pil_img)
                hashes.append(phash)

            n_frames = len(hashes)
            pairs: List[Tuple[int, int]] = []
            for i in range(n_frames):
                for j in range(i + 5, min(i + MAX_DUPLICATE_GAP_FRAMES + 1, n_frames)):
                    hamming_dist = hashes[i] - hashes[j]
                    if hamming_dist <= IMAGEHASH_HAMMING_THRESHOLD:
                        pairs.append((i, j))

            components = _union_find_components(n_frames, pairs)
            components.sort(key=len, reverse=True)  # largest duplicate runs first
            for cluster in components[:MAX_DUPLICATE_CLUSTER_FINDINGS]:
                indices = sorted(cluster)
                t_start = timestamp_list[indices[0]]
                t_end = timestamp_list[indices[-1]]
                duration_sec = t_end - t_start
                finding = Finding(
                    id=str(uuid.uuid4()),
                    type=FindingType.FREEZE_OR_DUPLICATION,
                    severity=Severity.MEDIUM,
                    location={'start': t_start, 'end': t_end},
                    explanation=(
                        f"Duplicate or near-duplicate run: {len(indices)} frames from {t_start:.1f}s to {t_end:.1f}s "
                        f"({duration_sec:.1f}s) detected via perceptual hashing (possible freeze or loop)."
                    ),
                    metrics={
                        'frame_count': len(indices),
                        'duration_sec': round(duration_sec, 2),
                        'method': 'ImageHash (clustered)'
                    },
                    evidence_artifacts=[]
                )
                findings.append(finding)
            if len(components) > MAX_DUPLICATE_CLUSTER_FINDINGS:
                logger.info(
                    f"ImageHash: capped duplicate clusters at {MAX_DUPLICATE_CLUSTER_FINDINGS} "
                    f"(total clusters found: {len(components)})"
                )
        except Exception as e:
            logger.error(f"ImageHash detection failed: {e}")
        
        # 9. Run detection with adaptive thresholds
        for i, metrics in enumerate(all_metrics):
            timestamp1, frame1 = frames[i]
            timestamp2, frame2 = frames[i + 1]
            
            # Check for abrupt transitions
            if anomaly := detector.detect_abrupt_transition(metrics):
                finding = Finding(
                    id=str(uuid.uuid4()),
                    type=anomaly['type'],
                    severity=anomaly['severity'],
                    location={'start': timestamp1, 'end': timestamp2},
                    explanation=anomaly['explanation'],
                    metrics=anomaly['metrics'],
                    evidence_artifacts=self._save_evidence(frame1, frame2, timestamp1, timestamp2)
                )
                findings.append(finding)
            
            # Check for freeze/duplication
            duration = timestamp2 - timestamp1
            if anomaly := detector.detect_freeze_duplication(metrics, duration):
                finding = Finding(
                    id=str(uuid.uuid4()),
                    type=anomaly['type'],
                    severity=anomaly['severity'],
                    location={'start': timestamp1, 'end': timestamp2},
                    explanation=anomaly['explanation'],
                    metrics=anomaly['metrics'],
                    evidence_artifacts=self._save_evidence(frame1, frame2, timestamp1, timestamp2)
                )
                findings.append(finding)
            
            # Check for quality drift
            if anomaly := detector.detect_quality_drift(metrics):
                finding = Finding(
                    id=str(uuid.uuid4()),
                    type=anomaly['type'],
                    severity=anomaly['severity'],
                    location={'start': timestamp1, 'end': timestamp2},
                    explanation=anomaly['explanation'],
                    metrics=anomaly['metrics'],
                    evidence_artifacts=self._save_evidence(frame1, frame2, timestamp1, timestamp2)
                )
                findings.append(finding)
        
        # 10. Group adjacent findings and remove duplicates
        findings = self._remove_duplicate_findings(findings)
        findings = self._group_adjacent_findings(findings)

        # 11. Optional cap on total temporal findings (prioritize by severity then start time)
        if MAX_TOTAL_FINDINGS > 0 and len(findings) > MAX_TOTAL_FINDINGS:
            severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
            findings.sort(
                key=lambda f: (severity_order.get(f.severity, 2), f.location.get('start', 0))
            )
            findings = findings[:MAX_TOTAL_FINDINGS]
            logger.info(f"Capped total findings at {MAX_TOTAL_FINDINGS} (had more)")

        # 12. Spatial forensics on flagged frames + 5 % random baseline
        spatial_findings: List[Finding] = []
        try:
            spatial_findings = self._run_spatial_forensics(
                video_path, findings, timestamp_list
            )
        except Exception as e:
            logger.error(f"Spatial forensics failed (non-fatal): {e}")

        # 12b. Audio-video sync analysis
        av_findings: List[Finding] = []
        try:
            av_findings = self._analyze_av_sync(video_path, frames)
        except Exception as e:
            logger.error(f"AV sync analysis failed (non-fatal): {e}")

        # Merge AV sync into temporal findings — AV sync is time-based and
        # contributes to the temporal score in the unified verdict.
        all_temporal = findings + av_findings

        # 13. Unified verdict
        verdict, tampering_confidence, tampering_type, verdict_explanation = \
            self._compute_verdict(all_temporal, spatial_findings)

        logger.info(
            f"Verdict: {verdict} | confidence={tampering_confidence} | "
            f"type={tampering_type}"
        )

        end_time = datetime.utcnow()

        return AnalysisResult(
            job_id=self.job_id,
            media_type=MediaType.VIDEO,
            media_info={
                'duration': video_info['duration'],
                'fps': video_info['fps'],
                'resolution': f"{video_info['width']}x{video_info['height']}",
                'codec': video_info['codec']
            },
            findings=all_temporal,
            spatial_findings=spatial_findings,
            verdict=verdict,
            tampering_confidence=tampering_confidence,
            tampering_type=tampering_type,
            verdict_explanation=verdict_explanation,
            parameters={
                'mode': mode.value,
                'sampling_mode': sampling_mode.value
            },
            created_at=start_time.isoformat(),
            completed_at=end_time.isoformat()
        )
    
    def _analyze_av_sync(
        self,
        video_path: str,
        frames: List[Tuple[float, np.ndarray]],
    ) -> List[Finding]:
        """
        Detect audio-video synchronisation anomalies.

        Even a single deleted or inserted frame shifts the relationship between
        the audio and video tracks — a desync that is very hard to disguise
        without re-editing the audio entirely.

        Two independent signals are checked:

        1. **Duration mismatch** (ffprobe): if the audio and video streams have
           different durations (delta > 100 ms), that is a high-confidence sign
           of temporal editing.

        2. **Onset ↔ motion correlation** (librosa + frame diff): strong audio
           events (speech onsets, impacts, transients) should coincide with
           visible motion in the video. If a strong onset occurs at a timestamp
           where the video is nearly static, the tracks are likely desynced.

        Skips gracefully if the video has no audio track or if librosa is absent.
        """
        import subprocess
        import json as _json

        findings: List[Finding] = []

        # ── 1. Stream duration mismatch via ffprobe ────────────────────────────
        has_audio = False
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_streams', video_path
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                info = _json.loads(proc.stdout)
                audio_dur: Optional[float] = None
                video_dur: Optional[float] = None
                for s in info.get('streams', []):
                    dur = s.get('duration')
                    if dur is None:
                        continue
                    try:
                        dur_f = float(dur)
                    except (ValueError, TypeError):
                        continue
                    if s.get('codec_type') == 'video':
                        video_dur = dur_f
                    elif s.get('codec_type') == 'audio':
                        audio_dur = dur_f
                        has_audio = True

                if not has_audio:
                    logger.info("AV sync: no audio track — skipping")
                    return []

                if audio_dur is not None and video_dur is not None:
                    delta_ms = abs(audio_dur - video_dur) * 1000.0
                    if delta_ms > 100.0:
                        severity = Severity.HIGH if delta_ms > 500.0 else Severity.MEDIUM
                        findings.append(Finding(
                            id=str(uuid.uuid4()),
                            type=FindingType.AV_SYNC_ANOMALY,
                            severity=severity,
                            location={
                                'start': 0.0,
                                'end': round(max(audio_dur, video_dur), 3),
                            },
                            explanation=(
                                f"Audio duration ({audio_dur:.3f}s) and video duration "
                                f"({video_dur:.3f}s) differ by {delta_ms:.0f}ms. "
                                f"Frame insertions or deletions almost always create this "
                                f"mismatch."
                            ),
                            metrics={
                                'audio_duration_sec': round(audio_dur, 3),
                                'video_duration_sec': round(video_dur, 3),
                                'duration_delta_ms': round(delta_ms, 1),
                            },
                            evidence_artifacts=[],
                        ))
        except Exception as e:
            logger.warning(f"AV sync duration check failed: {e}")
            return []  # if ffprobe failed entirely we can't proceed

        if not has_audio:
            return findings

        # ── 2. Audio onset ↔ video motion correlation ──────────────────────────
        try:
            # Decode audio directly to raw float32 PCM via ffmpeg stdout.
            # Avoids librosa.load() / audioread which conflict with the
            # 'coverage' package at runtime.
            _SR = 22050
            proc = subprocess.run(
                [
                    'ffmpeg', '-i', video_path,
                    '-vn',                   # drop video stream
                    '-f', 'f32le',           # raw 32-bit float little-endian
                    '-ac', '1',              # mono
                    '-ar', str(_SR),         # target sample rate
                    '-',                     # write to stdout
                    '-loglevel', 'error',
                ],
                capture_output=True, timeout=120,
            )
            if proc.returncode != 0 or not proc.stdout:
                logger.warning("AV sync: ffmpeg audio extraction failed")
                return findings

            y  = np.frombuffer(proc.stdout, dtype=np.float32)
            sr = _SR

            # Skip silent audio
            if np.max(np.abs(y)) < 1e-4:
                logger.info("AV sync: audio track is silent — skipping onset check")
                return findings

            # Onset strength envelope and detected onset times
            hop_length = 512
            onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
            onset_frames_idx = librosa.onset.onset_detect(
                y=y, sr=sr, hop_length=hop_length, delta=0.3
            )
            if len(onset_frames_idx) == 0:
                return findings

            onset_times = librosa.frames_to_time(
                onset_frames_idx, sr=sr, hop_length=hop_length
            )
            # Strength of each detected onset
            env_idx = np.clip(onset_frames_idx, 0, len(onset_env) - 1)
            onset_strengths = onset_env[env_idx]

            # Keep only the top-25 % strongest onsets to reduce false positives
            strength_thresh = float(np.percentile(onset_env, 75))
            strong_mask = onset_strengths >= strength_thresh
            strong_times = onset_times[strong_mask]

            if len(strong_times) == 0 or len(frames) < 2:
                return findings

            # Pre-compute frame-to-frame motion (mean absolute grayscale diff)
            frame_mids: List[float] = []
            motion_mags: List[float] = []
            for i in range(len(frames) - 1):
                t_mid = (frames[i][0] + frames[i + 1][0]) / 2.0
                g1 = cv2.cvtColor(frames[i][1],     cv2.COLOR_BGR2GRAY).astype(np.float32)
                g2 = cv2.cvtColor(frames[i + 1][1], cv2.COLOR_BGR2GRAY).astype(np.float32)
                motion_mags.append(float(np.mean(np.abs(g1 - g2))))
                frame_mids.append(t_mid)

            motion_arr    = np.array(motion_mags)
            motion_median = float(np.median(motion_arr))
            motion_std    = float(np.std(motion_arr))

            # Only run correlation if the video has meaningful motion at all
            if motion_median < 0.5:
                logger.info("AV sync: video motion too low for reliable onset correlation")
                return findings

            frame_mids_arr = np.array(frame_mids)
            dedupe_times: set = set()  # avoid duplicate findings at same timestamp

            for onset_t, onset_str in zip(strong_times, onset_strengths[strong_mask]):
                # Nearest sampled frame interval
                dists = np.abs(frame_mids_arr - onset_t)
                nearest_i = int(np.argmin(dists))
                nearest_dist = float(dists[nearest_i])

                # Skip if no sampled frame is within 1 s of the onset
                if nearest_dist > 1.0:
                    continue

                mot = motion_mags[nearest_i]

                # Flag: strong audio event, near-zero video motion
                # Threshold: motion < 30 % of median AND median is meaningful
                if mot < motion_median * 0.30:
                    desync_ms = nearest_dist * 1000.0
                    bucket = round(onset_t, 1)  # 0.1 s buckets to deduplicate
                    if bucket in dedupe_times:
                        continue
                    dedupe_times.add(bucket)

                    severity = Severity.HIGH if desync_ms > 200.0 else Severity.MEDIUM
                    findings.append(Finding(
                        id=str(uuid.uuid4()),
                        type=FindingType.AV_SYNC_ANOMALY,
                        severity=severity,
                        location={
                            'start': round(onset_t, 3),
                            'end': round(onset_t + 0.5, 3),
                        },
                        explanation=(
                            f"Strong audio onset at {onset_t:.2f}s (strength "
                            f"{onset_str:.2f}) with near-zero video motion "
                            f"({mot:.2f} vs baseline {motion_median:.2f}). "
                            f"Possible AV desync from frame deletion or insertion."
                        ),
                        metrics={
                            'onset_time_sec': round(float(onset_t), 3),
                            'onset_strength': round(float(onset_str), 3),
                            'video_motion': round(mot, 4),
                            'motion_baseline': round(motion_median, 4),
                            'estimated_desync_ms': round(desync_ms, 1),
                        },
                        evidence_artifacts=[],
                    ))

        except ImportError:
            logger.warning("AV sync onset analysis skipped: librosa not installed")
        except Exception as e:
            logger.warning(f"AV sync onset analysis failed: {e}")

        logger.info(f"AV sync analysis: {len(findings)} finding(s)")
        return findings

    def _compute_verdict(
        self,
        temporal_findings: List[Finding],
        spatial_findings: List[Finding],
    ) -> Tuple[str, float, str, str]:
        """
        Compute the unified tampering verdict from temporal and spatial findings.

        Scoring
        -------
        Each finding contributes points by severity:
            HIGH   → 25 pts
            MEDIUM → 12 pts
            LOW    →  4 pts

        Temporal and spatial scores are each capped at 100, then blended:
            tampering_confidence = (temporal_score × 0.4) + (spatial_score × 0.6)

        Verdict thresholds
        ------------------
            ≥ 80 *and* multiple HIGH-severity signals across modalities → LIKELY_TAMPERED
            ≥ 30 otherwise                                            → REVIEW_RECOMMENDED
            < 30                                                      → LIKELY_AUTHENTIC

        Tampering type
        --------------
            temporal + spatial → BOTH
            temporal only      → TEMPORAL_EDIT
            spatial only       → CONTENT_MANIPULATION
            neither            → NONE

        Returns
        -------
        (verdict, tampering_confidence, tampering_type, verdict_explanation)
        """
        SEVERITY_POINTS: Dict[Severity, float] = {
            Severity.HIGH:   FA_SEV_HIGH_PTS,
            Severity.MEDIUM: FA_SEV_MEDIUM_PTS,
            Severity.LOW:    FA_SEV_LOW_PTS,
        }
        SEV_ORDER = ['low', 'medium', 'high']

        temporal_score = min(
            sum(SEVERITY_POINTS.get(f.severity, 0) for f in temporal_findings),
            100.0,
        )
        spatial_score = min(
            sum(SEVERITY_POINTS.get(f.severity, 0) for f in spatial_findings),
            100.0,
        )

        confidence = round((temporal_score * 0.4) + (spatial_score * 0.6), 1)

        # ── Verdict ────────────────────────────────────────────────────────────
        high_temporal = sum(1 for f in temporal_findings if f.severity == Severity.HIGH)
        high_spatial  = sum(1 for f in spatial_findings  if f.severity == Severity.HIGH)
        distinct_modalities = int(bool(high_temporal)) + int(bool(high_spatial))
        multi_high = (high_temporal + high_spatial) >= 2 or distinct_modalities == 2

        if confidence >= FA_TAMPERED_CONFIDENCE and multi_high:
            verdict = "LIKELY_TAMPERED"
        elif confidence >= FA_REVIEW_CONFIDENCE:
            verdict = "REVIEW_RECOMMENDED"
        else:
            verdict = "LIKELY_AUTHENTIC"

        # ── Tampering type ─────────────────────────────────────────────────────
        has_temporal = bool(temporal_findings)
        has_spatial  = bool(spatial_findings)
        if has_temporal and has_spatial:
            tampering_type = "BOTH"
        elif has_temporal:
            tampering_type = "TEMPORAL_EDIT"
        elif has_spatial:
            tampering_type = "CONTENT_MANIPULATION"
        else:
            tampering_type = "NONE"

        # ── Human-readable explanation ─────────────────────────────────────────
        def _summarise(findings: List[Finding], label: str, score: float) -> str:
            if not findings:
                return f"no {label} anomalies detected"
            n = len(findings)
            n_high = sum(1 for f in findings if f.severity == Severity.HIGH)
            # Top-3 distinct finding types, most severe first
            sorted_f = sorted(
                findings,
                key=lambda f: SEV_ORDER.index(f.severity.value),
                reverse=True,
            )
            types_seen: List[str] = []
            for f in sorted_f:
                t = str(f.type).replace("FindingType.", "").replace("_", " ").title()
                if t not in types_seen:
                    types_seen.append(t)
                if len(types_seen) == 3:
                    break
            type_str = ", ".join(types_seen)
            return (
                f"{n} {label} finding(s) ({n_high} HIGH) — "
                f"{type_str} — score {score:.0f}/100"
            )

        t_summary = _summarise(temporal_findings, "temporal", temporal_score)
        s_summary = _summarise(spatial_findings,  "spatial",  spatial_score)

        explanation = (
            f"Confidence {confidence:.1f}/100 → {verdict}. "
            f"{t_summary.capitalize()}; {s_summary}."
        )

        return verdict, confidence, tampering_type, explanation

    def _run_spatial_forensics(
        self,
        video_path: str,
        temporal_findings: List[Finding],
        frame_timestamps: List[float],
    ) -> List[Finding]:
        """
        Run spatial forensics (ELA, noise analysis, copy-move) on a targeted
        subset of frames:

          1. Every frame flagged by temporal analysis (the primary suspects).
          2. A random 5 % baseline sample of remaining frames (catches within-frame
             manipulation that doesn't create a temporal anomaly).

        Total frame count is capped at 20 to bound runtime. Each candidate frame
        is written to disk as a JPEG and passed to the visual_forens functions,
        which expect file paths. Findings are only created when a score exceeds
        the anomaly thresholds defined in visual_forens.generate_forensic_report.
        """
        from forensic_primitives import (
            error_level_analysis,
            noise_analysis,
            copy_move_detection_sift,
        )

        spatial_dir = os.path.join(self.output_dir, 'spatial')
        os.makedirs(spatial_dir, exist_ok=True)

        # ── Build the set of timestamps to analyse ──────────────────────────────
        flagged_times: List[float] = sorted({
            f.location['start']
            for f in temporal_findings
            if 'start' in f.location
        })

        # 5 % random baseline from non-flagged frames
        flagged_set = set(flagged_times)
        available = [t for t in frame_timestamps if t not in flagged_set]
        n_baseline = max(1, int(len(frame_timestamps) * 0.05))
        baseline = (
            random.sample(available, min(n_baseline, len(available)))
            if available else []
        )

        # Merge and cap at 20 frames total
        all_times = sorted(flagged_set | set(baseline))[:20]

        if not all_times:
            return []

        # ── Open video for seeking ───────────────────────────────────────────────
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning("Spatial forensics: could not open video for frame extraction")
            return []

        findings: List[Finding] = []

        for t in all_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            if not ret:
                continue

            # Save frame as PNG — ELA requires a lossless source so the
            # JPEG recompression in error_level_analysis measures genuine
            # compression inconsistencies, not our own save artefacts.
            frame_tag = f"frame_{t:.3f}".replace('.', '_')
            frame_path = os.path.join(spatial_dir, f"{frame_tag}.png")
            cv2.imwrite(frame_path, frame)

            # ── ELA ───────────────────────────────────────────────────────────
            try:
                ela_out = os.path.join(spatial_dir, f"{frame_tag}_ela.png")
                ela = error_level_analysis(frame_path, ela_out)
                ela_score = ela.get('ela_score', 0) if not ela.get('error') else 0
                if ela_score > FA_ELA_MEDIUM_THRESHOLD:
                    severity = Severity.HIGH if ela_score > FA_ELA_HIGH_THRESHOLD else Severity.MEDIUM
                    findings.append(Finding(
                        id=str(uuid.uuid4()),
                        type=FindingType.ELA_ANOMALY,
                        severity=severity,
                        location={'start': round(t, 3), 'end': round(t, 3)},
                        explanation=(
                            f"ELA score {ela_score:.1f} at {t:.2f}s — compression "
                            f"inconsistency ({ela.get('interpretation', '')}) suggests "
                            f"localised editing within this frame."
                        ),
                        metrics={
                            'ela_score': round(ela_score, 2),
                            'frame_time_sec': round(t, 3),
                        },
                        evidence_artifacts=[ela_out] if os.path.exists(ela_out) else [],
                    ))
            except Exception as e:
                logger.warning(f"ELA failed for frame at {t:.2f}s: {e}")

            # ── Noise analysis ────────────────────────────────────────────────
            try:
                noise_out = os.path.join(spatial_dir, f"{frame_tag}_noise.png")
                nr = noise_analysis(frame_path, noise_out)
                noise_score = nr.get('noise_inconsistency_score', 0) if not nr.get('error') else 0
                if noise_score > FA_NOISE_MEDIUM_THRESHOLD:
                    severity = Severity.HIGH if noise_score > FA_NOISE_HIGH_THRESHOLD else Severity.MEDIUM
                    findings.append(Finding(
                        id=str(uuid.uuid4()),
                        type=FindingType.NOISE_ANOMALY,
                        severity=severity,
                        location={'start': round(t, 3), 'end': round(t, 3)},
                        explanation=(
                            f"Noise inconsistency score {noise_score:.1f} at {t:.2f}s — "
                            f"uneven noise patterns ({nr.get('interpretation', '')}) "
                            f"suggest pasted or composited content."
                        ),
                        metrics={
                            'noise_inconsistency_score': round(noise_score, 2),
                            'frame_time_sec': round(t, 3),
                        },
                        evidence_artifacts=[noise_out] if os.path.exists(noise_out) else [],
                    ))
            except Exception as e:
                logger.warning(f"Noise analysis failed for frame at {t:.2f}s: {e}")

            # ── Copy-move detection ───────────────────────────────────────────
            try:
                clone_out = os.path.join(spatial_dir, f"{frame_tag}_clone.png")
                cm = copy_move_detection_sift(frame_path, clone_out)
                clone_score = cm.get('clone_score', 0) if not cm.get('error') else 0
                if clone_score > FA_CLONE_MEDIUM_THRESHOLD:
                    severity = Severity.HIGH if clone_score > FA_CLONE_HIGH_THRESHOLD else Severity.MEDIUM
                    findings.append(Finding(
                        id=str(uuid.uuid4()),
                        type=FindingType.COPY_MOVE_ANOMALY,
                        severity=severity,
                        location={'start': round(t, 3), 'end': round(t, 3)},
                        explanation=(
                            f"Copy-move score {clone_score:.2f} at {t:.2f}s — "
                            f"{cm.get('matches_found', 0)} cloned region match(es) detected "
                            f"within the frame."
                        ),
                        metrics={
                            'clone_score': round(clone_score, 2),
                            'matches_found': cm.get('matches_found', 0),
                            'frame_time_sec': round(t, 3),
                        },
                        evidence_artifacts=[clone_out] if os.path.exists(clone_out) else [],
                    ))
            except Exception as e:
                logger.warning(f"Copy-move detection failed for frame at {t:.2f}s: {e}")

        cap.release()
        logger.info(
            f"Spatial forensics: {len(findings)} finding(s) across "
            f"{len(all_times)} frames "
            f"({len(flagged_times)} flagged + {len(baseline)} baseline)"
        )
        return findings

    def _analyze_gop_distribution(self, video_path: str) -> List[Finding]:
        """
        Detect irregular I-frame (GOP) distribution using ffprobe.

        Re-encoding at a splice point almost always produces an unexpected I-frame
        burst — the codec forces a new I-frame at the edit boundary. Normal recordings
        have a predictable GOP interval (e.g. every 0.5s for a 30fps/15-GOP stream).
        Flagging deviations beyond ±20% of the median interval catches most splices
        with zero additional dependencies (only ffprobe is required).

        Severity: HIGH if 3+ anomalous I-frames cluster within 2 s, else MEDIUM.
        """
        import subprocess

        try:
            cmd = [
                'ffprobe', '-v', 'quiet',
                '-select_streams', 'v:0',
                '-show_frames',
                '-show_entries', 'frame=pict_type,pkt_pts_time',
                '-of', 'csv',
                video_path
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                logger.warning(f"GOP analysis: ffprobe exited with code {proc.returncode}")
                return []
        except Exception as e:
            logger.warning(f"GOP analysis skipped: {e}")
            return []

        # Parse lines: "frame,<timestamp>,<pict_type>"
        iframe_times: List[float] = []
        for line in proc.stdout.splitlines():
            parts = line.strip().split(',')
            if len(parts) >= 3 and parts[2].strip().upper() == 'I':
                try:
                    iframe_times.append(float(parts[1]))
                except ValueError:
                    continue

        if len(iframe_times) < 4:
            # Too few I-frames to establish a reliable pattern
            return []

        intervals = np.diff(iframe_times)
        median_gop = float(np.median(intervals))

        if median_gop < 0.05:
            # All-intra codec (e.g. MotionJPEG) — every frame is an I-frame, nothing to flag
            return []

        lower = median_gop * 0.8   # −20 %
        upper = median_gop * 1.2   # +20 %

        anomalies: List[Dict[str, Any]] = []
        for i, interval in enumerate(intervals):
            if interval < lower or interval > upper:
                anomalies.append({
                    'time': iframe_times[i + 1],
                    'interval': float(interval),
                    'ratio': float(interval / median_gop),
                })

        if not anomalies:
            return []

        # Group anomalies that fall within a 2-second window of each other
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = [anomalies[0]]
        for a in anomalies[1:]:
            if a['time'] - current[-1]['time'] <= 2.0:
                current.append(a)
            else:
                groups.append(current)
                current = [a]
        groups.append(current)

        findings: List[Finding] = []
        for group in groups:
            severity = Severity.HIGH if len(group) >= 3 else Severity.MEDIUM
            t_start = max(0.0, group[0]['time'] - 0.5)
            t_end = group[-1]['time'] + 0.5
            avg_ratio = float(np.mean([g['ratio'] for g in group]))

            if avg_ratio < 0.8:
                reason = (
                    f"short GOP ({avg_ratio:.2f}× expected median) — consistent with "
                    f"re-encoding at a splice point"
                )
            else:
                reason = (
                    f"long GOP ({avg_ratio:.2f}× expected median) — possible frame "
                    f"insertion or dropped I-frame"
                )

            findings.append(Finding(
                id=str(uuid.uuid4()),
                type=FindingType.IRREGULAR_GOP,
                severity=severity,
                location={'start': round(t_start, 3), 'end': round(t_end, 3)},
                explanation=(
                    f"{len(group)} irregular I-frame interval(s) near {group[0]['time']:.2f}s: "
                    f"{reason}. Median GOP: {median_gop:.3f}s."
                ),
                metrics={
                    'anomalous_iframe_count': len(group),
                    'median_gop_sec': round(median_gop, 4),
                    'avg_interval_ratio': round(avg_ratio, 4),
                },
                evidence_artifacts=[]
            ))

        logger.info(
            f"GOP analysis: {len(findings)} finding(s) from {len(anomalies)} "
            f"anomalous I-frame(s) (median GOP: {median_gop:.3f}s)"
        )
        return findings

    def _analyze_optical_flow(
        self,
        frames: List[Tuple[float, np.ndarray]],
        all_metrics: List[Dict[str, float]],
    ) -> List[Finding]:
        """
        Detect motion vector discontinuities using dense optical flow.

        SSIM catches large visual changes but can miss splices in high-motion
        scenes where frame differences are naturally large. Optical flow measures
        *how* the scene is moving, not just how different it looks. A genuine cut
        has coherent before/after flow; a splice often creates an incoherent flow
        discontinuity detectable as either:

          1. A magnitude spike with no corresponding scene cut (SSIM still high) —
             motion "appears from nowhere", suggesting an inserted segment.
          2. An abrupt direction reversal (≥90°) in scenes with meaningful motion
             and no visual scene cut — coherent panning/tracking suddenly inverts.

        Frames are downscaled to ≤640 px wide before flow computation to keep
        runtime reasonable on long videos.
        """
        if len(frames) < 3:
            return []

        # ── Compute per-pair flow statistics ───────────────────────────────────
        flow_magnitudes: List[float] = []
        flow_directions: List[float] = []

        for i in range(len(frames) - 1):
            _, f1 = frames[i]
            _, f2 = frames[i + 1]

            g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY)

            # Downscale for speed — optical flow detail is not needed at full res
            h, w = g1.shape
            if w > 640:
                scale = 640.0 / w
                g1 = cv2.resize(g1, (int(w * scale), int(h * scale)))
                g2 = cv2.resize(g2, (int(w * scale), int(h * scale)))

            flow = cv2.calcOpticalFlowFarneback(
                g1, g2, None,
                pyr_scale=0.5, levels=3, winsize=15,
                iterations=3, poly_n=5, poly_sigma=1.2, flags=0
            )

            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            mean_mag = float(np.mean(mag))

            # Dominant direction: angle of the mean flow vector
            mean_vx = float(np.mean(flow[..., 0]))
            mean_vy = float(np.mean(flow[..., 1]))
            direction = float(np.degrees(np.arctan2(mean_vy, mean_vx))) % 360.0

            flow_magnitudes.append(mean_mag)
            flow_directions.append(direction)

        if len(flow_magnitudes) < 3:
            return []

        # ── Establish baseline ──────────────────────────────────────────────────
        mag_arr = np.array(flow_magnitudes)
        baseline_mag = float(np.median(mag_arr))
        mag_std = float(np.std(mag_arr))

        # Spike: anything more than 2.5 standard deviations above the median
        spike_threshold = baseline_mag + 2.5 * mag_std
        # Minimum motion required before direction is meaningful
        min_motion = max(0.5, baseline_mag * 0.5)

        findings: List[Finding] = []

        for i in range(1, len(flow_magnitudes) - 1):
            t_start, f1 = frames[i]
            t_end, _   = frames[i + 1]
            mag        = flow_magnitudes[i]
            ssim_score = all_metrics[i]['ssim'] if i < len(all_metrics) else 1.0

            # ── Signal 1: magnitude spike without a scene cut ─────────────────
            if mag > spike_threshold and ssim_score > 0.70:
                ratio = mag / max(baseline_mag, 0.01)
                severity = Severity.HIGH if ratio > 4.0 else Severity.MEDIUM
                findings.append(Finding(
                    id=str(uuid.uuid4()),
                    type=FindingType.MOTION_DISCONTINUITY,
                    severity=severity,
                    location={'start': round(t_start, 3), 'end': round(t_end, 3)},
                    explanation=(
                        f"Flow magnitude spike ({mag:.2f}, {ratio:.1f}× baseline) at "
                        f"{t_start:.2f}s without a scene cut (SSIM {ssim_score:.2f}). "
                        f"Motion appears without a corresponding visual transition — "
                        f"possible inserted segment."
                    ),
                    metrics={
                        'flow_magnitude': round(mag, 4),
                        'baseline_magnitude': round(baseline_mag, 4),
                        'magnitude_ratio': round(ratio, 3),
                        'ssim': round(ssim_score, 4),
                    },
                    evidence_artifacts=self._save_evidence(f1, frames[i + 1][1], t_start, t_end)
                ))

            # ── Signal 2: abrupt direction reversal without a scene cut ────────
            prev_mag = flow_magnitudes[i - 1]
            if mag > min_motion and prev_mag > min_motion and ssim_score > 0.60:
                prev_dir = flow_directions[i - 1]
                curr_dir = flow_directions[i]
                diff = abs(prev_dir - curr_dir)
                angle_diff = min(diff, 360.0 - diff)  # shortest arc

                if angle_diff > 90.0:
                    severity = Severity.HIGH if angle_diff > 150.0 else Severity.MEDIUM
                    findings.append(Finding(
                        id=str(uuid.uuid4()),
                        type=FindingType.MOTION_DISCONTINUITY,
                        severity=severity,
                        location={'start': round(t_start, 3), 'end': round(t_end, 3)},
                        explanation=(
                            f"Motion direction reversal of {angle_diff:.0f}° at "
                            f"{t_start:.2f}s without a scene cut (SSIM {ssim_score:.2f}). "
                            f"Coherent scene motion suddenly inverts — possible splice."
                        ),
                        metrics={
                            'direction_change_deg': round(angle_diff, 1),
                            'prev_direction_deg': round(prev_dir, 1),
                            'curr_direction_deg': round(curr_dir, 1),
                            'flow_magnitude': round(mag, 4),
                            'ssim': round(ssim_score, 4),
                        },
                        evidence_artifacts=self._save_evidence(f1, frames[i + 1][1], t_start, t_end)
                    ))

        logger.info(
            f"Optical flow analysis: {len(findings)} finding(s) "
            f"(baseline: {baseline_mag:.3f}, spike threshold: {spike_threshold:.3f})"
        )
        return findings

    def _remove_duplicate_findings(self, findings: List[Finding]) -> List[Finding]:
        """Remove duplicate findings at same location"""
        unique = []
        seen_locations = set()
        
        for finding in findings:
            loc_key = (finding.location.get('start'), finding.location.get('end'), str(finding.type))
            if loc_key not in seen_locations:
                unique.append(finding)
                seen_locations.add(loc_key)
        
        return unique
    def _save_evidence(self, frame1: np.ndarray, frame2: np.ndarray, id1, id2) -> List[str]:
        """Save evidence artifacts (frames, diff)"""
        artifacts = []

        # RESIZE FRAMES TO MATCH (FIX FOR SIZE MISMATCH)
        h1, w1 = frame1.shape[:2]
        h2, w2 = frame2.shape[:2]
        
        if (h1, w1) != (h2, w2):
            # Resize frame2 to match frame1
            frame2 = cv2.resize(frame2, (w1, h1))
        
        # Save before frame
        before_path = os.path.join(self.output_dir, f"before_{id1}.jpg")
        cv2.imwrite(before_path, frame1)
        artifacts.append(before_path)
        
        # Save after frame
        after_path = os.path.join(self.output_dir, f"after_{id2}.jpg")
        cv2.imwrite(after_path, frame2)
        artifacts.append(after_path)
        
        # Save difference map
        diff = cv2.absdiff(frame1, frame2)
        diff_path = os.path.join(self.output_dir, f"diff_{id1}_{id2}.jpg")
        cv2.imwrite(diff_path, diff)
        artifacts.append(diff_path)
        
        return artifacts
    
    def _group_adjacent_findings(self, findings: List[Finding], threshold: float = 2.0) -> List[Finding]:
        """Group adjacent findings of the same type"""
        if not findings:
            return findings
        
        # Sort by start time/index
        findings.sort(key=lambda f: f.location.get('start', 0))
        
        grouped = []
        current_group = [findings[0]]
        
        for i in range(1, len(findings)):
            prev = current_group[-1]
            curr = findings[i]
            
            # Check if same type and adjacent
            if (prev.type == curr.type and 
                abs(curr.location.get('start', 0) - prev.location.get('end', 0)) < threshold):
                current_group.append(curr)
            else:
                # Merge current group
                if len(current_group) > 1:
                    merged = self._merge_findings(current_group)
                    grouped.append(merged)
                else:
                    grouped.append(current_group[0])
                current_group = [curr]
        
        # Handle last group
        if len(current_group) > 1:
            merged = self._merge_findings(current_group)
            grouped.append(merged)
        else:
            grouped.append(current_group[0])
        
        return grouped
    
    def _merge_findings(self, findings: List[Finding]) -> Finding:
        """Merge multiple findings into one"""
        first = findings[0]
        last = findings[-1]
        
        # Merge metrics
        merged_metrics = {}
        for key in first.metrics:
            values = [f.metrics.get(key, None) for f in findings]
            # Only average numeric values; for non-numeric (e.g. strings like "method")
            # just take the first non-None value to avoid type errors like int + str.
            numeric_values = [v for v in values if isinstance(v, (int, float))]
            if numeric_values:
                merged_metrics[key] = sum(numeric_values) / len(numeric_values)
            else:
                merged_metrics[key] = next((v for v in values if v is not None), None)
        
        # Collect all evidence
        all_evidence = []
        for f in findings:
            all_evidence.extend(f.evidence_artifacts)
        
        return Finding(
            id=str(uuid.uuid4()),
            type=first.type,
            severity = max(
                (f.severity for f in findings),
                key=lambda s: ['low', 'medium', 'high'].index(s.value)
            ),
            location={
                'start': first.location.get('start'),
                'end': last.location.get('end')
            },
            explanation=f"Merged segment with {len(findings)} anomalies. " + first.explanation,
            metrics=merged_metrics,
            evidence_artifacts=all_evidence[:6]  # Limit evidence
        )

