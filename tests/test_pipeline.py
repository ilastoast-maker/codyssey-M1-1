import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock
from yfinance.exceptions import YFRateLimitError

from src.pipeline import Settings, add_features, forecast_baselines, validate_and_clean
from src.universal import UniversalSettings, prepare_analysis_frame
from src import universal


def sample_frame(rows: int = 140) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=rows)
    close = np.linspace(500, 650, rows)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": np.arange(rows) + 1_000,
            "Turnover": (np.arange(rows) + 1_000) * close,
            "Change": np.r_[np.nan, np.diff(close)],
            "Trades": np.arange(rows) + 100,
        }
    )


def test_cleaning_and_feature_windows():
    clean, audit = validate_and_clean(sample_frame())
    result = add_features(clean, Settings())
    assert audit["rows_after_cleaning"] == 140
    assert result["MA20"].first_valid_index() == 19
    assert result["MA60"].first_valid_index() == 59
    assert result["Daily_Return"].isna().sum() == 1


def test_forecast_has_no_holdout_leakage():
    featured = add_features(sample_frame(), Settings())
    forecast, metrics = forecast_baselines(featured, horizon=20)
    training_last = featured.iloc[-21]["Close"]
    assert len(forecast) == 20
    assert (forecast["Last_Value"] == training_last).all()
    assert metrics["Last_Value_MAE"] > 0
    changed = featured.copy()
    changed.loc[changed.index[-20:], "Close"] *= 10
    changed_forecast, _ = forecast_baselines(changed, horizon=20)
    pd.testing.assert_frame_equal(forecast[["Last_Value", "Drift"]], changed_forecast[["Last_Value", "Drift"]])


def test_provider_auto_detection_and_safe_slug():
    assert UniversalSettings("2330.TW", "TSMC").resolved_provider == "twse"
    assert UniversalSettings("NVDA", "NVIDIA").resolved_provider == "yahoo"
    assert UniversalSettings("BRK-B", "Berkshire Hathaway").slug == "brk_b"


def test_adjusted_price_is_used_for_analysis():
    raw = sample_frame()
    raw["AnalysisPrice"] = raw["Close"] * 0.5
    raw["PriceBasis"] = "Adjusted Close"
    result, audit = prepare_analysis_frame(raw, UniversalSettings("AAPL", "Apple"))
    assert result["Close"].iloc[-1] == raw["AnalysisPrice"].iloc[-1]
    assert audit["price_basis"] == "Adjusted Close"


def test_requested_period_filters_cached_data_and_restarts_returns():
    raw = sample_frame(400)
    selected = raw.iloc[100:300]
    settings = UniversalSettings("AAPL", "Apple", start=str(selected.Date.iloc[0].date()), end=str(selected.Date.iloc[-1].date()))
    result, audit = prepare_analysis_frame(raw, settings)
    assert len(result) == audit["rows_after_cleaning"] == 200
    assert result.Date.min() == selected.Date.min()
    assert result.Date.max() == selected.Date.max()
    assert pd.isna(result.Daily_Return.iloc[0])


def test_short_period_fails_before_analysis():
    with pytest.raises(ValueError, match="최소 100"):
        prepare_analysis_frame(sample_frame(90), UniversalSettings("AAPL", "Apple"))


def test_missing_adjusted_price_updates_audit():
    raw = sample_frame()
    raw["AnalysisPrice"] = raw.Close
    raw.loc[0, "AnalysisPrice"] = np.nan
    result, audit = prepare_analysis_frame(raw, UniversalSettings("AAPL", "Apple"))
    assert len(result) == audit["rows_after_cleaning"] == 139
    assert audit["start_date"] == str(raw.Date.iloc[1].date())
    assert audit["analysis_price_rows_removed"] == 1


def test_missing_and_duplicate_rows_are_audited():
    raw = sample_frame()
    raw.loc[0, "Volume"] = np.nan
    raw = pd.concat([raw, raw.iloc[[1]]], ignore_index=True)
    clean, audit = validate_and_clean(raw)
    assert len(clean) == 139
    assert audit["missing_rows_removed"] == 1
    assert audit["duplicate_dates_removed"] == 1


def test_invalid_ohlc_stops_instead_of_being_silently_dropped():
    raw = sample_frame()
    raw.loc[0, "High"] = raw.loc[0, "Low"] - 1
    with pytest.raises(ValueError, match="invalid OHLC"):
        validate_and_clean(raw)


def test_bundled_tsmc_cache_works_with_explicit_ticker_without_network(monkeypatch):
    monkeypatch.setattr(universal, "collect_market_data", Mock(side_effect=AssertionError("network call")))
    raw, source = universal.load_market_data(UniversalSettings("2330.TW", "TSMC"), skip_download=True)
    assert len(raw) == 1214
    assert "저장" in source


def test_new_download_is_persisted_then_reused(monkeypatch, tmp_path):
    monkeypatch.setattr(universal, "ROOT", tmp_path)
    download = Mock(return_value=sample_frame())
    monkeypatch.setattr(universal, "download_yahoo_data", download)
    settings = UniversalSettings("NVDA", "NVIDIA")
    universal.load_market_data(settings)
    universal.load_market_data(settings)
    assert download.call_count == 1
    assert universal.output_paths(settings)["raw"].exists()
    assert universal.output_paths(settings)["source"].exists()
    universal.load_market_data(settings, refresh=True)
    assert download.call_count == 2


def test_cache_does_not_silently_cover_uncollected_dates(monkeypatch, tmp_path):
    monkeypatch.setattr(universal, "ROOT", tmp_path)
    monkeypatch.setattr(universal, "download_yahoo_data", Mock(return_value=sample_frame()))
    universal.load_market_data(UniversalSettings("NVDA", "NVIDIA", start="2024-01-01", end="2024-12-31"))
    with pytest.raises(FileNotFoundError, match="요청 기간"):
        universal.load_market_data(UniversalSettings("NVDA", "NVIDIA"), skip_download=True)


def test_yahoo_rate_limit_is_reported_without_retry_loop(monkeypatch):
    ticker = Mock()
    ticker.history.side_effect = YFRateLimitError()
    monkeypatch.setattr(universal.yf, "Ticker", Mock(return_value=ticker))
    pause = Mock()
    monkeypatch.setattr(universal.time, "sleep", pause)
    with pytest.raises(RuntimeError, match="429"):
        universal.download_yahoo_data(UniversalSettings("NVDA", "NVIDIA"))
    assert ticker.history.call_count == 1
    pause.assert_not_called()


def test_yahoo_adjusted_close_and_timezone_normalization(monkeypatch):
    raw = sample_frame().set_index("Date").tz_localize("America/New_York")
    raw["Adj Close"] = raw.Close * 0.5
    ticker = Mock()
    ticker.history.return_value = raw
    monkeypatch.setattr(universal.yf, "Ticker", Mock(return_value=ticker))
    result = universal.download_yahoo_data(UniversalSettings("AAPL", "Apple"))
    assert result.Date.dt.tz is None
    assert result.AnalysisPrice.iloc[-1] == result.Close.iloc[-1] * 0.5


@pytest.mark.parametrize("kwargs", [dict(start="2025-01-01", end="2024-01-01"), dict(short_window=60), dict(forecast_horizon=0)])
def test_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        UniversalSettings("AAPL", "Apple", **kwargs)
