import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from video_tools import (VideoError, analyze_video, csv_bytes, empty_rows, export_bundle,
                         extract_audio, make_boundaries, probe_video, timecode)


DEMO = Path(__file__).resolve().parents[1] / "assets" / "demo.mp4"


def test_real_scene_detection_and_export():
    info, segments, limited = analyze_video(DEMO)
    assert info.duration == pytest.approx(10, abs=0.1)
    assert info.has_audio
    assert (info.width, info.height) == (640, 360)
    assert len(segments) == 3
    assert [s.start for s in segments] == pytest.approx([0, 3, 6], abs=0.3)
    assert sum(s.duration for s in segments) == pytest.approx(info.duration)
    assert not limited
    assert all(s.image.startswith(b"\xff\xd8") for s in segments)
    rows = empty_rows(segments)
    rows[0]["画面内容"] = "测试图与彩色画面"
    bundle = export_bundle("test.mp4", info, segments, rows, "测试台词", "AI 说明")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        report = archive.read("report.md").decode()
        data = json.loads(archive.read("analysis.json"))
        assert "测试图与彩色画面" in report
        assert "测试台词" in report
        assert data["segments"][0]["notes"]["画面内容"] == "测试图与彩色画面"
        assert data["video"]["has_audio"]
        assert archive.read("shots.csv").startswith(b"\xef\xbb\xbf")
        assert all(f"frames/{s.index:03d}.jpg" in archive.namelist() for s in segments)


def test_sampling_and_limit_are_contiguous():
    info, segments, limited = analyze_video(DEMO, mode="interval", interval=1, max_segments=3)
    assert limited
    assert len(segments) == 3
    assert segments[0].start == 0
    assert segments[-1].end == info.duration
    assert all(a.end == b.start for a, b in zip(segments, segments[1:]))


def test_audio_extraction(tmp_path):
    output = tmp_path / "speech.m4a"
    extract_audio(DEMO, output)
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                            capture_output=True, check=True)
    streams = json.loads(result.stdout)["streams"]
    assert len(streams) == 1
    assert streams[0]["codec_type"] == "audio"
    assert streams[0]["channels"] == 1


def test_no_audio_video(tmp_path):
    path = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(DEMO), "-an", "-c:v", "copy", str(path)],
                   capture_output=True, check=True)
    assert not probe_video(path).has_audio


def test_corrupt_file(tmp_path):
    path = tmp_path / "fake.mp4"
    path.write_text("This is not a video")
    with pytest.raises(VideoError, match="解码"):
        probe_video(path)


def test_short_video_and_duplicate_boundaries():
    assert make_boundaries(0.1, [], 5) == ([0.0, 0.1], False)
    points, _ = make_boundaries(10, [0, 3, 3, 3.1, 6, 10, float("nan")], 5)
    assert points == [0, 3, 6, 10]


def test_single_frame_video(tmp_path):
    path = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=320x240:r=24:d=0.04", "-c:v", "libx264", "-threads", "1", str(path)],
                   capture_output=True, check=True)
    info, segments, limited = analyze_video(path)
    assert info.duration < 0.1
    assert len(segments) == 1
    assert segments[0].image.startswith(b"\xff\xd8")


def test_timecode_rounding_carries_minutes():
    assert timecode(59.9999) == "01:00.000"
    assert timecode(0.005) == "00:00.005"


def test_csv_notes_do_not_execute_as_formulas():
    data = csv_bytes([{"片段": 1, "画面内容": " =SUM(1,1)"}]).decode("utf-8-sig")
    assert "' =SUM(1,1)" in data


@pytest.mark.parametrize("kwargs", [dict(mode="bad"), dict(mode="interval", interval=0),
                                    dict(threshold=2), dict(max_segments=0)])
def test_invalid_parameters(kwargs):
    with pytest.raises(VideoError):
        analyze_video(DEMO, **kwargs)
