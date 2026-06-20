import pandas as pd
import yfinance as yf
import json
import os
import requests
import io
import re
from datetime import datetime, timedelta

# ==========================================
# 1. 설정
# ==========================================
ETF_LIST = ['XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLC', 'XLU', 'XLY', 'XLP', 'XLB']
METRICS_FILE = "XL_metrics.json"
DETAILS_DIR = "details"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def format_value(value):
    if value == 0: return "$0"
    abs_val = abs(value)
    if abs_val >= 1e9: return f"${abs_val/1e9:.2f}B"
    elif abs_val >= 1e6: return f"${abs_val/1e6:.2f}M"
    else: return f"${abs_val:,.0f}"

def format_flow(value):
    if value == 0: return "변동 없음"
    return f"{format_value(value)} {'유입' if value > 0 else '유출'}"

def extract_date_from_excel(excel_bytes):
    df_meta = pd.read_excel(excel_bytes, nrows=4, header=None)
    for row in df_meta.values:
        for cell in row:
            if isinstance(cell, str) and "As of" in cell:
                match = re.search(r'As of\s+([0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})', cell)
                if match:
                    raw_date = match.group(1)
                    return datetime.strptime(raw_date, "%d-%b-%Y")
    return None

def is_valid_ticker(val):
    if pd.isna(val): return False
    val_str = str(val).strip()
    if not val_str or val_str == '-': return False
    if any(char.isdigit() for char in val_str): return False
    return True

def main():
    print("🚀 ETF 수급 분석 시작 (가격 정보 포함)...\n")
    
    if not os.path.exists(DETAILS_DIR):
        os.makedirs(DETAILS_DIR)

    if os.path.exists(METRICS_FILE):
        with open(METRICS_FILE, 'r', encoding='utf-8') as f:
            metrics_data = json.load(f)
    else:
        metrics_data = {etf: {"history": {}} for etf in ETF_LIST}

    for etf in ETF_LIST:
        print(f">>> {etf} 분석 중...")
        
        etf_dir = os.path.join(DETAILS_DIR, etf)
        if not os.path.exists(etf_dir):
            os.makedirs(etf_dir)

        # 1. 엑셀 다운로드 및 파싱
        excel_url = f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{etf.lower()}.xlsx"
        try:
            response = requests.get(excel_url, headers=HEADERS)
            response.raise_for_status()
            excel_bytes = io.BytesIO(response.content)
            
            excel_date_obj = extract_date_from_excel(excel_bytes)
            if not excel_date_obj:
                continue
            excel_date_str = excel_date_obj.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"    [오류] {etf} 엑셀 다운로드 실패: {e}")
            continue

        if excel_date_str in metrics_data.get(etf, {}).get("history", {}):
            print(f"    * {etf}: 이미 오늘자({excel_date_str}) 데이터가 있습니다.\n")
            continue

        excel_bytes.seek(0)
        df_holdings = pd.read_excel(excel_bytes, skiprows=4)
        df_holdings.columns = df_holdings.columns.str.strip()
        df_holdings = df_holdings[df_holdings['Ticker'].apply(is_valid_ticker)]
        
        if df_holdings['Weight'].sum() < 2.0:
            df_holdings['Weight'] = df_holdings['Weight'] * 100

        current_raw_data = {}
        for _, row in df_holdings.iterrows():
            clean_ticker = str(row['Ticker']).strip().replace('.', '-')
            current_raw_data[clean_ticker] = {
                "shares": float(row['Shares Held']),
                "weight": float(row['Weight'])
            }

        all_tickers = list(current_raw_data.keys())

        # 2. YFinance 시장 데이터 로드
        end_date_yf = (excel_date_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        start_date_yf = (excel_date_obj - timedelta(days=45)).strftime("%Y-%m-%d")
        
        spy_data = yf.download("SPY", start=start_date_yf, end=end_date_yf, progress=False)['Close']
        etf_history = yf.download(etf, start=start_date_yf, end=end_date_yf, progress=False)
        stocks_data = yf.download(all_tickers, start=start_date_yf, end=end_date_yf, progress=False)['Close']

        # 3. ETF 자체 가격 및 변화량 계산
        etf_price, etf_change_amt, etf_change_pct = 0.0, 0.0, 0.0
        try:
            etf_close_arr = etf_history['Close'].to_numpy().flatten()
            if len(etf_close_arr) >= 1:
                etf_price = float(etf_close_arr[-1])
            if len(etf_close_arr) >= 2:
                prev_price = float(etf_close_arr[-2])
                etf_change_amt = etf_price - prev_price
                etf_change_pct = (etf_change_amt / prev_price) * 100
        except Exception as e:
            print(f"    * ETF 가격 계산 오류: {e}")

        # SPY 5일 수익률 사전 계산 (개별 종목 초과수익률 비교용)
        spy_return_5d = 0.0
        try:
            spy_close_arr = spy_data.to_numpy().flatten()
            if len(spy_close_arr) >= 6:
                spy_return_5d = ((float(spy_close_arr[-1]) - float(spy_close_arr[-6])) / float(spy_close_arr[-6])) * 100
        except Exception as e:
            print(f"    * SPY 5일 수익률 계산 오류: {e}")

        # 4. 전체 지표(Metrics) 계산
        etf_vol_arr = etf_history['Volume'].to_numpy().flatten()
        current_vol = float(etf_vol_arr[-1])
        avg_vol_20 = float(etf_vol_arr[-21:-1].mean()) if len(etf_vol_arr) > 20 else 0.0
        rvol = current_vol / avg_vol_20 if avg_vol_20 > 0 else 0

        try:
            excess_return = (((etf_price - float(etf_close_arr[-6])) / float(etf_close_arr[-6])) * 100) - spy_return_5d
        except:
            excess_return = 0.0

        weighted_breadth = 0.0
        if isinstance(stocks_data, pd.DataFrame) and len(stocks_data) >= 2:
            for ticker, data in current_raw_data.items():
                if ticker in stocks_data.columns:
                    valid_series = stocks_data[ticker].dropna()
                    if len(valid_series) >= 2:
                        prices = valid_series.to_numpy().flatten()
                        if float(prices[-1]) > float(prices[-2]):
                            weighted_breadth += data["weight"]

        # 5. 종목별 상세 흐름 계산
        total_fund_flow_dollar = 0.0
        ticker_details = {}
        
        saved_detail_files = sorted([f for f in os.listdir(etf_dir) if f.endswith('.json')])
        is_first_run = len(saved_detail_files) == 0

        last_raw_data = {}
        if not is_first_run:
            last_file_path = os.path.join(etf_dir, saved_detail_files[-1])
            with open(last_file_path, 'r', encoding='utf-8') as f:
                last_detail_data = json.load(f)
                last_raw_data = last_detail_data.get("raw_data", {})

        for ticker, c_data in current_raw_data.items():
            c_shares = c_data["shares"]
            c_weight = c_data["weight"]

            share_diff = c_shares - last_raw_data.get(ticker, {}).get("shares", c_shares) if not is_first_run else 0

            price, price_change_pct = 0.0, 0.0
            stock_excess_return = 0.0  # 개별 종목의 SPY 대비 5일 초과 수익률 변수 초기화

            if isinstance(stocks_data, pd.DataFrame) and ticker in stocks_data.columns:
                valid_series = stocks_data[ticker].dropna()
                prices_arr = valid_series.to_numpy().flatten()
                
                # 1일 주가 변화량 계산
                if len(prices_arr) >= 1: 
                    price = float(prices_arr[-1])
                if len(prices_arr) >= 2 and float(prices_arr[-2]) > 0:
                    price_change_pct = ((price - float(prices_arr[-2])) / float(prices_arr[-2])) * 100
                
                # 5일 초과 수익률 계산 (종목의 5일 수익률 - SPY의 5일 수익률)
                if len(prices_arr) >= 6 and float(prices_arr[-6]) > 0:
                    stock_return_5d = ((price - float(prices_arr[-6])) / float(prices_arr[-6])) * 100
                    stock_excess_return = stock_return_5d - spy_return_5d

            holdings_value = c_shares * price
            dollar_flow = share_diff * price
            total_fund_flow_dollar += dollar_flow
            flow_ratio_pct = (dollar_flow / holdings_value * 100) if holdings_value > 0 else 0.0

            ticker_details[ticker] = {
                "price": round(price, 2),
                "price_change_pct": round(price_change_pct, 2),
                "rs_momentum": round(stock_excess_return, 2),  # 종목별 초과 수익률 수치 추가
                "weight_current": round(c_weight, 4),
                "shares_current": int(c_shares),
                "shares_change": int(share_diff),
                "dollar_flow": round(dollar_flow, 2),
                "display": {
                    "price": f"${price:.2f}",
                    "price_change_pct": f"{'+' if price_change_pct > 0 else ''}{price_change_pct:.2f}%",
                    "rs_momentum": f"SPY 대비 {'+' if stock_excess_return > 0 else ''}{stock_excess_return:.2f}%p",  # 포맷팅 추가
                    "shares_current": f"{int(c_shares):,}주",
                    "holdings_value": format_value(holdings_value),
                    "dollar_flow": format_flow(dollar_flow),
                    "flow_ratio_pct": f"{flow_ratio_pct:+.2f}%"
                }
            }

        # 6. 20일 이동평균 배수 계산
        fund_flow_display = "데이터 누적 중" if is_first_run else format_flow(total_fund_flow_dollar)
        flow_ratio_20d_display, flow_ratio_20d_value = "데이터 부족", 0.0

        if etf not in metrics_data: metrics_data[etf] = {"history": {}}
        past_dates = sorted(list(metrics_data[etf]["history"].keys()))
        
        if len(past_dates) > 0:
            recent_20 = past_dates[-20:]
            past_flows_abs = [abs(metrics_data[etf]["history"][d]["Fund_Flow"]["value"]) for d in recent_20]
            avg_flow_20d = sum(past_flows_abs) / len(past_flows_abs) if len(past_flows_abs) > 0 else 0
            
            if avg_flow_20d > 0:
                flow_ratio_20d_value = abs(total_fund_flow_dollar) / avg_flow_20d
                flow_ratio_20d_display = f"{flow_ratio_20d_value:.1f}배"

        # 7. 데이터 저장 1: XL_metrics.json (ETF 가격 정보 포함)
        metrics_data[etf]["history"][excel_date_str] = {
            "ETF_Price": {
                "value": round(etf_price, 2),
                "change_amt": round(etf_change_amt, 2),
                "change_pct": round(etf_change_pct, 2),
                "display": f"${etf_price:.2f} ({'+' if etf_change_amt > 0 else ''}{etf_change_amt:.2f} / {'+' if etf_change_pct > 0 else ''}{etf_change_pct:.2f}%)"
            },
            "Fund_Flow": {"value": round(total_fund_flow_dollar, 2), "display": fund_flow_display},
            "Fund_Flow_Intensity_20D": {"value": round(flow_ratio_20d_value, 2), "display": flow_ratio_20d_display},
            "Relative_Volume": {"value": round(rvol, 2), "display": f"{rvol:.1f}배"},
            "RS_Momentum": {"value": round(excess_return, 2), "display": f"SPY 대비 {'+' if excess_return > 0 else ''}{excess_return:.2f}%p"},
            "Weighted_Breadth": {"value": round(weighted_breadth, 2), "display": f"{weighted_breadth:.1f}%"}
        }

        # 8. 데이터 저장 2: details/ETF명/YYYY-MM-DD.json
        detail_file_path = os.path.join(etf_dir, f"{excel_date_str}.json")
        detail_data = {
            "etf": etf,
            "date": excel_date_str,
            "ticker_details": ticker_details,
            "raw_data": current_raw_data 
        }
        
        with open(detail_file_path, 'w', encoding='utf-8') as f:
            json.dump(detail_data, f, indent=4, ensure_ascii=False)

        print(f"    [성공] 지표 업데이트 완료 & 상세파일 생성 ({detail_file_path})\n")

    with open(METRICS_FILE, 'w', encoding='utf-8') as f:
        json.dump(metrics_data, f, indent=4, ensure_ascii=False)
        
    print("모든 처리가 완료되었습니다!")

if __name__ == "__main__":
    main()