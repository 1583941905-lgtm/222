"""Local media analysis and portable exports. No network calls in this module."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

MAX_DURATION = 20 * 60
MAX_BYTES = 200 * 1024 * 1024
MAX_SEGMENTS = 60
NOTE_FIELDS = ("画面内容", "景别与运镜", "台词与字幕", "剪辑与声音", "复刻笔记")


class VideoError(Exception):
    """A media failure that is safe to display to an end user."""


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    codec: str


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    frame_time: float
    image: bytes

    @property
    def duration(self) -> float:
        return self.end - self.start


def check_dependencies() -> None:
    if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
        raise VideoError("缺少 FFmpeg。请安装 ffmpeg（包含 ffprobe）后重新启动应用，详见 README。")


def run_media(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise VideoError("找不到 FFmpeg / ffprobe，请先安装 FFmpeg。") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoError("视频处理超时。请使用较短的视频，或降低视频分辨率后重试。") from exc
    if completed.returncode:
        raise VideoError("无法解码该媒体文件。请确认文件完整，或转换为 H.264 MP4 后重试。")
    return completed


def probe_video(path: Path) -> VideoInfo:
    check_dependencies()
    if path.stat().st_size > MAX_BYTES:
        raise VideoError("视频不能超过 200 MB。")
    data = run_media([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ], timeout=30)
    try:
        metadata = json.loads(data.stdout)
        streams = metadata.get("streams", [])
        # Attached cover images are not playable video streams.
        video = next(s for s in streams if s.get("codec_type") == "video"
                     and not s.get("disposition", {}).get("attached_pic"))
        duration = float(metadata.get("format", {}).get("duration") or video.get("duration") or 0)
        width, height = int(video["width"]), int(video["height"])
        try:
            fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    except (StopIteration, KeyError, TypeError, ValueError) as exc:
        raise VideoError("文件中没有可读取的视频轨道。") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VideoError("无法读取视频时长，请重新导出为 MP4 后上传。")
    if duration > MAX_DURATION:
        raise VideoError("目前支持 20 分钟以内的视频，请先截取需要拆解的片段。")
    if min(width, height) <= 0 or max(width, height) > 7680:
        raise VideoError("视频分辨率不受支持，请缩小到 8K 以内。")
    rotation = video.get("tags", {}).get("rotate", 0)
    for item in video.get("side_data_list", []):
        if "rotation" in item:
            rotation = item["rotation"]
    if abs(float(rotation)) % 180 == 90:
        width, height = height, width
    return VideoInfo(duration, width, height, fps,
                     any(s.get("codec_type") == "audio" for s in streams),
                     video.get("codec_name", "unknown"))


def make_boundaries(duration: float, cuts: list[float], max_segments: int) -> tuple[list[float], bool]:
    """Keep a full, ordered timeline; thin boundaries if the configured limit is hit."""
    if not 1 <= max_segments <= MAX_SEGMENTS:
        raise VideoError(f"片段数量必须在 1–{MAX_SEGMENTS} 之间。")
    boundaries = [0.0]
    for cut in sorted(set(cuts)):
        if math.isfinite(cut) and cut - boundaries[-1] >= 0.5 and duration - cut >= 0.5:
            boundaries.append(float(cut))
    boundaries.append(duration)
    limited = len(boundaries) - 1 > max_segments
    if limited:
        last = len(boundaries) - 1
        boundaries = [boundaries[round(i * last / max_segments)] for i in range(max_segments + 1)]
    return boundaries, limited


def detect_boundaries(path: Path, info: VideoInfo, mode: str, threshold: float,
                      interval: float, max_segments: int) -> tuple[list[float], bool]:
    if mode == "interval":
        if not math.isfinite(interval) or interval < 1:
            raise VideoError("采样间隔至少为 1 秒。")
        cuts = [i * interval for i in range(1, math.ceil(info.duration / interval))]
    elif mode == "scene":
        if not 0.05 <= threshold <= 0.8:
            raise VideoError("镜头阈值必须在 0.05–0.8 之间。")
        if info.duration < 0.5:
            return make_boundaries(info.duration, [], max_segments)
        # At 4 samples/s boundaries have approximately 0.25 s precision.
        vf = f"fps=4,scale=320:320:force_original_aspect_ratio=decrease:force_divisible_by=2,select='gt(scene,{threshold})',showinfo"
        result = run_media([
            "ffmpeg", "-hide_banner", "-nostdin", "-threads", "2", "-i", str(path),
            "-map", "0:V:0", "-vf", vf, "-an", "-f", "null", "-"
        ], timeout=300)
        log = result.stderr.decode("utf-8", errors="replace")
        cuts = [float(t) for t in re.findall(r"pts_time:\s*([0-9.eE+\-]+)", log)]
    else:
        raise VideoError("未知的拆解模式。")
    return make_boundaries(info.duration, cuts, max_segments)


def extract_frame(path: Path, timestamp: float) -> bytes:
    result = run_media([
        "ffmpeg", "-v", "error", "-nostdin", "-threads", "2", "-ss", f"{timestamp:.4f}",
        "-i", str(path), "-map", "0:V:0", "-frames:v", "1", "-vf",
        "scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-f", "image2pipe", "-c:v", "mjpeg", "-threads", "1", "-q:v", "4", "-"
    ], timeout=45)
    if not result.stdout:
        raise VideoError("无法提取关键帧，请尝试转换视频格式。")
    return result.stdout


def analyze_video(path: Path, mode: str = "scene", threshold: float = 0.28,
                  interval: float = 5.0, max_segments: int = 30,
                  progress: Callable[[float], None] | None = None) -> tuple[VideoInfo, list[Segment], bool]:
    info = probe_video(path)
    boundaries, limited = detect_boundaries(path, info, mode, threshold, interval, max_segments)
    segments = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        # Midpoint helps avoid black transition frames at the start of a shot.
        frame_time = (start + end) / 2
        if info.fps > 0:
            frame_time = min(frame_time, max(0.0, info.duration - 1 / info.fps))
        frame = extract_frame(path, frame_time)
        segments.append(Segment(index, start, end, frame_time, frame))
        if progress:
            progress(index / (len(boundaries) - 1))
    return info, segments, limited


def extract_audio(path: Path, destination: Path) -> None:
    run_media([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-threads", "2", "-i", str(path),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac",
        "-b:a", "64k", str(destination)
    ], timeout=180)


def timecode(seconds: float) -> str:
    total_ms = round(max(0, seconds) * 1000)
    minutes, rest = divmod(total_ms, 60000)
    secs, ms = divmod(rest, 1000)
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def empty_rows(segments: list[Segment]) -> list[dict]:
    return [{"片段": s.index, "开始": timecode(s.start), "结束": timecode(s.end),
             "时长（秒）": round(s.duration, 3), **{field: "" for field in NOTE_FIELDS}}
            for s in segments]


def csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    fields = ["片段", "开始", "结束", "时长（秒）", *NOTE_FIELDS]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Prevent spreadsheet formula execution when opening user-authored notes.
        writer.writerow({k: "'" + value if isinstance(value, str) and
                         value.lstrip().startswith(("=", "+", "-", "@")) else value
                         for k, value in row.items()})
    return output.getvalue().encode("utf-8-sig")


def markdown_report(name: str, info: VideoInfo, segments: list[Segment], rows: list[dict],
                    transcript: str = "", ai_report: str = "", method: str = "scene",
                    limited: bool = False) -> str:
    lines = ["# 视频拆解报告", "", f"原文件：{name.replace(chr(10), ' ')}", "",
             f"- 时长：{timecode(info.duration)}", f"- 分辨率：{info.width} × {info.height}",
             f"- 帧率：{info.fps:.2f} fps", f"- 音轨：{'有' if info.has_audio else '无'}",
             f"- 拆解方式：{'场景变化检测（约 0.25 秒精度）' if method == 'scene' else '等间隔采样'}",
             f"- 片段数量：{len(segments)}", ""]
    if limited:
        lines += ["> 达到片段上限，相邻片段已合并；部分片段包含多个镜头。", ""]
    lines += ["## 时间轴与笔记", ""]
    for s, row in zip(segments, rows):
        lines += [f"### 片段 {s.index:02d} · {timecode(s.start)} → {timecode(s.end)}", "",
                  f"![关键帧](frames/{s.index:03d}.jpg)", "",
                  f"关键帧时间：{timecode(s.frame_time)}", ""]
        lines += [f"- **{field}**：{row.get(field) or '待补充'}" for field in NOTE_FIELDS]
        lines.append("")
    if transcript.strip():
        lines += ["## 转写 / 台词参考", "", transcript, ""]
    if ai_report.strip():
        lines += ["## AI 内容拆解", "", ai_report, ""]
    lines += ["> 关键帧仅代表采样时刻。场景检测可能遗漏渐变、快速切换，需人工复核。", ""]
    return "\n".join(lines)


def export_bundle(name: str, info: VideoInfo, segments: list[Segment], rows: list[dict],
                  transcript: str = "", ai_report: str = "", method: str = "scene",
                  limited: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", markdown_report(name, info, segments, rows, transcript,
                                                      ai_report, method, limited))
        archive.writestr("shots.csv", csv_bytes(rows))
        archive.writestr("analysis.json", json.dumps({
            "filename": name, "video": asdict(info), "method": method, "limited": limited,
            "segments": [{"index": s.index, "start": s.start, "end": s.end,
                          "frame_time": s.frame_time, "image": f"frames/{s.index:03d}.jpg",
                          "notes": {field: row.get(field, "") for field in NOTE_FIELDS}}
                         for s, row in zip(segments, rows)],
            "transcript": transcript, "ai_report": ai_report,
        }, ensure_ascii=False, indent=2))
        for segment in segments:
            archive.writestr(f"frames/{segment.index:03d}.jpg", segment.image)
    return output.getvalue()
