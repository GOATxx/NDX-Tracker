import yfinance as yf
import pandas as pd
import cloudscraper
from io import StringIO
import os
from datetime import datetime
import time

print("🚀 나스닥 100 기여도 계산 (비중 공식 적용) 시작...\n")

# ==========================================
# 1. 어제 나스닥 100 지수(종가) 가져오기
# ==========================================
print("[1/3] 어제 나스닥 100 지수를 확인합니다...")
ndx = yf.Ticker("^NDX")
hist_ndx = ndx.history(period="2d")

if len(hist_ndx) < 2:
    print("🚨 지수 데이터를 가져오는 데 실패했습니다.")
    exit()

yesterday_ndx_close = hist_ndx['Close'].iloc[0] # 어제 종가
print(f" - 어제 나스닥 100 종가: {yesterday_ndx_close:,.2f} pt")

# ==========================================
# 2. Slickcharts에서 QQQ 종목별 비중(Weight) 수집
# ==========================================
print("\n[2/3] Slickcharts에서 QQQ 종목별 비중(Weight)을 크롤링합니다...")
url = 'https://www.slickcharts.com/symbol/QQQ/holdings'

# requests 대신 cloudscraper를 생성합니다.
scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})

try:
    # scraper.get() 을 사용해 보안을 우회하여 데이터를 가져옵니다.
    response = scraper.get(url)
    
    # 만약 여전히 403 에러가 나면 여기서 멈춥니다.
    response.raise_for_status() 
    
    tables = pd.read_html(StringIO(response.text))
    weights_df = tables[0]
    
    # --- 아래부터는 앞서 작성한 데이터 정제 코드와 완전히 동일합니다 ---
    
    # 1. 티커(Symbol)가 아예 없거나(NaN), '-' 기호로 표기된 경우 삭제
    weights_df = weights_df.dropna(subset=['Symbol'])
    weights_df = weights_df[weights_df['Symbol'] != '-']
    
    # 2. 회사명(Holding)에 현금이나 선물 관련 키워드가 들어간 경우 솎아내기
    exclude_keywords = ['Cash', 'Future', 'Collateral', 'Cme', 'Swap']
    for keyword in exclude_keywords:
        weights_df = weights_df[~weights_df['Holding'].str.contains(keyword, case=False, na=False)]
    
    # 필요한 컬럼만 추출
    weights_df = weights_df[['Symbol', 'Portfolio%']]
    
    # 🧹 [추가된 핵심 코드] '%' 기호를 빈칸으로 없애고, 실수(float)로 변환합니다.
    weights_df['Portfolio%'] = weights_df['Portfolio%'].astype(str).str.replace('%', '').astype(float)
    
    # yfinance 포맷에 맞게 티커 수정 (예: BRK.B -> BRK-B)
    weights_df['Symbol'] = weights_df['Symbol'].str.replace('.', '-')
    
    print(f" - 성공! 순수 주식 {len(weights_df)}개 종목의 비중을 가져왔습니다.")
    
except Exception as e:
    print(f"🚨 비중 데이터 수집 실패: {e}")
    exit()

# ==========================================
# 3. yfinance에서 등락률 가져오기 및 기여도 계산
# ==========================================
print("\n[3/3] 주가 데이터 수집, 기여도(Point) 및 시가총액 계산 중...")
stock_data = []

# 전체 100개를 다 가져오려면 weights_df.iterrows()를 사용하세요.
for index, row in weights_df.iterrows():
    ticker = row['Symbol']
    weight_percent = float(row['Portfolio%'])
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="2d")
        
        # 💡 [추가된 부분] 시가총액 가져오기 (데이터가 누락될 경우를 대비한 안전장치 포함)
        try:
            market_cap = stock.fast_info['market_cap']
        except:
            market_cap = stock.info.get('marketCap', 0)
        
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[0]
            current_price = hist['Close'].iloc[1]
            
            # 1) 등락률 계산 (%)
            change_percent = ((current_price - prev_close) / prev_close) * 100
            
            # 2) 마법의 수학 공식 적용 (기여도)
            point_contribution = (change_percent / 100) * (weight_percent / 100) * yesterday_ndx_close
            
            # 3) 결과 저장 (Market Cap 추가)
            stock_data.append({
                'Ticker': ticker,
                'Current Price ($)': round(current_price, 2),
                'Change (%)': round(change_percent, 2),
                'Market Cap ($)': market_cap,
                'Weight (%)': weight_percent,
                'Point Contribution': round(point_contribution, 2)

            })
            print(f" [{ticker}] 비중: {weight_percent:5.2f}% | 변동: {change_percent:+5.2f}% | 기여도: {point_contribution:+6.2f} pt")
            
        time.sleep(0.2) # yfinance 서버 차단 방지용 휴식
        
    except Exception as e:
        print(f" [{ticker}] 데이터 수집 에러: {e}")


# ==========================================
# 4. 데이터 정리, 검증 및 JSON 저장
# ==========================================
if not stock_data:
    print("\n🚨 [경고] 수집된 주식 데이터가 없습니다.")
else:
    final_df = pd.DataFrame(stock_data)

    # 📊 데이터 검증: 총 변동 포인트 합산
    total_points_change = final_df['Point Contribution'].sum()
    print(f"\n📈 [검증] 수집된 종목들의 나스닥 지수 변동 총합: {total_points_change:+.2f} pt")

    # JSON 파일로 저장
    os.makedirs('data', exist_ok=True)
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # 1. 날짜별 기록용 저장
    file_path = f'data/{today_str}.json'
    final_df.to_json(file_path, orient='records', force_ascii=False, indent=4)
    
    # 2. 웹사이트가 항상 읽어갈 최신(latest) 파일 덮어쓰기
    latest_path = 'data/latest.json'
    final_df.to_json(latest_path, orient='records', force_ascii=False, indent=4)
    
    print(f"✅ 완료! {file_path} 및 latest.json이 저장되었습니다.")