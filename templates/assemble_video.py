"""
Provision Video Dubbing - Step 6: Assemble Dubbed Video
========================================================
Strips original audio, positions dubbed segments at correct timestamps,
and merges everything into the final video.

Usage:
    python assemble_video.py --video input_video.mp4 --segments-info segments_info.json --output output/dubbed_video.mp4

Optionally create a shortened version:
    python assemble_video.py --video input_video.mp4 --segments-info segments_info.json --output output/dubbed_video.mp4 --shorten

Prerequisites:
    FFmpeg/FFprobe on PATH

Output:
    output/dubbed_video.mp4        - Full dubbed video
    output/dubbed_video_short.mp4  - (Optional) shortened version with dead air removed
"""

import argparse
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """Load configuration from config.json if available."""
    config_path = os.path.join(PROJECT_DIR, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# FFmpeg Helpers
# ---------------------------------------------------------------------------

def get_video_duration(video_path):
    """Get video duration in seconds using FFprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def run_ffmpeg(cmd, description=""):
    """Run an FFmpeg command with error handling."""
    if description:
        print(f"  {description}")
    print(f"  Running: {' '.join(cmd[:6])}...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: FFmpeg failed:")
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Pipeline Steps
# ---------------------------------------------------------------------------

def strip_audio(video_path, output_path):
    """Remove audio track from video, keeping video stream intact."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-an", "-c:v", "copy",
        output_path,
    ]
    return run_ffmpeg(cmd, "Stripping audio from video")


def create_silent_base(duration, output_path):
    """Create a silent audio track of the specified duration."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-q:a", "9",
        "-acodec", "libmp3lame",
        output_path,
    ]
    return run_ffmpeg(cmd, f"Creating silent base track ({duration:.1f}s)")


def build_dubbed_audio(silent_base_path, segments_info, output_path):
    """
    Overlay all dubbed segments onto the silent base at their correct timestamps.
    Uses FFmpeg's adelay filter and amix with normalize=0 to preserve volume.
    """
    if not segments_info:
        print("  ERROR: No segments to assemble.")
        return False

    # Build FFmpeg command
    inputs = ["-i", silent_base_path]
    filter_parts = []
    mix_inputs = ["[0]"]  # Start with the silent base

    for i, seg in enumerate(segments_info):
        audio_path = seg.get("audio_path", "")
        if not audio_path or not os.path.exists(audio_path):
            # Try to find it relative to audio_segments dir
            audio_file = seg.get("audio_file", "")
            audio_path = os.path.join(PROJECT_DIR, "audio_segments", audio_file)

        if not os.path.exists(audio_path):
            print(f"  WARNING: Audio file not found: {audio_path}, skipping segment {seg['index']}")
            continue

        inputs.extend(["-i", audio_path])
        input_idx = i + 1  # +1 because [0] is the silent base

        # Calculate delay in milliseconds
        start_ms = int(seg["original_start"] * 1000)

        # adelay: delay both channels by start_ms
        filter_parts.append(f"[{input_idx}]adelay={start_ms}|{start_ms}[a{input_idx}]")
        mix_inputs.append(f"[a{input_idx}]")

    if len(mix_inputs) <= 1:
        print("  ERROR: No valid audio segments to mix.")
        return False

    # Combine all with amix, normalize=0 to prevent volume ducking
    num_inputs = len(mix_inputs)
    filter_complex = ";".join(filter_parts)
    filter_complex += f";{''.join(mix_inputs)}amix=inputs={num_inputs}:normalize=0"

    cmd = [
        "ffmpeg", "-y",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-ac", "2",
        "-ar", "44100",
        output_path,
    ]

    return run_ffmpeg(cmd, f"Mixing {num_inputs - 1} dubbed segments onto base track")


def merge_video_audio(video_path, audio_path, output_path):
    """Merge the silent video with the dubbed audio track."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    return run_ffmpeg(cmd, "Merging video and dubbed audio")


# ---------------------------------------------------------------------------
# Shortened Version
# ---------------------------------------------------------------------------

def create_shortened_version(video_path, segments_info, output_path, gap_threshold=2.0, padding=0.5):
    """
    Create a shortened version of the dubbed video by removing dead air.

    Strategy:
    1. Identify active regions (segment start - padding to segment end + padding)
    2. Merge overlapping regions
    3. Extract and concatenate
    """
    if not segments_info:
        print("  No segments for shortened version.")
        return False

    # Build active regions with padding
    regions = []
    for seg in segments_info:
        start = max(0, seg["original_start"] - padding)
        end = seg["original_start"] + max(seg["actual_duration"], seg["original_duration"]) + padding
        regions.append((start, end))

    # Sort and merge overlapping regions
    regions.sort(key=lambda r: r[0])
    merged = [regions[0]]
    for start, end in regions[1:]:
        if start <= merged[-1][1] + gap_threshold:
            # Merge: extend the current region
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    print(f"  Shortened version: {len(merged)} active regions from {len(segments_info)} segments")

    # Create concat file with segments
    concat_list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    temp_clips = []

    for i, (start, end) in enumerate(merged):
        clip_path = os.path.join(os.path.dirname(output_path), f"clip_{i:03d}.mp4")
        temp_clips.append(clip_path)

        duration = end - start
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            clip_path,
        ]
        success = run_ffmpeg(cmd, f"  Extracting clip {i + 1}/{len(merged)} ({start:.1f}s - {end:.1f}s)")
        if not success:
            print(f"  WARNING: Failed to extract clip {i + 1}")

    # Write concat list
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip_path in temp_clips:
            if os.path.exists(clip_path):
                # Use forward slashes for FFmpeg compatibility
                f.write(f"file '{clip_path.replace(os.sep, '/')}'\n")

    # Concatenate clips
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    success = run_ffmpeg(cmd, "Concatenating clips into shortened video")

    # Clean up temp clips
    for clip_path in temp_clips:
        if os.path.exists(clip_path):
            os.remove(clip_path)
    if os.path.exists(concat_list_path):
        os.remove(concat_list_path)

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Assemble dubbed video from audio segments")
    parser.add_argument("--video", required=True, help="Path to original video")
    parser.add_argument("--segments-info", required=True, help="Path to segments_info.json (or adjusted_segments_info.json)")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--shorten", action="store_true", help="Also create a shortened version")
    parser.add_argument("--shorten-gap", type=float, default=2.0, help="Gap threshold for shortening (seconds)")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    segments_info_path = os.path.abspath(args.segments_info)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)

    if not os.path.exists(segments_info_path):
        print(f"ERROR: Segments info file not found: {segments_info_path}")
        sys.exit(1)

    with open(segments_info_path, "r", encoding="utf-8") as f:
        segments_info = json.load(f)

    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    # Intermediate file paths
    silent_video_path = os.path.join(output_dir, "silent_video.mp4")
    silent_audio_path = os.path.join(output_dir, "silence.mp3")
    dubbed_audio_path = os.path.join(output_dir, "dubbed_audio.wav")

    print(f"Assembling dubbed video from {len(segments_info)} segments\n")

    # Step 1: Get video duration
    print("Step 1: Detecting video duration...")
    duration = get_video_duration(video_path)
    print(f"  Video duration: {duration:.1f}s\n")

    # Step 2: Strip audio
    print("Step 2: Stripping audio from original video...")
    if not strip_audio(video_path, silent_video_path):
        sys.exit(1)
    print()

    # Step 3: Create silent base track
    print("Step 3: Creating silent base track...")
    if not create_silent_base(duration, silent_audio_path):
        sys.exit(1)
    print()

    # Step 4: Build dubbed audio track
    print("Step 4: Building dubbed audio track...")
    if not build_dubbed_audio(silent_audio_path, segments_info, dubbed_audio_path):
        sys.exit(1)
    print()

    # Step 5: Merge video and audio
    print("Step 5: Merging video and dubbed audio...")
    if not merge_video_audio(silent_video_path, dubbed_audio_path, output_path):
        sys.exit(1)
    print()

    print(f"Dubbed video saved: {output_path}")

    # Step 6 (optional): Create shortened version
    if args.shorten:
        print("\nStep 6: Creating shortened version...")
        short_path = output_path.replace(".mp4", "_short.mp4")
        config = load_config()
        gap_threshold = args.shorten_gap or config.get("shortened_gap_threshold", 2.0)

        if create_shortened_version(output_path, segments_info, short_path, gap_threshold=gap_threshold):
            print(f"\nShortened video saved: {short_path}")
        else:
            print("\nWARNING: Failed to create shortened version.")

    # Clean up intermediate files
    for temp_file in [silent_audio_path]:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    print("\nDone!")


if __name__ == "__main__":
    main()
