"""
Visual Forensics
----------------
Standalone image (and video) manipulation detection module.

Depends only on forensic_primitives for low-level pixel analysis so that
this module and frame_analysis.py remain fully decoupled — neither imports
from the other.
"""

import os
import logging
import cv2
import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import hashlib
from datetime import datetime
from scipy import ndimage
from skimage.metrics import structural_similarity as ssim
import json

from forensic_primitives import (
    error_level_analysis,
    noise_analysis,
    copy_move_detection_sift,
    copy_move_detection_dct_fallback,
)
from crop_detection import detect_crop
from screenshot_detection import detect_screenshot
from forensic_config import (
    VF_ELA_HIGH_THRESHOLD, VF_ELA_MEDIUM_THRESHOLD,
    VF_NOISE_HIGH_THRESHOLD, VF_NOISE_MEDIUM_THRESHOLD,
    VF_CLONE_HIGH_THRESHOLD, VF_CLONE_MEDIUM_THRESHOLD,
    VF_ELA_HIGH_PTS, VF_ELA_MEDIUM_PTS,
    VF_NOISE_HIGH_PTS, VF_NOISE_MEDIUM_PTS,
    VF_CLONE_HIGH_PTS, VF_CLONE_MEDIUM_PTS,
    VF_JPEG_RECOMPRESS_PTS, VF_MISSING_EXIF_PTS,
    VF_TAMPERED_THRESHOLD, VF_REVIEW_THRESHOLD,
    VF_VIDEO_MEAN_WEIGHT, VF_VIDEO_MAX_WEIGHT,
    VF_VIDEO_TAMPERED_THRESHOLD, VF_VIDEO_REVIEW_THRESHOLD,
)

logger = logging.getLogger(__name__)


def extract_metadata(image_path):
    """Extract comprehensive metadata from image (unchanged from original)"""
    try:
        img = Image.open(image_path)
        
        # File metadata
        file_stats = os.stat(image_path)
        file_size = file_stats.st_size
        
        metadata = {
            'filename': os.path.basename(image_path),
            'file_size_bytes': file_size,
            'file_size_mb': round(file_size / (1024 * 1024), 2),
            'format': img.format,
            'dimensions': f"{img.width}x{img.height}",
            'mode': img.mode,
            'created': datetime.fromtimestamp(file_stats.st_ctime).isoformat(),
            'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
        }
        
        # EXIF data
        exif_data = {}
        try:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    
                    if tag == "GPSInfo":
                        gps_data = {}
                        for gps_tag_id, gps_value in value.items():
                            gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                            gps_data[gps_tag] = str(gps_value)
                        exif_data['GPSInfo'] = gps_data
                    else:
                        exif_data[tag] = str(value) if not isinstance(value, (int, float, str)) else value
        except Exception:
            exif_data = {}
        
        metadata['exif'] = exif_data
        
        # Generate hashes
        with open(image_path, 'rb') as f:
            file_bytes = f.read()
            metadata['md5'] = hashlib.md5(file_bytes).hexdigest()
            metadata['sha256'] = hashlib.sha256(file_bytes).hexdigest()
        
        return metadata
    
    except Exception as e:
        return {'error': f'Metadata extraction failed: {str(e)}'}



def jpeg_compression_analysis(image_path):
    """JPEG compression analysis (unchanged from original)"""
    try:
        img = Image.open(image_path)
        
        if img.format != 'JPEG':
            return {
                'format': img.format,
                'message': 'Not a JPEG file - compression analysis only works on JPEG images'
            }
        
        qtables = None
        if hasattr(img, 'quantization'):
            qtables = img.quantization
        
        quality_estimate = 'Unknown'
        compression_level = 'Unknown'
        
        file_size = os.path.getsize(image_path)
        pixels = img.width * img.height
        bytes_per_pixel = file_size / pixels
        
        if bytes_per_pixel > 2:
            quality_estimate = 'High (90-100)'
            compression_level = 'Low'
        elif bytes_per_pixel > 1:
            quality_estimate = 'Medium (70-89)'
            compression_level = 'Medium'
        else:
            quality_estimate = 'Low (<70)'
            compression_level = 'High'
        
        cv_img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        
        block_artifacts = 0
        h, w = cv_img.shape
        for i in range(8, h, 8):
            for j in range(8, w, 8):
                if i < h and j < w:
                    horizontal_diff = abs(int(cv_img[i, j]) - int(cv_img[i-1, j]))
                    vertical_diff = abs(int(cv_img[i, j]) - int(cv_img[i, j-1]))
                    if horizontal_diff > 10 or vertical_diff > 10:
                        block_artifacts += 1
        
        artifact_score = (block_artifacts / ((h // 8) * (w // 8))) * 100
        
        return {
            'format': 'JPEG',
            'quality_estimate': quality_estimate,
            'compression_level': compression_level,
            'bytes_per_pixel': round(bytes_per_pixel, 3),
            'block_artifacts': block_artifacts,
            'artifact_score': round(artifact_score, 2),
            'quantization_tables_present': qtables is not None,
            'double_compression_likelihood': 'High' if artifact_score > 30 else 'Medium' if artifact_score > 15 else 'Low',
            'interpretation': 'Possibly re-compressed (edited)' if artifact_score > 20 else 'Single compression (likely original)'
        }
    except Exception as e:
        return {'error': f'JPEG analysis failed: {str(e)}'}


def detect_ai_generated_heuristic(image_path, output_path=None):
    """
    Heuristic-based AI detection (for fallback when ML model unavailable)
    Enhanced version with 2025 techniques
    """
    try:
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        
        indicators = []
        ai_score = 0
        
        # 1. Frequency domain analysis
        f_transform = np.fft.fft2(gray)
        f_shift = np.fft.fftshift(f_transform)
        magnitude = np.log(np.abs(f_shift) + 1)
        
        center_y, center_x = height // 2, width // 2
        radius = min(center_y, center_x) // 2
        y, x = np.ogrid[:height, :width]
        mask_high = ((x - center_x)**2 + (y - center_y)**2) > radius**2
        high_freq_energy = np.mean(magnitude[mask_high])
        low_freq_energy = np.mean(magnitude[~mask_high])
        freq_ratio = high_freq_energy / (low_freq_energy + 1e-10)
        
        if freq_ratio < 0.18:
            ai_score += 20
            indicators.append("Low high-frequency energy (strong AI indicator)")
        
        # 2. Saturation uniformity
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        sat_std = np.std(s)
        if sat_std < 25:
            ai_score += 20
            indicators.append("Very uniform saturation – typical of AI")
        
        # 3. Texture variance
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        lap_var = laplacian.var()
        if lap_var < 50:
            ai_score += 25
            indicators.append("Extremely low texture variance – very strong AI signal")
        
        # 4. Frequency-domain flatness
        dft = cv2.dft(np.float32(gray), flags=cv2.DFT_COMPLEX_OUTPUT)
        dft_shift = np.fft.fftshift(dft)
        magnitude_spectrum = cv2.magnitude(dft_shift[:,:,0], dft_shift[:,:,1])
        log_spectrum = np.log(magnitude_spectrum + 1)
        spectrum_std = np.std(log_spectrum)
        if spectrum_std < 1.1:
            ai_score += 20
            indicators.append("Flat frequency spectrum – modern AI fingerprint")
        
        # 5. Edge density
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size
        if edge_density < 0.012:
            ai_score += 20
            indicators.append("Overly smooth edges – classic AI artifact")
        
        # 6. LBP entropy
        def compute_lbp(image, radius=1, n_points=8):
            lbp = np.zeros_like(image)
            for i in range(radius, image.shape[0] - radius):
                for j in range(radius, image.shape[1] - radius):
                    center = image[i, j]
                    binary = 0
                    for k in range(n_points):
                        angle = 2 * np.pi * k / n_points
                        x = int(round(i + radius * np.cos(angle)))
                        y = int(round(j - radius * np.sin(angle)))
                        binary |= (1 << k) if image[x, y] >= center else 0
                    lbp[i, j] = binary
            return lbp
        
        gray_small = cv2.resize(gray, (200, 200))
        lbp = compute_lbp(gray_small)
        lbp_hist, _ = np.histogram(lbp.ravel(), bins=256, range=(0, 256))
        lbp_entropy = -np.sum((lbp_hist/lbp_hist.sum() + 1e-10) * np.log2(lbp_hist/lbp_hist.sum() + 1e-10))
        
        if lbp_entropy < 4.8:
            ai_score += 25
            indicators.append("Very low LBP entropy – strongest synthetic indicator")
        
        # 7. Face-specific artifacts
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        
        face_artifacts = []
        if len(faces) > 0:
            for (x, y, w, h) in faces:
                face_roi = gray[y:y+h, x:x+w]
                
                if face_roi.size > 0:
                    face_flipped = cv2.flip(face_roi, 1)
                    if face_roi.shape == face_flipped.shape:
                        symmetry = ssim(face_roi, face_flipped)
                        
                        if symmetry > 0.85:
                            ai_score += 10
                            face_artifacts.append("Unusually high facial symmetry")
                        
                        face_edges = cv2.Canny(face_roi, 30, 100)
                        boundary_region = face_edges[0:5, :].sum() + face_edges[-5:, :].sum()
                        if boundary_region < 100:
                            ai_score += 10
                            face_artifacts.append("Smooth face boundary (possible blend)")
        
        if face_artifacts:
            indicators.extend(face_artifacts)
        
        # Create heatmap if output path provided
        if output_path:
            block_size = 32
            heatmap = np.zeros((height, width), dtype=np.float32)
            
            for i in range(0, height - block_size, block_size // 2):
                for j in range(0, width - block_size, block_size // 2):
                    block = gray[i:i+block_size, j:j+block_size]
                    block_lap = cv2.Laplacian(block, cv2.CV_64F).var()
                    block_std = np.std(block)
                    
                    suspicion = max(0, 50 - block_lap) + max(0, 30 - block_std)
                    heatmap[i:i+block_size, j:j+block_size] = suspicion
            
            heatmap_normalized = cv2.normalize(heatmap, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            heatmap_colored = cv2.applyColorMap(heatmap_normalized, cv2.COLORMAP_JET)
            
            overlay = cv2.addWeighted(img, 0.6, heatmap_colored, 0.4, 0)
            
            timestamp_text = f"AI Detection (Heuristic) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            cv2.putText(overlay, timestamp_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            cv2.imwrite(output_path, overlay)
        
        ai_score = min(ai_score, 100)
        
        if ai_score >= 85:
            verdict = "Very Likely AI-Generated"
            confidence = "High"
        elif ai_score >= 60:
            verdict = "Likely AI-Generated"
            confidence = "High"
        elif ai_score >= 40:
            verdict = "Possibly AI-Generated"
            confidence = "Medium"
        else:
            verdict = "Likely Real"
            confidence = "High"
        
        return {
            'ai_generation_score': ai_score,
            'verdict': verdict,
            'confidence': confidence,
            'indicators': indicators if indicators else ["No significant AI indicators found"],
            'faces_detected': len(faces),
            'analysis_details': {
                'frequency_ratio': round(freq_ratio, 3),
                'saturation_std': round(sat_std, 2),
                'texture_variance': round(lap_var, 2),
                'edge_density': round(edge_density, 4),
                'lbp_entropy': round(lbp_entropy, 2)
            },
            'heatmap': output_path,
            'method': 'Heuristic Analysis'
        }
        
    except Exception as e:
        return {'error': f'AI detection failed: {str(e)}'}


def reverse_image_search(image_path):
    """Generate reverse image search URLs (unchanged)"""
    try:
        with open(image_path, 'rb') as f:
            image_hash = hashlib.md5(f.read()).hexdigest()
        
        img = Image.open(image_path)
        
        return {
            'image_hash': image_hash,
            'dimensions': f"{img.width}x{img.height}",
            'search_engines': {
                'google': 'https://images.google.com/searchbyimage/upload',
                'yandex': 'https://yandex.com/images/search?rpt=imageview&url=',
                'tineye': 'https://tineye.com/search',
                'bing': 'https://www.bing.com/images/search?view=detailv2&iss=sbi'
            },
            'instructions': 'Upload the image to these services to find similar images or sources',
            'note': 'Automated reverse image search requires API keys from these services'
        }
    except Exception as e:
        return {'error': f'Reverse search prep failed: {str(e)}'}


def create_combined_heatmap(output_dir):
    """Combine all heatmaps (unchanged from original)"""
    try:
        heatmap_files = {
            'ela': os.path.join(output_dir, 'ela_heatmap.png'),
            'noise': os.path.join(output_dir, 'noise_heatmap.png'),
            'clone': os.path.join(output_dir, 'clone_detection.png')
        }
        
        heatmaps = {}
        for key, path in heatmap_files.items():
            if os.path.exists(path):
                img = cv2.imread(path)
                if img is not None:
                    heatmaps[key] = img
        
        if not heatmaps:
            return None
        
        reference_size = None
        for img in heatmaps.values():
            reference_size = (img.shape[1], img.shape[0])
            break
        
        for key in heatmaps:
            if heatmaps[key].shape[:2] != (reference_size[1], reference_size[0]):
                heatmaps[key] = cv2.resize(heatmaps[key], reference_size)
        
        combined = np.zeros_like(list(heatmaps.values())[0], dtype=np.float32)
        for img in heatmaps.values():
            combined += img.astype(np.float32)
        
        combined = (combined / len(heatmaps)).astype(np.uint8)
        
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(combined, 'COMBINED MANIPULATION HEATMAP', (10, 30), 
                    font, 1, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(combined, 'Red/Yellow = High Suspicion | Blue/Green = Low Suspicion', 
                    (10, 60), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        
        timestamp_text = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.putText(combined, timestamp_text, (10, combined.shape[0] - 20), 
                    font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        
        combined_path = os.path.join(output_dir, 'combined_heatmap.png')
        cv2.imwrite(combined_path, combined)
        
        return combined_path
    
    except Exception as e:
        return None


def generate_user_friendly_summary(report):
    """Generate non-technical summary (AI detection removed)"""
    try:
        manipulation = report.get('manipulation_detection', {})
        overall = report.get('overall_assessment', {})
        metadata = report.get('metadata', {})
        ela_score = manipulation.get('ela', {}).get('ela_score', 0)
        noise_score = manipulation.get('noise', {}).get('noise_inconsistency_score', 0)
        clone_score = manipulation.get('copy_move', {}).get('clone_score', 0)
        tampering_likelihood = overall.get('tampering_likelihood', 0)
        verdict = overall.get('verdict', 'Unknown')
        
        if tampering_likelihood > 60:
            status = "🔴 HIGH RISK"
            recommendation = "This image shows strong signs of manipulation. Do not trust it without verification."
        elif tampering_likelihood > 30:
            status = "🟡 MEDIUM RISK"
            recommendation = "This image shows some suspicious patterns. Verify with other sources before trusting."
        else:
            status = "🟢 LOW RISK"
            recommendation = "This image appears mostly authentic, but always verify important claims."
        
        issues_found = []
        if ela_score > 15:
            issues_found.append("• Uneven compression levels detected (possible editing)")
        elif ela_score > 8:
            issues_found.append("• Minor compression inconsistencies found")
        
        if noise_score > 50:
            issues_found.append("• Significant noise pattern inconsistencies (likely edited)")
        elif noise_score > 25:
            issues_found.append("• Some noise irregularities detected")
        
        if clone_score > 5:
            issues_found.append("• Copied/cloned regions found (parts duplicated)")
        elif clone_score > 2:
            issues_found.append("• Possible copy-move manipulation detected")
        
        jpeg_info = manipulation.get('jpeg_compression', {})
        if jpeg_info.get('double_compression_likelihood') == 'High':
            issues_found.append("• Image has been re-saved multiple times (sign of editing)")
        
        exif_data = metadata.get('exif', {})
        if not exif_data or len(exif_data) < 3:
            issues_found.append("• Missing camera information (metadata stripped)")
        
        if not issues_found:
            issues_found.append("• No major manipulation indicators found")
        
        positive_findings = []
        if ela_score <= 8:
            positive_findings.append("• Consistent compression patterns across image")
        if noise_score <= 25:
            positive_findings.append("• Uniform noise distribution detected")
        if clone_score <= 2:
            positive_findings.append("• No copy-paste manipulation detected")
        if jpeg_info.get('double_compression_likelihood') in ['Low', 'Medium']:
            positive_findings.append("• Single compression detected (not re-edited)")
        if exif_data and len(exif_data) >= 3:
            positive_findings.append("• Original camera metadata present and intact")
        
        if not positive_findings:
            positive_findings.append("• Limited authentic indicators found")
        
        summary = {
            'status': status,
            'trust_level': verdict,
            'tampering_probability': f"{tampering_likelihood}%",
            'issues_found': issues_found,
            'positive_findings': positive_findings,
            'recommendation': recommendation,
            'image_info': {
                'format': metadata.get('format', 'Unknown'),
                'dimensions': metadata.get('dimensions', 'Unknown'),
                'file_size': metadata.get('file_size_mb', 'Unknown'),
                'has_gps': 'GPSInfo' in exif_data
            },
            'explanation': {
                'what_we_checked': [
                    "Compression patterns (Error Level Analysis)",
                    "Noise consistency across image",
                    "Copy-paste detection (SIFT keypoints)",
                    "JPEG re-compression signs",
                    "Metadata authenticity"
                ],
                'how_to_read': {
                    'LOW RISK (0-30%)': 'Image appears authentic',
                    'MEDIUM RISK (31-60%)': 'Some suspicious patterns detected',
                    'HIGH RISK (61-100%)': 'Strong manipulation indicators'
                }
            },
            'combined_heatmap': report.get('combined_heatmap_path'),
            'note': 'Analysis based on forensic techniques only (AI detection disabled)'
        }
        
        return summary
    
    except Exception as e:
        return {'error': f'Summary generation failed: {str(e)}'}


def enhance_and_analyze(image_path, output_dir):
    """Enhance image and perform various analyses (unchanged)"""
    try:
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        results = {}
        
        enhanced = cv2.equalizeHist(gray)
        enhanced_path = os.path.join(output_dir, 'enhanced_luminance.png')
        cv2.imwrite(enhanced_path, enhanced)
        results['enhanced_luminance'] = enhanced_path
        
        edges = cv2.Canny(gray, 100, 200)
        edges_path = os.path.join(output_dir, 'edge_detection.png')
        cv2.imwrite(edges_path, edges)
        results['edge_detection'] = edges_path
        
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        hist_peaks = len([i for i in range(1, 255) if hist[i] > hist[i-1] and hist[i] > hist[i+1]])
        
        results['histogram_analysis'] = {
            'peaks': int(hist_peaks),
            'mean_brightness': round(float(np.mean(gray)), 2),
            'std_brightness': round(float(np.std(gray)), 2),
            'interpretation': 'Normal distribution' if 15 < hist_peaks < 40 else 'Suspicious pattern'
        }
        
        dct = cv2.dct(np.float32(gray))
        dct_log = np.log(np.abs(dct) + 1)
        dct_normalized = cv2.normalize(dct_log, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        dct_path = os.path.join(output_dir, 'frequency_analysis.png')
        cv2.imwrite(dct_path, dct_normalized)
        results['frequency_analysis'] = dct_path
        
        return results
    
    except Exception as e:
        return {'error': f'Enhancement failed: {str(e)}'}


def generate_verification_hashes(image_path):
    """Generate multiple hashes (unchanged)"""
    try:
        with open(image_path, 'rb') as f:
            file_bytes = f.read()
        
        return {
            'md5': hashlib.md5(file_bytes).hexdigest(),
            'sha256': hashlib.sha256(file_bytes).hexdigest(),
            'sha1': hashlib.sha1(file_bytes).hexdigest()
        }
    except Exception as e:
        return {'error': str(e)}


def generate_forensic_report(image_path, output_dir, ai_detector=None,
                             is_video_frame: bool = False):
    """
    Generate a complete forensic analysis report for an image file.

    Parameters
    ----------
    image_path    : path to the image to analyse
    output_dir    : directory where heatmaps and artefacts are written
    ai_detector   : reserved for future ML model (currently disabled)
    is_video_frame: set True when the image is a frame extracted from a video.
                    This skips checks that are meaningless or systematically
                    biased for extracted frames:
                      - JPEG compression analysis (frames saved as PNG)
                      - Reverse image search preparation
                      - EXIF-missing penalty (extracted frames never have EXIF)
                      - User-friendly summary (not needed for per-frame calls)
    """
    try:
        report = {
            'analysis_timestamp':  datetime.now().isoformat(),
            'image_file':          os.path.basename(image_path),
            'analyzer_version':    '2.0 Enhanced (Forensics-Only)',
            'ai_detection_enabled': False,
            'is_video_frame':      is_video_frame,
        }

        # ── Metadata ──────────────────────────────────────────────────────────
        report['metadata'] = extract_metadata(image_path)

        # ── Core manipulation detection ───────────────────────────────────────
        ela_output   = os.path.join(output_dir, 'ela_heatmap.png')
        noise_output = os.path.join(output_dir, 'noise_heatmap.png')
        clone_output = os.path.join(output_dir, 'clone_detection.png')

        manipulation = {
            'ela':       error_level_analysis(image_path, ela_output),
            'noise':     noise_analysis(image_path, noise_output),
            'copy_move': copy_move_detection_sift(image_path, clone_output),
        }

        # JPEG compression analysis is only meaningful for original JPEG files,
        # not for PNG frames extracted from video.
        if not is_video_frame:
            manipulation['jpeg_compression'] = jpeg_compression_analysis(image_path)
        else:
            manipulation['jpeg_compression'] = {
                'skipped': True,
                'note': 'JPEG analysis skipped for video frame (source is PNG)',
            }

        manipulation['ai_generated'] = {
            'enabled': False,
            'note': 'AI detection disabled - forensics analysis only',
        }

        # Crop and screenshot detection — skipped for video frames since
        # extracted frames never have camera EXIF and use PNG format.
        if not is_video_frame:
            try:
                manipulation['crop_detection'] = detect_crop(image_path)
            except Exception as e:
                manipulation['crop_detection'] = {
                    'error': f'crop detection failed: {e}',
                    'crop_signals': [],
                    'crop_score': 0,
                    'confidence': 0.0,
                }
            try:
                manipulation['screenshot_detection'] = detect_screenshot(image_path)
            except Exception as e:
                manipulation['screenshot_detection'] = {
                    'error': f'screenshot detection failed: {e}',
                    'screenshot_signals': [],
                    'screenshot_score': 0,
                    'confidence': 0.0,
                }

        report['manipulation_detection'] = manipulation

        # ── Enhancement & hashes ─────────────────────────────────────────────
        report['enhancement_analysis'] = enhance_and_analyze(image_path, output_dir)
        report['verification']         = generate_verification_hashes(image_path)

        # Reverse image search is only useful for original files, not frames.
        if not is_video_frame:
            report['reverse_search'] = reverse_image_search(image_path)

        # ── Tampering likelihood ──────────────────────────────────────────────
        ela_score   = manipulation['ela'].get('ela_score', 0)
        noise_score = manipulation['noise'].get('noise_inconsistency_score', 0)
        clone_score = manipulation['copy_move'].get('clone_score', 0)

        tampering_likelihood = 0.0

        if ela_score > VF_ELA_HIGH_THRESHOLD:
            tampering_likelihood += VF_ELA_HIGH_PTS
        elif ela_score > VF_ELA_MEDIUM_THRESHOLD:
            tampering_likelihood += VF_ELA_MEDIUM_PTS

        if noise_score > VF_NOISE_HIGH_THRESHOLD:
            tampering_likelihood += VF_NOISE_HIGH_PTS
        elif noise_score > VF_NOISE_MEDIUM_THRESHOLD:
            tampering_likelihood += VF_NOISE_MEDIUM_PTS

        if clone_score > VF_CLONE_HIGH_THRESHOLD:
            tampering_likelihood += VF_CLONE_HIGH_PTS
        elif clone_score > VF_CLONE_MEDIUM_THRESHOLD:
            tampering_likelihood += VF_CLONE_MEDIUM_PTS

        if not is_video_frame:
            # JPEG double-compression is only a valid signal for original files.
            jpeg_analysis = manipulation.get('jpeg_compression', {})
            if (not jpeg_analysis.get('error')
                    and not jpeg_analysis.get('skipped')
                    and jpeg_analysis.get('double_compression_likelihood') == 'High'):
                tampering_likelihood += VF_JPEG_RECOMPRESS_PTS

            # Missing EXIF is suspicious for original photos; for extracted
            # frames it is always absent and should not inflate the score.
            if not report['metadata'].get('exif') or len(report['metadata'].get('exif', {})) < 3:
                tampering_likelihood += VF_MISSING_EXIF_PTS

        tampering_likelihood = min(tampering_likelihood, 100)

        if tampering_likelihood > VF_TAMPERED_THRESHOLD:
            verdict = 'Likely Tampered'
        elif tampering_likelihood > VF_REVIEW_THRESHOLD:
            verdict = 'Possibly Tampered'
        else:
            verdict = 'Likely Authentic'

        report['overall_assessment'] = {
            'tampering_likelihood': round(tampering_likelihood, 1),
            'verdict':    verdict,
            'confidence': 'High' if abs(tampering_likelihood - 50) > 30 else 'Medium',
            'note':       'Assessment based on forensic analysis only (AI detection disabled)',
        }

        combined_heatmap = create_combined_heatmap(output_dir)
        report['combined_heatmap_available'] = combined_heatmap is not None
        report['combined_heatmap_path']      = combined_heatmap

        # Summary is only generated for top-level image reports, not per-frame.
        if not is_video_frame:
            report['user_friendly_summary'] = generate_user_friendly_summary(report)

        return report

    except Exception as e:
        return {'error': f'Report generation failed: {str(e)}'}


# ============================================================================
# VIDEO VISUAL FORENSICS
# ============================================================================

def generate_forensic_report_video(video_path: str, output_dir: str,
                                   max_frames: int = 12) -> dict:
    """
    Run visual (spatial/content) forensics on a video by analysing a
    representative sample of frames independently with the same pipeline
    used for still images.

    Strategy
    --------
    * Divide the video into *max_frames* equal-interval segments and take
      one frame from each.  This gives even coverage regardless of duration.
    * Each frame is written to disk as a JPEG and passed through
      generate_forensic_report().
    * Per-frame scores are aggregated (mean + max) into an overall
      tampering likelihood and a single verdict:
          > 60  → Likely Tampered
          > 30  → Possibly Tampered
          ≤ 30  → Likely Authentic

    Returns
    -------
    A dict with:
      verdict              – human-readable verdict string
      tampering_likelihood – 0-100 aggregate score
      confidence           – 'High' | 'Medium'
      frames_analyzed      – number of frames actually processed
      frame_results        – list of per-frame assessment dicts
      analysis_timestamp   – ISO timestamp
    """
    os.makedirs(output_dir, exist_ok=True)
    frames_dir = os.path.join(output_dir, 'vf_frames')
    os.makedirs(frames_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'error': f'Cannot open video: {video_path}'}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0

    if total_frames <= 0:
        cap.release()
        return {'error': 'Could not determine frame count for video'}

    # Build evenly-spaced frame indices (avoid first/last 2 % to skip
    # black leader / trailer frames that skew ELA scores).
    margin      = max(1, int(total_frames * 0.02))
    sample_pool = range(margin, total_frames - margin)
    step        = max(1, len(sample_pool) // max_frames)
    indices     = list(sample_pool)[::step][:max_frames]

    frame_results  = []
    ela_scores     = []
    noise_scores   = []
    clone_scores   = []

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue

        timestamp = round(idx / fps, 3)
        tag       = f"frame_{idx:06d}"
        # PNG — lossless so ELA measures real compression inconsistencies,
        # not artefacts introduced by our own JPEG save.
        frame_path = os.path.join(frames_dir, f"{tag}.png")
        cv2.imwrite(frame_path, frame)

        frame_out_dir = os.path.join(frames_dir, tag)
        os.makedirs(frame_out_dir, exist_ok=True)

        rpt = generate_forensic_report(frame_path, frame_out_dir, ai_detector=None,
                                       is_video_frame=True)

        assessment = rpt.get('overall_assessment', {})
        likelihood = assessment.get('tampering_likelihood', 0)

        frame_results.append({
            'frame_index':       idx,
            'timestamp_sec':     timestamp,
            'tampering_likelihood': likelihood,
            'verdict':           assessment.get('verdict', 'Unknown'),
            'ela_score':         rpt.get('manipulation_detection', {}).get('ela', {}).get('ela_score', 0),
            'noise_score':       rpt.get('manipulation_detection', {}).get('noise', {}).get('noise_inconsistency_score', 0),
            'clone_score':       rpt.get('manipulation_detection', {}).get('copy_move', {}).get('clone_score', 0),
        })

        ela_scores.append(rpt.get('manipulation_detection', {}).get('ela', {}).get('ela_score', 0))
        noise_scores.append(rpt.get('manipulation_detection', {}).get('noise', {}).get('noise_inconsistency_score', 0))
        clone_scores.append(rpt.get('manipulation_detection', {}).get('copy_move', {}).get('clone_score', 0))

    cap.release()

    if not frame_results:
        return {'error': 'No frames could be extracted from video'}

    likelihoods = [r['tampering_likelihood'] for r in frame_results]

    # Aggregate: configurable weight on mean (overall level) vs max (worst frame)
    mean_likelihood = sum(likelihoods) / len(likelihoods)
    max_likelihood  = max(likelihoods)
    aggregate = round(
        min(
            mean_likelihood * VF_VIDEO_MEAN_WEIGHT +
            max_likelihood  * VF_VIDEO_MAX_WEIGHT,
            100,
        ),
        1,
    )

    if aggregate > VF_VIDEO_TAMPERED_THRESHOLD:
        verdict    = 'Likely Tampered'
        confidence = 'High' if aggregate > 80 else 'Medium'
    elif aggregate > VF_VIDEO_REVIEW_THRESHOLD:
        verdict    = 'Possibly Tampered'
        confidence = 'Medium'
    else:
        verdict    = 'Likely Authentic'
        confidence = 'High' if aggregate < 15 else 'Medium'

    flagged = sum(1 for r in frame_results if r['tampering_likelihood'] > 30)

    # Write per-frame detail to disk rather than keeping it in memory /
    # returning it inline — this prevents bloating the Celery result backend
    # (Redis) with large per-frame report data.
    frame_results_path = os.path.join(output_dir, 'frame_results.json')
    try:
        with open(frame_results_path, 'w') as fh:
            json.dump(frame_results, fh, indent=2)
    except Exception as e:
        logger.warning(f'Could not write frame_results.json: {e}')
        frame_results_path = None

    return {
        'verdict':               verdict,
        'tampering_likelihood':  aggregate,
        'confidence':            confidence,
        'frames_analyzed':       len(frame_results),
        'flagged_frames':        flagged,
        'mean_ela_score':        round(sum(ela_scores) / len(ela_scores), 2) if ela_scores else 0,
        'mean_noise_score':      round(sum(noise_scores) / len(noise_scores), 2) if noise_scores else 0,
        'mean_clone_score':      round(sum(clone_scores) / len(clone_scores), 2) if clone_scores else 0,
        'frame_results_path':    frame_results_path,   # path on disk, not inline data
        'analysis_timestamp':    datetime.now().isoformat(),
        'note':                  'Spatial/content forensics on sampled frames — temporal analysis handled by frame_analysis module',
    }