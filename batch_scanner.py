import yfinance as yf
import pandas as pd
import json
import os
import requests
import time
from datetime import datetime, timedelta
import twstock
import warnings
warnings.filterwarnings('ignore')

def get_all_taiwan_stocks():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在取得全台股與 ETF 代碼清單...")
    tickers = []
    for code, info in twstock.codes.items():
        # 🌟 關鍵解鎖：把 'ETF' 也加入抓取清單中！
        if info.type in ['股票', 'ETF']:
            tickers.append(f"{code}.TW" if info.market == '上市' else f"{code}.TWO")
    return tickers

def check_institutional_buy(ticker):
    clean_ticker = ticker.split('.')[0] 
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={clean_ticker}&start_date={start_date}"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        
        if data.get('msg') != 'success':
            print(f"    [❌ API 阻擋] {data.get('msg')}")
            return False, False
            
        if not data.get('data'):
            print(f"    [⚠️ 無法人數據] {clean_ticker} 近期無進出紀錄")
            return False, False
        
        df = pd.DataFrame(data['data'])
        df['buy'] = pd.to_numeric(df['buy'].astype(str).str.replace(',', '', regex=False), errors='coerce').fillna(0)
        df['sell'] = pd.to_numeric(df['sell'].astype(str).str.replace(',', '', regex=False), errors='coerce').fillna(0)
        
        dates = sorted(df['date'].unique())[-3:]
        f_net_list = []
        t_net_list = []
        
        for d in dates:
            daily = df[df['date'] == d]
            foreign = daily[daily['name'].str.contains('外資|Foreign', na=False, case=False)]
            trust = daily[daily['name'].str.contains('投信|Trust|Investment', na=False, case=False)]
            
            f_net = (foreign['buy'].sum() - foreign['sell'].sum()) / 1000
            t_net = (trust['buy'].sum() - trust['sell'].sum()) / 1000
            
            f_net_list.append(round(f_net))
            t_net_list.append(round(t_net))
            
        f_total = sum(f_net_list)
        t_total = sum(t_net_list)
        
        foreign_pass = bool((f_total > 0) and (len(f_net_list) > 0 and f_net_list[-1] > 0))
        trust_pass = bool((t_total > 0) and (len(t_net_list) > 0 and t_net_list[-1] > 0))
        
        if f_total == 0 and t_total == 0:
            print(f"    [🔍 X光探照] 算出來是0！原始欄位: {df.columns.tolist()} | 原始名稱: {df['name'].unique()[:3].tolist()}")
        else:
            print(f"    外資近{len(dates)}次進出(張): {f_net_list} (累積:{f_total}) | 投信: {t_net_list} (累積:{t_total})")
            
        return foreign_pass, trust_pass
        
    except Exception as e:
        print(f"    [❌ 程式錯誤] {e}")
        return False, False

def run_daily_scan():
    TICKERS = get_all_taiwan_stocks()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 開始下載報價 (約需數分鐘)...")
    data = yf.download(TICKERS, period="100d", group_by='ticker', progress=True)
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 開始計算全市場技術指標...")
    all_stocks_data = []
    
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna()
            if len(df) < 60: continue
                
            df['5MA'] = df['Close'].rolling(window=5).mean()
            df['10MA'] = df['Close'].rolling(window=10).mean()
            df['20MA'] = df['Close'].rolling(window=20).mean()
            df['60MA'] = df['Close'].rolling(window=60).mean()
            df['5VMA'] = df['Volume'].rolling(window=5).mean()
            
            df['20_High'] = df['Close'].shift(1).rolling(window=20).max()
            df['60_High'] = df['Close'].shift(1).rolling(window=60).max()
            df['10_Low'] = df['Low'].shift(1).rolling(window=10).min()
            
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            
            is_golden = bool((yesterday['5MA'] <= yesterday['10MA']) and (today['5MA'] > today['10MA']))
            is_bull = bool((today['5MA'] > today['10MA']) and (today['10MA'] > today['20MA']) and (today['20MA'] > today['60MA']))
            is_20H = bool(today['Close'] > today['20_High'])
            is_60H = bool(today['Close'] > today['60_High'])
            vol_ratio = 0.0 if float(today['5VMA']) == 0 else round(float(today['Volume'] / today['5VMA']), 2)
            
            open_p = float(today['Open'])
            close_p = float(today['Close'])
            high_p = float(today['High'])
            low_p = float(today['Low'])
            
            real_body = abs(close_p - open_p)
            lower_shadow = min(close_p, open_p) - low_p
            upper_shadow = high_p - max(close_p, open_p)
            
            is_bottom = bool(low_p < float(today['10_Low']))
            is_hammer = bool((lower_shadow > 2 * real_body) and (lower_shadow > upper_shadow) and (lower_shadow > 0))
            is_bottom_reversal = bool(is_bottom and is_hammer)
            
            last_date = str(df.index[-1].date())
            
            if float(today['5VMA']) < 500000:
                continue
            
            if not (is_golden or is_bull or is_bottom_reversal or vol_ratio >= 1.5):
                continue

            clean_symbol = ticker.split('.')[0]
            try:
                stock_name = twstock.codes[clean_symbol].name
                # 🌟 新增：辨識它是股票還是 ETF
                stock_type = twstock.codes[clean_symbol].type
            except:
                stock_name = "未知"
                stock_type = "股票"

            stock_info = {
                'symbol': clean_symbol,  
                'name': stock_name,      
                'type': stock_type,       # 🌟 存入資料庫
                'date': last_date,
                'price': round(close_p, 2),
                'volume': int(today['Volume'] / 1000),
                'vol_ratio': vol_ratio,
                'is_golden': is_golden,
                'is_bull': is_bull,
                'is_20H': is_20H,
                'is_60H': is_60H,
                'is_bottom_reversal': is_bottom_reversal,
                'foreign_buy': False, 
                'trust_buy': False
            }
            all_stocks_data.append(stock_info)
            
        except Exception:
            continue

    chip_candidates = [s for s in all_stocks_data if s['vol_ratio'] >= 1.2 and (s['is_golden'] or s['is_bull'] or s['is_20H'] or s['is_60H'] or s['is_bottom_reversal'])]
    chip_candidates = sorted(chip_candidates, key=lambda x: x['vol_ratio'], reverse=True)[:100]
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 尋找 Top {len(chip_candidates)} 爆量/型態強勢股的法人籌碼...")
    
    for i, stock in enumerate(chip_candidates):
        print(f"查詢籌碼 ({i+1}/{len(chip_candidates)}): {stock['symbol']} ...")
        f_buy, t_buy = check_institutional_buy(stock['symbol'])
        
        for main_stock in all_stocks_data:
            if main_stock['symbol'] == stock['symbol']:
                main_stock['foreign_buy'] = f_buy
                main_stock['trust_buy'] = t_buy
                break
                
        time.sleep(1.5) 

    save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_stocks_data, f, ensure_ascii=False, indent=4)
        
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 掃描與建檔完畢！共 {len(all_stocks_data)} 檔寫入。")

if __name__ == "__main__":
    run_daily_scan()