"""
Provision Video Dubbing - Step 3: Generate TTS Audio
=====================================================
Uses ElevenLabs API v1 to generate speech audio for each translated segment.

Usage:
    python generate_audio.py --input translated_segments.json --output-dir audio_segments --voice-id pNInz6obpgDQGcFmaJgB

Prerequisites:
    pip install requests
    FFmpeg/FFprobe on PATH
    export ELEVENLABS_API_KEY="your-key"

Output:
    audio_segments/segment_001.mp3
    audio_segments/segment_002.mp3
    ...
    segments_info.json  - Metadata with actual durations
"""

import argparse
import json
import os
import subprocess
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# Default voice settings (can be overridden via config.json)
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}

DEFAULT_MODEL_ID = "eleven_multilingual_v2"


def load_config():
    """Load configuration from config.json if available."""
    config_path = os.path.join(PROJECT_DIR, "config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    return config


def get_api_key(config):
    """Get ElevenLabs API key from config or environment."""
    key = config.get("elevenlabs_api_key") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ERROR: ELEVENLABS_API_KEY not set. Set it in config.json or as an environment variable.")
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# Audio Duration Detection
# ---------------------------------------------------------------------------

def get_audio_duration(file_path):
    """Use FFprobe to get the duration of an audio file in seconds."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"WARNING: Could not detect duration for {file_path}: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# TTS Generation
# ---------------------------------------------------------------------------

def generate_segment_audio(api_key, voice_id, model_id, text, voice_settings, output_path):
    """Call ElevenLabs TTS API for a single segment and save the audio."""
    url = f"{ELEVENLABS_API_URL}/{voice_id}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }

    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": voice_settings,
    }

    response = requests.post(url, json=payload, headers=headers, timeout=60)

    if response.status_code == 200:
        with open(output_path, "wb") as f:
            f.write(response.content)
        return True
    elif response.status_code == 429:
        # Rate limited - wait and retry
        print("  Rate limited. Waiting 10 seconds...")
        time.sleep(10)
        return generate_segment_audio(api_key, voice_id, model_id, text, voice_settings, output_path)
    else:
        print(f"  ERROR: ElevenLabs API returned {response.status_code}: {response.text}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate TTS audio for translated segments")
    parser.add_argument("--input", required=True, help="Path to translated_segments.json")
    parser.add_argument("--output-dir", default="audio_segments", help="Directory for audio files")
    parser.add_argument("--voice-id", default=None, help="ElevenLabs voice ID")
    parser.add_argument("--model-id", default=None, help="ElevenLabs model ID")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.exists(input_path):
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    config = load_config()
    api_key = get_api_key(config)

    # Resolve voice settings from args > config > defaults
    voice_id = args.voice_id or config.get("elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB")
    model_id = args.model_id or config.get("elevenlabs_model_id", DEFAULT_MODEL_ID)
    voice_settings = config.get("elevenlabs_voice_settings", DEFAULT_VOICE_SETTINGS)

    print(f"Generating TTS audio for {len(segments)} segments")
    print(f"  Voice ID: {voice_id}")
    print(f"  Model: {model_id}")
    print(f"  Output dir: {output_dir}")
    print()

    segments_info = []

    for i, seg in enumerate(segments):
        idx = i + 1
        filename = f"segment_{idx:03d}.mp3"
        output_path = os.path.join(output_dir, filename)

        text = seg.get("translated_text", seg.get("text", ""))
        if not text.strip():
            print(f"  [{idx}/{len(segments)}] Skipping empty segment")
            continue

        print(f"  [{idx}/{len(segments)}] Generating: {text[:60]}...")

        success = generate_segment_audio(api_key, voice_id, model_id, text, voice_settings, output_path)

        if success:
            actual_duration = get_audio_duration(output_path)
            original_duration = seg.get("end", 0) - seg.get("start", 0)

            info = {
                "index": idx,
                "original_start": seg.get("start", 0),
                "original_end": seg.get("end", 0),
                "original_duration": round(original_duration, 3),
                "speaker": seg.get("speaker", ""),
                "original_text": seg.get("text", ""),
                "translated_text": text,
                "audio_file": filename,
                "audio_path": output_path,
                "actual_duration": round(actual_duration, 3),
                "duration_ratio": round(actual_duration / original_duration, 3) if original_duration > 0 else 0,
            }
            segments_info.append(info)
            print(f"           Duration: {actual_duration:.2f}s (original: {original_duration:.2f}s, ratio: {info['duration_ratio']:.2f})")
        else:
            print(f"           FAILED - skipping segment {idx}")

        # Small delay between API calls to avoid rate limiting
        time.sleep(0.5)

    # Save segments info
    info_path = os.path.join(PROJECT_DIR, "segments_info.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(segments_info, f, indent=2, ensure_ascii=False)

    print(f"\nGenerated {len(segments_info)} audio segments.")
    print(f"Saved segments info: {info_path}")
    print(f"\nNext step: analyze_timing.py")


if __name__ == "__main__":
    main()
