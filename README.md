# 帧析 · 视频拆解工作台

一个从上传视频到分镜笔记、关键帧和创作报告的中文 Streamlit 应用。

## 已实现

- 上传 MP4、MOV、MKV、WebM、M4V；最大 200 MB，最长 20 分钟。
- 场景变化检测或等间隔采样，提取各片段的中间关键帧。
- 时长、画面尺寸、片段长度图表，以及定位播放。
- 可编辑的画面、景别与运镜、台词、剪辑与声音、复刻笔记。
- ZIP 导出 Markdown 报告、CSV、JSON 和 JPEG 关键帧；CSV 可单独下载。
- 内置 10 秒示例，不用准备视频也能体验。
- 可选 OpenAI 语音转写和图片内容分析。没有 API Key 也可使用全部本地功能。

## 启动

需要 Python 3.10+ 和 FFmpeg（含 ffprobe）。

安装 FFmpeg：

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y ffmpeg

# macOS（Homebrew）
brew install ffmpeg
```

Windows 可运行 `winget install Gyan.FFmpeg`，然后重新打开终端，确认 `ffmpeg -version` 和 `ffprobe -version` 可用。

在项目目录运行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Windows 激活环境使用 `.venv\Scripts\activate`。打开终端中显示的地址，通常为 `http://localhost:8501`。

在本次 Codex 工作区，可以直接运行：

```bash
cd /workspace/222
.venv/bin/streamlit run app.py --server.address 0.0.0.0
```

## 使用

1. 上传视频，或点击“先用 10 秒示例试试看”。
2. 在侧栏选择检测方式。剪辑视频可用场景检测；长镜头和讲解可用等间隔采样。
3. 点击“开始拆解”，浏览关键帧、定位播放，并填写分镜笔记。
4. 可在“AI 设置”填写自己的 OpenAI API Key，再转写语音或生成内容拆解。无需将密钥发送给开发者。
5. 下载完整 ZIP。解压后将 `report.md` 与 `frames/` 放在一起查看，图片即可显示。

也可通过环境变量设置密钥：

```bash
export OPENAI_API_KEY="你的密钥"
streamlit run app.py
```

默认图片模型为 `gpt-4.1-mini`，默认转写模型为 `whisper-1`，可在侧栏修改为账号有权限使用的对应模型。AI 请求需要能通过 HTTPS 访问 `api.openai.com`；受限的 Codex 云环境可能需要在环境配置中允许该域名。本地拆解不需要访问它。

## 数据与分析边界

- 本地功能只在运行应用的服务器处理视频，无外部 AI 调用。临时视频在拆解/转写操作结束后自动删除。
- API Key 由输入框或环境变量提供，不写入报告或仓库；输入值随当前 Streamlit 会话保留。
- 只有点击语音转写才上传提取的音轨；只有点击 AI 拆解才上传最多 12 张采样图片和输入的台词。API 费用由密钥所属账号承担。
- 结果和笔记保留在会话内存中，重启或切换视频后不会保留，请及时下载。
- 场景检测以每秒 4 帧进行估算，边界精度约 0.25 秒；渐变、极快切换和相似画面可能漏检。
- 达到片段上限后合并相邻片段；分析片段数量不等于真实镜头数量。等间隔模式也不是镜头识别。
- AI 只看到静态采样帧及可选转写，不能完整观看视频，不分析实际配乐；输出需要人工复核。
- 示例由测试图、纯色画面和正弦音组成，无真实人声。

## 验证

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

测试使用 FFmpeg 处理真实合成视频，覆盖切换边界、时间轴完整性、关键帧、音轨、无效文件、导出内容和 Streamlit 页面流程。AI 测试使用模拟客户端，不产生 API 费用。

## 部署

可用 `streamlit run app.py --server.address 0.0.0.0` 启动服务。若使用 Streamlit Community Cloud，仓库中的 `packages.txt` 会安装 FFmpeg，入口选择 `app.py`。公开部署前请根据使用者范围配置平台的访问控制和资源限制。
