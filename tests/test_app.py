from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def button_named(app, label):
    return next(button for button in app.button if button.label == label)


def test_demo_workflow_and_clear_results():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    button_named(app, "先用 10 秒示例试试看").click().run()
    button_named(app, "开始拆解 →").click().run()
    assert not app.exception
    assert len(app.metric) == 4
    assert app.metric[1].value == "3"
    assert app.session_state["analysis"]["info"].has_audio
    assert len(app.get("download_button")) == 2
    button_named(app, "清除当前结果").click().run()
    assert not app.exception
    assert not app.metric
