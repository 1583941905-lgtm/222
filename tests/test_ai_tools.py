from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_tools import AIError, analyze_content, client_for, representative_segments, transcribe
from video_tools import Segment, VideoInfo


def test_key_is_required():
    with pytest.raises(AIError):
        client_for("")


def test_selection_keeps_first_last_and_limit():
    segments = [Segment(i, i, i + 1, i + 0.5, b"jpeg") for i in range(60)]
    selected = representative_segments(segments)
    assert len(selected) == 12
    assert selected[0] == segments[0]
    assert selected[-1] == segments[-1]


def test_vision_request_contains_frames_and_transcript():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="拆解完成"))])
    with patch("ai_tools.client_for", return_value=client):
        client.__enter__.return_value = client
        result = analyze_content(VideoInfo(5, 640, 360, 24, True, "h264"),
                                 [Segment(1, 0, 5, 2.5, b"jpeg")], "转写台词", "学习剪辑", "secret")
    assert result == "拆解完成"
    sent = client.chat.completions.create.call_args.kwargs
    assert "转写台词" in sent["messages"][1]["content"][0]["text"]
    assert sent["messages"][1]["content"][-1]["type"] == "image_url"
    assert "secret" not in repr(sent)


def test_transcription_reads_real_file_handle(tmp_path):
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"audio")
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text="你好")
    with patch("ai_tools.client_for", return_value=client):
        client.__enter__.return_value = client
        assert transcribe(audio, "secret") == "你好"
