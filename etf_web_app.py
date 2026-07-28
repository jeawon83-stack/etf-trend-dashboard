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
import sqlite3
from datetime import datetime, timedelta
import json
import os
import requests
from google.oauth2.service_account import Credentials
import gspread

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

    font_path = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic.ttf")
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams['font.family'] = fm.FontProperties(fname=font_path).get_name()
    plt.rcParams['axes.unicode_minus'] = False

set_korean_font()

# ── Google Sheets 연동 ───────────────────────────────────────────
SPREADSHEET_ID = "11CsPSsBYxb9xdHD4DUgF644unusLJ1xw4RlNSI6RII8"
SHEET_NAME     = "note"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]

@st.cache_resource
def get_gsheet():
    """Google Sheets 클라이언트 연결 (Streamlit Secrets에서 인증 정보 읽기)"""
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=SCOPES
        )
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        return sheet
    except Exception as e:
        st.warning(f"Google Sheets 연결 실패: {e}\n로컬 파일로 대체 실행합니다.")
        return None

def load_holdings() -> dict:
    """Google Sheets에서 보유종목 불러오기. 실패 시 로컬 파일로 폴백."""
    sheet = get_gsheet()
    if sheet:
        try:
            rows = sheet.get_all_records()   # [{"종목코드":..,"종목명":..,"매수가":..}, ...]
            return {
                str(r["종목코드"]): {
                    "name":      str(r["종목명"]),
                    "buy_price": float(r["매수가"])
                }
                for r in rows if r.get("종목코드")
            }
        except Exception as e:
            st.warning(f"시트 읽기 오류: {e}")
    # 폴백: 로컬 파일
    if os.path.exists("etf_holdings.json"):
        try:
            with open("etf_holdings.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_holdings(holdings: dict):
    """Google Sheets에 보유종목 저장. 실패 시 로컬 파일로 폴백."""
    sheet = get_gsheet()
    if sheet:
        try:
            # 시트 전체를 덮어쓰기 (헤더 + 데이터)
            rows = [["종목코드", "종목명", "매수가"]]
            for code, h in holdings.items():
                rows.append([code, h["name"], h["buy_price"]])
            sheet.clear()
            sheet.update(rows, "A1")
            return
        except Exception as e:
            st.warning(f"시트 저장 오류: {e}")
    # 폴백: 로컬 파일
    with open("etf_holdings.json", "w", encoding="utf-8") as f:
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
    "091180": "KODEX 자동차",
    "266390": "KODEX 경기소비재",
    "266410": "KODEX 필수소비재",
    "266360": "KODEX K콘텐츠",         # 구 KRX 미디어&엔터테인먼트
    "445290": "KODEX 로봇액티브",
    "495850": "KODEX 코리아밸류업",
    "0038A0": "KODEX 미국휴머노이드로봇",
    "0080G0": "KODEX 방산TOP10",
    # ── 해외 지수 ──────────────────────────────────────────────
    "379800": "KODEX 미국S&P500",
    "379810": "KODEX 미국나스닥100",
    "314250": "KODEX 미국빅테크10(H)",
    "487230": "KODEX 미국AI전력핵심인프라",
    "390390": "KODEX 미국반도체",
    "0167Z0": "KODEX 미국우주항공",
    "099140": "KODEX China H",
    "283580": "KODEX 차이나CSI300",
    "101280": "KODEX 일본TOPIX100",
    "251350": "KODEX MSCI선진국",
    # ── 원자재/채권 ────────────────────────────────────────────
    "132030": "KODEX 골드선물(H)",
    "471230": "KODEX 국고채10년액티브",
    "308620": "KODEX 미국10년국채선물",
    "153130": "KODEX 단기채권",
    "214980": "KODEX 단기채권PLUS",
    "273130": "KODEX 종합채권(AA-이상)액티브",
    "144600": "KODEX 은선물(H)",
}

# ── 레버리지(항상 제외) / 인버스(토글로 제어) 키워드 분리 ───────────────
# 레버리지·2배(곱버스)는 위험도가 높아 토글과 무관하게 항상 제외합니다.
# 1배 인버스는 하락장 대응용으로 토글을 켜면 포함할 수 있습니다.
LEVERAGE_KEYWORDS = ["레버리지", "2X", "곱버스"]
INVERSE_KEYWORDS  = ["인버스"]  # "선물인버스" 등도 이 substring으로 함께 걸러짐

def is_excluded(name: str, include_inverse: bool = False) -> bool:
    if any(kw in name for kw in LEVERAGE_KEYWORDS):
        return True  # 레버리지/2배는 토글과 무관하게 항상 제외
    if not include_inverse and any(kw in name for kw in INVERSE_KEYWORDS):
        return True
    return False

# 내장 목록 자체에서도 레버리지는 미리 제거 (이중 안전장치, 폴백용 목록이라 인버스는 유지)
# ※ KRX API/DB 조회가 실패할 때를 대비한 "폴백(fallback)" 목록으로만 사용됩니다.
COMMON_ETFS = {code: name for code, name in COMMON_ETFS.items()
               if not any(kw in name for kw in LEVERAGE_KEYWORDS)}

# ── KRX 데이터 DB (수집기가 미리 채워둔 데이터를 읽기만 함) ──────────────
# krx_data_collector.py 를 주기적으로 실행하면 이 DB(etf_data.db)가 자동으로
# 최신 상태로 갱신됩니다. 앱은 실시간으로 KRX/pykrx를 호출하지 않고
# 이 로컬 DB만 읽기 때문에 훨씬 빠르고 안정적으로 동작합니다.
# (최근 약 500일치만 유지하는 "롤링 윈도우" 방식이라 파일 용량이 크게 늘지 않습니다)
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_data.db")

def get_db_conn():
    """DB 연결 반환. DB 파일이 없으면 None"""
    if os.path.exists(DB_FILE):
        return sqlite3.connect(DB_FILE)
    return None

@st.cache_data(ttl=1800)
def get_etf_universe_from_db():
    """DB에 저장된 가장 최근 기준일 기준 ETF 목록 조회.
    반환: (딕셔너리 또는 None, 기준일 또는 None)"""
    conn = get_db_conn()
    if conn is None:
        return None, None
    try:
        last_dd = conn.execute("SELECT MAX(bas_dd) FROM etf_daily").fetchone()[0]
        if not last_dd:
            return None, None
        rows = conn.execute(
            "SELECT isu_cd, isu_nm FROM etf_daily WHERE bas_dd = ?", (last_dd,)
        ).fetchall()
        return {code: name for code, name in rows}, last_dd
    except Exception:
        return None, None
    finally:
        conn.close()

def get_etf_universe() -> dict:
    """현재 앱 전체에서 사용할 ETF 유니버스 (DB 우선, 없으면 내장 목록으로 폴백)"""
    universe, _ = get_etf_universe_from_db()
    return universe if universe else COMMON_ETFS

def get_etf_universe_status():
    """화면 표시용 (소스라벨, 기준일, 안내메시지) 반환"""
    universe, last_dd = get_etf_universe_from_db()
    if universe:
        return "KRX DB", last_dd, None
    return "내장 목록(폴백)", None, "etf_data.db 파일이 없습니다. krx_data_collector.py를 먼저 실행해주세요."

def search_etf(keyword: str) -> dict:
    """ETF 유니버스(DB 또는 폴백)에서 검색 (레버리지는 항상 제외, 인버스는 토글에 따름)"""
    results = {}
    include_inverse = st.session_state.get("include_inverse", False)
    keyword_lower = keyword.lower().replace(" ", "")
    for code, name in get_etf_universe().items():
        if is_excluded(name, include_inverse=include_inverse):
            continue
        if keyword_lower in name.lower().replace(" ", ""):
            results[f"{name} ({code})"] = (name, code)
    return results

# ── 데이터 수집 (DB에서 읽기) ────────────────────────────────────────
@st.cache_data(ttl=1800)
def fetch_data(ticker_code: str) -> pd.DataFrame:
    """DB에서 종목 일별 시세 조회"""
    conn = get_db_conn()
    if conn is None:
        return pd.DataFrame()
    try:
        rows = conn.execute("""
            SELECT bas_dd, cls_prc, opn_prc, hgh_prc, low_prc, trd_vol, fluc_rt
            FROM etf_daily
            WHERE isu_cd = ? AND cls_prc IS NOT NULL
            ORDER BY bas_dd ASC
        """, (ticker_code,)).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["날짜", "종가", "시가", "고가", "저가", "거래량", "등락률"])
        df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d")
        df = df.set_index("날짜")
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

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
        gc_trading_days_ago = (len(close) - 1) - gc_idx  # 영업일 기준 경과일 (0 = 오늘 크로스)
    else:
        gc_price = ret_pct = days_held = None
        gc_date  = None
        gc_trading_days_ago = None

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
        "골드크로스경과영업일": gc_trading_days_ago,
        "추세경사":       slope_pct,
        "_close":  close,
        "_ma5":    ma5,
        "_ma20":   ma20,
        "_ma120":  ma120,
        "_gc_date":  gc_date,
        "_gc_price": gc_price,
    }

# ── UI 헬퍼: 배지(badge) & 반응형 지표 카드 ─────────────────────────
def render_badge(text: str, fg: str, bg: str) -> str:
    return (
        f"<span style='background:{bg}; color:{fg}; padding:3px 11px; "
        f"border-radius:12px; font-size:0.85rem; font-weight:600; "
        f"white-space:nowrap; display:inline-block;'>{text}</span>"
    )

def signal_badge(signal_text: str) -> str:
    """매매신호(매수/매도/보유 등)를 색깔 배지로 (텍스트에 이미 아이콘이 포함돼 있으므로 추가하지 않음)"""
    if "매수" in signal_text:
        return render_badge(signal_text, "#0a7d2c", "#e3f7e8")
    elif "매도" in signal_text:
        return render_badge(signal_text, "#0b5fc7", "#e5f0fd")
    else:
        return render_badge(signal_text, "#555555", "#ececec")

def stop_loss_badge(is_danger: bool) -> str:
    """손절 필요 여부를 색깔 배지로"""
    if is_danger:
        return render_badge("🚨 손절 필요", "#b3261e", "#fbe4e2")
    return render_badge("✅ 정상", "#0a7d2c", "#e3f7e8")

def metric_card(label: str, value: str, sub: str = None, sub_color: str = None) -> str:
    """지표 카드 한 칸의 HTML (st.metric 대체, 폭에 맞춰 자동 줄바꿈)"""
    sub_html = ""
    if sub:
        color = sub_color or "inherit"
        sub_html = f"<div style='font-size:0.72rem; color:{color}; margin-top:2px;'>{sub}</div>"
    return (
        "<div style='background:rgba(120,120,120,0.06); border-radius:8px; "
        "padding:9px 12px; min-width:0;'>"
        f"<div style='font-size:0.75rem; opacity:0.65; white-space:nowrap;'>{label}</div>"
        f"<div style='font-size:1.05rem; font-weight:700; word-break:break-word; margin-top:2px;'>{value}</div>"
        f"{sub_html}</div>"
    )

def metric_grid(cards: list) -> str:
    """카드 리스트를 화면 폭에 따라 자동으로 열 개수가 조절되는 그리드로 묶음"""
    cards_html = "".join(cards)
    return (
        "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(115px, 1fr)); "
        f"gap:8px; margin:8px 0 16px 0;'>{cards_html}</div>"
    )

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
        # 손절 기준선 (-5%)
        stop_loss_price = buy_price * 0.95
        ax.axhline(y=stop_loss_price, color="#F44336", linewidth=1.3, linestyle=":", alpha=0.8,
                   label=f"손절기준 -5% ({stop_loss_price:,.0f}원)")

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
#  Gemini AI 애널리스트 코멘트
# ════════════════════════════════════════════════════════════════
GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-3.1-flash-lite"]

def call_gemini(prompt: str) -> str:
    """Gemini API 호출. Streamlit Secrets에 GEMINI_API_KEY가 있어야 동작합니다.
    모델이 지원 중단되어 404가 나는 경우를 대비해 여러 모델명을 순서대로 시도합니다."""
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        return "⚠️ Gemini API 키가 설정되지 않았어요. Streamlit Secrets에 GEMINI_API_KEY를 추가해주세요."

    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            resp = requests.post(
                f"{url}?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": 1500,
                        # 최신 모델(2.5/3.x)은 답변 전 내부 "사고" 과정을 거치는데,
                        # 이를 꺼야 사고 과정이 답변에 섞이거나 토큰을 다 써서 잘리는 문제를 막을 수 있음
                        "thinkingConfig": {"thinkingBudget": 0},
                    },
                },
                timeout=30,
            )
            if resp.status_code == 404:
                last_error = f"{model}: 404 (모델 없음)"
                continue   # 다음 모델로 재시도
            resp.raise_for_status()
            result = resp.json()

            candidate = result["candidates"][0]
            parts = candidate.get("content", {}).get("parts", [])
            # 여러 part가 있을 수 있으므로 text만 모두 합침 (thinking part는 thinkingBudget=0이면 생기지 않음)
            text = "".join(p.get("text", "") for p in parts).strip()

            if not text:
                # 토큰 부족 등으로 본문이 비어있는 경우
                finish_reason = candidate.get("finishReason", "UNKNOWN")
                last_error = f"{model}: 빈 응답 (finishReason={finish_reason})"
                continue

            return text
        except Exception as e:
            last_error = f"{model}: {e}"
            continue

    return f"⚠️ AI 응답 생성 중 오류가 발생했어요: {last_error}"

def build_single_stock_prompt(name: str, code: str, data: dict, buy_price: float = None) -> str:
    """개별 종목용 AI 분석 프롬프트 생성"""
    ret      = data["기대수익률"]
    days     = data["보유일수"]
    ret_str  = f"{ret:+.2f}% ({days}일 보유)" if ret is not None else "골드크로스 기록 없음"
    slope    = data["추세경사"]

    holding_info = ""
    if buy_price is not None:
        my_profit = (data["현재가"] - buy_price) / buy_price * 100
        holding_info = f"""
- 사용자 매수가: {buy_price:,.0f}원
- 사용자 현재 수익률: {my_profit:+.2f}%
- 손절 기준(-5%) 도달 여부: {"예 (손절 검토 필요)" if my_profit <= -5.0 else "아니오"}
"""

    prompt = f"""당신은 한국 ETF 시장을 분석하는 애널리스트입니다. 아래 데이터를 바탕으로 이 종목에 대한 간결한 코멘트를 작성해주세요.

[종목 정보]
- 종목명: {name} ({code})
- 현재가: {data['현재가']:,.0f}원 (전일대비 {data['전일대비']:+,.0f}원, {data['전일대비율']:+.2f}%)
- MA5: {data['MA5']:,.0f} / MA20: {data['MA20']:,.0f} / MA120: {data['MA120']:,.0f}
- 매매 신호: {data['신호']}
- 추세: {data['추세']}
- 최근 5일 추세 경사(MA20 변화율): {slope:+.2f}%
- 직전 골드크로스 시점 매수 가정 수익률: {ret_str}
{holding_info}

[작성 지침]
- 4~6문장 정도의 간결한 한국어로 작성
- 현재 추세와 신호가 의미하는 바를 설명
- 매수/매도/관망 중 어느 쪽에 가까운 상황인지 의견 제시 (단정적 투자 권유가 아닌 데이터 기반 해석으로)
- 투자 손익에 대한 법적 책임이 없는 정보 제공 목적임을 마지막에 짧게 명시
- 과도한 확신이나 자극적 표현은 피하고, 데이터에 기반한 차분한 톤 유지
- 분석 과정이나 검토 메모 없이, 사용자에게 보여줄 최종 코멘트 문장만 바로 작성"""
    return prompt

def build_summary_prompt(sorted_golden: list) -> str:
    """추천 종목 전체 요약용 AI 분석 프롬프트 생성"""
    lines = []
    for rank, (code, info) in enumerate(sorted_golden, 1):
        name = info["name"]
        data = info["data"]
        ret  = data["기대수익률"]
        ret_str = f"{ret:+.2f}%" if ret is not None else "-"
        lines.append(
            f"{rank}. {name}({code}) - 현재가 {data['현재가']:,.0f}원, "
            f"경사 {data['추세경사']:+.2f}%, 예상수익률 {ret_str}, 추세: {data['추세']}"
        )
    stock_list_text = "\n".join(lines)

    prompt = f"""당신은 한국 ETF 시장을 분석하는 애널리스트입니다. 아래는 추세추종 전략(매수: 5일선>20일선>120일선 정배열, 매도: 5일선<20일선)으로 골라낸 매수신호 상태 ETF 상위 종목들입니다. 이 목록을 보고 오늘 시장 전반에 대한 간결한 브리핑을 작성해주세요.

[오늘의 매수신호 ETF 목록 - 추세 경사 급한 순]
{stock_list_text}

[작성 지침]
- 5~7문장 정도의 한국어 브리핑
- 어떤 섹터/테마가 강세를 보이는지 패턴을 짚어줄 것 (예: 반도체/2차전지 등 특정 산업군 쏠림 여부)
- 국내 종목과 해외(미국/중국/일본 등) 종목 비중도 함께 언급
- 채권/금 등 안전자산 ETF가 포함되어 있다면 그 의미도 짧게 해석
- 전체적인 시장 분위기에 대한 균형 잡힌 해석 제공 (과도한 낙관/비관 지양)
- 투자 손익에 대한 법적 책임이 없는 정보 제공 목적임을 마지막에 짧게 명시
- 분석 과정이나 검토 메모 없이, 사용자에게 보여줄 최종 브리핑 문장만 바로 작성"""
    return prompt


def scan_all_etfs() -> dict:
    """ETF 유니버스(DB 또는 폴백) 전체를 스캔해서 신호 결과 딕셔너리로 반환 (레버리지는 항상 제외, 인버스는 토글에 따름)"""
    results = {}
    include_inverse = st.session_state.get("include_inverse", False)
    for code, name in get_etf_universe().items():
        if is_excluded(name, include_inverse=include_inverse):
            continue
        df   = fetch_data(code)
        data = calc_signals(df)
        if data is not None:
            results[code] = {"name": name, "data": data}
    return results

# ════════════════════════════════════════════════════════════════
#  UI 시작
# ════════════════════════════════════════════════════════════════

# ── 타이틀 (높이 줄임) ──────────────────────────────────────────
st.markdown(
    f"<h3 style='margin-bottom:0'>📈 ETF 추세 추종 대시보드 &nbsp;"
    f"<span style='font-size:0.6em; color:gray; font-weight:normal'>"
    f"기준일 {datetime.today().strftime('%Y.%m.%d')} &nbsp;|&nbsp; MA5 / MA20 / MA120</span></h3>",
    unsafe_allow_html=True
)

# ── 지표 카드(metric) 글자 크기 조정: 좁은 화면에서 잘리지 않도록 ──
st.markdown("""
<style>
    div[data-testid="stMetric"] {
        background-color: rgba(120, 120, 120, 0.06);
        border-radius: 8px;
        padding: 8px 10px;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.15rem !important;
        white-space: normal !important;
        overflow-wrap: break-word !important;
        line-height: 1.25 !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.78rem !important;
        opacity: 0.75;
    }
    div[data-testid="stMetricDelta"] {
        font-size: 0.78rem !important;
    }
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
if "holdings" not in st.session_state:
    st.session_state.holdings = load_holdings()
if "selected_code" not in st.session_state:
    st.session_state.selected_code = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None
if "include_inverse" not in st.session_state:
    st.session_state.include_inverse = False  # 기본값: 인버스 제외 (하락장 대응용, 필요시 토글로 켬)

if get_db_conn() is None:
    st.warning("⚠️ etf_data.db 파일을 찾을 수 없어요. `python krx_data_collector.py` 를 먼저 실행해서 데이터를 수집해주세요. (수집 전까지는 내장 목록으로 임시 동작합니다)")

# 새로고침 버튼 + 인버스 포함 토글 (타이틀 바로 아래)
col_refresh, col_inverse, _ = st.columns([1, 2, 3])
with col_refresh:
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col_inverse:
    st.session_state.include_inverse = st.toggle(
        "🔻 인버스(1배) 포함 — 하락장 대응",
        value=st.session_state.include_inverse,
        help="레버리지·2배 상품은 이 토글과 무관하게 항상 제외됩니다. 켜면 1배 인버스 ETF도 추천/검색 대상에 포함됩니다."
    )

st.divider()

# ── 상단: 좌(추천) / 우(보유종목) ───────────────────────────────
top_left, top_right = st.columns(2)

# ─────────────────────────────────────────────────────
# 좌측: 추세추종 추천 종목
# ─────────────────────────────────────────────────────
with top_left:
    st.markdown("#### 🟡 추세추종 추천 TOP 10")
    _universe = get_etf_universe()
    _include_inverse = st.session_state.get("include_inverse", False)
    _scan_universe = {c: n for c, n in _universe.items() if not is_excluded(n, include_inverse=_include_inverse)}
    _source_label, _bas_dd, _error = get_etf_universe_status()
    _inverse_note = " (인버스 포함)" if _include_inverse else ""
    _caption = f"국내 ETF {len(_scan_universe)}개 ({_source_label}"
    _caption += f", 기준일 {_bas_dd})" if _bas_dd else ")"
    _caption += f"{_inverse_note} 중 정배열(5>20>120) + 급경사 상위 10개"
    st.caption(_caption)
    if _error:
        st.caption(f"⚠️ KRX API 사용 불가 — {_error}")

    with st.spinner("ETF 전체 스캔 중..."):
        scan_results = scan_all_etfs()

    golden_list = {
        code: info for code, info in scan_results.items()
        if info["data"]["매수신호"]
    }

    # ── 필터: 최근 N영업일 이내 골드크로스만 보기 ──────────────────
    filter_c1, filter_c2 = st.columns([1.3, 2])
    with filter_c1:
        filter_recent_gc = st.checkbox("최근 골드크로스만", value=False,
                                        help="골드크로스(MA5가 MA20을 상향 돌파)가 발생한 지 얼마 안 된 종목만 보여줍니다")
    with filter_c2:
        recent_days = st.slider("영업일 이내", min_value=1, max_value=20, value=5,
                                 disabled=not filter_recent_gc, label_visibility="collapsed" if filter_recent_gc else "visible")

    if filter_recent_gc:
        golden_list = {
            code: info for code, info in golden_list.items()
            if info["data"]["골드크로스경과영업일"] is not None
            and info["data"]["골드크로스경과영업일"] <= recent_days
        }

    if not golden_list:
        msg = "현재 매수신호(정배열) 상태인 ETF가 없습니다."
        if filter_recent_gc:
            msg = f"최근 {recent_days}영업일 이내 골드크로스가 발생한 매수신호 ETF가 없습니다."
        st.info(msg)
    else:
        sorted_golden = sorted(
            golden_list.items(),
            key=lambda x: x[1]["data"]["추세경사"],
            reverse=True
        )[:10]

        # 표 헤더
        st.markdown(
            "<div style='display:flex; font-size:0.78rem; opacity:0.6; "
            "padding:2px 6px; margin-bottom:2px;'>"
            "<div style='width:32px;'>#</div>"
            "<div style='flex:2;'>종목명</div>"
            "<div style='flex:1; text-align:right;'>현재가</div>"
            "<div style='flex:1; text-align:right;'>경사</div>"
            "<div style='flex:1; text-align:right;'>예상수익률</div>"
            "<div style='width:56px;'></div>"
            "</div>",
            unsafe_allow_html=True
        )

        # 고정 높이 컨테이너 (약 5개 표시, 나머지 스크롤)
        with st.container(height=300):
            for rank, (code, info) in enumerate(sorted_golden, 1):
                name  = info["name"]
                data  = info["data"]
                ret   = data["기대수익률"]
                slope = data["추세경사"]
                ret_str = f"{ret:+.2f}%" if ret is not None else "-"

                row_c1, row_c2, row_c3 = st.columns([5.2, 2.6, 0.9])
                with row_c1:
                    gc_ago = data["골드크로스경과영업일"]
                    gc_ago_str = f" · 크로스 {gc_ago}영업일 전" if gc_ago is not None else ""
                    st.markdown(
                        f"<div style='padding-top:6px;'>"
                        f"<b>#{rank}</b> 🟡 {name}"
                        f"<div style='font-size:0.75rem; opacity:0.65;'>{data['추세']}{gc_ago_str}</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                with row_c2:
                    st.markdown(
                        f"<div style='padding-top:6px; text-align:right; font-size:0.85rem;'>"
                        f"{data['현재가']:,.0f}원<br>"
                        f"<span style='opacity:0.65;'>경사 {slope:+.2f}% · {ret_str}</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                with row_c3:
                    if st.button("📊", key=f"rec_{code}", help="차트 보기", use_container_width=True):
                        st.session_state.selected_code = code
                        st.session_state.selected_name = name
                st.markdown("<hr style='margin:4px 0; opacity:0.15;'>", unsafe_allow_html=True)

        if st.button("🤖 AI 시장 브리핑 보기", key="ai_summary_btn", use_container_width=True):
            with st.spinner("AI가 오늘의 추천 종목을 분석하고 있어요..."):
                summary_text = call_gemini(build_summary_prompt(sorted_golden))
            st.session_state.ai_summary_text = summary_text

        if st.session_state.get("ai_summary_text"):
            st.info(st.session_state.ai_summary_text)

# ─────────────────────────────────────────────────────
# 우측: 내 보유 종목
# ─────────────────────────────────────────────────────
with top_right:
    st.markdown("#### 💼 내 보유 종목")

    @st.fragment
    def render_holdings_section():
        # 종목 추가 폼 (접힘 상태로 기본 표시)
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
                code = h_code_direct.strip().upper()
                if len(code) >= 5 and len(code) <= 7 and code.isalnum():
                    chosen_code = code
                    chosen_name = get_etf_universe().get(code, f"종목_{code}")
                else:
                    st.error("종목코드는 5~7자리 숫자/영문 조합이어야 합니다.")

            buy_price_input = st.number_input("매수가격 (원)", min_value=0.0, step=100.0, format="%.0f")

            if st.button("✅ 추가", use_container_width=True):
                if chosen_code and buy_price_input > 0:
                    st.session_state.holdings[chosen_code] = {
                        "name": chosen_name,
                        "buy_price": buy_price_input
                    }
                    save_holdings(st.session_state.holdings)
                    st.success(f"[{chosen_name}] {buy_price_input:,.0f}원으로 추가됨!")
                    st.rerun(scope="fragment")  # 이 섹션만 새로고침 (좌측 추천 목록은 그대로 유지)
                elif not chosen_code:
                    st.error("종목을 검색하거나 코드를 입력해주세요.")
                else:
                    st.error("매수가격을 입력해주세요.")

        # 보유 종목 리스트 (고정 높이 + 스크롤)
        if not st.session_state.holdings:
            st.info("아직 등록된 보유 종목이 없습니다.")
        else:
            with st.container(height=300):
                for code, h in list(st.session_state.holdings.items()):
                    name      = h["name"]
                    buy_price = h["buy_price"]

                    df   = fetch_data(code)
                    data = calc_signals(df)

                    row_col1, row_col2, row_col3 = st.columns([4.4, 0.8, 0.8])
                    with row_col1:
                        if data is None:
                            st.markdown(
                                f"<div style='padding-top:6px;'>⚠️ {name} "
                                f"<span style='opacity:0.6; font-size:0.8rem;'>(데이터 부족)</span></div>",
                                unsafe_allow_html=True
                            )
                        else:
                            cur_price  = data["현재가"]
                            profit_pct = (cur_price - buy_price) / buy_price * 100
                            is_danger  = profit_pct <= -5.0
                            profit_color = "#c0392b" if profit_pct < 0 else "#0a7d2c"
                            profit_icon  = "🔺" if profit_pct >= 0 else "🔻"

                            st.markdown(
                                f"<div style='padding-top:4px;'><b>{name}</b>  "
                                f"{signal_badge(data['신호'])}"
                                f"{'  ' + render_badge('🚨 손절', '#b3261e', '#fbe4e2') if is_danger else ''}"
                                f"<div style='font-size:0.85rem; margin-top:2px;'>"
                                f"현재 {cur_price:,.0f}원 &nbsp;·&nbsp; "
                                f"<span style='color:{profit_color};'>{profit_icon} {profit_pct:+.2f}%</span>"
                                f"</div></div>",
                                unsafe_allow_html=True
                            )
                    with row_col2:
                        if st.button("📊", key=f"hold_{code}", help="차트 보기", use_container_width=True):
                            st.session_state.selected_code = code
                            st.session_state.selected_name = name
                            st.rerun()  # 차트 영역(fragment 바깥)을 갱신하려면 전체 새로고침 필요
                    with row_col3:
                        if st.button("🗑️", key=f"del_hold_{code}"):
                            del st.session_state.holdings[code]
                            save_holdings(st.session_state.holdings)
                            st.rerun(scope="fragment")  # 이 섹션만 새로고침 (좌측 추천 목록은 다시 안 돌음)

    render_holdings_section()

st.divider()

# ── 하단: 선택한 종목의 상세 차트 ───────────────────────────────
st.markdown("#### 📊 종목 상세 차트")

if st.session_state.selected_code is None:
    st.info("👆 위에서 추천 종목이나 보유 종목을 클릭하면 여기에 차트가 표시됩니다.")
else:
    code = st.session_state.selected_code
    name = st.session_state.selected_name

    df   = fetch_data(code)
    data = calc_signals(df)

    if data is None:
        st.error(f"{name} ({code}) 데이터가 부족합니다. (상장 후 120거래일 이상 필요)")
    else:
        buy_price = None
        if code in st.session_state.holdings:
            buy_price = st.session_state.holdings[code]["buy_price"]

        ret     = data["기대수익률"]
        days    = data["보유일수"]
        ret_str = f"{ret:+.2f}% ({days}일 보유)" if ret is not None else "골드크로스 없음"

        if buy_price is not None:
            my_profit        = (data["현재가"] - buy_price) / buy_price * 100
            is_danger         = my_profit <= -5.0
            profit_color      = "#c0392b" if my_profit < 0 else "#0a7d2c"
            delta_color       = "#c0392b" if data["전일대비"] < 0 else "#0a7d2c"

            cards = [
                metric_card("현재가", f"{data['현재가']:,.0f}원",
                            f"{data['전일대비']:+,.0f} ({data['전일대비율']:+.2f}%)", delta_color),
                metric_card("내 매수가", f"{buy_price:,.0f}원"),
                metric_card("내 수익률", f"{my_profit:+.2f}%", sub_color=profit_color),
                metric_card("손절기준(-5%)", f"{buy_price * 0.95:,.0f}원"),
                metric_card("손절 여부", stop_loss_badge(is_danger)),
                metric_card("매매신호", signal_badge(data["신호"])),
            ]
            st.markdown(metric_grid(cards), unsafe_allow_html=True)
        else:
            delta_color = "#c0392b" if data["전일대비"] < 0 else "#0a7d2c"
            cards = [
                metric_card("현재가", f"{data['현재가']:,.0f}원",
                            f"{data['전일대비']:+,.0f} ({data['전일대비율']:+.2f}%)", delta_color),
                metric_card("신호", signal_badge(data["신호"])),
                metric_card("추세", data["추세"]),
                metric_card("골드크로스 수익률", ret_str),
            ]
            st.markdown(metric_grid(cards), unsafe_allow_html=True)

        fig = make_chart(name, code, data, buy_price=buy_price)
        st.pyplot(fig)
        plt.close(fig)

        st.divider()
        if st.button("🤖 AI 애널리스트 의견 보기", key=f"ai_single_{code}", use_container_width=True):
            with st.spinner(f"AI가 {name} 종목을 분석하고 있어요..."):
                prompt = build_single_stock_prompt(name, code, data, buy_price=buy_price)
                comment_text = call_gemini(prompt)
            st.session_state[f"ai_comment_{code}"] = comment_text

        if st.session_state.get(f"ai_comment_{code}"):
            st.info(st.session_state[f"ai_comment_{code}"])