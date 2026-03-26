"""
AudioForensics — Test Suite
Phase 0: Foundation & Data Models

Run:  python tests/test_all_phases.py
      python tests/test_all_phases.py --phase 0
"""

from __future__ import annotations
import argparse
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Test infrastructure ───────────────────────────────────────────────────────

@dataclass
class TestResult:
    name:     str
    passed:   bool
    elapsed:  float
    error:    Optional[str] = None


@dataclass
class TestSuite:
    name:    str
    results: List[TestResult] = None

    def __post_init__(self):
        self.results = []

    def add(self, r: TestResult):
        self.results.append(r)

    @property
    def n_passed(self): return sum(1 for r in self.results if r.passed)
    @property
    def n_failed(self): return sum(1 for r in self.results if not r.passed)
    @property
    def all_passed(self): return self.n_failed == 0


RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"


def run_test(name: str, fn: Callable) -> TestResult:
    t0 = time.time()
    try:
        fn()
        return TestResult(name=name, passed=True, elapsed=round(time.time()-t0, 3))
    except Exception as e:
        return TestResult(name=name, passed=False,
                          elapsed=round(time.time()-t0, 3),
                          error=str(e))


def print_result(r: TestResult, verbose: bool = False):
    icon    = f"{GREEN}✓ PASS{RESET}" if r.passed else f"{RED}✗ FAIL{RESET}"
    elapsed = f"  {YELLOW}({r.elapsed:.2f}s){RESET}" if r.elapsed > 2.0 else f"  ({r.elapsed:.2f}s)"
    print(f"  {icon}  {r.name}{elapsed}")
    if not r.passed and r.error:
        print(f"         {RED}{r.error}{RESET}")


def print_suite_header(title: str):
    print(f"\n{BOLD}{BLUE}{'─'*60}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─'*60}{RESET}")


def print_suite_summary(suite: TestSuite):
    total = len(suite.results)
    p, f  = suite.n_passed, suite.n_failed
    color = GREEN if f == 0 else RED
    print(f"\n  {color}{BOLD}{p}/{total} passed{RESET}    {f} failed\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 0 TESTS — Foundation & Data Models
# ═══════════════════════════════════════════════════════════════════════════════

def test_phase0(verbose: bool = False) -> TestSuite:
    suite = TestSuite("Phase 0 — Foundation & Data Models")
    print_suite_header("Phase 0 — Foundation & Data Models")

    # ── T00: Imports ──────────────────────────────────────────────────────────
    def t00():
        from core.models import (
            Severity, Verdict, ENFGrid, AudioCodec,
            TemporalLocation, Finding, AudioProfile,
            ModuleScore, FusionResult, AudioCase, BaseAudioModule,
        )
    r = run_test("T00 — All core models import cleanly", t00)
    suite.add(r); print_result(r, verbose)

    # ── T01: Severity and Verdict enums ───────────────────────────────────────
    def t01():
        from core.models import Severity, Verdict
        assert Severity.HIGH.value   == "HIGH"
        assert Severity.MEDIUM.value == "MEDIUM"
        assert Severity.LOW.value    == "LOW"
        assert Verdict.TAMPERED.label         == "Tampered"
        assert Verdict.LIKELY_AUTHENTIC.label == "Likely Authentic"
        assert Verdict.TAMPERED.is_elevated   == True
        assert Verdict.LIKELY_AUTHENTIC.is_elevated == False
        assert Verdict.INCONCLUSIVE.is_elevated     == False
    r = run_test("T01 — Severity and Verdict enums correct", t01)
    suite.add(r); print_result(r, verbose)

    # ── T02: ENFGrid properties ───────────────────────────────────────────────
    def t02():
        from core.models import ENFGrid
        g50 = ENFGrid.GRID_50HZ
        g60 = ENFGrid.GRID_60HZ
        assert g50.nominal    == 50.0
        assert g50.band_low   == 49.0
        assert g50.band_high  == 51.0
        assert g50.harmonic_2 == 100.0
        assert g60.nominal    == 60.0
        assert g60.band_low   == 59.0
        assert g60.band_high  == 61.0
    r = run_test("T02 — ENFGrid band properties correct", t02)
    suite.add(r); print_result(r, verbose)

    # ── T03: AudioCodec properties ────────────────────────────────────────────
    def t03():
        from core.models import AudioCodec
        assert AudioCodec.MP3.is_lossy       == True
        assert AudioCodec.AAC.is_lossy       == True
        assert AudioCodec.FLAC.is_lossy      == False
        assert AudioCodec.PCM_S16LE.is_lossy == False
        assert AudioCodec.AMR_NB.typical_max_freq == 4000.0
        assert AudioCodec.AMR_WB.typical_max_freq == 8000.0
        assert AudioCodec.MP3.typical_max_freq    is None
    r = run_test("T03 — AudioCodec properties correct", t03)
    suite.add(r); print_result(r, verbose)

    # ── T04: TemporalLocation ─────────────────────────────────────────────────
    def t04():
        from core.models import TemporalLocation
        loc = TemporalLocation(start_seconds=2.5, end_seconds=7.5)
        assert loc.duration  == 5.0
        assert loc.midpoint  == 5.0
        assert loc.start_timecode == "00:02.500"
        assert loc.end_timecode   == "00:07.500"

        loc2 = TemporalLocation(start_seconds=6.0, end_seconds=9.0)
        assert loc.overlaps(loc2) == True

        loc3 = TemporalLocation(start_seconds=8.0, end_seconds=10.0)
        assert loc.overlaps(loc3) == False
    r = run_test("T04 — TemporalLocation arithmetic correct", t04)
    suite.add(r); print_result(r, verbose)

    # ── T05: Finding ──────────────────────────────────────────────────────────
    def t05():
        from core.models import Finding, Severity, TemporalLocation
        f = Finding(
            module      = "enf",
            title       = "ENF phase discontinuity",
            description = "Phase jump of 1.2 radians at t=5.0s",
            severity    = Severity.HIGH,
            confidence  = 0.91,
            temporal_location = TemporalLocation(4.8, 5.2),
        )
        assert f.confidence      == 0.91
        assert f.is_significant  == True
        assert f.severity        == Severity.HIGH

        # Confidence clamped to [0,1]
        f2 = Finding("test","t","d", Severity.LOW, confidence=1.5,
                     temporal_location=None)
        assert f2.confidence == 1.0

        f3 = Finding("test","t","d", Severity.LOW, confidence=-0.3,
                     temporal_location=None)
        assert f3.confidence == 0.0
    r = run_test("T05 — Finding dataclass and validation correct", t05)
    suite.add(r); print_result(r, verbose)

    # ── T06: ModuleScore ──────────────────────────────────────────────────────
    def t06():
        from core.models import ModuleScore, Finding, Severity
        ms = ModuleScore(
            module="enf", score=0.75, confidence=0.9, findings=[
                Finding("enf","title","desc", Severity.HIGH, 0.9, None),
                Finding("enf","t2","d2",      Severity.LOW,  0.3, None),
            ]
        )
        assert ms.is_elevated == True
        assert len(ms.significant_findings) == 1

        ms_low = ModuleScore(module="noise", score=0.15, confidence=0.8, findings=[])
        assert ms_low.is_elevated == False

        ms_skip = ModuleScore(module="voice", score=0.9, confidence=0.9,
                              findings=[], skipped=True)
        assert ms_skip.is_elevated == False   # skipped never elevated
    r = run_test("T06 — ModuleScore elevation and findings correct", t06)
    suite.add(r); print_result(r, verbose)

    # ── T07: AudioCase assembly ───────────────────────────────────────────────
    def t07():
        from core.models import (
            AudioCase, AudioProfile, ModuleScore, AudioCodec, ENFGrid
        )
        profile = AudioProfile(
            file_path="test.wav", file_size_bytes=320000,
            sha256_hash="abc"*21+"ab", sha256_partial="abc",
            container_format="wav", codec_name="pcm_s16le",
            codec_enum=AudioCodec.PCM_S16LE, sample_rate=16000,
            channels=1, duration_seconds=10.0, total_samples=160000,
            bit_depth=16, bitrate_bps=256000, encoder_string=None,
            creation_time=None, has_id3_tags=False, has_xmp_data=False,
            is_mono=True, dc_offset=0.001, peak_amplitude=0.8,
            rms_level=0.18, is_clipped=False, clipping_count=0,
            silence_ratio=0.05, effective_max_freq=7500.0,
            enf_grid=ENFGrid.GRID_50HZ,
        )
        case = AudioCase.create(profile)
        assert case.case_id  != ""
        assert case.has_results == False

        score = ModuleScore("enf", 0.8, 0.9, [])
        case.add_score(score)
        assert case.has_results == True
        assert case.get_score("enf") is score
        assert case.get_score("noise") is None
    r = run_test("T07 — AudioCase creation and score management correct", t07)
    suite.add(r); print_result(r, verbose)

    # ── T08: BaseAudioModule interface ────────────────────────────────────────
    def t08():
        from core.models import BaseAudioModule, AudioCase, AudioProfile, AudioCodec, ENFGrid

        class DummyModule(BaseAudioModule):
            MODULE_NAME = "dummy"
            def run(self, case):
                raise ValueError("deliberate test error")

        profile = AudioProfile(
            file_path="test.wav", file_size_bytes=1000,
            sha256_hash="x"*64, sha256_partial="x"*16,
            container_format="wav", codec_name="pcm_s16le",
            codec_enum=AudioCodec.PCM_S16LE, sample_rate=16000,
            channels=1, duration_seconds=5.0, total_samples=80000,
            bit_depth=16, bitrate_bps=None, encoder_string=None,
            creation_time=None, has_id3_tags=False, has_xmp_data=False,
            is_mono=True, dc_offset=0.0, peak_amplitude=0.5,
            rms_level=0.1, is_clipped=False, clipping_count=0,
            silence_ratio=0.0, effective_max_freq=None,
            enf_grid=ENFGrid.GRID_50HZ,
        )
        case = AudioCase.create(profile)

        mod    = DummyModule()
        result = mod._safe_run(case)
        assert result.skipped    == True
        assert result.module     == "dummy"
        assert "deliberate" in result.skip_reason
    r = run_test("T08 — BaseAudioModule _safe_run catches exceptions", t08)
    suite.add(r); print_result(r, verbose)

    # ── T09: Test generator — files created ───────────────────────────────────
    def t09():
        from core.test_generator import TestAudioGenerator
        gen   = TestAudioGenerator(enf_grid_hz=50.0)
        paths = gen.generate_all("/tmp/af_test_phase0")
        expected = [
            "authentic_speech", "authentic_music",
            "spliced_enf", "spliced_noise",
            "double_compressed", "room_change", "speaker_change",
        ]
        for name in expected:
            assert name in paths, f"Missing: {name}"
            p = paths[name]
            assert p.exists(),           f"File not created: {p}"
            assert p.stat().st_size > 0, f"Empty file: {p}"
    r = run_test("T09 — TestAudioGenerator creates all 7 test files", t09)
    suite.add(r); print_result(r, verbose)

    # ── T10: Test audio — sample rate and duration ────────────────────────────
    def t10():
        import scipy.io.wavfile as wf
        from core.test_generator import TestAudioGenerator
        gen   = TestAudioGenerator()
        paths = gen.generate_all("/tmp/af_test_phase0")

        for name, path in paths.items():
            sr, audio = wf.read(str(path))
            assert sr == 16000, f"{name}: expected 16000Hz, got {sr}"
            assert audio.ndim  == 1, f"{name}: expected mono"
            assert audio.dtype.name == "int16", f"{name}: expected int16"
            expected_samples = 16000 * 10
            assert abs(len(audio) - expected_samples) < 100, \
                f"{name}: expected ~{expected_samples} samples, got {len(audio)}"
    r = run_test("T10 — Test audio: 16kHz, mono, int16, 10s duration", t10)
    suite.add(r); print_result(r, verbose)

    # ── T11: Authentic vs spliced — ENF difference detectable ─────────────────
    def t11():
        """
        Verify our synthetic splice is actually detectable at the signal level.
        The ENF phase jump should be measurable — this validates the test data.
        """
        import numpy as np
        import scipy.io.wavfile as wf
        import scipy.signal as sig

        paths = {}
        from core.test_generator import TestAudioGenerator
        paths = TestAudioGenerator().generate_all("/tmp/af_test_phase0")

        sr, authentic = wf.read(str(paths["authentic_speech"]))
        sr, spliced   = wf.read(str(paths["spliced_enf"]))
        audio_a = authentic.astype(np.float32) / 32767.0
        audio_s = spliced.astype(np.float32)   / 32767.0

        # Extract ENF via narrow bandpass
        sos = sig.butter(8, [49.0, 51.0], btype='bandpass', fs=sr, output='sos')
        enf_a = sig.sosfilt(sos, audio_a)
        enf_s = sig.sosfilt(sos, audio_s)

        # Instantaneous phase via Hilbert
        phase_a = np.unwrap(np.angle(sig.hilbert(enf_a)))
        phase_s = np.unwrap(np.angle(sig.hilbert(enf_s)))

        # Phase derivative (instantaneous frequency)
        freq_a = np.diff(phase_a) / (2*np.pi) * sr
        freq_s = np.diff(phase_s) / (2*np.pi) * sr

        # Compute local frequency variance in 1s windows
        win = sr
        mid = len(freq_a) // 2

        # Around the splice point, the spliced signal should have
        # higher frequency variance than authentic
        var_auth   = np.var(freq_a[mid-win//2 : mid+win//2])
        var_splice = np.var(freq_s[mid-win//2 : mid+win//2])

        assert var_splice > var_auth, (
            f"Splice not detectable: spliced variance {var_splice:.4f} "
            f"not greater than authentic {var_auth:.4f}"
        )
    r = run_test("T11 — ENF phase discontinuity detectable in spliced_enf", t11)
    suite.add(r); print_result(r, verbose)

    # ── T12: Dependency check ─────────────────────────────────────────────────
    def t12():
        from core.check_deps import check_dependencies
        ok, missing_req, missing_opt = check_dependencies()
        assert ok, f"Missing required dependencies: {missing_req}"
    r = run_test("T12 — All required dependencies present", t12)
    suite.add(r); print_result(r, verbose)

    print_suite_summary(suite)
    return suite


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AudioForensics Test Suite")
    parser.add_argument("--phase", type=int, choices=[0],
                        help="Run a specific phase only")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  AudioForensics — Test Suite{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}")

    all_suites = []

    run_all = args.phase is None

    if run_all or args.phase == 0:
        all_suites.append(test_phase0(args.verbose))

    # Overall summary
    total_p = sum(s.n_passed for s in all_suites)
    total_f = sum(s.n_failed for s in all_suites)
    total   = total_p + total_f
    color   = GREEN if total_f == 0 else RED

    print(f"{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  TOTAL: {color}{total_p}/{total} passed{RESET}    {total_f} failed{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}\n")

    sys.exit(0 if total_f == 0 else 1)


if __name__ == "__main__":
    main()
