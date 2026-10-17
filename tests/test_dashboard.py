from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_dashboard_filters_windows_and_incomplete_date_selection():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert app.metric[0].value == "1,214"
    app.selectbox[0].set_value("2024년").run()
    assert not app.exception
    assert app.metric[0].value == "242"
    assert app.metric[1].value == "81.28%"
    app.slider[0].set_value(10)
    app.slider[1].set_value(100)
    app.slider[2].set_value(30)
    app.run()
    assert not app.exception
    app.date_input[0].set_value((date(2024, 1, 1),)).run()
    assert not app.exception
    assert "모두 선택" in app.info[0].value


def test_short_period_and_reversed_windows_show_guidance():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.date_input[0].set_value((date(2024, 1, 1), date(2024, 1, 31))).run()
    assert not app.exception
    assert "너무 짧" in app.warning[0].value
    app.date_input[0].set_value((date(2024, 1, 1), date(2024, 12, 31)))
    app.slider[0].set_value(50)
    app.slider[1].set_value(40)
    app.run()
    assert not app.exception
    assert "작아야" in app.warning[0].value
