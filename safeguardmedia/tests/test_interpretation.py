"""Unit tests for the interpretation layer."""

import pytest

from api.interpretation import (
    RISK_BAND_ELEVATED_MIN,
    RISK_BAND_HIGH_MIN,
    band_for,
    build_interpretation,
    build_summary,
    build_what_this_means,
    next_steps_for,
    resolve_unavailable,
)
from api.interpretation.copy import BANNED_WORDS


# ── bands ─────────────────────────────────────────────────────────────────────

class TestBands:
    def test_low_below_elevated_min(self):
        assert band_for(0) == "low"
        assert band_for(RISK_BAND_ELEVATED_MIN - 1) == "low"

    def test_elevated_at_elevated_min(self):
        assert band_for(RISK_BAND_ELEVATED_MIN) == "elevated"
        assert band_for(RISK_BAND_HIGH_MIN - 1) == "elevated"

    def test_high_at_high_min(self):
        assert band_for(RISK_BAND_HIGH_MIN) == "high"
        assert band_for(100) == "high"

    def test_clamps_out_of_range(self):
        assert band_for(-10) == "low"
        assert band_for(999) == "high"

    def test_default_cutoffs(self):
        # Locks in the v1 defaults so a silent edit to bands.py fails CI.
        assert RISK_BAND_ELEVATED_MIN == 40
        assert RISK_BAND_HIGH_MIN == 75


# ── next_steps ────────────────────────────────────────────────────────────────

class TestNextSteps:
    @pytest.mark.parametrize("media_type", ["image", "video", "audio", "frames"])
    def test_non_empty_per_media_type(self, media_type):
        steps = next_steps_for(media_type)
        assert len(steps) > 0

    def test_universal_steps_always_present(self):
        for media_type in ["image", "video", "audio", "frames"]:
            actions = [step["action"] for step in next_steps_for(media_type)]
            assert "verify_source" in actions
            assert "request_original" in actions
            assert "caution_before_share" in actions

    def test_image_has_reverse_lookup(self):
        actions = [s["action"] for s in next_steps_for("image")]
        assert "reverse_image_lookup" in actions

    def test_video_has_keyframe_lookup(self):
        actions = [s["action"] for s in next_steps_for("video")]
        assert "keyframe_reverse_lookup" in actions

    def test_audio_has_no_image_specific_features(self):
        actions = [s["action"] for s in next_steps_for("audio")]
        assert "reverse_image_lookup" not in actions
        assert "ocr_extract" not in actions

    def test_platform_features_carry_feature_string(self):
        for step in next_steps_for("image"):
            if step["type"] == "platform_feature":
                assert step.get("feature"), step

    def test_unknown_media_type_falls_back_to_manual(self):
        steps = next_steps_for("mystery")
        assert all(s["type"] == "manual" for s in steps)
        assert len(steps) >= 3


# ── copy ──────────────────────────────────────────────────────────────────────

class TestCopy:
    def test_summary_no_elevated(self):
        s = build_summary([], total_detectors=5, n_findings=0)
        assert "No detectors elevated" in s

    def test_summary_with_elevated(self):
        s = build_summary(
            ["noise", "copy_move", "compression"],
            total_detectors=5,
            n_findings=4,
        )
        assert "3 of 5" in s
        assert "noise" in s

    def test_summary_handles_zero_total(self):
        s = build_summary([], total_detectors=0, n_findings=0)
        assert s  # non-empty, doesn't crash

    def test_what_this_means_varies_by_band(self):
        high = build_what_this_means("high", "pre_calibration")
        elevated = build_what_this_means("elevated", "pre_calibration")
        low = build_what_this_means("low", "pre_calibration")
        assert high != elevated != low

    def test_pre_calibration_note_appended(self):
        s = build_what_this_means("high", "pre_calibration")
        assert "provisional" in s.lower()

    def test_calibrated_has_no_provisional_note(self):
        s = build_what_this_means("high", "calibrated")
        assert "provisional" not in s.lower()


# ── banned words lint ────────────────────────────────────────────────────────

class TestBannedWords:
    """Locks in the no-verdict-language rule. Guards against regressions
    in copy.py and any future hand-written strings in the interpretation
    layer."""

    def _assert_clean(self, text: str):
        import re

        lowered = text.lower()
        for word in BANNED_WORDS:
            # Word-boundary match so "authenticity" (a product feature
            # category) is not flagged by a ban on "authentic".
            assert not re.search(rf"\b{re.escape(word)}\b", lowered), (
                f"Banned verdict word '{word}' found in: {text!r}"
            )

    def test_summary_clean(self):
        for case in [
            ([], 5, 0),
            (["noise"], 5, 1),
            (["noise", "compression", "copy_move"], 5, 4),
        ]:
            self._assert_clean(build_summary(*case))

    @pytest.mark.parametrize("band", ["low", "elevated", "high"])
    @pytest.mark.parametrize("status", ["pre_calibration", "calibrated"])
    def test_what_this_means_clean(self, band, status):
        self._assert_clean(build_what_this_means(band, status))

    @pytest.mark.parametrize("media_type", ["image", "video", "audio", "frames"])
    def test_next_step_labels_clean(self, media_type):
        for step in next_steps_for(media_type):
            self._assert_clean(step["label"])


# ── checks_unavailable resolver ──────────────────────────────────────────────

class TestChecksUnavailable:
    def test_audio_skipped_modules(self):
        result = {"skipped_modules": ["enf", "voice"], "audio": {"codec": "aac"}}
        items = resolve_unavailable(result, "audio")
        names = [i["name"] for i in items]
        assert "enf" in names
        assert "voice" in names

    def test_audio_missing_codec(self):
        result = {"skipped_modules": [], "audio": {}}
        items = resolve_unavailable(result, "audio")
        assert any(i["name"] == "audio_metadata" for i in items)

    def test_video_no_audio_stream(self):
        result = {"skipped_modules": [], "video": {"has_audio": False}}
        items = resolve_unavailable(result, "video")
        assert any(i["name"] == "embedded_audio" for i in items)

    def test_image_missing_exif(self):
        result = {"report": {"metadata": {"exif": None}}}
        items = resolve_unavailable(result, "image")
        assert any(i["name"] == "exif_metadata" for i in items)

    def test_image_with_exif_present_empty(self):
        result = {"report": {"metadata": {"exif": {"Make": "Canon"}}}}
        items = resolve_unavailable(result, "image")
        assert all(i["name"] != "exif_metadata" for i in items)

    def test_frames_no_errors(self):
        result = {"media_info": {}}
        assert resolve_unavailable(result, "frames") == []

    def test_unknown_media_type(self):
        assert resolve_unavailable({}, "mystery") == []


# ── build_interpretation integration ─────────────────────────────────────────

class TestBuildInterpretation:
    def test_shape(self):
        interp = build_interpretation(
            media_type="image",
            risk_score=92,
            elevated_detectors=["noise", "copy_move", "compression"],
            total_detectors=5,
            n_findings=4,
            calibration_status="pre_calibration",
        )
        assert set(interp.keys()) == {"summary", "what_this_means", "next_steps"}
        assert isinstance(interp["next_steps"], list)
        assert interp["next_steps"]

    def test_plugs_into_schema(self):
        """Regression guard: the dict shape must round-trip through the
        pydantic Interpretation model so the adapters can drop it in
        directly."""
        from api.schemas.responses import Interpretation

        interp = build_interpretation(
            media_type="audio",
            risk_score=30,
            elevated_detectors=[],
            total_detectors=6,
            n_findings=0,
            calibration_status="pre_calibration",
        )
        Interpretation.model_validate(interp)
