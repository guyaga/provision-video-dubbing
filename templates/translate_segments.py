"""
Provision Video Dubbing - Step 2: Translate Segments
=====================================================
Uses Google Gemini 3.1 Pro to translate transcription segments
to the target language while preserving timestamps and structure.

Usage:
    python translate_segments.py --input transcription.json --language Spanish --output translated_segments.json

Prerequisites:
    pip install google-genai
    export GEMINI_API_KEY="your-key"

Output:
    translated_segments.json  - Segments with translated_text field added
"""

import argparse
import json
import os
import sys

from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """Load configuration from config.json if available."""
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
# Translation
# ---------------------------------------------------------------------------

def build_translation_prompt(segments, target_language):
    """Build the prompt for translating all segments at once."""
    segments_text = json.dumps(segments, indent=2, ensure_ascii=False)

    prompt = f"""Translate the following transcription segments into {target_language}.

IMPORTANT RULES:
1. Keep translations concise - they will be spoken aloud via TTS, so aim for similar
   spoken duration as the original text. Avoid unnecessarily wordy translations.
2. Use natural, conversational phrasing appropriate for {target_language}.
3. Preserve any technical terms or product names (e.g., "Provision ISR", model numbers).
4. Return ONLY a valid JSON array with the same structure, adding a "translated_text" field.
5. Keep all original fields (start, end, speaker, text) unchanged.

Input segments:
{segments_text}

Return the JSON array with the added "translated_text" field for each segment.
Do NOT include any text outside the JSON array.
"""
    return prompt


def translate_segments(client, segments, target_language):
    """Send segments to Gemini for translation."""
    print(f"Translating {len(segments)} segments to {target_language}...")

    prompt = build_translation_prompt(segments, target_language)

    response = client.models.generate_content(
        model="gemini-3.1-pro",
        contents=prompt,
    )

    raw_text = response.text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    try:
        translated = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse Gemini translation response as JSON: {e}")
        print("Raw response saved to translation_raw.txt for debugging.")
        raw_path = os.path.join(PROJECT_DIR, "translation_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response.text)
        sys.exit(1)

    # Validate that translated_text field exists in all segments
    missing = [i for i, seg in enumerate(translated) if "translated_text" not in seg]
    if missing:
        print(f"WARNING: {len(missing)} segments missing 'translated_text' field: {missing}")
        # Fall back: copy original text for missing translations
        for i in missing:
            translated[i]["translated_text"] = translated[i].get("text", "")

    print(f"Translation complete. {len(translated)} segments translated.")
    return translated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Translate transcription segments using Gemini 3.1 Pro")
    parser.add_argument("--input", required=True, help="Path to transcription.json")
    parser.add_argument("--language", required=True, help="Target language (e.g., Spanish, French, Hebrew)")
    parser.add_argument("--output", default="translated_segments.json", help="Output file path")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(input_path):
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    if not segments:
        print("ERROR: No segments found in input file.")
        sys.exit(1)

    config = load_config()
    api_key = get_api_key(config)

    client = genai.Client(api_key=api_key)

    translated = translate_segments(client, segments, args.language)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(translated, f, indent=2, ensure_ascii=False)

    print(f"Saved: {output_path}")
    print(f"\nTranslation complete! Next step: generate_audio.py")


if __name__ == "__main__":
    main()
