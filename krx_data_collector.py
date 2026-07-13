"""
krx_data_collector.py
──────────────────────────────────────────────────────────
KRX Open API(etf_bydd_trd)로 ETF 전종목 일별 시세를 받아와
etf_web_app.py 가 읽는 etf_data.db(SQLite)에 저장하는 수집기

[사전 준비]
1) KRX Open API 홈페이지에서 발급받은 인증키를 아래 둘 중 한 방법으로 등록
   방법 A) 환경변수로 등록 (권장)
       Windows(PowerShell): setx KRX_AUTH_KEY "발급받은_인증키"
       Mac/Linux           : export KRX_AUTH_KEY="발급받은_인증키"
   방법 B) 이 파일과 같은 폴더에 krx_auth.txt 파일을 만들고 인증키만 저장

[사용법]
   # 처음 한 번: 2014-01-01부터 어제까지 전체 수집 (시간 다소 걸림)
   python krx_data_collector.py

   # 최근 5 영업일만 갱신 (평소엔 이렇게 자주 돌리면 됨)
   python krx_data_collector.py --days 5

   # 특정 기간만 수집
   python krx_data_collector.py --start 20250101 --end 20250630
"""

import os
import sqlite3
import argparse
import time
from datetime import datetime, timedelta
import requests

# ── 설정 ──────────────────────────────────────────────────────────
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_data.db")
API_URL = "https://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd"

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
    """하루치 ETF 전체 시세 조회. 휴장일이면 빈 리스트 반환"""
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
    """start~end 중 DB에 아직 없는 평일 목록 (공휴일은 API가 빈 값을 줘서 자동 스킵됨)"""
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


def main():
    parser = argparse.ArgumentParser(description="KRX Open API ETF 일별시세 수집기")
    parser.add_argument("--start", help="시작일 YYYYMMDD (기본 20140101)")
    parser.add_argument("--end", help="종료일 YYYYMMDD (기본: 어제)")
    parser.add_argument("--days", type=int, help="최근 N 영업일만 갱신 (--start/--end 대신 사용)")
    args = parser.parse_args()

    auth_key = get_auth_key()
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    if args.days:
        start = (datetime.now() - timedelta(days=args.days * 2)).strftime("%Y%m%d")  # 주말 감안 넉넉히
        end = yesterday
    else:
        start = args.start or "20140101"
        end = args.end or yesterday

    targets = business_days_missing(conn, start, end)
    print(f"수집 대상: {len(targets)}개 영업일 ({start} ~ {end} 중 DB에 없는 날짜)")

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

    conn.close()
    print(f"완료: 저장 {ok}일 / 휴장 {empty}일 / 실패 {fail}일")


if __name__ == "__main__":
    main()
