from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from ai_tools import AIError, analyze_content, transcribe
from video_tools import (MAX_BYTES, NOTE_FIELDS, VideoError, analyze_video, check_dependencies,
                         csv_bytes, empty_rows, export_bundle, extract_audio, timecode)

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="帧析 · 视频拆解工作台", page_icon="🎞️", layout="wide")
st.markdown("""<style>
.block-container {max-width:1250px; padding-top:2.4rem; padding-bottom:3rem;}
h1 {font-weight:750!important; letter-spacing:-1.5px;}
h2, h3 {letter-spacing:-.4px;}
.eyebrow {font-size:12px; letter-spacing:2.6px; color:#71816c; font-weight:700;}
.hero-note {font-size:17px; line-height:1.9; color:#687165; max-width:680px;}
.guide {background:#edf0e8; border-radius:16px; padding:22px 25px; line-height:2; color:#52604b;}
.guide strong {color:#2d3a29;}
[data-testid="stMetric"] {background:#f1efe9; border-radius:12px; padding:14px 18px;}
[data-testid="stFileUploader"] {border-radius:14px;}
div.stButton>button, div.stDownloadButton>button {border-radius:9px;}
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 🎞️ 帧析")
    st.caption("把一条视频，拆成可复用的创作方法。")
    st.divider()
    st.markdown("#### 拆解设置")
    mode_label = st.radio("拆解方式", ["场景变化检测", "等间隔采样"],
                          help="场景变化检测适合剪辑视频；等间隔适合长镜头、讲解和录屏。")
    mode = "scene" if mode_label == "场景变化检测" else "interval"
    threshold = st.slider("场景变化阈值", 0.05, 0.80, 0.28, 0.01,
                          help="越低越敏感，检测到的切换越多。", disabled=mode != "scene")
    interval = st.slider("采样间隔（秒）", 1, 30, 5, disabled=mode != "interval")
    max_segments = st.slider("片段数量上限", 5, 60, 30, 5)
    st.divider()
    with st.expander("AI 设置 · 可选"):
        st.caption("本地拆解无需密钥。AI 功能会把采样图片、转写或音频发送至 OpenAI，按 API 用量计费。")
        api_key = st.text_input("OpenAI API Key", type="password", key="api_key",
                                help="也可在启动前设置 OPENAI_API_KEY 环境变量。")
        api_key = api_key.strip() or os.getenv("OPENAI_API_KEY", "")
        vision_model = st.text_input("支持图片的模型", value="gpt-4.1-mini")
        audio_model = st.text_input("语音转写模型", value="whisper-1")
    st.caption("原视频只在应用服务器的临时目录处理；处理结束后删除临时文件。结果保留在当前会话，请及时下载。")

left, right = st.columns([1.8, 1], gap="large")
with left:
    st.markdown('<div class="eyebrow">FRAME NOTES / 创作研究室</div>', unsafe_allow_html=True)
    st.title("好视频，逐帧看懂。")
    st.markdown('<p class="hero-note">从画面、节奏到表达，把灵感变成时间轴，<br>再把时间轴变成你的下一份拍摄脚本。</p>', unsafe_allow_html=True)
with right:
    st.markdown('<div class="guide"><strong>你的拆解路径</strong><br>01 &nbsp; 上传一条参考视频<br>02 &nbsp; 提取片段与关键帧<br>03 &nbsp; 写下观察，导出创作笔记</div>', unsafe_allow_html=True)

st.divider()
st.subheader("01 / 放入你的参考视频")
uploaded = st.file_uploader("上传视频", type=["mp4", "mov", "mkv", "webm", "m4v"],
                             help="最大 200 MB、最长 20 分钟；推荐 H.264 编码的 MP4。")
if st.button("先用 10 秒示例试试看", disabled=uploaded is not None):
    st.session_state["use_demo"] = True

if uploaded is not None:
    source = uploaded.getvalue()
    filename = uploaded.name
elif st.session_state.get("use_demo"):
    demo = ROOT / "assets" / "demo.mp4"
    if not demo.exists():
        st.error("示例视频缺失，请上传自己的视频。")
        st.stop()
    source = demo.read_bytes()
    filename = "示例视频.mp4"
    st.caption("示例由三段彩色画面和测试音组成，用于体验镜头检测；不包含真实台词。")
else:
    st.info("上传视频后即可开始。无需 API Key，也能完成片段拆解、笔记和报告导出。")
    st.stop()

if len(source) > MAX_BYTES:
    st.error("视频不能超过 200 MB。")
    st.stop()

fingerprint = hashlib.sha256(source).hexdigest()
if st.session_state.get("source_id") != fingerprint:
    st.session_state["source_id"] = fingerprint
    st.session_state.pop("analysis", None)
    # A new source must never inherit another video's transcript or report.
    for key in list(st.session_state):
        if key.startswith(("notes_", "transcript_", "ai_report_")):
            del st.session_state[key]

try:
    check_dependencies()
except VideoError as exc:
    st.error(str(exc))
    st.stop()

config = (mode, threshold if mode == "scene" else None,
          interval if mode == "interval" else None, max_segments)
preview, controls = st.columns([1.6, 1], gap="large")
with preview:
    st.video(source)
with controls:
    st.markdown("#### " + filename.replace("\n", " "))
    st.write(f"{len(source) / 1024 / 1024:.1f} MB · {mode_label}")
    st.caption("场景检测按每秒 4 帧估算切换点，不能保证识别所有真实镜头。达到数量上限时会合并相邻片段。")
    start = st.button("开始拆解 →", type="primary", width="stretch")
    if st.button("清除当前结果", disabled="analysis" not in st.session_state):
        st.session_state.pop("analysis", None)
        for key in list(st.session_state):
            if key.startswith(("notes_", "transcript_", "ai_report_")):
                del st.session_state[key]
        st.rerun()

if start:
    try:
        with st.status("正在识别视频与场景变化…", expanded=True) as status:
            progress = st.progress(0, text="识别切换点并提取关键帧")
            with tempfile.TemporaryDirectory(prefix="frame-notes-") as temp:
                path = Path(temp) / "input.video"
                path.write_bytes(source)
                info, segments, limited = analyze_video(
                    path, mode, threshold, interval, max_segments,
                    progress=lambda value: progress.progress(value, text="正在提取关键帧"))
            result_id = hashlib.sha256(repr((fingerprint, config)).encode()).hexdigest()[:16]
            # Reanalysis intentionally resets the table/report for these boundaries.
            st.session_state.pop(f"notes_{result_id}", None)
            st.session_state.pop(f"ai_report_{result_id}", None)
            st.session_state["analysis"] = dict(info=info, segments=segments, limited=limited,
                                                  config=config, id=result_id, mode=mode)
            status.update(label="拆解完成", state="complete", expanded=False)
        st.rerun()
    except (VideoError, OSError) as exc:
        st.error(str(exc) if isinstance(exc, VideoError) else "临时文件处理失败，请检查磁盘空间后重试。")

result = st.session_state.get("analysis")
if not result:
    st.stop()
if result["config"] != config:
    st.info("侧栏参数已变更。下方仍是上次的结果，点击“开始拆解”可应用新参数。")

info, segments = result["info"], result["segments"]
result_id = result["id"]
st.divider()
st.subheader("02 / 看清画面和节奏")
stats = st.columns(4)
for column, label, value in zip(stats, ["视频时长", "分析片段", "平均片段长度", "画面尺寸"],
                                [timecode(info.duration), str(len(segments)),
                                 f"{info.duration / len(segments):.2f} 秒", f"{info.width} × {info.height}"]):
    column.metric(label, value)
if result["limited"]:
    st.warning("达到片段数量上限：相邻片段已合并，部分片段可能包含多个镜头。可提高上限后重新拆解。")
elif result["mode"] == "scene" and len(segments) == 1:
    st.info("没有检测到明显切换。可降低阈值或改用等间隔采样，观察长镜头中的变化。")

frames_tab, notes_tab, ai_tab, export_tab = st.tabs(["关键帧与节奏", "分镜笔记", "AI 内容拆解", "导出报告"])
with frames_tab:
    st.caption("每段显示中间时刻的一张关键帧。点击“定位播放”，即可在该卡片下查看原片段。")
    chart = pd.DataFrame({"片段": [str(s.index) for s in segments],
                          "时长（秒）": [s.duration for s in segments]}).set_index("片段")
    st.bar_chart(chart, y="时长（秒）", color="#E96B47", height=180)
    for offset in range(0, len(segments), 3):
        for col, segment in zip(st.columns(3), segments[offset:offset + 3]):
            with col:
                st.image(segment.image, width="stretch")
                st.markdown(f"**{segment.index:02d}** · {timecode(segment.start)} → {timecode(segment.end)}")
                st.caption(f"持续 {segment.duration:.2f} 秒 · 采样于 {timecode(segment.frame_time)}")
                if st.button("定位播放", key=f"seek_{result_id}_{segment.index}"):
                    # A second player keeps the main preview stable across reruns.
                    st.video(source, start_time=segment.start, end_time=segment.end)

with notes_tab:
    st.caption("直接编辑表格，记录可见画面与复刻思路。笔记会随报告一起导出。")
    notes = st.data_editor(pd.DataFrame(empty_rows(segments)), key=f"notes_{result_id}",
                           hide_index=True, width="stretch", num_rows="fixed",
                           disabled=["片段", "开始", "结束", "时长（秒）"],
                           column_config={field: st.column_config.TextColumn(field, width="large")
                                          for field in NOTE_FIELDS})
rows = notes.fillna("").to_dict("records")

with ai_tab:
    st.markdown("#### 把观察整理成可执行的创作方案")
    st.caption("可先粘贴台词，也可转写音轨。画面分析最多发送 12 张关键帧；AI 不能完整观看原视频或听到配乐。")
    transcript_key = f"transcript_{fingerprint}"
    if st.button("转写视频语音", disabled=not info.has_audio or not api_key or not audio_model.strip()):
        try:
            with st.spinner("正在提取音轨并转写…"):
                with tempfile.TemporaryDirectory(prefix="frame-notes-audio-") as temp:
                    path = Path(temp) / "input.video"
                    path.write_bytes(source)
                    audio = Path(temp) / "audio.m4a"
                    extract_audio(path, audio)
                    st.session_state[transcript_key] = transcribe(audio, api_key, audio_model)
        except (AIError, VideoError, OSError) as exc:
            st.error(str(exc) if isinstance(exc, (AIError, VideoError)) else "音频临时文件处理失败。")
    if not info.has_audio:
        st.caption("该视频没有音轨；仍可粘贴外部台词或分析画面。")
    transcript = st.text_area("台词 / 转写参考（可编辑）", key=transcript_key, height=150,
                               placeholder="粘贴台词、字幕或转写。留空时，AI 仅分析可见画面。")
    goal = st.text_input("这次想研究什么？", value="学习开头钩子、内容结构和拍摄方法，输出复刻清单。")
    if not api_key:
        st.info("在侧栏的“AI 设置”中填写 API Key 即可启用。分镜笔记和导出无需密钥。")
    if st.button("生成 AI 拆解", type="primary", disabled=not api_key or not vision_model.strip()):
        try:
            with st.spinner("正在分析采样画面与台词…"):
                st.session_state[f"ai_report_{result_id}"] = analyze_content(
                    info, segments, transcript, goal, api_key, vision_model)
        except AIError as exc:
            st.error(str(exc))
    ai_report = st.session_state.get(f"ai_report_{result_id}", "")
    if ai_report:
        st.markdown(ai_report)
        st.caption("这是基于生成当时的采样帧和台词生成的结果；修改台词后请重新生成。")

with export_tab:
    st.markdown("#### 留住这次拆解，下次拍摄直接用。")
    st.write("完整包包含 Markdown 报告、CSV 分镜表、JSON 数据和所有关键帧。Markdown 中的图片请连同 frames 文件夹一起打开。")
    export_col, csv_col = st.columns(2)
    bundle = export_bundle(filename, info, segments, rows, transcript, ai_report,
                           result["mode"], result["limited"])
    export_col.download_button("下载完整拆解包 · ZIP", bundle, file_name="video-breakdown.zip",
                               mime="application/zip", width="stretch", type="primary")
    csv_col.download_button("下载分镜表 · CSV", csv_bytes(rows), file_name="shots.csv",
                            mime="text/csv", width="stretch")
    st.caption("当前会话中的结果不会自动保存到仓库。关闭或重启应用前，请先下载。")
