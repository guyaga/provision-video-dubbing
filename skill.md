---
name: provision-video-dubbing
description: "Full video dubbing pipeline for Provision ISR - transcribe, translate, and dub videos into any language. Uses Gemini 3.1 Pro for transcription/translation and ElevenLabs for natural TTS. Handles timing analysis, gap detection, overlap fixing, and final video assembly. Use for: video dubbing, video translation, multilingual product videos. Triggers: provision video, dub video, translate video, provision dubbing, video localization"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Provision Video Dubbing Pipeline

Full video dubbing pipeline for Provision ISR product demo videos and training content. Transcribes, translates, and re-voices video into any target language while preserving timing and visual sync.

## Pipeline Overview

```
Original Video
    |
    v
[1] Transcribe (Gemini 3.1 Pro) --> transcription.json / transcription.csv
    |
    v
[2] Translate (Gemini 3.1 Pro) --> translated_segments.json
    |
    v
[3] Generate TTS (ElevenLabs) --> segment_001.mp3, segment_002.mp3, ... + segments_info.json
    |
    v
[4] Analyze Timing --> detected_gaps.json (gaps, overlaps, speed mismatches)
    |
    v
[5] Fix Timing Issues --> adjusted_segments_info.json
    |
    v
[6] Assemble Video (FFmpeg) --> dubbed_video.mp4
    |
    v
[7] (Optional) Shorten --> dubbed_video_short.mp4
```

## Prerequisites

### Python Packages
```bash
pip install google-genai elevenlabs requests
```

### System Dependencies
- **FFmpeg** must be installed and on PATH. Verify with `ffmpeg -version`.
- **FFprobe** (bundled with FFmpeg) is used for duration detection.

### API Keys
Set these environment variables before running any step:
```bash
export GEMINI_API_KEY="your-gemini-api-key"
export ELEVENLABS_API_KEY="your-elevenlabs-api-key"
```

Or place them in the project `config.json` (see below).

## Project Structure

```
project_dir/
  config.json                  # API keys, voice settings, language config
  input_video.mp4              # Original video to dub
  transcription.json           # Step 1 output
  transcription.csv            # Step 1 output (spreadsheet-friendly)
  translated_segments.json     # Step 2 output
  audio_segments/              # Step 3 output
    segment_001.mp3
    segment_002.mp3
    ...
  segments_info.json           # Step 3 output (durations, paths)
  detected_gaps.json           # Step 4 output
  adjusted_segments_info.json  # Step 5 output (after timing fixes)
  output/
    silent_video.mp4           # Video with audio stripped
    dubbed_audio.wav           # Combined dubbed audio track
    dubbed_video.mp4           # Final dubbed video
    dubbed_video_short.mp4     # Optional shortened version
```

## Step-by-Step Workflow

### Step 1: Transcribe Video

Use `templates/transcribe_video.py` as a starting point. Copy it into the project directory and configure.

```bash
python transcribe_video.py --video input_video.mp4 --output-dir .
```

This script:
- Uploads the video to Gemini's file API
- Polls until the upload is processed
- Sends a prompt requesting timestamped transcription with speaker labels
- Parses the response into structured segments: `{start, end, speaker, text}`
- Saves `transcription.json` and `transcription.csv`

Key Gemini prompt for transcription:
```
Transcribe this video with precise timestamps. For each segment provide:
- start time (seconds, float)
- end time (seconds, float)
- speaker label (Speaker 1, Speaker 2, etc.)
- spoken text

Return as JSON array of objects with keys: start, end, speaker, text
```

### Step 2: Translate Segments

Use `templates/translate_segments.py`.

```bash
python translate_segments.py --input transcription.json --language Spanish --output translated_segments.json
```

This script:
- Loads the transcription JSON
- Sends all segments to Gemini for translation
- Preserves timestamps and speaker labels
- Saves `translated_segments.json` with the same structure plus `translated_text` field

The translation prompt instructs Gemini to keep translations concise (matching approximate spoken duration of the original) and natural-sounding.

### Step 3: Generate TTS Audio

Use `templates/generate_audio.py`.

```bash
python generate_audio.py --input translated_segments.json --output-dir audio_segments --voice-id <voice_id>
```

This script:
- Reads translated segments
- Calls ElevenLabs Text-to-Speech API v1 for each segment
- Saves individual MP3 files named `segment_001.mp3`, `segment_002.mp3`, etc.
- Uses FFprobe to measure actual duration of each generated audio file
- Outputs `segments_info.json` with: segment index, original start/end, translated text, audio file path, actual audio duration

### Step 4: Analyze Timing

Use `templates/analyze_timing.py`.

```bash
python analyze_timing.py --segments-info segments_info.json --output detected_gaps.json
```

This script compares original timestamps with actual TTS durations and detects:
- **Gaps**: silence between where one segment ends and the next begins (> 0.3s)
- **Overlaps**: where a dubbed segment's audio is longer than the original window and would bleed into the next segment
- **Speed mismatches**: where TTS duration differs significantly from original duration (ratio > 1.3 or < 0.7)

Output `detected_gaps.json` contains an array of issues with suggested fixes.

### Step 5: Fix Timing Issues

Timing fixes are applied based on the analysis. Common strategies:

1. **Overlaps**: Shift the overlapping segment's start earlier (into a preceding gap) or trim silence from the TTS audio using FFmpeg.
2. **Gaps**: Leave as natural pauses, or shift segments to reduce dead air.
3. **Ending segments**: If the last segment extends beyond the original video duration, the video can be extended with the final frame, or the segment can be sped up slightly.
4. **Speed adjustment**: Use FFmpeg's `atempo` filter to speed up or slow down a segment (within 0.8x-1.5x range to keep it natural).

```bash
# Example: speed up a segment by 1.2x
ffmpeg -i segment_005.mp3 -filter:a "atempo=1.2" segment_005_adjusted.mp3
```

The adjusted segments are saved to `adjusted_segments_info.json`.

### Step 6: Assemble Final Video

Use `templates/assemble_video.py`.

```bash
python assemble_video.py --video input_video.mp4 --segments-info adjusted_segments_info.json --output output/dubbed_video.mp4
```

This script:
1. Strips audio from original video: `ffmpeg -i input.mp4 -an -c:v copy silent_video.mp4`
2. Gets total video duration via FFprobe
3. Creates a silent audio base track: `ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t <duration> -q:a 9 -acodec libmp3lame silence.mp3`
4. Builds an FFmpeg command that overlays each segment at its timestamp using `adelay` and `amix`:
   ```
   ffmpeg -i silence.mp3 -i seg1.mp3 -i seg2.mp3 ...
     -filter_complex "[1]adelay=<ms>|<ms>[a1];[2]adelay=<ms>|<ms>[a2];...
     [0][a1][a2]...amix=inputs=N:normalize=0"
     -ac 2 dubbed_audio.wav
   ```
5. Merges dubbed audio with silent video:
   ```
   ffmpeg -i silent_video.mp4 -i dubbed_audio.wav -c:v copy -c:a aac -b:a 192k dubbed_video.mp4
   ```

**Important**: Use `normalize=0` in `amix` to prevent volume ducking when segments are sparse.

### Step 7: (Optional) Create Shortened Version

To remove dead air and create a more compact video:

1. Identify all gaps longer than a threshold (e.g., 2 seconds)
2. Build a list of "keep" intervals (segments with padding)
3. Use FFmpeg's `select` and `concat` filters to cut and rejoin:
   ```bash
   # Create a concat file with segments
   ffmpeg -i dubbed_video.mp4 -vf "select='..." -af "aselect='..." -y dubbed_video_short.mp4
   ```

Alternatively, use the segment-based approach:
1. Cut each active section into a clip
2. Concatenate clips using FFmpeg concat demuxer

## Configuration

The `config.json` file holds all project settings:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "elevenlabs_api_key": "YOUR_ELEVENLABS_API_KEY",
  "input_video": "input_video.mp4",
  "target_language": "Spanish",
  "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
  "elevenlabs_model_id": "eleven_multilingual_v2",
  "elevenlabs_voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": true
  },
  "output_dir": "output",
  "audio_segments_dir": "audio_segments",
  "gap_threshold_seconds": 0.3,
  "overlap_trim_enabled": true,
  "speed_adjust_range": [0.8, 1.5],
  "shortened_gap_threshold": 2.0
}
```

## ElevenLabs Voice Recommendations by Language

| Language   | Recommended Voice         | Voice ID                     | Notes                          |
|------------|---------------------------|------------------------------|--------------------------------|
| Spanish    | Adam (multilingual)       | pNInz6obpgDQGcFmaJgB        | Clear male, good for demos     |
| French     | Antoni (multilingual)     | ErXwobaYiN019PkySvjV        | Professional tone              |
| German     | Arnold (multilingual)     | VR6AewLTigWG4xSOukaG        | Authoritative, training style  |
| Portuguese | Josh (multilingual)       | TxGEqnHWrfWFTfGW9XjX        | Warm, conversational           |
| Hebrew     | Rachel (multilingual)     | 21m00Tcm4TlvDq8ikWAM        | Clear female voice             |
| Italian    | Domi (multilingual)       | AZnzlk1XvdvUeBnXmlld        | Energetic, good for marketing  |
| Chinese    | Bella (multilingual)      | EXAVITQu4vr4xnSDxMaL        | Soft, professional             |
| Arabic     | Elli (multilingual)       | MF3mGyEYCl7XYWbV9V6O        | Clear enunciation              |
| Japanese   | Adam (multilingual)       | pNInz6obpgDQGcFmaJgB        | Use with eleven_multilingual_v2|

**Note**: Always use `eleven_multilingual_v2` model for non-English languages. Check the ElevenLabs voice library for the latest available voices. Voice IDs may change -- verify in the ElevenLabs dashboard.

## Handling Timing Issues

### Gaps Between Segments
Gaps occur when the original video has silence between spoken segments. Small gaps (< 0.5s) are natural and should be preserved. Large gaps (> 2s) can be shortened in the "short" version.

### Overlaps
Overlaps happen when the translated text is longer than the original, causing TTS audio to be longer than the original time window. Resolution strategies (in order of preference):
1. **Absorb into preceding gap**: If there is a gap before the segment, shift the start earlier
2. **Speed up slightly**: Use `atempo` filter (up to 1.3x) to fit within the window
3. **Trim trailing silence**: Many TTS outputs have trailing silence that can be removed
4. **Allow slight overlap**: For very small overlaps (< 0.2s), the perceptual impact is minimal

### Ending Segment Issues
If the last dubbed segment extends beyond the video duration:
1. Speed up the final segment
2. Extend the video by holding the last frame:
   ```bash
   ffmpeg -i video.mp4 -vf "tpad=stop_mode=clone:stop_duration=3" extended_video.mp4
   ```

## Shortened Video Creation

The shortened version removes extended silence to create a tighter cut:

1. Parse `segments_info.json` to identify all active audio regions
2. Add padding (e.g., 0.5s) before and after each segment
3. Merge overlapping padded regions
4. Extract each region as a clip
5. Concatenate clips with short crossfade transitions
6. This can reduce a 10-minute video with pauses to a 6-7 minute version

## Troubleshooting

### Gemini upload times out
- Large videos (> 500MB) may take several minutes to upload. The script polls with exponential backoff.
- If it keeps failing, consider compressing the video first: `ffmpeg -i input.mp4 -crf 28 -preset fast compressed.mp4`

### Transcription quality is poor
- Ensure the video has clear audio. Pre-process with noise reduction if needed:
  ```bash
  ffmpeg -i input.mp4 -af "highpass=f=200, lowpass=f=3000, afftdn=nf=-25" clean_audio.wav
  ```
- Try providing language hints in the Gemini prompt

### ElevenLabs returns 401
- Verify `ELEVENLABS_API_KEY` is set and valid
- Check your ElevenLabs plan has sufficient character quota

### Audio segments sound robotic
- Increase `stability` in voice settings (0.5 to 0.7)
- Increase `similarity_boost` (0.75 to 0.9)
- Use `eleven_multilingual_v2` model for best quality

### FFmpeg amix volume is too low
- Ensure `normalize=0` is set in the amix filter
- If combining many segments, volume may still drop. Use `volume` filter to boost:
  ```bash
  -filter_complex "...amix=inputs=N:normalize=0,volume=1.5"
  ```

### Final video has audio sync drift
- This usually means segment timestamps were slightly off. Re-run timing analysis.
- Ensure all audio files are the same sample rate (44100 Hz). Convert if needed:
  ```bash
  ffmpeg -i segment.mp3 -ar 44100 segment_44100.mp3
  ```

### Shortened video has jumpy cuts
- Increase padding around segments (e.g., 0.75s instead of 0.5s)
- Add crossfade transitions between cuts:
  ```bash
  ffmpeg -i clip1.mp4 -i clip2.mp4 -filter_complex "xfade=transition=fade:duration=0.5:offset=<time>" output.mp4
  ```
