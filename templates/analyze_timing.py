"""
Provision Video Dubbing - Step 4: Analyze Timing
==================================================
Compares original timestamps with generated audio durations to detect
gaps, overlaps, and speed mismatches. Suggests fixes for each issue.

Usage:
    python analyze_timing.py --segments-info segments_info.json --output detected_gaps.json

Output:
    detected_gaps.json  - Array of timing issues with suggested fixes
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Thresholds
GAP_THRESHOLD = 0.3       # Minimum gap (seconds) to report
OVERLAP_THRESHOLD = 0.05   # Minimum overlap (seconds) to report
SPEED_RATIO_HIGH = 1.3     # TTS duration / original duration above this = too slow
SPEED_RATIO_LOW = 0.7      # TTS duration / original duration below this = too fast


def load_config():
    """Load thresholds from config.json if available."""
    config_path = os.path.join(PROJECT_DIR, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    return {}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_timing(segments_info, gap_threshold=GAP_THRESHOLD):
    """
    Analyze timing of dubbed segments vs original timestamps.

    Returns a list of detected issues, each with:
    - type: "gap", "overlap", or "speed_mismatch"
    - segment_index: which segment(s) are involved
    - details: numeric details of the issue
    - suggestion: recommended fix
    """
    issues = []

    for i, seg in enumerate(segments_info):
        original_start = seg["original_start"]
        original_end = seg["original_end"]
        original_duration = seg["original_duration"]
        actual_duration = seg["actual_duration"]
        dubbed_end = original_start + actual_duration

        # --- Speed mismatch detection ---
        ratio = seg.get("duration_ratio", 0)
        if ratio > SPEED_RATIO_HIGH:
            issues.append({
                "type": "speed_mismatch",
                "subtype": "too_slow",
                "segment_index": seg["index"],
                "original_duration": original_duration,
                "actual_duration": actual_duration,
                "ratio": ratio,
                "overshoot_seconds": round(actual_duration - original_duration, 3),
                "suggestion": f"Speed up segment by {ratio:.2f}x using atempo filter, "
                              f"or trim {actual_duration - original_duration:.2f}s of trailing silence.",
            })
        elif ratio < SPEED_RATIO_LOW and ratio > 0:
            issues.append({
                "type": "speed_mismatch",
                "subtype": "too_fast",
                "segment_index": seg["index"],
                "original_duration": original_duration,
                "actual_duration": actual_duration,
                "ratio": ratio,
                "suggestion": f"Segment is much shorter than original. Consider slowing down "
                              f"to {1/ratio:.2f}x or adding padding.",
            })

        # --- Gap and overlap detection between consecutive segments ---
        if i < len(segments_info) - 1:
            next_seg = segments_info[i + 1]
            next_start = next_seg["original_start"]

            # Gap between current dubbed end and next segment start
            gap = next_start - dubbed_end
            original_gap = next_start - original_end

            if gap < -OVERLAP_THRESHOLD:
                # Overlap: dubbed audio bleeds into next segment
                overlap_seconds = abs(gap)
                issues.append({
                    "type": "overlap",
                    "segment_index": seg["index"],
                    "next_segment_index": next_seg["index"],
                    "overlap_seconds": round(overlap_seconds, 3),
                    "dubbed_end": round(dubbed_end, 3),
                    "next_start": round(next_start, 3),
                    "original_gap": round(original_gap, 3),
                    "suggestion": f"Segment {seg['index']} audio overlaps into segment {next_seg['index']} "
                                  f"by {overlap_seconds:.2f}s. "
                                  f"Options: (1) speed up segment {seg['index']} by atempo, "
                                  f"(2) trim trailing silence, "
                                  f"(3) shift start earlier if preceding gap allows.",
                })

            elif original_gap > gap_threshold:
                # Notable gap in original timeline
                issues.append({
                    "type": "gap",
                    "after_segment_index": seg["index"],
                    "before_segment_index": next_seg["index"],
                    "gap_seconds": round(original_gap, 3),
                    "effective_gap_with_dubbing": round(gap, 3),
                    "suggestion": f"Gap of {original_gap:.2f}s between segments {seg['index']} and "
                                  f"{next_seg['index']}. "
                                  f"After dubbing, effective gap is {gap:.2f}s. "
                                  f"Can be shortened in the compact version.",
                })

    # --- Check if last segment extends beyond reasonable video end ---
    if segments_info:
        last = segments_info[-1]
        last_dubbed_end = last["original_start"] + last["actual_duration"]
        if last_dubbed_end > last["original_end"] + 1.0:
            overshoot = last_dubbed_end - last["original_end"]
            issues.append({
                "type": "ending_overshoot",
                "segment_index": last["index"],
                "original_end": last["original_end"],
                "dubbed_end": round(last_dubbed_end, 3),
                "overshoot_seconds": round(overshoot, 3),
                "suggestion": f"Last segment extends {overshoot:.2f}s beyond original end. "
                              f"Options: (1) extend video with last frame using tpad, "
                              f"(2) speed up last segment, "
                              f"(3) accept if video has trailing content.",
            })

    return issues


def print_summary(issues):
    """Print a human-readable summary of detected issues."""
    if not issues:
        print("\nNo timing issues detected! All segments fit within their original windows.")
        return

    print(f"\nDetected {len(issues)} timing issue(s):\n")

    type_counts = {}
    for issue in issues:
        t = issue["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count}")

    print()
    for issue in issues:
        if issue["type"] == "overlap":
            print(f"  OVERLAP: Segment {issue['segment_index']} -> {issue['next_segment_index']}: "
                  f"{issue['overlap_seconds']:.2f}s overlap")
        elif issue["type"] == "gap":
            print(f"  GAP: After segment {issue['after_segment_index']}: "
                  f"{issue['gap_seconds']:.2f}s gap (effective: {issue['effective_gap_with_dubbing']:.2f}s)")
        elif issue["type"] == "speed_mismatch":
            print(f"  SPEED: Segment {issue['segment_index']}: "
                  f"ratio={issue['ratio']:.2f} ({issue['subtype']})")
        elif issue["type"] == "ending_overshoot":
            print(f"  ENDING: Last segment overshoots by {issue['overshoot_seconds']:.2f}s")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze timing of dubbed segments")
    parser.add_argument("--segments-info", required=True, help="Path to segments_info.json")
    parser.add_argument("--output", default="detected_gaps.json", help="Output file path")
    args = parser.parse_args()

    input_path = os.path.abspath(args.segments_info)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(input_path):
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        segments_info = json.load(f)

    if not segments_info:
        print("ERROR: No segments found in input file.")
        sys.exit(1)

    config = load_config()
    gap_threshold = config.get("gap_threshold_seconds", GAP_THRESHOLD)

    print(f"Analyzing timing for {len(segments_info)} segments...")
    issues = analyze_timing(segments_info, gap_threshold=gap_threshold)

    print_summary(issues)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(issues, f, indent=2, ensure_ascii=False)

    print(f"\nSaved: {output_path}")
    print(f"\nNext step: Fix timing issues (if any), then assemble_video.py")


if __name__ == "__main__":
    main()
