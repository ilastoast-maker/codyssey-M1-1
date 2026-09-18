from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from sklearn.metrics import mean_absolute_error, mean_squared_error
from statsmodels.tsa.seasonal import seasonal_decompose


ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw" / "tsmc_2330_tw_2021_2025.csv"
PROCESSED_PATH = ROOT / "data" / "processed" / "tsmc_features.csv"
METRICS_PATH = ROOT / "data" / "processed" / "metrics.json"
IMAGES_DIR = ROOT / "images"
REPORT_PATH = ROOT / "REPORT.md"
TWSE_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"


@dataclass(frozen=True)
class Settings:
    stock_no: str = "2330"
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    short_window: int = 20
    long_window: int = 60
    forecast_horizon: int = 60


def _request_month(year: int, month: int, stock_no: str, retries: int = 4) -> list[list[str]]:
    params = {"response": "json", "date": f"{year}{month:02d}01", "stockNo": stock_no}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                TWSE_URL,
                params=params,
                headers={"User-Agent": "tsmc-time-series-course-project/1.0"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("stat") != "OK":
                raise RuntimeError(f"TWSE response was not OK: {payload.get('stat')}")
            return payload.get("data", [])
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {year}-{month:02d} from TWSE") from last_error


def _roc_date_to_timestamp(value: str) -> pd.Timestamp:
    year, month, day = (int(part) for part in value.split("/"))
    return pd.Timestamp(year=year + 1911, month=month, day=day)


def _number(value: str) -> float:
    cleaned = value.replace(",", "").strip()
    if cleaned in {"", "--", "---"}:
        return np.nan
    return float(cleaned)


def collect_twse_data(settings: Settings, output_path: Path = RAW_PATH) -> pd.DataFrame:
    months = pd.period_range(settings.start, settings.end, freq="M")
    results: dict[tuple[int, int], list[list[str]]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_request_month, period.year, period.month, settings.stock_no): (
                period.year,
                period.month,
            )
            for period in months
        }
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()

    records: list[dict[str, object]] = []
    for key in sorted(results):
        for row in results[key]:
            records.append(
                {
                    "Date": _roc_date_to_timestamp(row[0]),
                    "Volume": _number(row[1]),
                    "Turnover": _number(row[2]),
                    "Open": _number(row[3]),
                    "High": _number(row[4]),
                    "Low": _number(row[5]),
                    "Close": _number(row[6]),
                    "Change": _number(row[7].replace("X", "")),
                    "Trades": _number(row[8]),
                }
            )

    frame = pd.DataFrame.from_records(records).sort_values("Date").drop_duplicates("Date")
    frame = frame.loc[
        frame["Date"].between(pd.Timestamp(settings.start), pd.Timestamp(settings.end))
    ].reset_index(drop=True)
    if len(frame) < 100:
        raise ValueError(f"Expected at least 100 observations, received {len(frame)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False, date_format="%Y-%m-%d")
    return frame


def validate_and_clean(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    df = frame.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    duplicate_count = int(df.duplicated("Date").sum())
    missing_before = {column: int(value) for column, value in df.isna().sum().items()}
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
    df = df.drop_duplicates("Date").sort_values("Date").reset_index(drop=True)

    invalid_price = (
        (df[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        | (df["High"] < df["Low"])
        | (df["High"] < df[["Open", "Close"]].max(axis=1))
        | (df["Low"] > df[["Open", "Close"]].min(axis=1))
    )
    invalid_count = int(invalid_price.sum())
    if invalid_count:
        raise ValueError(f"Found {invalid_count} invalid OHLC rows")

    audit = {
        "rows_after_cleaning": int(len(df)),
        "start_date": df["Date"].min().strftime("%Y-%m-%d"),
        "end_date": df["Date"].max().strftime("%Y-%m-%d"),
        "duplicate_dates_removed": duplicate_count,
        "missing_before_cleaning": missing_before,
        "invalid_ohlc_rows": invalid_count,
        "calendar_gaps_filled": 0,
    }
    return df, audit


def add_features(frame: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    df = frame.copy().set_index("Date")
    df["Daily_Return"] = df["Close"].pct_change(fill_method=None)
    df["Log_Return"] = np.log(df["Close"] / df["Close"].shift(1))
    df["MA20"] = df["Close"].rolling(settings.short_window).mean()
    df["MA60"] = df["Close"].rolling(settings.long_window).mean()
    df["Volatility20"] = df["Daily_Return"].rolling(settings.short_window).std() * math.sqrt(252)
    df["Volume_MA20"] = df["Volume"].rolling(settings.short_window).mean()
    df["Volume_Ratio"] = df["Volume"] / df["Volume_MA20"]

    valid_returns = df["Daily_Return"].dropna()
    q1, q3 = valid_returns.quantile([0.25, 0.75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    df["Return_Outlier"] = (df["Daily_Return"] < lower) | (df["Daily_Return"] > upper)
    df["Regime"] = np.where(
        df["MA20"].isna() | df["MA60"].isna(),
        "Insufficient history",
        np.where(df["MA20"] >= df["MA60"], "MA20 >= MA60", "MA20 < MA60"),
    )
    return df.reset_index()


def forecast_baselines(df: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, dict[str, float]]:
    if len(df) <= horizon + 2:
        raise ValueError("Not enough observations for the requested holdout horizon")
    train = df.iloc[:-horizon].copy()
    test = df.iloc[-horizon:].copy()
    last_value = float(train["Close"].iloc[-1])
    steps = np.arange(1, horizon + 1)
    daily_drift = (float(train["Close"].iloc[-1]) - float(train["Close"].iloc[0])) / (len(train) - 1)
    result = test[["Date", "Close"]].copy()
    result["Last_Value"] = last_value
    result["Drift"] = last_value + daily_drift * steps

    metrics: dict[str, float] = {}
    for column in ("Last_Value", "Drift"):
        metrics[f"{column}_MAE"] = float(mean_absolute_error(result["Close"], result[column]))
        metrics[f"{column}_RMSE"] = float(
            mean_squared_error(result["Close"], result[column]) ** 0.5
        )
        metrics[f"{column}_MAPE"] = float(
            np.mean(np.abs((result["Close"] - result[column]) / result["Close"])) * 100
        )
    return result, metrics


def calculate_metrics(df: pd.DataFrame, audit: dict[str, object], settings: Settings) -> dict[str, object]:
    returns = df["Daily_Return"].dropna()
    volume_threshold = float(df["Volume"].quantile(0.90))
    high_volume = df["Volume"] >= volume_threshold
    regime_stats = (
        df.loc[df["Regime"] != "Insufficient history"]
        .groupby("Regime")["Daily_Return"]
        .agg(["count", "mean", "std"])
        .fillna(0)
    )
    forecast_df, forecast_metrics = forecast_baselines(df, settings.forecast_horizon)

    metrics: dict[str, object] = {
        "audit": audit,
        "start_close": float(df["Close"].iloc[0]),
        "end_close": float(df["Close"].iloc[-1]),
        "price_change_pct": float((df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100),
        "annualized_volatility_pct": float(returns.std() * math.sqrt(252) * 100),
        "best_day": df.loc[df["Daily_Return"].idxmax(), "Date"].strftime("%Y-%m-%d"),
        "best_day_return_pct": float(returns.max() * 100),
        "worst_day": df.loc[df["Daily_Return"].idxmin(), "Date"].strftime("%Y-%m-%d"),
        "worst_day_return_pct": float(returns.min() * 100),
        "outlier_days": int(df["Return_Outlier"].sum()),
        "volume_threshold": volume_threshold,
        "high_volume_abs_return_pct": float(df.loc[high_volume, "Daily_Return"].abs().mean() * 100),
        "normal_volume_abs_return_pct": float(df.loc[~high_volume, "Daily_Return"].abs().mean() * 100),
        "regime_stats": {
            index: {
                "count": int(row["count"]),
                "mean_daily_return_pct": float(row["mean"] * 100),
                "annualized_volatility_pct": float(row["std"] * math.sqrt(252) * 100),
            }
            for index, row in regime_stats.iterrows()
        },
        "forecast": forecast_metrics,
        "forecast_start": forecast_df["Date"].min().strftime("%Y-%m-%d"),
        "forecast_end": forecast_df["Date"].max().strftime("%Y-%m-%d"),
    }
    return metrics


def _save_figure(filename: str) -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(IMAGES_DIR / filename, dpi=160, bbox_inches="tight")
    plt.close()


def create_visualizations(df: pd.DataFrame, settings: Settings) -> None:
    sns.set_theme(style="whitegrid")

    plt.figure(figsize=(13, 6))
    plt.plot(df["Date"], df["Close"], label="Close", linewidth=1.2)
    plt.plot(df["Date"], df["MA20"], label="MA20", linewidth=1.0)
    plt.plot(df["Date"], df["MA60"], label="MA60", linewidth=1.2)
    plt.title("TSMC (2330.TW) Close and Moving Averages")
    plt.ylabel("TWD")
    plt.legend()
    _save_figure("01_price_and_moving_averages.png")

    outliers = df[df["Return_Outlier"]]
    plt.figure(figsize=(13, 5))
    plt.plot(df["Date"], df["Daily_Return"] * 100, color="#4C78A8", linewidth=0.7)
    plt.scatter(outliers["Date"], outliers["Daily_Return"] * 100, color="#E45756", s=18, label="IQR outlier")
    plt.axhline(0, color="black", linewidth=0.7)
    plt.title("Daily Returns and IQR Outliers")
    plt.ylabel("Return (%)")
    plt.legend()
    _save_figure("02_daily_returns_outliers.png")

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(df["Date"], df["Volatility20"] * 100, color="#F58518")
    axes[0].set_title("20-Day Rolling Annualized Volatility")
    axes[0].set_ylabel("Volatility (%)")
    axes[1].bar(df["Date"], df["Volume"] / 1_000_000, color="#54A24B", width=1.0)
    axes[1].plot(df["Date"], df["Volume_MA20"] / 1_000_000, color="black", linewidth=1.0, label="20-day average")
    axes[1].set_ylabel("Volume (million shares)")
    axes[1].legend()
    _save_figure("03_volatility_and_volume.png")

    monthly = df.set_index("Date")["Close"].resample("ME").last().pct_change(fill_method=None) * 100
    heat = monthly.to_frame("return")
    heat["Year"] = heat.index.year
    heat["Month"] = heat.index.month
    pivot = heat.pivot(index="Year", columns="Month", values="return")
    plt.figure(figsize=(12, 4.5))
    sns.heatmap(pivot, annot=True, fmt=".1f", center=0, cmap="RdYlGn", cbar_kws={"label": "%"})
    plt.title("Monthly Close-to-Close Returns (%)")
    _save_figure("04_monthly_return_heatmap.png")

    monthly_close = df.set_index("Date")["Close"].resample("ME").last().dropna()
    decomposition = seasonal_decompose(monthly_close, model="additive", period=12, extrapolate_trend="freq")
    fig = decomposition.plot()
    fig.set_size_inches(13, 9)
    fig.suptitle("Monthly Price Decomposition (Additive, Period=12)", y=1.01)
    _save_figure("05_seasonal_decomposition.png")

    forecast_df, _ = forecast_baselines(df, settings.forecast_horizon)
    history = df.iloc[-(settings.forecast_horizon + 120) : -settings.forecast_horizon]
    plt.figure(figsize=(13, 6))
    plt.plot(history["Date"], history["Close"], color="#9D9D9D", label="Train history")
    plt.plot(forecast_df["Date"], forecast_df["Close"], color="black", linewidth=2, label="Actual holdout")
    plt.plot(forecast_df["Date"], forecast_df["Last_Value"], linestyle="--", label="Last-value baseline")
    plt.plot(forecast_df["Date"], forecast_df["Drift"], linestyle=":", label="Drift baseline")
    plt.axvline(forecast_df["Date"].iloc[0], color="#E45756", linewidth=1)
    plt.title(f"Baseline Forecast Evaluation ({settings.forecast_horizon}-Trading-Day Holdout)")
    plt.ylabel("TWD")
    plt.legend()
    _save_figure("06_baseline_forecast.png")


def write_report(metrics: dict[str, object], settings: Settings) -> None:
    audit = metrics["audit"]
    regimes = metrics["regime_stats"]
    bullish = regimes.get("MA20 >= MA60", {})
    bearish = regimes.get("MA20 < MA60", {})
    forecast = metrics["forecast"]
    high = metrics["high_volume_abs_return_pct"]
    normal = metrics["normal_volume_abs_return_pct"]
    volume_ratio = high / normal if normal else float("nan")

    report = f"""# TSMC 2021~2025년 주가 트렌드 분석 리포트

## 1. 분석 주제 및 선정 이유

이 프로젝트는 대만증권거래소에 상장된 TSMC 본주(2330.TW)의 일별 가격과 거래량을 이용해 장기 추세, 변동성, 이상 움직임과 거래량의 관계를 분석한다. AI 반도체 성장기가 포함된 기간이지만, 가격 데이터만으로 원인을 확정하지 않고 **관찰 가능한 사실**과 **가능한 해석**을 구분한다.

## 2. 분석 질문

1. 2021~2025년에 장기 추세와 이동평균 기반 시장 국면은 어떻게 변했는가?
2. MA20이 MA60 이상인 구간과 미만인 구간의 수익률·변동성은 어떻게 다른가?
3. 가장 큰 급등·급락과 IQR 이상치는 언제였는가?
4. 거래량 상위 10% 거래일의 절대수익률은 나머지 거래일보다 큰가?
5. 월별 수익률에 반복되는 패턴이 보이는가?
6. 단순 예측 기준선이 마지막 60거래일을 어느 정도 설명하는가?

## 3. 데이터 설명 및 정제

- 출처: [Taiwan Stock Exchange Corporation, STOCK_DAY](https://www.twse.com.tw/exchangeReport/STOCK_DAY)
- 종목: TSMC(2330.TW)
- 조회 기간: {settings.start} ~ {settings.end}
- 실제 관측 기간: {audit['start_date']} ~ {audit['end_date']}
- 데이터 포인트: {audit['rows_after_cleaning']:,}개 거래일
- 컬럼: 시가, 고가, 저가, 종가, 거래량, 거래대금, 전일 대비 변화, 거래 건수
- 중복 날짜 제거: {audit['duplicate_dates_removed']}개
- 잘못된 OHLC 행: {audit['invalid_ohlc_rows']}개
- 휴장일 보간: 하지 않음. 휴장일은 오류나 결측 관측치가 아니므로 인위적으로 채우지 않았다.
- 이상치: 삭제하지 않고 IQR 기준으로 표시했다. 실제 시장 충격일 가능성이 있기 때문이다.

이 분석은 TWSE 공식 **종가**를 사용한다. 현금배당을 재투자한 수정주가·총수익률 분석이 아니므로 배당락의 영향이 일부 포함될 수 있다.

## 4. 분석 방법

- 20일·60일 단순이동평균으로 단기/중기 추세 비교
- 일간 수익률 및 월말 종가 기준 월간 수익률
- 20일 수익률 표준편차를 252거래일 기준으로 연율화한 변동성
- IQR(1.5×IQR) 기준 일간 수익률 이상치 탐지
- 거래량 상위 10%와 나머지 구간의 평균 절대수익률 비교
- 월별 종가를 이용한 가법 계절 분해(주기 12개월)
- 마지막 {settings.forecast_horizon}거래일 홀드아웃에 대한 마지막 값·드리프트 기준선 예측

## 5. 시각화 및 결과

### 5.1 가격과 이동평균

![TSMC 가격 및 이동평균](images/01_price_and_moving_averages.png)

### 5.2 일간 수익률과 이상치

![일간 수익률 이상치](images/02_daily_returns_outliers.png)

### 5.3 변동성과 거래량

![롤링 변동성과 거래량](images/03_volatility_and_volume.png)

### 5.4 월별 수익률

![월별 수익률 히트맵](images/04_monthly_return_heatmap.png)

## 6. 인사이트

### 인사이트 1 — 장기 가격 변화와 추세 국면

- **관찰(Fact):** 첫 종가 {metrics['start_close']:,.2f} TWD에서 마지막 종가 {metrics['end_close']:,.2f} TWD로 변해 기간 가격 변화율은 {metrics['price_change_pct']:.2f}%였다. MA20 ≥ MA60 구간은 {bullish.get('count', 0):,}거래일, 반대 구간은 {bearish.get('count', 0):,}거래일이었다.
- **해석(Hypothesis):** 이동평균 우위 구간은 상승 모멘텀이 지속된 시기를 표시할 수 있지만, 이동평균은 후행지표이므로 전환 원인을 설명하지 못한다.
- **행동(Action):** 주요 교차 시점 주변의 분기 실적·가이던스·산업 수요 자료를 별도로 대조한다.

### 인사이트 2 — 시장 국면별 수익률과 위험

- **관찰(Fact):** MA20 ≥ MA60 구간의 평균 일간 수익률은 {bullish.get('mean_daily_return_pct', float('nan')):.3f}%, 연율화 변동성은 {bullish.get('annualized_volatility_pct', float('nan')):.2f}%였다. MA20 < MA60 구간은 각각 {bearish.get('mean_daily_return_pct', float('nan')):.3f}%, {bearish.get('annualized_volatility_pct', float('nan')):.2f}%였다.
- **해석(Hypothesis):** 두 구간 차이는 추세와 위험의 동행 가능성을 보여주지만, 이동평균 자체가 가격으로 계산되므로 독립적인 인과 설명은 아니다.
- **행동(Action):** 동일 기준을 대만 가권지수에 적용해 TSMC 고유 효과와 시장 공통 효과를 분리한다.

### 인사이트 3 — 급등·급락은 삭제할 오류가 아니다

- **관찰(Fact):** IQR 기준 이상치는 {metrics['outlier_days']}일이었다. 최대 상승은 {metrics['best_day']}의 {metrics['best_day_return_pct']:.2f}%, 최대 하락은 {metrics['worst_day']}의 {metrics['worst_day_return_pct']:.2f}%였다. 전체 연율화 변동성은 {metrics['annualized_volatility_pct']:.2f}%였다.
- **해석(Hypothesis):** 이 날짜들은 실적, 거시경제 또는 지정학적 뉴스 후보일 수 있으나 가격 데이터만으로 원인을 확정할 수 없다.
- **행동(Action):** 해당 날짜 전후 공시와 신뢰 가능한 뉴스 타임라인을 확인한다.

### 인사이트 4 — 거래량과 가격 움직임

- **관찰(Fact):** 거래량 상위 10% 거래일의 평균 절대수익률은 {high:.3f}%, 나머지는 {normal:.3f}%로, 전자가 약 {volume_ratio:.2f}배였다.
- **해석(Hypothesis):** 거래 집중일에 정보 반영과 가격 재평가가 함께 나타났을 가능성이 있다. 그러나 동시 움직임은 거래량이 변동의 원인임을 뜻하지 않는다.
- **행동(Action):** 상승일과 하락일을 분리하고 거래량 급증 전후 수익률을 비교한다.

## 7. 보너스 1 — 탐색형 대시보드

`streamlit run app.py`로 실행한다. 날짜 범위, 단기·장기 이동평균, 변동성 창을 바꾸며 가격·수익률·위험·거래량을 탐색할 수 있다. 제출 시 로컬 실행 화면 녹화 또는 스크린샷 세트를 추가하면 된다.

## 8. 보너스 2 — 시계열 심화

### 8.1 월별 시계열 분해

![계절 분해](images/05_seasonal_decomposition.png)

- **관찰:** 월말 종가를 추세·12개월 계절 성분·잔차로 분해했다.
- **한계:** 5년은 연간 계절성을 안정적으로 주장하기에 짧고, 주가는 고정된 계절성이 약한 비정상 시계열이다. 분해 결과는 탐색적 표현으로만 사용한다.

### 8.2 단순 기준선 예측

![기준선 예측](images/06_baseline_forecast.png)

마지막 {settings.forecast_horizon}거래일({metrics['forecast_start']}~{metrics['forecast_end']})을 훈련에서 제외했다.

| 모델 | MAE(TWD) | RMSE(TWD) | MAPE |
|---|---:|---:|---:|
| 마지막 값 유지 | {forecast['Last_Value_MAE']:.2f} | {forecast['Last_Value_RMSE']:.2f} | {forecast['Last_Value_MAPE']:.2f}% |
| 선형 드리프트 | {forecast['Drift_MAE']:.2f} | {forecast['Drift_RMSE']:.2f} | {forecast['Drift_MAPE']:.2f}% |

이는 투자 예측 모델이 아니라 복잡한 모델이 최소한 넘어야 하는 기준선이다. 단일 홀드아웃 결과는 시기에 민감하며 거래비용, 배당, 외부 변수를 반영하지 않는다.

## 9. 결론 및 한계점

가격·수익률·롤링 변동성·거래량을 결합하면 단순 가격 그래프보다 시장 국면을 구체적으로 설명할 수 있다. 그러나 본 분석은 관찰 연구이며 기업 실적, 환율, 금리, 산업 지수, 지정학적 사건을 직접 모델링하지 않았다. 또한 수정주가가 아닌 TWSE 종가를 사용했고, 계절성과 예측은 짧은 표본에 기반한다. 따라서 결과를 매매 신호나 인과관계로 해석해서는 안 된다.

## 10. AI 사용 로그

| 사용 작업 | 사용 이유 | 검증 방법 |
|---|---|---|
| TWSE 수집·정제 코드 초안 | 반복 구현 시간 절감 | 데이터 개수·날짜·중복·결측·OHLC 논리 검사 |
| 이동평균·수익률·변동성 코드 | 계산 대안 탐색 | 정의를 코드에 명시하고 표본 행을 수동 대조 |
| 시각화 코드 | 일관된 그래프 생성 | 축·단위·기간·파일 링크 확인 |
| 인사이트 문장 구조 | 관찰과 해석 분리 | 모든 수치가 metrics.json에서 재생성되는지 확인 |
| 예측 기준선 구현 | 누수 없는 비교 기준 마련 | 마지막 60일을 완전히 분리하고 MAE/RMSE/MAPE 재계산 |

AI가 제안한 결과를 그대로 사실로 채택하지 않았으며, 수치 결과는 전체 파이프라인 재실행으로 검증했다.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def run(skip_download: bool = False) -> dict[str, object]:
    settings = Settings()
    if skip_download:
        if not RAW_PATH.exists():
            raise FileNotFoundError(f"Raw data not found: {RAW_PATH}")
        raw = pd.read_csv(RAW_PATH)
    else:
        raw = collect_twse_data(settings)
    clean, audit = validate_and_clean(raw)
    featured = add_features(clean, settings)
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    featured.to_csv(PROCESSED_PATH, index=False, date_format="%Y-%m-%d")
    metrics = calculate_metrics(featured, audit, settings)
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    create_visualizations(featured, settings)
    write_report(metrics, settings)
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the TSMC time-series analysis pipeline")
    parser.add_argument("--skip-download", action="store_true", help="Reuse the checked-in raw CSV")
    args = parser.parse_args()
    output = run(skip_download=args.skip_download)
    print(json.dumps(output, ensure_ascii=False, indent=2))

