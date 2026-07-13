"""
krx_data_collector.py
──────────────────────────────────────────────────────────
KRX Open API(etf_bydd_trd)로 ETF 전종목 일별 시세를 받아와
etf_web_app.py 가 읽는 etf_data.db(SQLite)에 저장하는 수집기

★ 롤링 윈도우 방식: 최근 WINDOW_DAYS(기본 500일)치만 유지하고,
   그보다 오래된 데이터는 자동으로 삭제합니다.
   → DB 파일이 계속 커지지 않아서 GitHub에 안전하게 올릴 수 있습니다.
   (MA120 계산 + 90일 차트 표시에 필요한 기간보다 여유 있게 잡은 값입니다)

[사전 준비]
1) KRX Open API 홈페이지에서 발급받은 인증키를 아래 둘 중 한 방법으로 등록
   방법 A) 환경변수로 등록 (권장)
       Windows(cmd)         : set KRX_AUTH_KEY=발급받은_인증키
       Windows(PowerShell)  : $env:KRX_AUTH_KEY="발급받은_인증키"
       Mac/Linux            : export KRX_AUTH_KEY="발급받은_인증키"
   방법 B) 이 파일과 같은 폴더에 krx_auth.txt 파일을 만들고 인증키만 저장

[사용법]
   # 평소엔 이렇게만 실행하면 됨 (매일 스케줄러로 돌리는 걸 추천)
   python krx_data_collector.py

   # 처음 한 번 넉넉하게 채우고 싶을 때 (윈도우 전체를 강제로 다시 채움)
   python krx_data_collector.py --full

   # 윈도우 크기를 바꾸고 싶을 때 (예: 300일만 유지)
   python krx_data_collector.py --window-days 300
"""

import os
import sqlite3
import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

# ── 설정 ──────────────────────────────────────────────────────────
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_data.db")
API_URL = "https://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd"
DEFAULT_WINDOW_DAYS = 500   # 롤링 윈도우 기본 크기 (달력일 기준, 약 300~330 거래일)
KST = ZoneInfo("Asia/Seoul")  # GitHub Actions 서버(UTC)에서 돌려도 한국시간 기준으로 동작

# etf_web_app.py 와 동일한 인버스/레버리지 제외 키워드
EXCLUDE_KEYWORDS = ["인버스", "레버리지", "2X", "선물인버스", "곱버스"]


def is_excluded(name: str) -> bool:
    return any(kw in name for kw in EXCLUDE_KEYWORDS)


def get_auth_key() -> str:
    key = os.environ.get("KRX_AUTH_KEY")
    if key:
        return key.strip()
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "krx_auth.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    raise RuntimeError(
        "KRX 인증키를 찾을 수 없습니다.\n"
        "  방법1) 환경변수 KRX_AUTH_KEY 로 설정\n"
        "  방법2) 이 스크립트와 같은 폴더에 krx_auth.txt 파일을 만들고 인증키만 저장"
    )


def init_db(conn: sqlite3.Connection):
    """etf_web_app.py 의 조회 쿼리와 컬럼이 정확히 일치해야 함"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily (
            bas_dd   TEXT NOT NULL,
            isu_cd   TEXT NOT NULL,
            isu_nm   TEXT,
            cls_prc  REAL,
            opn_prc  REAL,
            hgh_prc  REAL,
            low_prc  REAL,
            trd_vol  REAL,
            fluc_rt  REAL,
            PRIMARY KEY (bas_dd, isu_cd)
        )
    """)
    conn.commit()


def to_float(v):
    """'-' 나 빈 문자열은 None, 콤마 섞인 숫자는 float 으로 변환"""
    if v is None:
        return None
    v = str(v).replace(",", "").strip()
    if v in ("", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch_one_day(auth_key: str, bas_dd: str) -> list:
    """하루치 ETF 전체 시세 조회 (OHLCV 포함). 휴장일이면 빈 리스트 반환"""
    headers = {"AUTH_KEY": auth_key}
    params = {"basDd": bas_dd}
    resp = requests.get(API_URL, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"KRX API 오류 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data.get("OutBlock_1", []) or []


def save_day(conn: sqlite3.Connection, rows: list) -> int:
    """하루치 데이터 저장 (인버스/레버리지 제외). 저장 건수 반환"""
    saved = 0
    for row in rows:
        name = row.get("ISU_NM", "")
        if is_excluded(name):
            continue
        conn.execute("""
            INSERT OR REPLACE INTO etf_daily
                (bas_dd, isu_cd, isu_nm, cls_prc, opn_prc, hgh_prc, low_prc, trd_vol, fluc_rt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row.get("BAS_DD"),
            row.get("ISU_CD"),
            name,
            to_float(row.get("TDD_CLSPRC")),
            to_float(row.get("TDD_OPNPRC")),
            to_float(row.get("TDD_HGPRC")),
            to_float(row.get("TDD_LWPRC")),
            to_float(row.get("ACC_TRDVOL")),
            to_float(row.get("FLUC_RT")),
        ))
        saved += 1
    conn.commit()
    return saved


def business_days_missing(conn, start: str, end: str) -> list:
    """start~end 중 DB에 아예 없는 평일 목록 (공휴일은 API가 빈 값을 줘서 자동 스킵됨)"""
    existing = {r[0] for r in conn.execute("SELECT DISTINCT bas_dd FROM etf_daily").fetchall()}
    d = datetime.strptime(start, "%Y%m%d")
    end_d = datetime.strptime(end, "%Y%m%d")
    result = []
    while d <= end_d:
        if d.weekday() < 5:  # 월~금만
            dd = d.strftime("%Y%m%d")
            if dd not in existing:
                result.append(dd)
        d += timedelta(days=1)
    return result


def find_incomplete_days(conn, start: str, end: str, min_ratio: float = 0.7) -> list:
    """
    윈도우 내에서 '데이터는 있지만 일부 종목만 저장된' 불완전한 날짜를 찾아냄.
    (네트워크 순간 오류 등으로 일부 종목만 저장된 경우, 기존 로직은 이런 날짜를
     "이미 수집됨"으로 착각해서 다시 안 건드렸음 → 이 함수가 그 빈틈을 잡아냄)
    """
    rows = conn.execute("""
        SELECT bas_dd, COUNT(DISTINCT isu_cd) FROM etf_daily
        WHERE bas_dd BETWEEN ? AND ?
        GROUP BY bas_dd
    """, (start, end)).fetchall()
    if not rows:
        return []
    baseline = max(cnt for _, cnt in rows)  # 가장 잘 채워진 날짜의 종목 수를 정상 기준으로 삼음
    threshold = baseline * min_ratio
    return [dd for dd, cnt in rows if cnt < threshold]


def prune_old_data(conn: sqlite3.Connection, window_days: int):
    """윈도우보다 오래된 데이터 삭제 (롤링 윈도우 유지)"""
    cutoff = (datetime.now(KST) - timedelta(days=window_days)).strftime("%Y%m%d")
    cur = conn.execute("DELETE FROM etf_daily WHERE bas_dd < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def main():
    parser = argparse.ArgumentParser(description="KRX Open API ETF 일별시세 수집기 (롤링 윈도우)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                         help=f"유지할 기간 (달력일, 기본 {DEFAULT_WINDOW_DAYS}일)")
    parser.add_argument("--full", action="store_true",
                         help="윈도우 전체 기간을 처음부터 다시 채움 (평소엔 필요 없음)")
    args = parser.parse_args()

    auth_key = get_auth_key()
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    today = datetime.now(KST).strftime("%Y%m%d")  # 저녁에 자동 실행되므로 당일 데이터까지 시도
    window_start = (datetime.now(KST) - timedelta(days=args.window_days)).strftime("%Y%m%d")

    if args.full:
        # 윈도우 시작일부터 강제로 다시 수집 (DB에 이미 있어도 다시 받아옴)
        conn.execute("DELETE FROM etf_daily WHERE bas_dd >= ?", (window_start,))
        conn.commit()

    missing_days = business_days_missing(conn, window_start, today)
    incomplete_days = find_incomplete_days(conn, window_start, today)
    # 완전 누락 + 불완전(일부만 저장된) 날짜를 합쳐서 수집 대상으로 삼음 (중복 제거, 날짜순 정렬)
    targets = sorted(set(missing_days) | set(incomplete_days))

    print(f"수집 대상: {len(targets)}개 영업일 (윈도우: 최근 {args.window_days}일, {window_start} ~ {today})")
    print(f"  ├─ 완전히 없는 날짜: {len(missing_days)}개")
    print(f"  └─ 일부만 저장된 불완전한 날짜: {len(incomplete_days)}개"
          + (f" {sorted(incomplete_days)}" if incomplete_days else ""))

    ok, empty, fail = 0, 0, 0
    for i, dd in enumerate(targets, 1):
        try:
            rows = fetch_one_day(auth_key, dd)
            if not rows:
                empty += 1
                print(f"[{i}/{len(targets)}] {dd} → 휴장일 (데이터 없음)")
            else:
                n = save_day(conn, rows)
                ok += 1
                print(f"[{i}/{len(targets)}] {dd} → {n}건 저장")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(targets)}] {dd} → 실패: {e}")
        time.sleep(0.3)  # 하루 10,000회 호출 제한 보호

    # ── 롤링 윈도우 정리: 오래된 데이터 삭제 ──────────────────────
    removed = prune_old_data(conn, args.window_days)
    if removed:
        print(f"🧹 윈도우 밖 오래된 데이터 {removed}건 삭제 (최근 {args.window_days}일만 유지)")

    # ── 최종 상태 요약 ─────────────────────────────────────────
    isu_cnt = conn.execute("SELECT COUNT(DISTINCT isu_cd) FROM etf_daily").fetchone()[0]
    rec_cnt = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
    last_dd = conn.execute("SELECT MAX(bas_dd) FROM etf_daily").fetchone()[0]
    db_size_mb = os.path.getsize(DB_FILE) / (1024 * 1024) if os.path.exists(DB_FILE) else 0
    conn.close()

    print(f"완료: 저장 {ok}일 / 휴장 {empty}일 / 실패 {fail}일")
    print(f"현재 DB 상태: 종목 {isu_cnt:,}개 | 레코드 {rec_cnt:,}건 | 최신 기준일 {last_dd} | 파일 크기 {db_size_mb:.1f}MB")


if __name__ == "__main__":
    main()