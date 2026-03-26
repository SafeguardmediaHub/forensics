import os
import argparse
import subprocess
import numpy as np
import soundfile as sf
from scipy.signal import resample


# --------------------------------------------------------
# Utility: Convert to WAV if needed (for m4a, mp3, etc.)
# --------------------------------------------------------
def convert_to_wav(input_path):
    if input_path.lower().endswith(".wav"):
        return input_path

    wav_path = input_path + "_converted.wav"

    command = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        wav_path
    ]

    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return wav_path


# --------------------------------------------------------
# Load Audio (Safe Version)
# --------------------------------------------------------
def load_audio(path):
    path = convert_to_wav(path)
    audio, sr = sf.read(path)

    # Convert stereo to mono
    if len(audio.shape) > 1:
        audio = np.mean(audio, axis=1)

    return audio.astype(np.float32), sr


# --------------------------------------------------------
# 1️⃣ Content Deletion
# --------------------------------------------------------
def content_deletion(audio, deletion_ratio=0.2):
    length = len(audio)
    start = int(length * (0.5 - deletion_ratio / 2))
    end = int(length * (0.5 + deletion_ratio / 2))

    return np.concatenate([audio[:start], audio[end:]])


# --------------------------------------------------------
# 2️⃣ Speaker Substitution
# --------------------------------------------------------
def speaker_substitution(audio, sr, alt_audio, alt_sr, ratio=0.2):
    if alt_sr != sr:
        alt_audio = resample(alt_audio, int(len(alt_audio) * sr / alt_sr))

    length = len(audio)
    start = int(length * (0.5 - ratio / 2))
    end = int(length * (0.5 + ratio / 2))
    segment_length = end - start

    if len(alt_audio) < segment_length:
        repeats = int(np.ceil(segment_length / len(alt_audio)))
        alt_audio = np.tile(alt_audio, repeats)

    alt_segment = alt_audio[:segment_length]

    return np.concatenate([
        audio[:start],
        alt_segment,
        audio[end:]
    ])


# --------------------------------------------------------
# 3️⃣ Heavy Clipping
# --------------------------------------------------------
def heavy_clipping(audio, clip_level=0.85):
    gain = 1 + (clip_level * 10)
    boosted = audio * gain

    threshold = 1 - clip_level
    return np.clip(boosted, -threshold, threshold)


# --------------------------------------------------------
# MAIN
# --------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Safe Audio Attack Generator")

    parser.add_argument("--input", required=True, help="Original audio file")
    parser.add_argument("--alt", required=True, help="Alternate speaker file")
    parser.add_argument("--output", default="edited_audio", help="Output directory")
    parser.add_argument("--clip_level", type=float, default=0.85)
    parser.add_argument("--delete_ratio", type=float, default=0.2)

    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("Loading original audio...")
    audio, sr = load_audio(args.input)

    print("Loading alternate speaker...")
    alt_audio, alt_sr = load_audio(args.alt)

    print("Generating content deletion...")
    deleted = content_deletion(audio, args.delete_ratio)
    sf.write(os.path.join(args.output, "content_deleted.wav"), deleted, sr)

    print("Generating speaker substitution...")
    substituted = speaker_substitution(audio, sr, alt_audio, alt_sr, args.delete_ratio)
    sf.write(os.path.join(args.output, "speaker_substituted.wav"), substituted, sr)

    print(f"Generating clipping at {int(args.clip_level * 100)}% intensity...")
    clipped = heavy_clipping(audio, args.clip_level)
    sf.write(os.path.join(args.output, "clipped.wav"), clipped, sr)

    print("\nAll files saved to:", args.output)


if __name__ == "__main__":
    main()