import os
import json
import io
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

print("🚀 QNDX 보유량 기반 QQQ 자금 유입 예상치(Estimate) 연산 시작...\n")

# 경로 설정
DATES_FILE_PATH = 'data/available_dates.json'
QNDX_PREV_FILE = 'data/QNDX_holdings.json'
QQQ_METRICS_PATH = 'data/QQQ_metrics.json'

# ==========================================
# 0. 과거 데이터(available_dates.json의 최신 날짜 스냅샷) 불러오기
# ==========================================
prev_shares_data = {}
prev_prices_data = {}
latest_date = None

if os.path.exists(DATES_FILE_PATH):
    try:
        with open(DATES_FILE_PATH, 'r', encoding='utf-8') as f:
            dates_list = json.load(f)
            if isinstance(dates_list, list) and len(dates_list) > 0:
                latest_date = dates_list[0]
                prev_file_path = f'data/{latest_date}.json'
    except Exception as e:
        print(f"⚠️ available_dates.json 읽기 실패: {e}")

if not latest_date or not os.path.exists(prev_file_path):
    prev_file_path = 'data/latest_backup.json'

if os.path.exists(prev_file_path):
    try:
        with open(prev_file_path, 'r', encoding='utf-8') as f:
            old_json = json.load(f)
            old_data = old_json.get('data', []) if isinstance(old_json, dict) else old_json
            
            for item in old_data:
                ticker = item.get('Ticker')
                if ticker:
                    if 'Shares Held' in item:
                        prev_shares_data[ticker] = float(item['Shares Held'])
                    if 'Current Price ($)' in item:
                        prev_prices_data[ticker] = float(item['Current Price ($)'])
                        
            print(f"📂 이전 거래일 QQQ 데이터 로드 완료. (조회 기준일: {latest_date if latest_date else 'latest_backup'})")
    except Exception as e:
        print(f"⚠️ 이전 QQQ 데이터 파싱 중 오류 발생: {e}")
        exit()
else:
    print("🚨 수급 변화를 대조할 이전 거래일 QQQ 데이터가 존재하지 않습니다.")
    exit()

# ==========================================
# 1. SSGA로부터 QNDX 일일 보유 수량 엑셀 다운로드 및 B열 3행 날짜 추출
# ==========================================
print("\n[1/4] SSGA(State Street)에서 QNDX 일일 보유 수량 엑셀 수집 중...")
qndx_url = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-qndx.xlsx"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

try:
    response = requests.get(qndx_url, headers=headers)
    response.raise_for_status()
    excel_file = response.content
    print(" ✅ QNDX 엑셀 다운로드 성공.")
except Exception as e:
    print(f"🚨 QNDX 엑셀 수집 실패: {e}")
    exit()

# [수정] B열 3행에서 정확히 날짜 정보 파싱 (0-indexed 기준: Row 2, Column 1)
qndx_date = None
try:
    df_raw = pd.read_excel(io.BytesIO(excel_file), header=None, engine='openpyxl')
    if df_raw.shape[0] >= 3 and df_raw.shape[1] >= 2:
        raw_cell_value = str(df_raw.iloc[2, 1])  # 3행(index 2), B열(index 1)
        print(f" - 엑셀 B열 3행 원본 값: '{raw_cell_value}'")
        
        clean_date_str = raw_cell_value.lower().replace("as of", "").strip()
        qndx_date = pd.to_datetime(clean_date_str).strftime('%Y-%m-%d')
        print(f" ✅ 추출된 QNDX 데이터 기준일: {qndx_date}")
    else:
        print("⚠️ 엑셀 데이터 크기가 B열 3행을 읽기에 너무 작습니다.")
except Exception as e:
    print(f"⚠️ 엑셀 B열 3행 날짜 추출 실패: {e}")

# 날짜 추출 실패 시 대체 로직
if not qndx_date:
    if latest_date:
        qndx_date = pd.bdate_range(start=latest_date, periods=2)[1].strftime('%Y-%m-%d')
        print(f" - 날짜 파싱 실패로 임시 다음 영업일로 설정: {qndx_date}")
    else:
        qndx_date = datetime.today().strftime('%Y-%m-%d')

# --- 날짜 중복 체크 및 이전 데이터 불러오기 ---
prev_qndx_date = None
prev_qndx_holdings = {}

if os.path.exists(QNDX_PREV_FILE):
    try:
        with open(QNDX_PREV_FILE, 'r', encoding='utf-8') as f:
            prev_data = json.load(f)
            if isinstance(prev_data, dict):
                if "qndx_date" in prev_data:
                    prev_qndx_date = prev_data.get("qndx_date")
                    prev_qndx_holdings = prev_data.get("holdings", {})
                else:
                    # 기존 포맷(단순 딕셔너리 구조)과의 하위 호환성 유지
                    prev_qndx_holdings = prev_data
    except Exception as e:
        print(f"⚠️ {QNDX_PREV_FILE} 로드 중 오류 발생: {e}")

# 새로 추출한 qndx_date가 이전 파일의 qndx_date와 같은 경우 중단
if prev_qndx_date == qndx_date:
    print(f"\n📢 이미 처리된 기준일({qndx_date})의 QNDX 데이터입니다. 중복 연산을 방지하기 위해 분석을 종료합니다.")
    exit()

# ==========================================
# 2. QNDX 엑셀 시트 파싱 및 종목별 보유 수량 추출
# ==========================================
print("\n[2/4] QNDX 보유 종목 수량 분석 중...")
try:
    # 4번째 행까지의 설명 문구를 건너뛰고 로드
    df_qndx = pd.read_excel(io.BytesIO(excel_file), skiprows=4, engine='openpyxl')
    df_qndx.columns = [str(col).strip() for col in df_qndx.columns]
    
    ticker_col = next((col for col in df_qndx.columns if col.lower() in ['ticker', 'symbol', 'identifier']), None)
    shares_col = next((col for col in df_qndx.columns if col.lower() in ['shares held', 'shares', 'units', 'quantity']), None)
    
    if not ticker_col or not shares_col:
        raise ValueError(f"필수 컬럼(Ticker/Shares Held)을 찾을 수 없습니다. 컬럼명: {list(df_qndx.columns)}")
        
    df_qndx = df_qndx.dropna(subset=[ticker_col])
    df_qndx = df_qndx[df_qndx[ticker_col].apply(lambda x: isinstance(x, str) and len(x.strip()) <= 10)]
    df_qndx[ticker_col] = df_qndx[ticker_col].str.strip().str.replace('.', '-', regex=False)
    df_qndx[shares_col] = pd.to_numeric(df_qndx[shares_col], errors='coerce').fillna(0).astype(int)
        
    current_qndx_holdings = dict(zip(df_qndx[ticker_col], df_qndx[shares_col]))
    print(f" ✅ 총 {len(current_qndx_holdings)}개의 QNDX 자산 보유 정보 추출 완료.")
except Exception as e:
    print(f"🚨 QNDX 데이터 구조 정제 실패: {e}")
    exit()

# ==========================================
# 3. 전 거래일 QNDX 데이터와 대조하여 종목별 증감율 계산
# ==========================================
print("\n[3/4] 전 거래일 대조 및 수급 증감율 연산 중...")
# (이전 파일 로드 처리는 상단 중복 체크 단계에서 먼저 진행되었습니다)

qndx_change_rates = {}
for ticker, current_shares in current_qndx_holdings.items():
    prev_shares = prev_qndx_holdings.get(ticker, 0.0)
    if prev_shares > 0:
        qndx_change_rates[ticker] = (current_shares - prev_shares) / prev_shares
    else:
        qndx_change_rates[ticker] = 0.0

# ==========================================
# 4. QQQ 자금 유출입 예상치(Estimate) 연산
# ==========================================
print("\n[4/4] QQQ 예상 자금 흐름(Estimated Fund Flow) 산출 중...")
fund_flow_sum = 0.0
matched_count = 0

for ticker, qqq_prev_shares in prev_shares_data.items():
    change_rate = qndx_change_rates.get(ticker, 0.0)
    prev_close = prev_prices_data.get(ticker, 0.0)
    
    if prev_close > 0:
        fund_flow_sum += qqq_prev_shares * change_rate * prev_close
        if change_rate != 0:
            matched_count += 1

print(f" - 수급 변동 감지 및 일치 종목 수: {matched_count}개 / {len(prev_shares_data)}개")
print(f" - 계산된 QQQ 예상 자금 흐름: {fund_flow_sum:+.2f} USD")

try:
    # qndx_date 정보를 상위에 포함하여 저장 구조 개편
    qndx_save_data = {
        "qndx_date": qndx_date,
        "holdings": current_qndx_holdings
    }
    with open(QNDX_PREV_FILE, 'w', encoding='utf-8') as f:
        json.dump(qndx_save_data, f, ensure_ascii=False, indent=4)
    print(f"💾 당일 QNDX 수량 정보 로컬 백업 완료. (기준일: {qndx_date})")
except Exception as e:
    print(f"⚠️ {QNDX_PREV_FILE} 저장 실패: {e}")

# ==========================================
# 5. [수정] 지정된 엑셀 날짜로 QQQ 가격 조회 및 metrics 기록
# ==========================================
print(f"\n📊 QQQ 가격을 {qndx_date} 기준일로 조회 및 기록 중...")

qqq_price = 0.0
try:
    qqq_ticker = yf.Ticker("QQQ")
    
    start_date_str = qndx_date
    end_date_obj = pd.to_datetime(qndx_date) + pd.Timedelta(days=1)
    end_date_str = end_date_obj.strftime('%Y-%m-%d')
    
    qqq_hist = qqq_ticker.history(start=start_date_str, end=end_date_str, auto_adjust=False)
    
    if not qqq_hist.empty:
        qqq_price = qqq_hist['Close'].iloc[0]
        print(f" ✅ {qndx_date} QQQ 조회 성공: ${qqq_price:.2f}")
    else:
        print(f" ⚠️ {qndx_date} 거래 데이터가 없습니다. 가장 최근 영업일 종가로 대체합니다.")
        qqq_hist_fallback = qqq_ticker.history(period="1d", auto_adjust=False)
        qqq_price = qqq_hist_fallback['Close'].iloc[-1]
        print(f"  ↳ 대체 QQQ 종가: ${qqq_price:.2f}")
except Exception as e:
    print(f"⚠️ QQQ 가격 조회 중 오류 발생: {e}")

# metrics에 예상치 데이터 기록 (정렬 제거 버전)
try:
    metrics_data = {}
    if os.path.exists(QQQ_METRICS_PATH):
        with open(QQQ_METRICS_PATH, 'r', encoding='utf-8') as f:
            metrics_data = json.load(f)
            
    if "QQQ" not in metrics_data:
        metrics_data["QQQ"] = {}
    if "history" not in metrics_data["QQQ"]:
        metrics_data["QQQ"]["history"] = {}

    metrics_data["QQQ"]["history"][qndx_date] = {
        "ETF_Price": {
            "value": round(qqq_price, 2) if qqq_price > 0 else "N/A",
            "display": f"${qqq_price:,.2f}" if qqq_price > 0 else "N/A"
        },
        "Fund_Flow": {
            "value": round(fund_flow_sum, 2)
        },
        "is_estimate": True
    }

    with open(QQQ_METRICS_PATH, 'w', encoding='utf-8') as f:
        json.dump(metrics_data, f, ensure_ascii=False, indent=4)
        
    print(f"✨ 완료! 예상 데이터가 {QQQ_METRICS_PATH}에 정상 기록되었습니다.")
    print(f"   [기준 예상일]: {qndx_date}, [추정 수급량]: {fund_flow_sum:+.2f} USD")
except Exception as e:
    print(f"🚨 QQQ_metrics.json 쓰기 작업 도중 에러 발생: {e}")
    # 저녁에 SPDR 업데이트 되면 실행하기
    # 업데이트 할 거 : contribution_and_weight.py (index.html은 업데이트 함)
    # 추가 할 거 : estimate_qqq_flow.py