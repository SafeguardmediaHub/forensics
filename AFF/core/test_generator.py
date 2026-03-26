"""
AudioForensics — Synthetic Test Audio Generator (Phase 0)

Creates known-ground-truth audio files for testing.
No external dependencies beyond numpy and scipy.
All files written as 16-bit WAV using scipy.io.wavfile.

Generated files:
    authentic_speech.wav    — Clean recording, consistent noise, stable ENF
    authentic_music.wav     — Music-like content, no speech features
    spliced_enf.wav         — Two sessions spliced; ENF phase jump at midpoint
    spliced_noise.wav       — Two rooms spliced; noise floor changes at cut
    double_compressed.wav   — Simulated double MP3 compression artifacts
    room_change.wav         — Two different reverb environments joined
    speaker_change.wav      — Two different fundamental frequencies joined

Usage:
    from core.test_generator import TestAudioGenerator
    gen = TestAudioGenerator()
    paths = gen.generate_all("/tmp/af_test")
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal


class TestAudioGenerator:
    """
    Generates synthetic audio files with known forensic ground truth.

    All audio at 16kHz, 16-bit, mono — the forensic analysis standard.
    Duration: 10 seconds per file (long enough for all modules to work).
    """

    SAMPLE_RATE  = 16000
    DURATION_S   = 10
    N_SAMPLES    = SAMPLE_RATE * DURATION_S
    DTYPE        = np.int16
    MAX_VAL      = 32767

    # ENF constants (50Hz grid — Nigeria/Europe default)
    ENF_NOMINAL  = 50.0
    ENF_VARIANCE = 0.02    # ±0.02Hz natural drift

    def __init__(self, enf_grid_hz: float = 50.0):
        self.enf_grid_hz = enf_grid_hz
        self.rng = np.random.default_rng(seed=42)  # reproducible

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_all(self, output_dir: str) -> Dict[str, Path]:
        """Generate all test audio files. Returns {name: path} dict."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        generators = {
            "authentic_speech":    self.authentic_speech,
            "authentic_music":     self.authentic_music,
            "spliced_enf":         self.spliced_enf,
            "spliced_noise":       self.spliced_noise,
            "double_compressed":   self.double_compressed,
            "room_change":         self.room_change,
            "speaker_change":      self.speaker_change,
        }

        paths = {}
        for name, fn in generators.items():
            path = out / f"af_test_{name}.wav"
            audio = fn()
            self._write_wav(path, audio)
            paths[name] = path

        return paths

    # ── Authentic audio ───────────────────────────────────────────────────────

    def authentic_speech(self) -> np.ndarray:
        """
        Genuine single-session recording.
        - Consistent Gaussian noise floor throughout
        - Stable ENF at 50Hz with natural ±0.02Hz drift
        - Synthetic voiced speech (harmonic series)
        - No discontinuities
        """
        t = self._time()

        # Noise floor — consistent throughout
        noise = self._room_noise(level=0.015, seed=1)

        # ENF — natural drift, continuous phase
        enf = self._continuous_enf(t, seed=10)

        # Synthetic speech — harmonic series with F0 around 150Hz
        speech = self._synthetic_speech(t, f0=150.0, n_harmonics=8, amplitude=0.25)

        audio = noise + enf + speech
        return self._normalise(audio, target_peak=0.7)

    def authentic_music(self) -> np.ndarray:
        """
        Music-like content. No voiced speech features.
        Consistent noise, stable ENF, multiple tones.
        """
        t = self._time()
        noise = self._room_noise(level=0.010, seed=2)
        enf   = self._continuous_enf(t, seed=20)

        # Music: chord of tones (A major: 440, 554, 659 Hz)
        music = (
            0.20 * np.sin(2*np.pi*440.0*t) +
            0.15 * np.sin(2*np.pi*554.4*t) +
            0.15 * np.sin(2*np.pi*659.3*t) +
            0.08 * np.sin(2*np.pi*880.0*t)   # octave
        )
        audio = noise + enf + music
        return self._normalise(audio, target_peak=0.75)

    # ── Tampered audio ────────────────────────────────────────────────────────

    def spliced_enf(self) -> np.ndarray:
        """
        Two sessions spliced at the midpoint (5 seconds).
        GROUND TRUTH: ENF phase discontinuity at t=5.0s.

        Session A: ENF starting at phase 0, drifting naturally
        Session B: ENF starting at a different phase (π/2 offset)
        Both sessions otherwise identical — only ENF reveals the splice.
        """
        t = self._time()
        mid = self.N_SAMPLES // 2

        noise  = self._room_noise(level=0.015, seed=3)
        speech = self._synthetic_speech(t, f0=150.0, n_harmonics=8, amplitude=0.25)

        # Session A ENF — continuous from start
        enf_a = self._continuous_enf(t, seed=30)

        # Session B ENF — same frequency, but phase is shifted by π/2
        # This is what a different recording session looks like
        enf_b = self._continuous_enf(t, seed=31, phase_offset=np.pi/2)

        # Splice: first half from session A, second half from session B
        enf_spliced = np.concatenate([enf_a[:mid], enf_b[mid:]])

        audio = noise + enf_spliced + speech
        return self._normalise(audio, target_peak=0.7)

    def spliced_noise(self) -> np.ndarray:
        """
        Two rooms spliced at t=5.0s.
        GROUND TRUTH: noise floor discontinuity at t=5.0s.

        Room A: office noise (low-frequency hum, ~200Hz HVAC)
        Room B: outdoor ambient (broadband, higher level)
        Both have same ENF — noise floor is the tell.
        """
        t   = self._time()
        mid = self.N_SAMPLES // 2

        enf    = self._continuous_enf(t, seed=40)
        speech = self._synthetic_speech(t, f0=160.0, n_harmonics=7, amplitude=0.20)

        # Room A: office — low-frequency dominated noise
        noise_a = self._room_noise(level=0.012, seed=4, color="pink")

        # Room B: outdoor — whiter, higher level
        noise_b = self._room_noise(level=0.025, seed=5, color="white")

        noise_spliced = np.concatenate([noise_a[:mid], noise_b[mid:]])

        audio = noise_spliced + enf + speech
        return self._normalise(audio, target_peak=0.7)

    def double_compressed(self) -> np.ndarray:
        """
        Simulates a recording that was saved as MP3, then re-saved.
        GROUND TRUTH: spectral artifacts from double lossy compression.

        Simulated by applying two rounds of aggressive low-pass filtering
        (mimicking codec bandwidth reduction) with slightly different cutoffs,
        then adding quantisation noise characteristic of codec artifacts.
        """
        t = self._time()

        noise  = self._room_noise(level=0.015, seed=6)
        enf    = self._continuous_enf(t, seed=50)
        speech = self._synthetic_speech(t, f0=145.0, n_harmonics=10, amplitude=0.30)

        audio = noise + enf + speech

        # First compression pass: LP filter at 7.5kHz (128kbps MP3 equivalent)
        sos1  = signal.butter(8, 7500.0, btype='low', fs=self.SAMPLE_RATE, output='sos')
        pass1 = signal.sosfilt(sos1, audio)

        # Add quantisation noise (codec artifact simulation)
        q_noise = self.rng.normal(0, 0.003, self.N_SAMPLES)
        pass1  += q_noise

        # Second compression pass: LP filter at 7.2kHz (slightly different encoder)
        sos2  = signal.butter(8, 7200.0, btype='low', fs=self.SAMPLE_RATE, output='sos')
        pass2 = signal.sosfilt(sos2, pass1)

        # Additional intermodulation distortion from double encoding
        pass2 += 0.005 * np.sin(2*np.pi*7300*t) * np.abs(audio)

        return self._normalise(pass2, target_peak=0.65)

    def room_change(self) -> np.ndarray:
        """
        Recording moves to a different room at t=5.0s.
        GROUND TRUTH: RT60 (reverberation time) changes at midpoint.

        Simulated by convolving with different exponential decay envelopes.
        """
        t   = self._time()
        mid = self.N_SAMPLES // 2

        enf    = self._continuous_enf(t, seed=60)
        speech = self._synthetic_speech(t, f0=155.0, n_harmonics=8, amplitude=0.25)
        noise  = self._room_noise(level=0.012, seed=7)

        dry = noise + enf + speech

        # Room A: small room — short RT60 (~0.3s)
        ir_a = self._room_impulse_response(rt60=0.3, seed=70)
        wet_a = signal.fftconvolve(dry[:mid], ir_a)[:mid]

        # Room B: large hall — long RT60 (~1.2s)
        ir_b = self._room_impulse_response(rt60=1.2, seed=71)
        wet_b = signal.fftconvolve(dry[mid:], ir_b)[:mid]

        # Smooth join to avoid hard click
        fade = 160  # 10ms at 16kHz
        wet_a[-fade:] *= np.linspace(1, 0, fade)
        wet_b[:fade]  *= np.linspace(0, 1, fade)

        audio = np.concatenate([wet_a, wet_b])
        return self._normalise(audio, target_peak=0.7)

    def speaker_change(self) -> np.ndarray:
        """
        Different speaker inserted at t=5.0s.
        GROUND TRUTH: fundamental frequency (F0) changes at midpoint.

        Speaker A: F0 ≈ 120Hz (lower male voice)
        Speaker B: F0 ≈ 220Hz (higher voice / different person)
        """
        t   = self._time()
        mid = self.N_SAMPLES // 2

        enf   = self._continuous_enf(t, seed=80)
        noise = self._room_noise(level=0.012, seed=8)

        # Speaker A — lower voice
        speech_a = self._synthetic_speech(t, f0=120.0, n_harmonics=10, amplitude=0.25)

        # Speaker B — higher pitch, different harmonic structure
        speech_b = self._synthetic_speech(t, f0=220.0, n_harmonics=7,  amplitude=0.22)

        speech_spliced = np.concatenate([speech_a[:mid], speech_b[mid:]])

        audio = noise + enf + speech_spliced
        return self._normalise(audio, target_peak=0.7)

    # ── DSP building blocks ───────────────────────────────────────────────────

    def _time(self) -> np.ndarray:
        """Time vector for one full recording."""
        return np.linspace(0, self.DURATION_S,
                           self.N_SAMPLES, endpoint=False)

    def _continuous_enf(
        self,
        t: np.ndarray,
        seed: int = 0,
        phase_offset: float = 0.0,
    ) -> np.ndarray:
        """
        Simulate ENF (Electric Network Frequency) signal.

        Models natural grid frequency fluctuation as a random walk
        around the nominal frequency. This is how real mains hum behaves.
        """
        rng = np.random.default_rng(seed=seed)
        n   = len(t)

        # Frequency random walk (Ornstein-Uhlenbeck process)
        freq_track = np.zeros(n)
        freq_track[0] = self.enf_grid_hz
        for i in range(1, n):
            drift = -0.1 * (freq_track[i-1] - self.enf_grid_hz)
            noise = rng.normal(0, self.ENF_VARIANCE / np.sqrt(self.SAMPLE_RATE))
            freq_track[i] = freq_track[i-1] + drift + noise

        # Instantaneous phase (integrate frequency)
        dt    = t[1] - t[0]
        phase = 2 * np.pi * np.cumsum(freq_track) * dt + phase_offset

        enf_signal = 0.008 * np.sin(phase)   # Very low amplitude — realistic

        return enf_signal

    def _room_noise(
        self,
        level: float = 0.015,
        seed: int = 0,
        color: str = "pink",
    ) -> np.ndarray:
        """
        Generate coloured noise simulating room ambient noise.
        pink: power ∝ 1/f — typical indoor environment
        white: flat spectrum — outdoor/broadband
        """
        rng   = np.random.default_rng(seed=seed)
        white = rng.normal(0, 1, self.N_SAMPLES)

        if color == "pink":
            # Pink noise via spectral shaping
            f = np.fft.rfftfreq(self.N_SAMPLES, d=1.0/self.SAMPLE_RATE)
            f[0] = 1.0  # avoid divide-by-zero
            spectrum = np.fft.rfft(white)
            pink_filter = 1.0 / np.sqrt(f)
            pink = np.fft.irfft(spectrum * pink_filter, n=self.N_SAMPLES)
            pink = pink / np.std(pink) * level
            return pink.astype(np.float64)
        else:
            return (white / np.std(white) * level).astype(np.float64)

    def _synthetic_speech(
        self,
        t: np.ndarray,
        f0: float = 150.0,
        n_harmonics: int = 8,
        amplitude: float = 0.25,
    ) -> np.ndarray:
        """
        Harmonic series simulating voiced speech.
        Real speech is a sum of harmonics of F0 with decreasing amplitude.
        Includes slow amplitude modulation (prosody simulation).
        """
        rng     = np.random.default_rng(seed=int(f0))
        signal_ = np.zeros(len(t))

        for k in range(1, n_harmonics + 1):
            # Harmonic amplitude decreases with order (vocal tract filter)
            harmonic_amp = amplitude / (k ** 0.8)
            # Slight random phase per harmonic
            phase = rng.uniform(0, 2 * np.pi)
            signal_ += harmonic_amp * np.sin(2 * np.pi * k * f0 * t + phase)

        # Prosody: slow amplitude envelope (syllable rate ~4Hz)
        envelope = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 4.0 * t))
        signal_ *= envelope

        return signal_

    def _room_impulse_response(
        self,
        rt60: float = 0.5,
        seed: int = 0,
    ) -> np.ndarray:
        """
        Simplified room impulse response (exponential decay + noise).
        rt60: reverberation time in seconds (time to decay by 60dB).
        """
        rng    = np.random.default_rng(seed=seed)
        n_ir   = int(rt60 * self.SAMPLE_RATE * 1.5)
        t_ir   = np.arange(n_ir) / self.SAMPLE_RATE
        decay  = np.exp(-6.908 * t_ir / rt60)  # 6.908 = ln(10^6/20)
        noise  = rng.normal(0, 1, n_ir)
        ir     = decay * noise
        # Normalise so convolution doesn't change overall level
        ir    /= (np.sum(np.abs(ir)) + 1e-9)
        return ir.astype(np.float64)

    def _normalise(self, audio: np.ndarray, target_peak: float = 0.8) -> np.ndarray:
        """Normalise to target peak and convert to int16."""
        peak = np.max(np.abs(audio))
        if peak > 1e-9:
            audio = audio / peak * target_peak
        return (audio * self.MAX_VAL).astype(np.int16)

    def _write_wav(self, path: Path, audio: np.ndarray) -> None:
        """Write int16 numpy array as WAV file."""
        wavfile.write(str(path), self.SAMPLE_RATE, audio)


# ── Convenience function ──────────────────────────────────────────────────────

def generate_test_audio(output_dir: str = "/tmp/af_test") -> Dict[str, Path]:
    """
    Generate all test audio files.
    Returns {name: path} dict.

    Quick usage:
        paths = generate_test_audio()
        # paths["authentic_speech"] → /tmp/af_test/af_test_authentic_speech.wav
        # paths["spliced_enf"]      → /tmp/af_test/af_test_spliced_enf.wav
    """
    return TestAudioGenerator().generate_all(output_dir)
