"""
Provision Video Dubbing - Step 1: Transcribe Video
===================================================
Uses Google Gemini 3.1 Pro to transcribe a video with precise timestamps
and speaker identification.

Usage:
    python transcribe_video.py --video input_video.mp4 --output-dir .

Prerequisites:
    pip install google-genai
    export GEMINI_API_KEY="your-key"

Output:
    transcription.json  - Structured segments with timestamps
    transcription.csv   - Spreadsheet-friendly format
"""

import argparse
import csv
import json
import os
import sys
import time

from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# TODO: Set your Gemini API key via environment variable or config.json
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """Load configuration from config.json if available, fall back to env vars."""
    config_path = os.path.join(PROJECT_DIR, "config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    return config


def get_api_key(config):
    """Get Gemini API key from config or environment."""
    key = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("ERROR: GEMINI_API_KEY not set. Set it in config.json or as an environment variable.")
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# Upload & Poll
# ---------------------------------------------------------------------------

def upload_video(client, video_path):
    """Upload a video file to Gemini and wait until it is processed."""
    print(f"Uploading video: {video_path}")
    video_file = client.files.upload(file=video_path)
    print(f"Upload started. File name: {video_file.name}")

    # Poll until the file is in ACTIVE state
    max_wait = 600  # seconds
    poll_interval = 5
    elapsed = 0
    while video_file.state.name == "PROCESSING":
        if elapsed >= max_wait:
            print("ERROR: Video processing timed out after 10 minutes.")
            sys.exit(1)
        print(f"  Processing... ({elapsed}s elapsed)")
        time.sleep(poll_interval)
        elapsed += poll_interval
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name == "FAILED":
        print(f"ERROR: Video processing failed. State: {video_file.state.name}")
        sys.exit(1)

    print(f"Video ready. State: {video_file.state.name}")
    return video_file


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

TRANSCRIPTION_PROMPT = """Transcribe this video with precise timestamps. For each spoken segment, provide:
- start: start time in seconds (float, e.g. 12.5)
- end: end time in seconds (float, e.g. 15.3)
- speaker: speaker label (Speaker 1, Speaker 2, etc.)
- text: the exact spoken text

Return ONLY a valid JSON array of objects. Example:
[
  {"start": 0.0, "end": 3.2, "speaker": "Speaker 1", "text": "Welcome to our product demo."},
  {"start": 3.5, "end": 7.8, "speaker": "Speaker 1", "text": "Today we will show you the new features."}
]

Be precise with timestamps. Capture all spoken words. Identify different speakers if there are multiple.
Do NOT include any text outside the JSON array.
"""


def transcribe_video(client, video_file):
    """Send the uploaded video to Gemini and request a timestamped transcription."""
    print("Requesting transcription from Gemini 3.1 Pro...")

    response = client.models.generate_content(
        model="gemini-3.1-pro",
        contents=[video_file, TRANSCRIPTION_PROMPT],
    )

    raw_text = response.text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first and last lines (the ``` markers)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    try:
        segments = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse Gemini response as JSON: {e}")
        print("Raw response saved to transcription_raw.txt for debugging.")
        raw_path = os.path.join(PROJECT_DIR, "transcription_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response.text)
        sys.exit(1)

    print(f"Transcription complete. {len(segments)} segments found.")
    return segments


# ---------------------------------------------------------------------------
# Save outputs
# ---------------------------------------------------------------------------

def save_transcription_json(segments, output_dir):
    """Save transcription segments as JSON."""
    path = os.path.join(output_dir, "transcription.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)
    print(f"Saved: {path}")
    return path


def save_transcription_csv(segments, output_dir):
    """Save transcription segments as CSV."""
    path = os.path.join(output_dir, "transcription.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["start", "end", "speaker", "text"])
        writer.writeheader()
        for seg in segments:
            writer.writerow({
                "start": seg.get("start", ""),
                "end": seg.get("end", ""),
                "speaker": seg.get("speaker", ""),
                "text": seg.get("text", ""),
            })
    print(f"Saved: {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Transcribe video using Gemini 3.1 Pro")
    parser.add_argument("--video", required=True, help="Path to input video file")
    parser.add_argument("--output-dir", default=".", help="Directory to save outputs")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    config = load_config()
    api_key = get_api_key(config)

    # Initialize Gemini client
    client = genai.Client(api_key=api_key)

    # Upload video
    video_file = upload_video(client, video_path)

    # Transcribe
    segments = transcribe_video(client, video_file)

    # Save outputs
    save_transcription_json(segments, output_dir)
    save_transcription_csv(segments, output_dir)

    print("\nTranscription complete! Next step: translate_segments.py")


if __name__ == "__main__":
    main()
