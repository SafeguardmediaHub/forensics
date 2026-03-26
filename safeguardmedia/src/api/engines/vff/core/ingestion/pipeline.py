"""
VideoForensics — Ingestion Pipeline
The main orchestrator for Phase 1.
Takes a raw video file path and produces a fully prepared ForensicCase
ready for module analysis.

Call pipeline.ingest(file_path) to process a new video.
Everything — validation, hashing, metadata extraction, frame extraction,
audio extraction, and audit trail initialization — happens here in
the correct order.
"""

from __future__ import annotations
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from core.models import ForensicCase, VideoProfile
from core.audit.audit_trail import AuditTrail
from core.ingestion.validator import FileValidator, ValidationResult
from core.ingestion.extractor import MetadataExtractor
from core.preprocessing.frame_extractor import FrameExtractor, FrameExtractionResult
from core.preprocessing.audio_extractor import AudioExtractor, ExtractedAudio
from config.settings import WORKING_DIR, LOG_DIR

logger = logging.getLogger("vf.ingestion.pipeline")


class IngestionPipeline:
    """
    Phase 1: Ingestion & Preprocessing Pipeline.

    Steps executed for every video:
        1.  Validate file (format, size, integrity)
        2.  Initialize ForensicCase and AuditTrail
        3.  Log ingestion event with file hash
        4.  Create working copy (never touch the original)
        5.  Extract full metadata profile (VideoProfile)
        6.  Extract frames to working directory
        7.  Extract audio track (if present)
        8.  Set quality flags on the profile
        9.  Return the ready ForensicCase

    The ForensicCase returned from here is what gets passed to every
    forensic analysis module in Phases 2–7.
    """

    def __init__(
        self,
        working_dir: Path = WORKING_DIR,
        log_dir: Path = LOG_DIR,
        extract_frames: bool = True,
        extract_audio: bool = True,
        frame_step: int = 1,
        max_frames: Optional[int] = None,
    ):
        """
        Args:
            working_dir:     Where working copies and extracted data are stored
            log_dir:         Where audit trail files are persisted
            extract_frames:  Whether to extract frames to disk
            extract_audio:   Whether to extract the audio track
            frame_step:      Extract every Nth frame (1 = all frames)
            max_frames:      Hard cap on frames extracted (None = no cap)
        """
        self.working_dir    = Path(working_dir)
        self.log_dir        = Path(log_dir)
        self.extract_frames = extract_frames
        self.extract_audio  = extract_audio
        self.frame_step     = frame_step
        self.max_frames     = max_frames

        self.validator    = FileValidator()
        self.extractor    = MetadataExtractor()

    def ingest(
        self,
        file_path: Path,
        submitted_by: str = "system",
    ) -> ForensicCase:
        """
        Main entry point. Ingests a video file and returns a ForensicCase.

        Always succeeds — errors are logged to the audit trail and
        the case is returned with whatever was successfully prepared.
        The caller should check case.video_profile is not None before
        passing to analysis modules.

        Args:
            file_path:    Path to the original video file
            submitted_by: Identity of the submitting analyst or system

        Returns:
            ForensicCase ready for module analysis
        """
        start_time = time.time()
        file_path = Path(file_path)

        logger.info(f"{'='*60}")
        logger.info(f"INGESTION START: {file_path.name}")
        logger.info(f"Submitted by: {submitted_by}")
        logger.info(f"{'='*60}")

        # ── Step 1: Create ForensicCase & AuditTrail ──────────────────────
        case = ForensicCase(submitted_by=submitted_by)
        audit = AuditTrail(
            case_id=case.case_id,
            log_dir=self.log_dir / "audit"
        )

        audit.log("ingestion_start", f"File submitted: {file_path.name}", actor=submitted_by)
        logger.info(f"Case ID: {case.case_id}")

        # ── Step 2: Validate File ──────────────────────────────────────────
        audit.log("validation_start", f"Validating: {file_path.name}")
        validation = self.validator.validate(file_path)

        if not validation.is_valid:
            error_msg = "; ".join(validation.errors)
            audit.log("validation_failed", error_msg)
            logger.error(f"Validation failed: {error_msg}")
            case.log_event("ingestion_failed", error_msg)
            return case

        audit.log(
            "validation_passed",
            f"Format: {validation.detected_format.upper()}  "
            f"Duration: {validation.duration_seconds:.1f}s  "
            f"Size: {validation.file_size_bytes / 1e6:.1f}MB",
            file_hash=validation.sha256_hash,
        )

        for warning in validation.warnings:
            audit.log("validation_warning", warning)
            logger.warning(f"Validation warning: {warning}")

        # ── Step 3: Create Working Copy ────────────────────────────────────
        case_dir = self.working_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        working_copy = case_dir / f"original{file_path.suffix}"
        try:
            shutil.copy2(str(file_path), str(working_copy))
            case.original_file_path = file_path
            case.working_file_path  = working_copy
            audit.log(
                "working_copy_created",
                f"Original preserved at: {working_copy}"
            )
            logger.info(f"Working copy created: {working_copy}")
        except Exception as e:
            audit.log("working_copy_failed", str(e))
            logger.error(f"Failed to create working copy: {e}")
            # Continue with original path
            case.original_file_path = file_path
            case.working_file_path  = file_path

        # ── Step 4: Extract Metadata Profile ──────────────────────────────
        audit.log("metadata_extraction_start", "Extracting video profile")

        profile = self.extractor.extract(
            working_copy if working_copy.exists() else file_path,
            sha256=validation.sha256_hash,
            md5=validation.md5_hash,
        )
        case.video_profile = profile

        audit.log(
            "metadata_extracted",
            f"Codec: {profile.codec}  "
            f"Resolution: {profile.resolution_label}  "
            f"FPS: {profile.frame_rate:.2f}  "
            f"Frames: {profile.total_frames}  "
            f"Audio: {'yes' if profile.has_audio else 'no'}  "
            f"Encoder: {profile.encoder_software or 'unknown'}"
        )

        # ── Step 5: Extract Frames ─────────────────────────────────────────
        frame_result = None
        if self.extract_frames and profile.has_video:
            audit.log("frame_extraction_start",
                      f"step={self.frame_step} max={self.max_frames or 'all'}")

            frame_extractor = FrameExtractor(working_dir=case_dir)
            frame_result = frame_extractor.extract_to_disk(
                video_path=working_copy if working_copy.exists() else file_path,
                profile=profile,
                case_id=case.case_id,
                step=self.frame_step,
                max_frames=self.max_frames,
            )

            if frame_result.success:
                audit.log(
                    "frames_extracted",
                    f"{frame_result.frames_extracted} frames → {frame_result.output_directory}"
                )
                logger.info(f"Frames extracted: {frame_result.frames_extracted}")
            else:
                audit.log("frame_extraction_failed", frame_result.error)
                logger.error(f"Frame extraction failed: {frame_result.error}")

        # ── Step 6: Extract Audio ──────────────────────────────────────────
        audio_result = None
        if self.extract_audio and profile.has_audio:
            audit.log("audio_extraction_start", "Extracting audio track")

            audio_extractor = AudioExtractor(working_dir=case_dir)
            audio_result = audio_extractor.extract(
                video_path=working_copy if working_copy.exists() else file_path,
                case_id=case.case_id,
                load_samples=True,
            )

            if audio_result.success:
                audit.log(
                    "audio_extracted",
                    f"{audio_result.sample_rate}Hz  "
                    f"{audio_result.channels}ch  "
                    f"{audio_result.duration_seconds:.1f}s"
                )
                logger.info(
                    f"Audio extracted: {audio_result.sample_rate}Hz "
                    f"{audio_result.channels}ch"
                )
            else:
                audit.log("audio_extraction_failed", audio_result.error)
                logger.warning(f"Audio extraction: {audio_result.error}")

        # ── Step 7: Finalize ───────────────────────────────────────────────
        elapsed = time.time() - start_time

        # Store extraction results in case metadata for modules to find
        case.log_event("ingestion_complete",
                       f"Elapsed: {elapsed:.2f}s  "
                       f"Frames: {frame_result.frames_extracted if frame_result else 0}  "
                       f"Audio: {'yes' if (audio_result and audio_result.success) else 'no'}")

        # Attach paths modules will need
        if frame_result and frame_result.success:
            case.log_event("frame_dir", str(frame_result.output_directory))
        if audio_result and audio_result.success:
            case.log_event("audio_path", str(audio_result.file_path))

        # Integrity verification of audit trail
        integrity = audit.verify_integrity()
        if not integrity["integrity_valid"]:
            logger.error("AUDIT TRAIL INTEGRITY VIOLATION DETECTED")
            case.log_event("audit_integrity_violation",
                           f"Violations: {integrity['violations']}")
        else:
            audit.log("audit_integrity_verified",
                      f"{integrity['total_entries']} entries verified")

        audit.log(
            "ingestion_complete",
            f"Case ready for analysis. Elapsed: {elapsed:.2f}s"
        )

        logger.info(f"{'='*60}")
        logger.info(f"INGESTION COMPLETE: {elapsed:.2f}s")
        logger.info(f"Case {case.case_id} ready for analysis")
        logger.info(f"{'='*60}")

        return case

    def get_frame_dir(self, case: ForensicCase) -> Optional[Path]:
        """Helper: get the frame directory for a case."""
        frame_dir = self.working_dir / case.case_id / case.case_id / "frames"
        if frame_dir.exists():
            return frame_dir
        # Also check direct path
        alt = self.working_dir / case.case_id / "frames"
        return alt if alt.exists() else None

    def get_audio_path(self, case: ForensicCase) -> Optional[Path]:
        """Helper: get the extracted audio WAV path for a case."""
        audio_path = self.working_dir / case.case_id / case.case_id / "audio" / "audio_track.wav"
        if audio_path.exists():
            return audio_path
        alt = self.working_dir / case.case_id / "audio" / "audio_track.wav"
        return alt if alt.exists() else None
