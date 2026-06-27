import yfinance as yf
import pandas as pd
import cloudscraper
import os
import json
from datetime import datetime
import time

print("🚀 나스닥 100 메타데이터 및 자금 흐름 계산 시작 (1일 1회 실행 모드)...\n")

# ==========================================
# 0. 과거 데이터(available_dates.json의 최신 날짜 스냅샷) 불러오기
# ==========================================
prev_shares_data = {}
old_market_date = None
old_QQQ_date = None
prev_file_path = None

dates_file_path = 'data/available_dates.json'

# available_dates.json에서 가장 최신 날짜 가져오기
if os.path.exists(dates_file_path):
    try:
        with open(dates_file_path, 'r', encoding='utf-8') as f:
            dates_list = json.load(f)
            if isinstance(dates_list, list) and len(dates_list) > 0:
                latest_date = dates_list[0]
                prev_file_path = f'data/{latest_date}.json'
                print(f"📂 available_dates.json에서 최신 날짜 감지: {latest_date}")
    except Exception as e:
        print(f"⚠️ available_dates.json을 읽는 중 에러 발생: {e}")

# 지정된 이전 파일이 없으면 latest.json을 대체용으로 로드 시도
if not prev_file_path or not os.path.exists(prev_file_path):
    prev_file_path = 'data/latest.json'
    if os.path.exists(prev_file_path):
        print("📂 특정 날짜 JSON 파일이 없어 latest.json 파일을 대체하여 로드합니다.")

# 이전 데이터 파일에서 market_date, QQQ_date, Shares Held 정보 추출
if prev_file_path and os.path.exists(prev_file_path):
    try:
        with open(prev_file_path, 'r', encoding='utf-8') as f:
            old_json = json.load(f)
            old_meta = old_json.get('metadata', {}) if isinstance(old_json, dict) else {}
            old_data = old_json.get('data', []) if isinstance(old_json, dict) else old_json
            
            old_market_date = old_meta.get('market_date')
            old_QQQ_date = old_meta.get('QQQ_date')
            
            for item in old_data:
                ticker = item.get('Ticker')
                if ticker and 'Shares Held' in item:
                    prev_shares_data[ticker] = item['Shares Held']
                        
        print(f"📂 이전 데이터 로드 완료. (이전 시장일: {old_market_date}, 이전 QQQ 기준일: {old_QQQ_date})")
    except Exception as e:
        print(f"⚠️ 이전 데이터를 읽는 중 에러 발생: {e}")

# ==========================================
# 1. 어제 나스닥 100 지수 및 [시장 거래일, 현재 지수] 가져오기
# ==========================================
print("\n[1/3] 나스닥 100 지수 및 시장 날짜 확인 중...")
ndx = yf.Ticker("^NDX")
hist_ndx = ndx.history(period="2d")

if len(hist_ndx) < 2:
    print("🚨 지수 데이터를 가져오는 데 실패했습니다.")
    exit()

yesterday_ndx_close = hist_ndx['Close'].iloc[0]
market_date = hist_ndx.index[1].strftime('%Y-%m-%d')

current_ndx_close = hist_ndx['Close'].iloc[1]
ndx_point_change = current_ndx_close - yesterday_ndx_close
ndx_percent_change = (ndx_point_change / yesterday_ndx_close) * 100

print(f" - 시장 거래일: {market_date}")
print(f" - 나스닥 100 지수: {current_ndx_close:,.2f} pt (전일 대비 {ndx_point_change:+.2f} pt / {ndx_percent_change:+.2f}%)")

# ==========================================
# 2. Invesco API 수집 및 [Invesco 기준일] 찾기
# ==========================================
print("\n[2/3] Invesco(QQQ ETF 운용사) 공식 API 데이터 수집 중...")
url = 'https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&interval=monthly&productType=ETF'
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

try:
    response = scraper.get(url)
    response.raise_for_status() 
    json_data = response.json()
    
    QQQ_date = "Unknown"
    def find_date(data):
        if isinstance(data, dict):
            for k, v in data.items():
                if k == 'effectiveBusinessDate': return str(v)[:10]
                if isinstance(v, (dict, list)):
                    res = find_date(v)
                    if res: return res
        elif isinstance(data, list):
            for item in data:
                res = find_date(item)
                if res: return res
        return None

    found_date = find_date(json_data)
    if found_date: QQQ_date = found_date
    print(f" - QQQ 기준일: {QQQ_date}")

    # 💡 [중복 실행 및 휴장일 검지] market_date 또는 QQQ_date 중 하나라도 이전과 동일하면 종료
    if market_date == old_market_date or QQQ_date == old_QQQ_date:
        print(f"\n🛑 [실행 중단] 시장 거래일({market_date}) 또는 QQQ 기준일({QQQ_date})이 이전 기록(시장일: {old_market_date}, QQQ 기준일: {old_QQQ_date})과 동일합니다. 수집을 정지합니다.")
        exit()

    holdings_list = None
    if isinstance(json_data, list): holdings_list = json_data
    elif isinstance(json_data, dict):
        for k, v in json_data.items():
            if isinstance(v, list) and len(v) > 10: holdings_list = v; break
        if not holdings_list:
            for k, v in json_data.items():
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        if isinstance(sv, list) and len(sv) > 10: holdings_list = sv; break

    weights_df = pd.DataFrame(holdings_list)
    symbol_col = next((col for col in weights_df.columns if col.lower() in ['ticker', 'symbol', 'holdingsymbol']), None)
    weight_col = next((col for col in weights_df.columns if col.lower() in ['percentageoftotalnetassets', 'weight', 'portfolio%', 'percent', 'allocation']), None)
    company_col = next((col for col in weights_df.columns if col.lower() in ['issuername', 'company', 'name', 'holding']), None)
    shares_col = next((col for col in weights_df.columns if col.lower() in ['units', 'holdingshares', 'shares', 'quantity']), None)

    weights_df = weights_df.dropna(subset=[symbol_col])
    weights_df = weights_df[weights_df[symbol_col] != '-']
    if company_col:
        exclude_keywords = ['CASH', 'Future', 'Collateral', 'CME', 'Swap', 'USD']
        for keyword in exclude_keywords:
            weights_df = weights_df[~weights_df[company_col].astype(str).str.contains(keyword, case=False, na=False)]
    
    weights_df = weights_df[[symbol_col, weight_col, shares_col]]
    weights_df.columns = ['Symbol', 'Portfolio%', 'Shares']
    
    if weights_df['Portfolio%'].dtype == 'object':
        weights_df['Portfolio%'] = weights_df['Portfolio%'].astype(str).str.replace('%', '').astype(float)
    else:
        weights_df['Portfolio%'] = weights_df['Portfolio%'].astype(float)
        
    if weights_df['Portfolio%'].max() <= 1.0: weights_df['Portfolio%'] *= 100
    weights_df['Shares'] = pd.to_numeric(weights_df['Shares'], errors='coerce').fillna(0)
    weights_df['Symbol'] = weights_df['Symbol'].str.replace('.', '-')
    
except Exception as e:
    print(f"🚨 데이터 수집 실패: {e}")
    exit()

# ==========================================
# 3. yfinance 주가 연동 및 기여도 계산
# ==========================================
print("\n[3/3] 최종 연산 중...")
stock_data = []

for index, row in weights_df.iterrows():
    ticker = row['Symbol']
    weight_percent = float(row['Portfolio%'])
    current_shares = float(row['Shares'])
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="2d")
        try: market_cap = stock.fast_info['market_cap']
        except: market_cap = stock.info.get('marketCap', 0)
        
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[0]
            current_price = hist['Close'].iloc[1]
            change_percent = ((current_price - prev_close) / prev_close) * 100
            
            point_contribution = (change_percent / 100) * (weight_percent / 100) * yesterday_ndx_close
            
            prev_shares = prev_shares_data.get(ticker, current_shares)
            shares_change = current_shares - prev_shares
            shares_change_pct = (shares_change / prev_shares * 100) if prev_shares > 0 else 0
            
            stock_data.append({
                'Ticker': ticker,
                'Current Price ($)': round(current_price, 2),
                'Change (%)': round(change_percent, 2),
                'Market Cap ($)': market_cap,
                'Point Contribution': round(point_contribution, 2),
                'Weight (%)': round(weight_percent, 2),
                'Shares Held': int(current_shares),
                'Shares Change': int(shares_change),
                'Shares Change (%)': round(shares_change_pct, 4)
            })
        time.sleep(0.2) 
    except Exception as e:
        pass

# ==========================================
# 4. JSON 저장
# ==========================================
if stock_data:
    os.makedirs('data', exist_ok=True)
    
    final_output = {
        "metadata": {
            "market_date": market_date,
            "QQQ_date": QQQ_date,
            "ndx_current_pt": round(current_ndx_close, 2),
            "ndx_point_change": round(ndx_point_change, 2),
            "ndx_percent_change": round(ndx_percent_change, 2)
        },
        "data": stock_data
    }
    
    file_path = f'data/{market_date}.json'
    latest_path = 'data/latest.json'
    
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
    
    print(f"\n✅ 완료! 나스닥 전체 지수 정보가 포함된 최종 JSON이 생성되었습니다.")

# ==========================================
# 5. 웹사이트용 '메뉴판(날짜 목록)' 만들기
# ==========================================
    print("\n[추가작업] 웹사이트에서 읽어갈 날짜 목록(메뉴판)을 생성합니다...")
    
    all_files = os.listdir('data')
    date_files = [
        f.replace('.json', '') for f in all_files 
        if f.endswith('.json') and f not in ['latest.json', 'available_dates.json']
    ]
    
    date_files.sort(reverse=True)
    
    dates_path = 'data/available_dates.json'
    with open(dates_path, 'w', encoding='utf-8') as f:
        json.dump(date_files, f, ensure_ascii=False, indent=4)
        
    print(f"✅ 총 {len(date_files)}개의 날짜 기록이 available_dates.json에 저장되었습니다.")