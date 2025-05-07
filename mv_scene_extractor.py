#!/usr/bin/env python3
"""
mv_scene_extractor.py

2D 뮤직비디오 장면 이미지 추출용 중간 프레임 타임코드 계산 스크립트
"""
import argparse
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector, HistogramDetector
import os
import subprocess


def detect_scenes(video_path, algorithm='adaptive', threshold=3.0, min_scene_len=15, window_size=2, min_content_val=15.0):
    """Detect scene boundaries using specified detector algorithm."""
    video_manager = VideoManager([video_path])
    scene_manager = SceneManager()
    # Choose detector
    if algorithm == 'adaptive':
        detector = AdaptiveDetector(
            adaptive_threshold=threshold,
            min_scene_len=min_scene_len,
            window_width=window_size,
            min_content_val=min_content_val
        )
    elif algorithm == 'content':
        detector = ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
    elif algorithm == 'threshold':
        detector = ThresholdDetector(threshold=threshold, min_scene_len=min_scene_len)
    elif algorithm == 'hist':
        detector = HistogramDetector(threshold=threshold, min_scene_len=min_scene_len)
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    scene_manager.add_detector(detector)
    video_manager.start()
    scene_manager.detect_scenes(frame_source=video_manager)
    scene_list = scene_manager.get_scene_list()
    video_manager.release()
    return scene_list


def calculate_midframes(scenes):
    """Calculate mid-point timecodes for each scene."""
    midframes = []
    for start, end in scenes:
        mid_sec = (start.get_seconds() + end.get_seconds()) / 2.0
        hours = int(mid_sec // 3600)
        minutes = int((mid_sec % 3600) // 60)
        seconds = mid_sec % 60
        timecode = f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"
        midframes.append(timecode)
    return midframes


def extract_frames(video_path, midframes, output_dir, image_ext='jpg'):
    """Extract single frames at given timecodes using ffmpeg."""
    os.makedirs(output_dir, exist_ok=True)
    for idx, tc in enumerate(midframes, start=1):
        out_path = os.path.join(output_dir, f"{idx:04d}.{image_ext}")
        subprocess.run(
            ['ffmpeg', '-y', '-ss', tc, '-i', video_path, '-vframes', '1', out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


def main():
    parser = argparse.ArgumentParser(
        description="Extract mid-frame timecodes from video scenes."
    )
    parser.add_argument(
        "video_path", help="Path to the input video file."
    )
    parser.add_argument(
        "-t", "--threshold", type=float, default=3.0,
        help="Adaptive threshold (default: 3.0)."
    )
    parser.add_argument(
        "--min-scene-len", type=int, default=15,
        help="Minimum scene length in frames (default: 15)."
    )
    parser.add_argument(
        "--window-size", type=int, default=2,
        help="Adaptive detector window size (default: 2)."
    )
    parser.add_argument(
        "--min-content-val", type=float, default=15.0,
        help="Minimum content value (default: 15.0)."
    )
    parser.add_argument(
        "-a", "--algorithm", choices=['adaptive','content','threshold','hist'], default='adaptive',
        help="Detector algorithm to use (adaptive, content, threshold, hist)."
    )
    parser.add_argument(
        "-o", "--output", default="midframes",
        help="Output directory for extracted images."
    )
    args = parser.parse_args()

    scenes = detect_scenes(
        args.video_path,
        args.algorithm,
        args.threshold,
        args.min_scene_len,
        args.window_size,
        args.min_content_val
    )
    midframes = calculate_midframes(scenes)

    extract_frames(args.video_path, midframes, args.output)
    print(f"Detected {len(scenes)} scenes.")
    print(f"Extracted {len(midframes)} images to {args.output}/")


if __name__ == "__main__":
    main()
