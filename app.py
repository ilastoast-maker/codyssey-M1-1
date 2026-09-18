from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st


DATA_PATH = Path(__file__).parent / "data" / "processed" / "tsmc_features.csv"

st.set_page_config(page_title="TSMC Trend Explorer", layout="wide")
st.title("TSMC(2330.TW) 시계열 탐색 대시보드")
st.caption("TWSE 공식 일별 거래 데이터 · 교육용 분석이며 투자 조언이 아닙니다.")

if not DATA_PATH.exists():
    st.error("처리 데이터가 없습니다. 먼저 `python -m src.pipeline`을 실행하세요.")
    st.stop()

df = pd.read_csv(DATA_PATH, parse_dates=["Date"])
min_date, max_date = df["Date"].min().date(), df["Date"].max().date()

with st.sidebar:
    st.header("필터")
    start_date, end_date = st.date_input(
        "분석 기간", value=(min_date, max_date), min_value=min_date, max_value=max_date
    )
    short_window = st.slider("단기 이동평균", 5, 50, 20)
    long_window = st.slider("장기 이동평균", 40, 200, 60)
    volatility_window = st.slider("변동성 창", 10, 60, 20)

if short_window >= long_window:
    st.warning("단기 이동평균은 장기 이동평균보다 작아야 합니다.")
    st.stop()

view = df[df["Date"].dt.date.between(start_date, end_date)].copy()
if len(view) < long_window + 2:
    st.warning("선택 기간이 너무 짧습니다. 장기 이동평균보다 충분히 긴 기간을 선택하세요.")
    st.stop()

view["Short_MA"] = view["Close"].rolling(short_window).mean()
view["Long_MA"] = view["Close"].rolling(long_window).mean()
view["Rolling_Volatility"] = view["Daily_Return"].rolling(volatility_window).std() * (252**0.5) * 100

price_change = (view["Close"].iloc[-1] / view["Close"].iloc[0] - 1) * 100
annual_vol = view["Daily_Return"].std() * (252**0.5) * 100
outliers = int(view["Return_Outlier"].sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("거래일", f"{len(view):,}")
c2.metric("기간 가격 변화", f"{price_change:.2f}%")
c3.metric("연율화 변동성", f"{annual_vol:.2f}%")
c4.metric("IQR 이상치", f"{outliers}일")

tab1, tab2, tab3 = st.tabs(["가격·추세", "수익률·위험", "거래량"])

with tab1:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(view["Date"], view["Close"], label="Close", linewidth=1.2)
    ax.plot(view["Date"], view["Short_MA"], label=f"MA{short_window}")
    ax.plot(view["Date"], view["Long_MA"], label=f"MA{long_window}")
    ax.set_ylabel("TWD")
    ax.legend()
    st.pyplot(fig)

with tab2:
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    axes[0].plot(view["Date"], view["Daily_Return"] * 100, linewidth=0.7)
    marked = view[view["Return_Outlier"]]
    axes[0].scatter(marked["Date"], marked["Daily_Return"] * 100, color="red", s=16)
    axes[0].set_ylabel("Daily return (%)")
    axes[1].plot(view["Date"], view["Rolling_Volatility"], color="orange")
    axes[1].set_ylabel("Annualized volatility (%)")
    st.pyplot(fig)

with tab3:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(view["Date"], view["Volume"] / 1_000_000, width=1.0)
    ax.set_ylabel("Million shares")
    st.pyplot(fig)
    st.dataframe(
        view.nlargest(10, "Volume")[["Date", "Volume", "Daily_Return"]],
        use_container_width=True,
        hide_index=True,
    )

st.markdown("### 해석 원칙")
st.write("그래프에서 확인한 움직임은 관찰이며, 실적·AI 수요·금리·지정학적 사건은 별도 자료로 검증해야 할 원인 가설입니다.")

