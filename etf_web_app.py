"""
ETF 추세 추종 분석기 v4 - 대시보드 버전
실행: streamlit run etf_web_app.py

좌측: 골드크로스 상태인 ETF 자동 추천
우측: 내가 보유한 종목 (매수가 입력 -> 수익률/신호 자동계산)
하단: 클릭한 종목의 상세 차트
"""

import warnings
warnings.filterwarnings('ignore')

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pykrx import stock
from datetime import datetime, timedelta
import json
import os

# ── 페이지 설정 ──────────────────────────────────────────────────
st.set_page_config(
    page_title="ETF 추세 추종 대시보드",
    page_icon="📈",
    layout="wide"
)

# ── 한글 폰트 설정 ──────────────────────────────────────────────
def set_korean_font():
    """로컬 PC와 클라우드(Linux) 환경 모두에서 한글이 보이도록 폰트를 준비합니다."""
    font_candidates = ['Malgun Gothic', 'AppleGothic', 'NanumGothic', 'NanumBarunGothic']
    available = [f.name for f in fm.fontManager.ttflist]

    for font in font_candidates:
        if font in available:
            plt.rcParams['font.family'] = font
            plt.rcParams['axes.unicode_minus'] = False
            return

    # 클라우드(Streamlit Community Cloud 등) 환경 - 위 폰트가 없으면
    # 프로젝트에 폰트 파일을 함께 올려두고 그걸 직접 로드합니다.
    font_path = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic.ttf")
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams['font.family'] = fm.FontProperties(fname=font_path).get_name()
    plt.rcParams['axes.unicode_minus'] = False

set_korean_font()

# ── 보유종목 저장/불러오기 ───────────────────────────────────────
HOLDINGS_FILE = "etf_holdings.json"

def load_holdings():
    if os.path.exists(HOLDINGS_FILE):
        try:
            with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_holdings(holdings: dict):
    with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)

# ── 내장 ETF 목록 (추세추종 스캔 대상) ────────────────────────────
COMMON_ETFS = {
    # ── 국내 지수 ──────────────────────────────────────────────
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "278530": "KODEX 200TR",
    # ── 국내 섹터 ──────────────────────────────────────────────
    "091160": "KODEX 반도체",
    "305720": "KODEX 2차전지산업",
    "117700": "KODEX 건설",
    "117460": "KODEX 에너지화학",
    "140710": "KODEX 운송",
    "091170": "KODEX 은행",
    "266390": "KODEX 경기소비재",
    "266410": "KODEX 필수소비재",
    "266360": "KODEX K콘텐츠",         # 구 KRX 미디어&엔터테인먼트
    # ── 해외 지수 ──────────────────────────────────────────────
    "379800": "KODEX 미국S&P500",
    "379810": "KODEX 미국나스닥100",
    "314250": "KODEX 미국빅테크10(H)",
    "099140": "KODEX China H",
    "283580": "KODEX 차이나CSI300",
    "101280": "KODEX 일본TOPIX100",
    # ── 원자재/채권 ────────────────────────────────────────────
    "132030": "KODEX 골드선물(H)",
    "308620": "KODEX 미국10년국채선물",
    "153130": "KODEX 단기채권",
    "214980": "KODEX 단기채권PLUS",
    "273130": "KODEX 종합채권(AA-이상)액티브",
}

# ── 인버스/레버리지(곱버스) 자동 제외 키워드 ───────────────────────
# 이름에 아래 키워드가 포함되면 추천·검색 대상에서 항상 제외합니다.
EXCLUDE_KEYWORDS = ["인버스", "레버리지", "2X", "선물인버스", "곱버스"]

def is_excluded(name: str) -> bool:
    return any(kw in name for kw in EXCLUDE_KEYWORDS)

# 내장 목록 자체에서도 제외 키워드에 해당하는 항목은 미리 제거 (이중 안전장치)
COMMON_ETFS = {code: name for code, name in COMMON_ETFS.items() if not is_excluded(name)}

def search_etf(keyword: str) -> dict:
    """내장 ETF 목록에서 검색 (인버스/레버리지는 결과에서 항상 제외)"""
    results = {}
    keyword_lower = keyword.lower().replace(" ", "")
    for code, name in COMMON_ETFS.items():
        if is_excluded(name):
            continue
        if keyword_lower in name.lower().replace(" ", ""):
            results[f"{name} ({code})"] = (name, code)
    return results

# ── 데이터 수집 ──────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def fetch_data(ticker_code: str) -> pd.DataFrame:
    end   = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=300)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker_code)
        return df
    except:
        return pd.DataFrame()

# ── 골드크로스 탐지 ──────────────────────────────────────────────
def find_last_golden_cross(ma5, ma20):
    for i in range(len(ma5) - 1, 0, -1):
        if pd.isna(ma5.iloc[i]) or pd.isna(ma20.iloc[i]):
            continue
        if pd.isna(ma5.iloc[i-1]) or pd.isna(ma20.iloc[i-1]):
            continue
        if ma5.iloc[i-1] <= ma20.iloc[i-1] and ma5.iloc[i] > ma20.iloc[i]:
            return ma5.index[i], i
    return None, None

# ── 신호 계산 ────────────────────────────────────────────────────
def calc_signals(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 120:
        return None

    close = df["종가"]
    ma5   = close.rolling(5).mean()
    ma20  = close.rolling(20).mean()
    ma120 = close.rolling(120).mean()

    last_price = close.iloc[-1]
    last_ma5   = ma5.iloc[-1]
    last_ma20  = ma20.iloc[-1]
    last_ma120 = ma120.iloc[-1]

    cross_5_20   = "골드크로스" if last_ma5 > last_ma20  else "데드크로스"
    cross_20_120 = "골드크로스" if last_ma20 > last_ma120 else "데드크로스"

    # ── 매수/매도 신호 (절충안) ─────────────────────────────────
    # 매수 신호: 5일선 > 20일선 > 120일선 (정배열) - 장기 하락장 진입 방지를 위해 신중하게
    # 매도 신호: 5일선 < 20일선 (5/20 교차) - 손실을 빠르게 차단하기 위해 민감하게
    is_buy_signal  = last_ma5 > last_ma20 > last_ma120   # 정배열일 때만 매수 신호
    is_sell_signal = last_ma5 < last_ma20                 # 5/20 데드크로스면 매도 신호

    if is_buy_signal:
        signal = "🟡 매수신호"
    elif is_sell_signal:
        signal = "🔵 매도신호"
    else:
        signal = "⚪ 보유/관망"   # 5>20이지만 20<120인 경우 (아직 정배열 완성 전)

    if last_ma5 > last_ma20 > last_ma120:
        trend = "📈 강한 상승"
    elif last_ma5 < last_ma20 < last_ma120:
        trend = "📉 강한 하락"
    elif last_ma5 > last_ma20:
        trend = "↗ 단기 상승"
    elif last_ma5 < last_ma20:
        trend = "↘ 단기 하락"
    else:
        trend = "➡ 횡보"

    if len(close) >= 2:
        change     = last_price - close.iloc[-2]
        change_pct = change / close.iloc[-2] * 100
    else:
        change = change_pct = 0

    gc_date, gc_idx = find_last_golden_cross(ma5, ma20)
    if gc_date is not None:
        gc_price  = close.iloc[gc_idx]
        ret_pct   = (last_price - gc_price) / gc_price * 100
        days_held = (close.index[-1] - gc_date).days
    else:
        gc_price = ret_pct = days_held = None
        gc_date  = None

    # ── 추세 경사(기울기) 계산 ──────────────────────────────────
    # MA20의 최근 5거래일 변화율(%)로 "추세가 얼마나 급한지"를 측정
    # (단순 가격 변화 대신 MA20을 쓰면 단기 노이즈가 줄어 더 안정적인 경사 비교가 됨)
    if len(ma20.dropna()) >= 6:
        ma20_now  = ma20.iloc[-1]
        ma20_prev = ma20.iloc[-6]
        slope_pct = (ma20_now - ma20_prev) / ma20_prev * 100
    else:
        slope_pct = 0.0

    return {
        "현재가":         last_price,
        "전일대비":       change,
        "전일대비율":     change_pct,
        "MA5":           last_ma5,
        "MA20":          last_ma20,
        "MA120":         last_ma120,
        "5/20 크로스":   cross_5_20,
        "20/120 크로스": cross_20_120,
        "매수신호":       is_buy_signal,
        "매도신호":       is_sell_signal,
        "신호":           signal,
        "추세":           trend,
        "골드크로스일":   gc_date,
        "매수가":         gc_price,
        "기대수익률":     ret_pct,
        "보유일수":       days_held,
        "추세경사":       slope_pct,
        "_close":  close,
        "_ma5":    ma5,
        "_ma20":   ma20,
        "_ma120":  ma120,
        "_gc_date":  gc_date,
        "_gc_price": gc_price,
    }

# ── 차트 생성 ────────────────────────────────────────────────────
def make_chart(name: str, code: str, data: dict, buy_price: float = None):
    fig, ax = plt.subplots(figsize=(14, 5))

    close = data["_close"].iloc[-90:]
    ma5   = data["_ma5"].iloc[-90:]
    ma20  = data["_ma20"].iloc[-90:]
    ma120 = data["_ma120"].iloc[-90:]

    ax.plot(close.index, close, color="#333333", linewidth=1.5, label="종가",  zorder=3)
    ax.plot(close.index, ma5,   color="#F4A300", linewidth=1.2, label="MA5",   linestyle="--")
    ax.plot(close.index, ma20,  color="#2196F3", linewidth=1.2, label="MA20",  linestyle="-.")
    ax.plot(close.index, ma120, color="#E91E63", linewidth=1.5, label="MA120")

    gc_date  = data["_gc_date"]
    gc_price = data["_gc_price"]
    if gc_date is not None and gc_date in close.index:
        ax.axvline(x=gc_date, color="#FF6600", linewidth=1.5, linestyle=":", alpha=0.8)
        ax.scatter([gc_date], [gc_price], color="#FF6600", zorder=5, s=90,
                   label=f"골드크로스 ({gc_date.strftime('%m/%d')})")

    # 매수가 라인 표시 (보유종목인 경우)
    if buy_price is not None:
        ax.axhline(y=buy_price, color="#9C27B0", linewidth=1.3, linestyle="--", alpha=0.7,
                   label=f"내 매수가 ({buy_price:,.0f}원)")

    is_golden = data["매수신호"]
    ax.set_facecolor("#FFF9E6" if is_golden else "#E8F4FD")
    ax.set_title(f"{name} ({code})  |  최근 90거래일", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", fontsize=10, framealpha=0.85)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.tick_params(axis="x", rotation=30, labelsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    plt.tight_layout()
    return fig

# ════════════════════════════════════════════════════════════════
#  데이터 준비: 전체 ETF 스캔 (캐시 활용, 매 실행마다 재계산하지 않음)
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800)
def scan_all_etfs() -> dict:
    """내장 ETF 전체를 스캔해서 신호 결과 딕셔너리로 반환 (인버스/레버리지 계열은 항상 제외)"""
    results = {}
    for code, name in COMMON_ETFS.items():
        if is_excluded(name):
            continue
        df   = fetch_data(code)
        data = calc_signals(df)
        if data is not None:
            results[code] = {"name": name, "data": data}
    return results

# ════════════════════════════════════════════════════════════════
#  UI 시작
# ════════════════════════════════════════════════════════════════
st.title("📈 ETF 추세 추종 대시보드")
st.caption(f"기준일: {datetime.today().strftime('%Y년 %m월 %d일')}  |  이동평균: MA5 / MA20 / MA120")

# 세션 상태 초기화
if "holdings" not in st.session_state:
    st.session_state.holdings = load_holdings()   # {code: {"name":.., "buy_price":..}}
if "selected_code" not in st.session_state:
    st.session_state.selected_code = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

# 새로고침 버튼
col_refresh, _ = st.columns([1, 5])
with col_refresh:
    if st.button("🔄 전체 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.divider()

# ── 상단: 좌(추천) / 우(보유종목) ───────────────────────────────
top_left, top_right = st.columns(2)

# ─────────────────────────────────────────────────────
# 좌측: 추세추종 추천 종목 (골드크로스 자동 스캔)
# ─────────────────────────────────────────────────────
with top_left:
    st.subheader("🟡 추세추종 추천 종목 (골드크로스 + 급경사 TOP 10)")
    st.caption(f"내장 KODEX ETF {len(COMMON_ETFS)}개(인버스·레버리지 제외) 중 매수신호(5>20>120 정배열) 상태이면서, 최근 5일간 추세 경사(MA20 변화율)가 가장 급한 10개")

    with st.spinner("ETF 전체 스캔 중... (처음 실행 시 1분 정도 걸려요)"):
        scan_results = scan_all_etfs()

    golden_list = {
        code: info for code, info in scan_results.items()
        if info["data"]["매수신호"]
    }

    if not golden_list:
        st.info("현재 매수신호(정배열) 상태인 ETF가 없습니다.")
    else:
        # 추세 경사(MA20 5일 변화율)가 급한 순으로 정렬 -> 상위 10개만
        sorted_golden = sorted(
            golden_list.items(),
            key=lambda x: x[1]["data"]["추세경사"],
            reverse=True
        )[:10]

        for rank, (code, info) in enumerate(sorted_golden, 1):
            name  = info["name"]
            data  = info["data"]
            ret   = data["기대수익률"]
            slope = data["추세경사"]
            ret_str = f"{ret:+.2f}%" if ret is not None else "-"

            btn_label = (
                f"#{rank}  🟡 {name}  |  {data['현재가']:,.0f}원  |  "
                f"경사 {slope:+.2f}%  |  추천수익률 {ret_str}  |  {data['추세']}"
            )
            if st.button(btn_label, key=f"rec_{code}", use_container_width=True):
                st.session_state.selected_code = code
                st.session_state.selected_name = name

# ─────────────────────────────────────────────────────
# 우측: 내 보유 종목
# ─────────────────────────────────────────────────────
with top_right:
    st.subheader("💼 내 보유 종목")

    with st.expander("➕ 보유 종목 추가", expanded=len(st.session_state.holdings) == 0):
        col1, col2 = st.columns(2)
        with col1:
            h_keyword = st.text_input("종목 검색", placeholder="예: TIGER 200", key="holding_search")
        with col2:
            h_code_direct = st.text_input("또는 종목코드 직접입력", placeholder="예: 102110", key="holding_code_direct")

        chosen_code = None
        chosen_name = None

        if h_keyword:
            sr = search_etf(h_keyword)
            if sr:
                sel = st.selectbox("검색 결과 선택", list(sr.keys()), key="holding_select")
                chosen_name, chosen_code = sr[sel]
            else:
                st.warning("검색 결과가 없습니다. 종목코드로 직접 입력해주세요.")

        if h_code_direct.strip():
            code = h_code_direct.strip()
            if code.isdigit() and len(code) == 6:
                chosen_code = code
                chosen_name = COMMON_ETFS.get(code, f"종목_{code}")
            else:
                st.error("종목코드는 숫자 6자리여야 합니다.")

        buy_price_input = st.number_input("매수가격 (원)", min_value=0.0, step=100.0, format="%.0f")

        if st.button("✅ 보유 종목으로 추가", use_container_width=True):
            if chosen_code and buy_price_input > 0:
                st.session_state.holdings[chosen_code] = {
                    "name": chosen_name,
                    "buy_price": buy_price_input
                }
                save_holdings(st.session_state.holdings)
                st.success(f"[{chosen_name}] 매수가 {buy_price_input:,.0f}원으로 추가됨!")
                st.rerun()
            elif not chosen_code:
                st.error("종목을 검색하거나 코드를 입력해주세요.")
            else:
                st.error("매수가격을 입력해주세요.")

    # 보유 종목 리스트 표시
    if not st.session_state.holdings:
        st.info("아직 등록된 보유 종목이 없습니다. 위에서 추가해주세요.")
    else:
        for code, h in list(st.session_state.holdings.items()):
            name      = h["name"]
            buy_price = h["buy_price"]

            df   = fetch_data(code)
            data = calc_signals(df)

            row_col1, row_col2 = st.columns([5, 1])
            with row_col1:
                if data is None:
                    if st.button(f"⚠️ {name} (데이터 부족)", key=f"hold_{code}", use_container_width=True):
                        st.session_state.selected_code = code
                        st.session_state.selected_name = name
                else:
                    cur_price = data["현재가"]
                    profit_pct = (cur_price - buy_price) / buy_price * 100
                    signal_icon = data["신호"]   # 🟡 매수신호 / 🔵 매도신호 / ⚪ 보유·관망
                    profit_icon = "🔺" if profit_pct >= 0 else "🔻"

                    btn_label = (
                        f"{name}  |  현재 {cur_price:,.0f}원  |  "
                        f"수익률 {profit_icon}{profit_pct:+.2f}%  |  {signal_icon}"
                    )
                    if st.button(btn_label, key=f"hold_{code}", use_container_width=True):
                        st.session_state.selected_code = code
                        st.session_state.selected_name = name
            with row_col2:
                if st.button("🗑️", key=f"del_hold_{code}"):
                    del st.session_state.holdings[code]
                    save_holdings(st.session_state.holdings)
                    st.rerun()

st.divider()

# ── 하단: 선택한 종목의 상세 차트 ───────────────────────────────
st.subheader("📊 종목 상세 차트")

if st.session_state.selected_code is None:
    st.info("👆 위에서 추천 종목이나 보유 종목을 클릭하면 여기에 추세추종 차트가 표시됩니다.")
else:
    code = st.session_state.selected_code
    name = st.session_state.selected_name

    df   = fetch_data(code)
    data = calc_signals(df)

    if data is None:
        st.error(f"{name} ({code}) 데이터가 부족하여 차트를 그릴 수 없습니다. (상장 후 120거래일 이상 필요)")
    else:
        # 보유 종목이면 매수가 정보 가져오기
        buy_price = None
        if code in st.session_state.holdings:
            buy_price = st.session_state.holdings[code]["buy_price"]

        # 지표 카드
        ret       = data["기대수익률"]
        days      = data["보유일수"]
        ret_str   = f"{ret:+.2f}% ({days}일 보유)" if ret is not None else "골드크로스 없음"

        if buy_price is not None:
            mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
            my_profit = (data["현재가"] - buy_price) / buy_price * 100
            mcol1.metric("현재가", f"{data['현재가']:,.0f}원",
                         f"{data['전일대비']:+,.0f} ({data['전일대비율']:+.2f}%)")
            mcol2.metric("내 매수가", f"{buy_price:,.0f}원")
            mcol3.metric("내 수익률", f"{my_profit:+.2f}%")
            mcol4.metric("신호", data["신호"])
            mcol5.metric("추세", data["추세"])
        else:
            mcol1, mcol2, mcol3, mcol4 = st.columns(4)
            mcol1.metric("현재가", f"{data['현재가']:,.0f}원",
                         f"{data['전일대비']:+,.0f} ({data['전일대비율']:+.2f}%)")
            mcol2.metric("신호", data["신호"])
            mcol3.metric("추세", data["추세"])
            mcol4.metric("골드크로스 수익률", ret_str)

        fig = make_chart(name, code, data, buy_price=buy_price)
        st.pyplot(fig)
        plt.close(fig)
