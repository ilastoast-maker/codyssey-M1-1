import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from src.universal import UniversalSettings, load_market_data, prepare_analysis_frame


st.set_page_config(page_title="Stock Trend Explorer", layout="wide")
st.title("주식 시계열 탐색 대시보드")
st.caption("티커를 입력해 동일한 분석을 적용합니다. 교육용이며 투자 조언이 아닙니다.")


@st.cache_data(ttl=3600, show_spinner="시장 데이터를 불러오는 중입니다...")
def load_ticker(ticker: str) -> tuple[pd.DataFrame, str]:
    ticker = ticker.strip().upper()
    settings = UniversalSettings(ticker=ticker, name=ticker)
    raw, source = load_market_data(settings)
    featured, _ = prepare_analysis_frame(raw, settings)
    return featured, source


with st.sidebar:
    st.header("종목")
    if "active_ticker" not in st.session_state:
        st.session_state.active_ticker = "2330.TW"
    ticker_input = st.text_input("티커", value=st.session_state.active_ticker, help="예: 2330.TW, NVDA, AAPL, MSFT")
    if st.button("종목 적용", use_container_width=True):
        st.session_state.active_ticker = ticker_input.strip().upper()

try:
    df, data_source = load_ticker(st.session_state.active_ticker)
except Exception as exc:
    st.error(f"데이터를 불러오지 못했습니다: {exc}")
    st.info("기본 종목 2330.TW는 포함된 데이터로 실행할 수 있습니다. 다른 종목은 최초 수집 성공 후 재사용할 수 있습니다.")
    st.stop()

st.subheader(st.session_state.active_ticker)
st.caption(f"출처: {data_source} · 가격은 배당 재투자를 포함한 투자 성과와 다를 수 있습니다.")

min_date, max_date = df["Date"].min().date(), df["Date"].max().date()

with st.sidebar:
    st.header("필터")
    years = sorted(df["Date"].dt.year.unique())
    preset = st.selectbox("기간 빠른 선택", ["전체 기간"] + [f"{year}년" for year in years])
    if preset == "전체 기간":
        default_dates = (min_date, max_date)
    else:
        year = int(preset[:-1])
        year_dates = df.loc[df["Date"].dt.year == year, "Date"]
        default_dates = (year_dates.min().date(), year_dates.max().date())
    selected_dates = st.date_input(
        "분석 기간", value=default_dates, min_value=min_date, max_value=max_date,
        key=f"dates_{st.session_state.active_ticker}_{preset}",
    )
    short_window = st.slider("단기 이동평균", 5, 50, 20)
    long_window = st.slider("장기 이동평균", 40, 200, 60)
    volatility_window = st.slider("변동성 창", 10, 60, 20)

if len(selected_dates) != 2:
    st.info("시작일과 종료일을 모두 선택하세요.")
    st.stop()
start_date, end_date = selected_dates

if short_window >= long_window:
    st.warning("단기 이동평균은 장기 이동평균보다 작아야 합니다.")
    st.stop()

df = df.copy()
# Indicators retain pre-filter history, so changing the visible range does not
# change the same day's moving average or reset its warm-up period.
df["Short_MA"] = df["Close"].rolling(short_window).mean()
df["Long_MA"] = df["Close"].rolling(long_window).mean()
df["Rolling_Volatility"] = df["Daily_Return"].rolling(volatility_window).std() * (252**0.5) * 100
view = df[df["Date"].dt.date.between(start_date, end_date)].copy()
if len(view) < long_window + 2:
    st.warning("선택 기간이 너무 짧습니다. 장기 이동평균보다 충분히 긴 기간을 선택하세요.")
    st.stop()

price_change = (view["Close"].iloc[-1] / view["Close"].iloc[0] - 1) * 100
annual_vol = view["Close"].pct_change(fill_method=None).std() * (252**0.5) * 100
outliers = int(view["Return_Outlier"].sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("거래일", f"{len(view):,}")
c2.metric("기간 가격 변화", f"{price_change:.2f}%")
c3.metric("연율화 변동성", f"{annual_vol:.2f}%")
c4.metric("IQR 이상치", f"{outliers}일")
st.caption("기간 가격 변화·요약 변동성은 선택 기간 내 거래일 기준입니다. 이동평균·롤링 변동성은 이전 이력을 포함하며, 이상치는 전체 데이터의 IQR 기준을 유지합니다.")

tab1, tab2, tab3 = st.tabs(["가격·추세", "수익률·위험", "거래량"])

with tab1:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(view["Date"], view["Close"], label="Close", linewidth=1.2)
    ax.plot(view["Date"], view["Short_MA"], label=f"MA{short_window}")
    ax.plot(view["Date"], view["Long_MA"], label=f"MA{long_window}")
    ax.set_ylabel("Analysis price")
    ax.legend()
    st.pyplot(fig)
    plt.close(fig)

with tab2:
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    axes[0].plot(view["Date"], view["Daily_Return"] * 100, linewidth=0.7)
    marked = view[view["Return_Outlier"]]
    axes[0].scatter(marked["Date"], marked["Daily_Return"] * 100, color="red", s=16)
    axes[0].set_ylabel("Daily return (%)")
    axes[1].plot(view["Date"], view["Rolling_Volatility"], color="orange")
    axes[1].set_ylabel("Annualized volatility (%)")
    st.pyplot(fig)
    plt.close(fig)

with tab3:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(view["Date"], view["Volume"] / 1_000_000, width=1.0)
    ax.set_ylabel("Million shares")
    st.pyplot(fig)
    plt.close(fig)
    top_volume = view.nlargest(10, "Volume")[["Date", "Volume", "Daily_Return"]].copy()
    top_volume["Date"] = top_volume["Date"].dt.strftime("%Y-%m-%d")
    top_volume["Volume"] = top_volume["Volume"].map(lambda value: f"{value:,.0f}")
    top_volume["Daily_Return"] = top_volume["Daily_Return"].map(
        lambda value: "계산 이력 부족" if pd.isna(value) else f"{value * 100:.2f}%"
    )
    st.table(top_volume.rename(columns={"Date": "날짜", "Volume": "거래량(주)", "Daily_Return": "일간 수익률"}).set_index("날짜"))

st.markdown("### 해석 원칙")
st.write("그래프에서 확인한 움직임은 관찰이며, 실적·AI 수요·금리·지정학적 사건은 별도 자료로 검증해야 할 원인 가설입니다.")
