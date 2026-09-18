"""Reusable stock analysis runner for TWSE and Yahoo Finance tickers."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yfinance as yf
from statsmodels.tsa.seasonal import seasonal_decompose

from src.pipeline import (
    ROOT,
    Settings as TwseSettings,
    add_features,
    calculate_metrics,
    collect_twse_data,
    forecast_baselines,
    validate_and_clean,
)


TICKER_PATTERN = re.compile(r"^[A-Za-z0-9.^=-]{1,20}$")


@dataclass(frozen=True)
class UniversalSettings:
    ticker: str
    name: str
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    provider: str = "auto"
    currency: str | None = None
    short_window: int = 20
    long_window: int = 60
    forecast_horizon: int = 60

    @property
    def normalized_ticker(self) -> str:
        ticker = self.ticker.strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("Ticker contains unsupported characters")
        return ticker

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "_", self.normalized_ticker.lower()).strip("_")

    @property
    def resolved_provider(self) -> str:
        provider = self.provider.lower()
        if provider not in {"auto", "twse", "yahoo"}:
            raise ValueError("provider must be one of: auto, twse, yahoo")
        if provider != "auto":
            return provider
        return "twse" if re.fullmatch(r"\d{4,6}(?:\.TW)?", self.normalized_ticker) else "yahoo"

    @property
    def resolved_currency(self) -> str:
        if self.currency:
            return self.currency.upper()
        return "TWD" if self.resolved_provider == "twse" else "price units"

    @property
    def output_dir(self) -> Path:
        return ROOT / "outputs" / self.slug


def output_paths(settings: UniversalSettings) -> dict[str, Path]:
    base = settings.output_dir
    return {
        "raw": base / "data" / "raw" / "market_data.csv",
        "processed": base / "data" / "processed" / "analysis.csv",
        "metrics": base / "data" / "processed" / "metrics.json",
        "images": base / "images",
        "report": base / "REPORT.md",
    }


def download_yahoo_data(settings: UniversalSettings, retries: int = 3) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            frame = yf.download(
                settings.normalized_ticker,
                start=settings.start,
                end=(pd.Timestamp(settings.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                timeout=30,
            )
            if frame.empty:
                raise RuntimeError("Yahoo Finance returned no rows")
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            frame = frame.reset_index().rename(columns={"index": "Date"})
            required = {"Date", "Open", "High", "Low", "Close", "Volume"}
            missing = required.difference(frame.columns)
            if missing:
                raise ValueError(f"Yahoo data is missing columns: {sorted(missing)}")
            frame["AnalysisPrice"] = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
            frame["PriceBasis"] = "Adjusted Close" if "Adj Close" in frame else "Close"
            return frame
        except Exception as exc:  # yfinance wraps network errors in several exception types
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        "Yahoo Finance download failed after retries. Reuse a cached CSV with --skip-download."
    ) from last_error


def collect_market_data(settings: UniversalSettings) -> pd.DataFrame:
    paths = output_paths(settings)
    if settings.resolved_provider == "twse":
        stock_no = settings.normalized_ticker.removesuffix(".TW")
        frame = collect_twse_data(
            TwseSettings(stock_no=stock_no, start=settings.start, end=settings.end),
            output_path=paths["raw"],
        )
        frame["AnalysisPrice"] = frame["Close"]
        frame["PriceBasis"] = "Close"
    else:
        frame = download_yahoo_data(settings)
        paths["raw"].parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(paths["raw"], index=False, date_format="%Y-%m-%d")
    return frame


def prepare_analysis_frame(raw: pd.DataFrame, settings: UniversalSettings) -> tuple[pd.DataFrame, dict[str, object]]:
    clean, audit = validate_and_clean(raw)
    if "AnalysisPrice" not in clean:
        clean["AnalysisPrice"] = clean["Close"]
    clean["AnalysisPrice"] = pd.to_numeric(clean["AnalysisPrice"], errors="coerce")
    missing_analysis_price = int(clean["AnalysisPrice"].isna().sum())
    clean = clean.dropna(subset=["AnalysisPrice"]).copy()
    clean["MarketClose"] = clean["Close"]
    clean["Close"] = clean["AnalysisPrice"]
    audit["analysis_price_rows_removed"] = missing_analysis_price
    audit["price_basis"] = (
        str(raw["PriceBasis"].dropna().iloc[0]) if "PriceBasis" in raw and not raw["PriceBasis"].dropna().empty else "Close"
    )
    features = add_features(
        clean,
        TwseSettings(
            start=settings.start,
            end=settings.end,
            short_window=settings.short_window,
            long_window=settings.long_window,
            forecast_horizon=settings.forecast_horizon,
        ),
    )
    return features, audit


def _save(images_dir: Path, filename: str) -> None:
    images_dir.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(images_dir / filename, dpi=160, bbox_inches="tight")
    plt.close()


def create_generic_visualizations(df: pd.DataFrame, settings: UniversalSettings) -> None:
    images = output_paths(settings)["images"]
    ticker = settings.normalized_ticker
    sns.set_theme(style="whitegrid")

    plt.figure(figsize=(13, 6))
    plt.plot(df["Date"], df["Close"], label="Analysis price", linewidth=1.2)
    plt.plot(df["Date"], df["MA20"], label=f"MA{settings.short_window}")
    plt.plot(df["Date"], df["MA60"], label=f"MA{settings.long_window}")
    plt.title(f"{settings.name} ({ticker}) Price and Moving Averages")
    plt.ylabel(settings.resolved_currency)
    plt.legend()
    _save(images, "01_price_and_moving_averages.png")

    outliers = df[df["Return_Outlier"]]
    plt.figure(figsize=(13, 5))
    plt.plot(df["Date"], df["Daily_Return"] * 100, linewidth=0.7)
    plt.scatter(outliers["Date"], outliers["Daily_Return"] * 100, color="red", s=18, label="IQR outlier")
    plt.axhline(0, color="black", linewidth=0.7)
    plt.title(f"{settings.name} Daily Returns and Outliers")
    plt.ylabel("Return (%)")
    plt.legend()
    _save(images, "02_daily_returns_outliers.png")

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(df["Date"], df["Volatility20"] * 100, color="orange")
    axes[0].set_ylabel("Volatility (%)")
    axes[0].set_title("Rolling Annualized Volatility")
    axes[1].bar(df["Date"], df["Volume"] / 1_000_000, width=1.0)
    axes[1].plot(df["Date"], df["Volume_MA20"] / 1_000_000, color="black", label="20-day average")
    axes[1].set_ylabel("Volume (million)")
    axes[1].legend()
    _save(images, "03_volatility_and_volume.png")

    monthly = df.set_index("Date")["Close"].resample("ME").last().pct_change(fill_method=None) * 100
    heat = monthly.to_frame("return")
    heat["Year"], heat["Month"] = heat.index.year, heat.index.month
    plt.figure(figsize=(12, 4.5))
    sns.heatmap(heat.pivot(index="Year", columns="Month", values="return"), annot=True, fmt=".1f", center=0, cmap="RdYlGn")
    plt.title("Monthly Returns (%)")
    _save(images, "04_monthly_return_heatmap.png")

    monthly_close = df.set_index("Date")["Close"].resample("ME").last().dropna()
    if len(monthly_close) >= 24:
        decomposition = seasonal_decompose(monthly_close, model="additive", period=12, extrapolate_trend="freq")
        fig = decomposition.plot()
        fig.set_size_inches(13, 9)
        fig.suptitle(f"{settings.name} Monthly Decomposition", y=1.01)
        _save(images, "05_seasonal_decomposition.png")

    forecast_df, _ = forecast_baselines(df, settings.forecast_horizon)
    history = df.iloc[-(settings.forecast_horizon + 120) : -settings.forecast_horizon]
    plt.figure(figsize=(13, 6))
    plt.plot(history["Date"], history["Close"], color="gray", label="Train history")
    plt.plot(forecast_df["Date"], forecast_df["Close"], color="black", linewidth=2, label="Actual holdout")
    plt.plot(forecast_df["Date"], forecast_df["Last_Value"], linestyle="--", label="Last value")
    plt.plot(forecast_df["Date"], forecast_df["Drift"], linestyle=":", label="Drift")
    plt.axvline(forecast_df["Date"].iloc[0], color="red", linewidth=1)
    plt.title(f"Baseline Forecast ({settings.forecast_horizon}-Trading-Day Holdout)")
    plt.ylabel(settings.resolved_currency)
    plt.legend()
    _save(images, "06_baseline_forecast.png")


def write_generic_report(metrics: dict[str, object], settings: UniversalSettings) -> None:
    paths = output_paths(settings)
    audit = metrics["audit"]
    regimes = metrics["regime_stats"]
    up = regimes.get("MA20 >= MA60", {})
    down = regimes.get("MA20 < MA60", {})
    forecast = metrics["forecast"]
    high = metrics["high_volume_abs_return_pct"]
    normal = metrics["normal_volume_abs_return_pct"]
    ratio = high / normal if normal else float("nan")
    decomposition_line = "![시계열 분해](images/05_seasonal_decomposition.png)" if (paths["images"] / "05_seasonal_decomposition.png").exists() else "월별 데이터가 24개 미만이어서 분해를 생략했다."

    text = f"""# {settings.name}({settings.normalized_ticker}) 시계열 분석 리포트

## 1. 분석 주제

{settings.name}의 가격 추세, 수익률, 변동성, 이상치와 거래량 관계를 분석한다. 가격 데이터에서 확인한 관찰과 외부 원인에 대한 가설을 구분한다.

## 2. 분석 질문

1. 장기 추세와 이동평균 기반 국면은 어떻게 변했는가?
2. MA20 ≥ MA60 구간과 반대 구간의 수익률·변동성은 다른가?
3. 급등·급락 이상치는 언제였는가?
4. 거래량 상위 10% 거래일의 절대수익률은 더 큰가?
5. 월별 패턴과 단순 예측 기준선에서 무엇을 확인할 수 있는가?

## 3. 데이터 및 정제

- 제공자: {settings.resolved_provider}
- 티커: {settings.normalized_ticker}
- 요청 기간: {settings.start} ~ {settings.end}
- 실제 기간: {audit['start_date']} ~ {audit['end_date']}
- 관측치: {audit['rows_after_cleaning']:,}개
- 분석 가격: {audit['price_basis']}
- 중복 제거: {audit['duplicate_dates_removed']}개
- 잘못된 OHLC 행: {audit['invalid_ohlc_rows']}개
- 휴장일: 보간하지 않음
- 이상치: 삭제하지 않고 IQR 기준으로 표시

## 4. 핵심 시각화

![가격과 이동평균](images/01_price_and_moving_averages.png)

![수익률 이상치](images/02_daily_returns_outliers.png)

![변동성과 거래량](images/03_volatility_and_volume.png)

![월별 수익률](images/04_monthly_return_heatmap.png)

## 5. 인사이트

### 1) 장기 변화

- **관찰:** 분석 가격은 {metrics['start_close']:,.2f}에서 {metrics['end_close']:,.2f}로 변해 {metrics['price_change_pct']:.2f}% 변화했다.
- **해석:** 장기 방향은 확인되지만 가격만으로 기업 실적이나 거시경제 요인을 인과적으로 확정할 수 없다.
- **행동:** 이동평균 교차 시점의 공시와 시장 지수를 추가로 대조한다.

### 2) 국면별 수익률과 위험

- **관찰:** MA20 ≥ MA60 구간의 평균 일간 수익률은 {up.get('mean_daily_return_pct', float('nan')):.3f}%, 연율화 변동성은 {up.get('annualized_volatility_pct', float('nan')):.2f}%였다. 반대 구간은 {down.get('mean_daily_return_pct', float('nan')):.3f}%, {down.get('annualized_volatility_pct', float('nan')):.2f}%였다.
- **해석:** 이동평균은 후행지표이며 가격으로부터 계산되므로 독립적인 원인 변수가 아니다.
- **행동:** 동일 기준을 시장 벤치마크에 적용해 개별 종목 효과와 비교한다.

### 3) 이상 움직임

- **관찰:** IQR 이상치는 {metrics['outlier_days']}일이었다. 최대 상승은 {metrics['best_day']}의 {metrics['best_day_return_pct']:.2f}%, 최대 하락은 {metrics['worst_day']}의 {metrics['worst_day_return_pct']:.2f}%였다.
- **해석:** 실제 사건일 가능성이 있으므로 오류로 간주해 삭제하지 않았다.
- **행동:** 해당 날짜 전후 공시와 뉴스를 별도로 확인한다.

### 4) 거래량과 변동

- **관찰:** 거래량 상위 10%의 평균 절대수익률은 {high:.3f}%, 나머지는 {normal:.3f}%로 약 {ratio:.2f}배였다.
- **해석:** 동시 움직임은 거래량이 가격 변동의 원인이라는 뜻이 아니다.
- **행동:** 상승·하락 거래일을 분리해 추가 검정한다.

## 6. 보너스 분석

### 시계열 분해

{decomposition_line}

5년 내외 표본은 연간 계절성을 안정적으로 주장하기에 짧으므로 탐색적으로만 해석한다.

### 기준선 예측

![기준선 예측](images/06_baseline_forecast.png)

| 기준선 | MAE | RMSE | MAPE |
|---|---:|---:|---:|
| 마지막 값 | {forecast['Last_Value_MAE']:.2f} | {forecast['Last_Value_RMSE']:.2f} | {forecast['Last_Value_MAPE']:.2f}% |
| 드리프트 | {forecast['Drift_MAE']:.2f} | {forecast['Drift_RMSE']:.2f} | {forecast['Drift_MAPE']:.2f}% |

이는 투자 예측 모델이 아니라 복잡한 모델이 넘어야 하는 최소 기준선이다.

## 7. 결론과 한계

분석은 가격·거래량의 통계적 관계를 보여주지만 인과관계를 증명하지 않는다. Yahoo 데이터는 비공식 인터페이스의 호출 제한 또는 정정 가능성이 있으며, 데이터 제공자에 따라 배당·분할 조정 방식이 다르다. 거래비용, 세금, 환율과 외부 변수도 반영하지 않았다.

## 8. AI 사용 로그

| 사용 작업 | 사용 이유 | 검증 방법 |
|---|---|---|
| 데이터 제공자별 수집 코드 | 반복 구현 절감 | 날짜·컬럼·관측치·OHLC 검사 |
| 분석 및 시각화 코드 | 계산 일관성 확보 | 테스트 및 전체 재실행 |
| 인사이트 문장 구조 | 관찰과 해석 분리 | 모든 수치를 metrics.json과 대조 |
| 예측 기준선 | 비교 기준 마련 | 홀드아웃 분리와 오류 지표 재계산 |
"""
    paths["report"].write_text(text, encoding="utf-8")


def run_universal(settings: UniversalSettings, skip_download: bool = False) -> dict[str, object]:
    paths = output_paths(settings)
    if skip_download:
        if not paths["raw"].exists():
            raise FileNotFoundError(f"Cached raw data not found: {paths['raw']}")
        raw = pd.read_csv(paths["raw"])
    else:
        raw = collect_market_data(settings)
    features, audit = prepare_analysis_frame(raw, settings)
    paths["processed"].parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(paths["processed"], index=False, date_format="%Y-%m-%d")
    metric_settings = TwseSettings(
        start=settings.start,
        end=settings.end,
        short_window=settings.short_window,
        long_window=settings.long_window,
        forecast_horizon=settings.forecast_horizon,
    )
    metrics = calculate_metrics(features, audit, metric_settings)
    metrics["ticker"] = settings.normalized_ticker
    metrics["name"] = settings.name
    metrics["provider"] = settings.resolved_provider
    paths["metrics"].write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    create_generic_visualizations(features, settings)
    write_generic_report(metrics, settings)
    return metrics

