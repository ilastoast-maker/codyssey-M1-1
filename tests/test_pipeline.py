import numpy as np
import pandas as pd

from src.pipeline import Settings, add_features, forecast_baselines, validate_and_clean
from src.universal import UniversalSettings, prepare_analysis_frame


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
