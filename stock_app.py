import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import google.generativeai as genai
from serpapi import GoogleSearch
from ta.volatility import BollingerBands
from ta.trend import MACD, SMAIndicator, EMAIndicator
from ta.momentum import RSIIndicator
from datetime import datetime, timedelta
import pytz
import os

# --- 配置 ---
st.set_page_config(layout="wide", page_title="Fin AI Agent")

# --- 輔助函數 ---
@st.cache_data
def get_stock_data_enhanced(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    info = stock.info

    financials = stock.financials.T if not stock.financials.empty else pd.DataFrame()
    balance_sheet = stock.balance_sheet.T if not stock.balance_sheet.empty else pd.DataFrame()
    cashflow = stock.cashflow.T if not stock.cashflow.empty else pd.DataFrame()

    hist_data_max = stock.history(period="5y")
    if not hist_data_max.empty:
        if 'Volume' in hist_data_max.columns and (hist_data_max['Volume'] == 0).all():
            st.warning(f"{ticker_symbol} 歷史數據中成交量 (Volume) 全部為 0。")
        elif 'Volume' not in hist_data_max.columns or hist_data_max['Volume'].isnull().all():
            st.warning(f"{ticker_symbol} 歷史數據中成交量 (Volume) 缺失或全為 NaN。")

        if hist_data_max.index.tz is None:
            try:
                tz_name = info.get('exchangeTimezoneName', 'America/New_York')
                hist_data_max.index = hist_data_max.index.tz_localize(tz_name, nonexistent='shift_forward', ambiguous='infer')
            except pytz.exceptions.UnknownTimeZoneError:
                st.warning(f"未知交易所時區: {info.get('exchangeTimezoneName')}。嘗試 'America/New_York'。")
                try:
                    hist_data_max.index = hist_data_max.index.tz_localize('America/New_York', nonexistent='shift_forward', ambiguous='infer')
                except Exception as e_ny:
                    st.warning(f"使用 'America/New_York' 本地化失敗: {e_ny}。嘗試 UTC。")
                    hist_data_max.index = hist_data_max.index.tz_localize('UTC', nonexistent='shift_forward', ambiguous='infer')
            except Exception as e_gen:
                st.warning(f"時區本地化出錯: {e_gen}。嘗試 UTC。")
                hist_data_max.index = hist_data_max.index.tz_localize('UTC', nonexistent='shift_forward', ambiguous='infer')
    else:
        st.warning(f"無法獲取 {ticker_symbol} 的5年歷史股價數據 (yf.history 返回空)。")

    dividends = stock.dividends
    major_holders = stock.major_holders
    institutional_holders = stock.institutional_holders
    recommendations = stock.recommendations
    news_yf = stock.news

    return info, financials, balance_sheet, cashflow, hist_data_max, dividends, major_holders, institutional_holders, recommendations, news_yf

# 修改後的 Gemini API 調用函數，支持多輪對話歷史
def get_ai_chat_response_from_gemini(api_key, user_query, chat_history_for_api, initial_context=""):
    if not api_key:
        return "錯誤：未提供 Google AI API 金鑰。"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')

        current_chat_session = model.start_chat(history=chat_history_for_api)
        response = current_chat_session.send_message(user_query)
        
        updated_history = current_chat_session.history
        
        return response.text if response.parts else "AI 分析無法生成內容。", updated_history

    except Exception as e:
        return f"Gemini AI 分析出錯: {e}", chat_history_for_api # 出錯時返回原始歷史

@st.cache_data
def get_serpapi_news(query, serp_api_key, num_results=5):
    if not serp_api_key:
        return None, "錯誤：未提供 SERP API 金鑰。"
    try:
        params = {
            "q": query,
            "engine": "google_news",
            "api_key": serp_api_key,
            "num": num_results,
            "tbm": "nws",
            "hl": "zh-tw",
            "gl": "tw"
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if "news_results" in results:
            return results["news_results"], None
        elif "organic_results" in results:
             return results["organic_results"], None
        else:
            return None, f"SERP API 未返回預期的 'news_results'。收到: {list(results.keys())}"
            
    except Exception as e:
        return None, f"SERP API 搜尋出錯: {e}"

# --- 側邊欄 ---
st.sidebar.title("📈 Fin Agent 股票分析")
ticker_symbol_input = st.sidebar.text_input("輸入股票代碼 (例如：NVDA)", "NVDA").upper()
google_api_key_input = st.sidebar.text_input("輸入 Google AI API 金鑰 (解鎖進階LLM評估功能)", type="password", key="google_api_key")
serp_api_key_input = st.sidebar.text_input("輸入 SERP API 金鑰 (解鎖進階新聞搜尋功能)", type="password", key="serp_api_key")

DEFAULT_PERIODS = ["1個月", "3個月", "6個月", "今年以來(YTD)", "1年", "2年", "5年", "全部"]
st.sidebar.subheader("股價圖表設定")
selected_period = st.sidebar.selectbox("選擇時間區間:", DEFAULT_PERIODS, index=5, key="sb_period_select")

analyze_button = st.sidebar.button("🚀 分析股票", key="btn_analyze")

# --- 主內容區 ---
st.title(f"【Fin Agent】AI 驅動進階股票分析 ({ticker_symbol_input or ''})")

# 初始化 session_state 中的聊天相關變數
if "initial_ai_analysis_done" not in st.session_state:
    st.session_state.initial_ai_analysis_done = False
if "chat_messages" not in st.session_state: # 用於 Streamlit UI 顯示
    st.session_state.chat_messages = []
if "gemini_chat_history" not in st.session_state: # 用於傳遞給 Gemini API
    st.session_state.gemini_chat_history = []
if "initial_analysis_context" not in st.session_state:
    st.session_state.initial_analysis_context = ""


if 'stock_data_loaded' not in st.session_state:
    st.session_state.stock_data_loaded = False
if 'current_ticker' not in st.session_state:
    st.session_state.current_ticker = ""
if 'serpapi_results' not in st.session_state:
    st.session_state.serpapi_results = None
if 'serpapi_error' not in st.session_state:
    st.session_state.serpapi_error = None


if analyze_button and ticker_symbol_input:
    if st.session_state.current_ticker != ticker_symbol_input or not st.session_state.stock_data_loaded:
        st.cache_data.clear()
        st.session_state.stock_data_loaded = False
        st.session_state.serpapi_results = None
        st.session_state.serpapi_error = None
        # 清空聊天記錄和初始分析標記
        st.session_state.initial_ai_analysis_done = False
        st.session_state.chat_messages = []
        st.session_state.gemini_chat_history = []
        st.session_state.initial_analysis_context = ""


    if not st.session_state.stock_data_loaded:
        with st.spinner(f"⏳ 正在獲取 {ticker_symbol_input} 的全方位數據..."):
            try:
                (info, financials, balance_sheet, cashflow, hist_data_max, dividends,
                 major_holders, institutional_holders, recommendations, news_yf) = get_stock_data_enhanced(ticker_symbol_input)

                st.session_state.info = info
                st.session_state.financials = financials
                st.session_state.balance_sheet = balance_sheet
                st.session_state.cashflow = cashflow
                st.session_state.hist_data_max = hist_data_max
                st.session_state.dividends = dividends
                st.session_state.major_holders = major_holders
                st.session_state.institutional_holders = institutional_holders
                st.session_state.recommendations = recommendations
                st.session_state.news_yf = news_yf
                st.session_state.current_ticker = ticker_symbol_input
                
                if hist_data_max is not None and not hist_data_max.empty:
                    st.session_state.stock_data_loaded = True

                    if serp_api_key_input and info.get('longName'):
                        company_name_for_search = info.get('longName', ticker_symbol_input)
                        search_query = f'"{company_name_for_search}" OR "{ticker_symbol_input}" 財經 OR 金融 OR 股票 OR 市場分析 新聞'
                        st.session_state.serpapi_results, st.session_state.serpapi_error = get_serpapi_news(search_query, serp_api_key_input, num_results=5)
                    elif not serp_api_key_input:
                        st.session_state.serpapi_error = "未提供 SERP API 金鑰，跳過外部新聞搜尋。"
                else:
                    st.session_state.stock_data_loaded = False
                    st.error(f"未能成功獲取 {ticker_symbol_input} 的歷史股價數據。請檢查股票代碼或稍後再試。")
                st.rerun() # Rerun to update UI with loaded data
            except Exception as e:
                st.error(f"獲取股票數據時發生嚴重錯誤: {e}")
                st.session_state.stock_data_loaded = False
                st.session_state.current_ticker = ticker_symbol_input # Keep current ticker to show error context

if st.session_state.stock_data_loaded and \
    hasattr(st.session_state, 'info') and st.session_state.info and \
    hasattr(st.session_state, 'hist_data_max') and \
    st.session_state.hist_data_max is not None and not st.session_state.hist_data_max.empty:

    info = st.session_state.info
    financials = st.session_state.financials
    balance_sheet = st.session_state.balance_sheet
    cashflow = st.session_state.cashflow
    hist_data_max = st.session_state.hist_data_max
    dividends = st.session_state.dividends
    major_holders = st.session_state.major_holders
    institutional_holders = st.session_state.institutional_holders
    recommendations = st.session_state.recommendations
    news_yf = st.session_state.news_yf
    current_ticker = st.session_state.current_ticker
    serpapi_results = st.session_state.serpapi_results
    serpapi_error = st.session_state.serpapi_error

    company_name = info.get('longName', current_ticker)
    st.header(f"{company_name} ({current_ticker})")
    st.write(f"行業: {info.get('industry', 'N/A')} | 產業: {info.get('sector', 'N/A')}")
    st.markdown("---")

    tab_titles = ["總覽", "股價分析", "財務數據", "公司資訊", "AI 智能分析與對話"]
    tab_overview, tab_price_analysis, tab_financials, tab_company_profile, tab_ai_chat = st.tabs(tab_titles)

    data_for_period = pd.DataFrame()
    if hist_data_max.index.tz:
        df_timezone = hist_data_max.index.tz
        end_date_aware = datetime.now(df_timezone)
        
        if selected_period == "1個月": start_date_aware = end_date_aware - timedelta(days=30)
        elif selected_period == "3個月": start_date_aware = end_date_aware - timedelta(days=90)
        elif selected_period == "6個月": start_date_aware = end_date_aware - timedelta(days=180)
        elif selected_period == "今年以來(YTD)": start_date_aware = datetime(end_date_aware.year, 1, 1, tzinfo=df_timezone)
        elif selected_period == "1年": start_date_aware = end_date_aware - timedelta(days=365)
        elif selected_period == "2年": start_date_aware = end_date_aware - timedelta(days=365*2)
        elif selected_period == "5年": start_date_aware = end_date_aware - timedelta(days=365*5)
        else: 
            start_date_aware = hist_data_max.index.min()
            if start_date_aware.tzinfo is None :
                start_date_aware = df_timezone.localize(start_date_aware) if hasattr(df_timezone, 'localize') else start_date_aware.replace(tzinfo=df_timezone)

        start_date_aware = start_date_aware.astimezone(df_timezone)
        end_date_aware = end_date_aware.astimezone(df_timezone)

        data_for_period = hist_data_max[
            (hist_data_max.index >= start_date_aware) &
            (hist_data_max.index <= end_date_aware)
        ].copy()
    else:
        st.warning("歷史數據缺乏有效的時區信息，可能導致圖表篩選不準確。")
        data_for_period = hist_data_max.copy()

    with tab_overview:
        st.subheader("關鍵指標與股價摘要")
        col1, col2, col3, col4 = st.columns(4)

        current_price_val_display = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))
        price_change_abs_val = info.get('regularMarketChange')
        previous_close_val_calc = info.get('regularMarketPreviousClose')
        yfinance_pct_raw_for_delta = info.get('regularMarketChangePercent')

        delta_metric_value = None 

        if isinstance(price_change_abs_val, (int, float)): 
            calculated_pct_change_for_delta = 0.0
            
            if isinstance(previous_close_val_calc, (int, float)) and previous_close_val_calc != 0:
                calculated_pct_change_for_delta = (price_change_abs_val / previous_close_val_calc) * 100.0
            elif isinstance(current_price_val_display, (int, float)) and current_price_val_display != price_change_abs_val:
                inferred_prev_close_for_delta = current_price_val_display - price_change_abs_val
                if inferred_prev_close_for_delta != 0:
                    calculated_pct_change_for_delta = (price_change_abs_val / inferred_prev_close_for_delta) * 100.0
                elif isinstance(yfinance_pct_raw_for_delta, (int, float)):
                    if abs(yfinance_pct_raw_for_delta) < 1.0 and yfinance_pct_raw_for_delta !=0: # Check if it's a ratio (e.g., 0.05 for 5%)
                        calculated_pct_change_for_delta = yfinance_pct_raw_for_delta * 100.0
                    else: # Assume it's already a percentage (e.g., 5 for 5%)
                        calculated_pct_change_for_delta = yfinance_pct_raw_for_delta
                else: calculated_pct_change_for_delta = 0.0 # Fallback
            elif isinstance(yfinance_pct_raw_for_delta, (int, float)):
                if abs(yfinance_pct_raw_for_delta) < 1.0 and yfinance_pct_raw_for_delta !=0:
                    calculated_pct_change_for_delta = yfinance_pct_raw_for_delta * 100.0
                else:
                    calculated_pct_change_for_delta = yfinance_pct_raw_for_delta
            else: 
                calculated_pct_change_for_delta = 0.0
            
            delta_metric_value = f"{price_change_abs_val:+.2f} ({calculated_pct_change_for_delta:.2f}%)"

        elif isinstance(yfinance_pct_raw_for_delta, (int, float)): # Only percentage available
            calculated_pct_change_for_delta = 0.0
            if abs(yfinance_pct_raw_for_delta) < 1.0 and yfinance_pct_raw_for_delta !=0 :
                calculated_pct_change_for_delta = yfinance_pct_raw_for_delta * 100.0
            else:
                calculated_pct_change_for_delta = yfinance_pct_raw_for_delta
            delta_metric_value = f"({calculated_pct_change_for_delta:.2f}%)" # No absolute change to show
        
        with col1: st.metric(label="當前價格", value=f"{current_price_val_display:.2f}" if isinstance(current_price_val_display, (int,float)) else "N/A", delta=delta_metric_value)
        
        with col2: st.metric(label="市值", value=f"{info.get('marketCap', 0)/1_000_000_000_000:.2f} 兆" if isinstance(info.get('marketCap'), (int, float)) and info.get('marketCap', 0) > 0 else "N/A")
        with col3: st.metric(label="本益比 (TTM)", value=f"{info.get('trailingPE'):.2f}" if isinstance(info.get('trailingPE'), (int, float)) else "N/A")
        with col4: st.metric(label="每股盈餘 (EPS)", value=f"{info.get('trailingEps'):.2f}" if isinstance(info.get('trailingEps'), (int, float)) else "N/A")

        col5, col6, col7, col8 = st.columns(4)
        with col5: st.metric(label="股價淨值比", value=f"{info.get('priceToBook'):.2f}" if isinstance(info.get('priceToBook'), (int, float)) else "N/A")
        
        dividend_yield_raw_val = info.get('dividendYield')
        dividend_yield_display_str = "N/A"
        if isinstance(dividend_yield_raw_val, (int, float)) and pd.notna(dividend_yield_raw_val) and dividend_yield_raw_val >= 0:
            final_yield_pct = 0.0
            if dividend_yield_raw_val == 0:
                final_yield_pct = 0.0
            elif dividend_yield_raw_val >= 1.0: # If yfinance returns e.g., 3.0 for 3%
                final_yield_pct = dividend_yield_raw_val
            else: # If yfinance returns e.g., 0.03 for 3%
                final_yield_pct = dividend_yield_raw_val * 100.0
            dividend_yield_display_str = f"{final_yield_pct:.2f}%"
        
        with col6: st.metric(label="股息殖利率", value=dividend_yield_display_str)
        
        with col7: st.metric(label="Beta係數", value=f"{info.get('beta'):.2f}" if isinstance(info.get('beta'), (int, float)) else "N/A")
        with col8: st.metric(label="成交量", value=f"{info.get('regularMarketVolume', 0):,}" if isinstance(info.get('regularMarketVolume'), (int, float)) else "N/A")

        st.subheader(f"近期股價走勢 ({selected_period})")
        if not data_for_period.empty and 'Close' in data_for_period.columns and not data_for_period['Close'].isnull().all():
            fig_overview_price = px.line(data_for_period, y="Close", title=f"{current_ticker} 收盤價 ({selected_period})")
            st.plotly_chart(fig_overview_price, use_container_width=True)
        elif not hist_data_max.empty :
            st.info(f"在選定的時間區間 ({selected_period}) 內缺少股價數據 (總覽圖)。")
        else:
            st.info("無歷史股價數據可供展示 (總覽圖)。")

    with tab_price_analysis:
        st.subheader(f"{current_ticker} 股價圖表與技術分析")
        hist_data_processed = data_for_period.copy()

        if hist_data_processed.empty:
            if not hist_data_max.empty:
                st.warning(f"在選定時間區間 ({selected_period}) 內沒有 {current_ticker} 的股價數據。圖表無法繪製。")
            else:
                st.warning(f"沒有 {current_ticker} 的原始歷史股價數據。圖表無法繪製。")
        else:
            if 'Close' in hist_data_processed.columns and not hist_data_processed['Close'].isnull().all():
                st.sidebar.subheader("移動平均線 (MA)")
                show_sma = st.sidebar.checkbox("顯示 SMA", value=True, key="cb_sma")
                sma_period = st.sidebar.slider("SMA 週期", 5, 100, 20, key="sl_sma")
                show_ema = st.sidebar.checkbox("顯示 EMA", value=False, key="cb_ema")
                ema_period = st.sidebar.slider("EMA 週期", 5, 100, 50, key="sl_ema")

                if show_sma and len(hist_data_processed['Close'].dropna()) >= sma_period:
                    hist_data_processed[f'SMA{sma_period}'] = SMAIndicator(close=hist_data_processed['Close'], window=sma_period, fillna=False).sma_indicator()
                if show_ema and len(hist_data_processed['Close'].dropna()) >= ema_period:
                    hist_data_processed[f'EMA{ema_period}'] = EMAIndicator(close=hist_data_processed['Close'], window=ema_period, fillna=False).ema_indicator()

                st.sidebar.subheader("相對強弱指數 (RSI)")
                show_rsi = st.sidebar.checkbox("顯示 RSI", value=True, key="cb_rsi")
                rsi_period = st.sidebar.slider("RSI 週期", 7, 30, 14, key="sl_rsi")
                if show_rsi and len(hist_data_processed['Close'].dropna()) >= rsi_period:
                    hist_data_processed['RSI'] = RSIIndicator(close=hist_data_processed['Close'], window=rsi_period, fillna=False).rsi()

                st.sidebar.subheader("MACD")
                show_macd = st.sidebar.checkbox("顯示 MACD", value=True, key="cb_macd")
                macd_fast = st.sidebar.slider("MACD 快線週期", 5, 50, 12, key="sl_macd_f")
                macd_slow = st.sidebar.slider("MACD 慢線週期", 10, 100, 26, key="sl_macd_s")
                macd_signal = st.sidebar.slider("MACD 信號線週期", 5, 50, 9, key="sl_macd_sig")
                if show_macd and len(hist_data_processed['Close'].dropna()) >= macd_slow: # macd_slow is usually largest window
                    macd_indicator = MACD(close=hist_data_processed['Close'], window_slow=macd_slow, window_fast=macd_fast, window_sign=macd_signal, fillna=False)
                    hist_data_processed['MACD_line'] = macd_indicator.macd()
                    hist_data_processed['MACD_signal'] = macd_indicator.macd_signal()
                    hist_data_processed['MACD_hist'] = macd_indicator.macd_diff()

                st.sidebar.subheader("布林帶 (Bollinger Bands)")
                show_bb = st.sidebar.checkbox("顯示布林帶", value=True, key="cb_bb")
                bb_period = st.sidebar.slider("布林帶週期", 5, 50, 20, key="sl_bb_p")
                bb_std_dev = st.sidebar.slider("布林帶標準差倍數", 1.0, 3.0, 2.0, step=0.1, key="sl_bb_std")
                if show_bb and len(hist_data_processed['Close'].dropna()) >= bb_period:
                    bb_indicator = BollingerBands(close=hist_data_processed['Close'], window=bb_period, window_dev=bb_std_dev, fillna=False)
                    hist_data_processed['BB_high'] = bb_indicator.bollinger_hband()
                    hist_data_processed['BB_low'] = bb_indicator.bollinger_lband()
                    hist_data_processed['BB_mid'] = bb_indicator.bollinger_mavg()
            else:
                st.warning("K線圖和技術指標無法計算，因 'Close' (收盤價) 數據缺失或無效。")

            fig_kline = go.Figure()
            ohlc_cols = ['Open', 'High', 'Low', 'Close']
            can_draw_candlestick = all(col in hist_data_processed.columns for col in ohlc_cols) and \
                                not hist_data_processed[ohlc_cols].isnull().all().all()

            if can_draw_candlestick:
                fig_kline.add_trace(go.Candlestick(x=hist_data_processed.index,
                                                open=hist_data_processed['Open'],
                                                high=hist_data_processed['High'],
                                                low=hist_data_processed['Low'],
                                                close=hist_data_processed['Close'], name="K線"))
            elif 'Close' in hist_data_processed.columns and not hist_data_processed['Close'].isnull().all():
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['Close'], mode='lines', name='收盤價 (線圖)'))
                st.caption("K線圖OHLC數據不完整，已改用收盤價線圖。")
            else:
                st.caption("K線圖和收盤價線圖均無法繪製，數據不足。")

            if show_sma and f'SMA{sma_period}' in hist_data_processed and not hist_data_processed[f'SMA{sma_period}'].isnull().all():
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed[f'SMA{sma_period}'], mode='lines', name=f'SMA {sma_period}', line=dict(color='orange')))
            if show_ema and f'EMA{ema_period}' in hist_data_processed and not hist_data_processed[f'EMA{ema_period}'].isnull().all():
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed[f'EMA{ema_period}'], mode='lines', name=f'EMA {ema_period}', line=dict(color='purple')))
            
            bb_plot_cols = ['BB_high', 'BB_low', 'BB_mid']
            can_draw_bb = all(col in hist_data_processed.columns for col in bb_plot_cols) and \
                        not hist_data_processed[bb_plot_cols].isnull().all().all()
            if show_bb and can_draw_bb:
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['BB_high'], mode='lines', name='布林帶上軌', line=dict(color='rgba(173,216,230,0.5)')))
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['BB_low'], mode='lines', name='布林帶下軌', line=dict(color='rgba(173,216,230,0.5)'), fill='tonexty', fillcolor='rgba(173,216,230,0.2)'))
                fig_kline.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['BB_mid'], mode='lines', name='布林帶中軌', line=dict(color='rgba(173,216,230,0.8)')))

            fig_kline.update_layout(title=f"{current_ticker} K線圖與技術指標 ({selected_period})",
                                    xaxis_title="日期", yaxis_title="價格",
                                    xaxis_rangeslider_visible=False, legend_title_text='指標')
            st.plotly_chart(fig_kline, use_container_width=True)

            if 'Volume' in hist_data_processed.columns and not hist_data_processed['Volume'].isnull().all() and hist_data_processed['Volume'].sum() > 0 :
                fig_volume = go.Figure()
                fig_volume.add_trace(go.Bar(x=hist_data_processed.index, y=hist_data_processed['Volume'], name="成交量", marker_color='rgba(0,0,100,0.6)'))
                fig_volume.update_layout(title=f"{current_ticker} 成交量 ({selected_period})", xaxis_title="日期", yaxis_title="成交量")
                st.plotly_chart(fig_volume, use_container_width=True, height=200)
            else:
                st.caption("成交量數據缺失、全為空或全為零，無法繪製成交量圖。")

            if show_rsi and 'RSI' in hist_data_processed and not hist_data_processed['RSI'].isnull().all():
                fig_rsi = go.Figure()
                fig_rsi.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['RSI'], mode='lines', name='RSI'))
                fig_rsi.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="超買 (70)", annotation_position="bottom right")
                fig_rsi.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="超賣 (30)", annotation_position="bottom right")
                fig_rsi.update_layout(title=f"{current_ticker} RSI ({rsi_period})", xaxis_title="日期", yaxis_title="RSI")
                st.plotly_chart(fig_rsi, use_container_width=True, height=300)

            macd_plot_cols = ['MACD_line', 'MACD_signal', 'MACD_hist']
            can_draw_macd = all(col in hist_data_processed.columns for col in macd_plot_cols) and \
                            not hist_data_processed[macd_plot_cols].isnull().all().all()
            if show_macd and can_draw_macd:
                fig_macd = go.Figure()
                fig_macd.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['MACD_line'], mode='lines', name='MACD 線', line=dict(color='blue')))
                fig_macd.add_trace(go.Scatter(x=hist_data_processed.index, y=hist_data_processed['MACD_signal'], mode='lines', name='信號線', line=dict(color='orange')))
                fig_macd.add_trace(go.Bar(x=hist_data_processed.index, y=hist_data_processed['MACD_hist'], name='MACD 柱', marker_color='rgba(128,128,128,0.5)'))
                fig_macd.update_layout(title=f"{current_ticker} MACD ({macd_fast},{macd_slow},{macd_signal})", xaxis_title="日期", yaxis_title="值")
                st.plotly_chart(fig_macd, use_container_width=True, height=300)

    with tab_financials:
        st.subheader("公司財務報表與比率")
        with st.expander("損益表 (Income Statement) - 年度"):
            if not financials.empty:
                st.dataframe(financials.head())
                plot_cols_income = [col for col in ['Total Revenue', 'Gross Profit', 'Net Income'] if col in financials.columns and not financials[col].isnull().all()]
                if plot_cols_income:
                    financials_plot = financials.reset_index()
                    financials_plot['日期'] = financials_plot['index'].astype(str).str.split('-').str[0] # More robust date extraction
                    fig_income = px.line(financials_plot, x='日期', y=plot_cols_income, title="營收、毛利與淨利潤趨勢", labels={'value': '金額', 'variable': '指標'})
                    st.plotly_chart(fig_income, use_container_width=True)
                elif not financials.empty : st.caption("損益表數據不足以繪圖。")
            else: st.warning(f"無法獲取 {current_ticker} 的損益表數據。")

        with st.expander("資產負債表 (Balance Sheet) - 年度"):
            if not balance_sheet.empty:
                st.dataframe(balance_sheet.head())
                plot_cols_balance = [col for col in ['Total Assets', 'Total Liab', 'Total Stockholder Equity'] if col in balance_sheet.columns and not balance_sheet[col].isnull().all()]
                if plot_cols_balance:
                    balance_sheet_plot = balance_sheet.reset_index()
                    balance_sheet_plot['日期'] = balance_sheet_plot['index'].astype(str).str.split('-').str[0]
                    fig_balance = px.line(balance_sheet_plot, x='日期', y=plot_cols_balance, title="資產、負債與股東權益趨勢", labels={'value': '金額', 'variable': '指標'})
                    st.plotly_chart(fig_balance, use_container_width=True)
                elif not balance_sheet.empty: st.caption("資產負債表數據不足以繪圖。")
            else: st.warning(f"無法獲取 {current_ticker} の資產負債表數據。")

        with st.expander("現金流量表 (Cash Flow Statement) - 年度"):
            if not cashflow.empty:
                cashflow_display = pd.DataFrame(index=cashflow.index)
                yf_op_cash_col = 'Total Cash From Operating Activities'
                yf_inv_cash_col = 'Total Cashflows From Investing Activities'
                yf_fin_cash_col = 'Total Cash From Financing Activities'
                yf_fcf_col = 'Free Cash Flow'
                yf_capex_col1 = 'Capital Expenditures' # Common yfinance name
                yf_capex_col2 = 'Capital Expenditure'  # Alternative common yfinance name

                display_op_cash = '營業現金流'
                display_inv_cash = '投資現金流'
                display_fin_cash = '融資現金流'
                display_fcf_yf = '自由現金流 (yfinance提供)'
                display_fcf_calc = '自由現金流 (計算)'


                if yf_fcf_col in cashflow.columns and not cashflow[yf_fcf_col].isnull().all():
                    cashflow_display[display_fcf_yf] = cashflow[yf_fcf_col]
                elif yf_op_cash_col in cashflow.columns and not cashflow[yf_op_cash_col].isnull().all():
                    op_c = cashflow[yf_op_cash_col]
                    cap_ex_val = None
                    if yf_capex_col1 in cashflow.columns and not cashflow[yf_capex_col1].isnull().all(): cap_ex_val = cashflow[yf_capex_col1]
                    elif yf_capex_col2 in cashflow.columns and not cashflow[yf_capex_col2].isnull().all(): cap_ex_val = cashflow[yf_capex_col2]
                    
                    if cap_ex_val is not None and pd.notna(op_c) and pd.notna(cap_ex_val): # Ensure cap_ex_val is not None before calculation
                        cashflow_display[display_fcf_calc] = op_c + cap_ex_val # Note: Capex is usually negative in cashflow statements, so FCF = OpCash + Capex (if Capex is negative)

                if yf_op_cash_col in cashflow.columns: cashflow_display[display_op_cash] = cashflow[yf_op_cash_col]
                if yf_inv_cash_col in cashflow.columns: cashflow_display[display_inv_cash] = cashflow[yf_inv_cash_col]
                if yf_fin_cash_col in cashflow.columns: cashflow_display[display_fin_cash] = cashflow[yf_fin_cash_col]
                
                cashflow_display = cashflow_display.dropna(axis=1, how='all')
                if not cashflow_display.empty:
                    st.dataframe(cashflow_display.head())
                    cols_to_plot_cf = [col for col in cashflow_display.columns if not cashflow_display[col].isnull().all()]
                    if cols_to_plot_cf:
                        cf_plot_df = cashflow_display[cols_to_plot_cf].reset_index().rename(columns={'index': '日期_full'})
                        cf_plot_df['日期'] = cf_plot_df['日期_full'].astype(str).str.split('-').str[0]
                        for col_plot in cols_to_plot_cf: cf_plot_df[col_plot] = pd.to_numeric(cf_plot_df[col_plot], errors='coerce')
                        
                        cf_plot_long = pd.melt(cf_plot_df, id_vars=['日期'], value_vars=cols_to_plot_cf, var_name='指標', value_name='金額').dropna(subset=['金額'])
                        if not cf_plot_long.empty:
                            fig_cf = px.bar(cf_plot_long, x='日期', y='金額', color='指標', barmode='group', title="現金流量關鍵指標")
                            st.plotly_chart(fig_cf, use_container_width=True)
                        else: st.caption("現金流量圖無有效數據可繪製。")
                    elif not cashflow_display.empty: st.caption("現金流量數據不足以繪製圖表。")
                else: st.info(f"{current_ticker} 的現金流量表數據不完整或缺失。")
            else: st.warning(f"無法獲取 {current_ticker} 的現金流量表數據。")
        
        with st.expander("關鍵財務比率"):
            st.write("以下是一些從公司資訊中提取的即時或近期財務比率：")
            ratios_data = {
                "本益比 (Trailing P/E)": info.get('trailingPE'), "預期本益比 (Forward P/E)": info.get('forwardPE'),
                "每股盈餘 (Trailing EPS)": info.get('trailingEps'), "預期每股盈餘 (Forward EPS)": info.get('forwardEps'),
                "股價淨值比 (P/B Ratio)": info.get('priceToBook'), "股價營收比 (P/S Ratio TTM)": info.get('priceToSalesTrailing12Months'),
                "股東權益報酬率 (ROE TTM)": info.get('returnOnEquity'), "資產報酬率 (ROA TTM)": info.get('returnOnAssets'),
                "毛利率 (Gross Margins)": info.get('grossMargins'), "營業利潤率 (Operating Margins)": info.get('operatingMargins'),
                "淨利率 (Profit Margins)": info.get('profitMargins'), "負債權益比 (Debt/Equity)": info.get('debtToEquity'),
                "流動比率 (Current Ratio)": info.get('currentRatio'), "速動比率 (Quick Ratio)": info.get('quickRatio'),
                "企業價值/營收 (EV/Revenue)": info.get('enterpriseToRevenue'), "企業價值/EBITDA (EV/EBITDA)": info.get('enterpriseToEbitda'),
            }
            for name_ratio, val_ratio in ratios_data.items():
                disp_val_ratio = "N/A"
                if pd.notna(val_ratio) and isinstance(val_ratio, (float, int)):
                    # Check for terms that imply percentage display
                    if any(k_pct in name_ratio for k_pct in ["Margins", "ROE", "ROA", "利率", "報酬率", "殖利率", "支付率"]): 
                        disp_val_ratio = f"{val_ratio*100:.2f}%"
                    else:
                        disp_val_ratio = f"{val_ratio:.2f}"
                elif pd.notna(val_ratio): disp_val_ratio = str(val_ratio)
                st.write(f"- {name_ratio}: {disp_val_ratio}")

            st.subheader("股息資訊")
            if not dividends.empty:
                st.write("最近股息發放歷史:"); st.dataframe(dividends.tail().sort_index(ascending=False))
                div_rate, payout_ratio_val = info.get('dividendRate'), info.get('payoutRatio')
                st.write(f"年股息金額: {div_rate if pd.notna(div_rate) else 'N/A'}")
                st.write(f"股息支付率: {payout_ratio_val*100:.2f}%" if isinstance(payout_ratio_val, float) and pd.notna(payout_ratio_val) else 'N/A')
            else: st.info(f"{current_ticker} 可能不發放股息，或近期無股息數據。")

    with tab_company_profile:
        st.subheader("公司業務摘要"); st.write(info.get('longBusinessSummary', '無詳細業務描述。')); st.markdown("---")
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            st.subheader("主要股東")
            if major_holders is not None and not major_holders.empty: st.dataframe(major_holders)
            else: st.info("無主要股東數據。")
        with col_h2:
            st.subheader("機構持股")
            if institutional_holders is not None and not institutional_holders.empty: st.dataframe(institutional_holders.head(10))
            else: st.info("無機構持股數據。")
        st.markdown("---")
        
        st.subheader("分析師建議")
        if recommendations is not None and not recommendations.empty:
            to_grade_col_name = None
            for col_name_rec_tab in recommendations.columns: 
                if col_name_rec_tab.lower() == 'to grade':
                    to_grade_col_name = col_name_rec_tab
                    break
            
            if to_grade_col_name and not recommendations[to_grade_col_name].value_counts().empty:
                summary_rec = recommendations[to_grade_col_name].value_counts()
                fig_recom_pie = px.pie(summary_rec, values=summary_rec.values, names=summary_rec.index, title="分析師建議分佈 (評級)")
                st.plotly_chart(fig_recom_pie, use_container_width=True)
            else:
                expected_summary_cols_original_case = ['strongBuy', 'buy', 'hold', 'sell', 'strongSell']
                actual_cols_present_rec = []
                recommendations_cols_lower_rec = [col.lower() for col in recommendations.columns]
                for expected_col_lower_case_rec in [c.lower() for c in expected_summary_cols_original_case]:
                    try:
                        idx_rec = recommendations_cols_lower_rec.index(expected_col_lower_case_rec)
                        actual_cols_present_rec.append(recommendations.columns[idx_rec])
                    except ValueError:
                        pass 
                
                if len(actual_cols_present_rec) == len(expected_summary_cols_original_case) and not recommendations.empty:
                    latest_row_rec = None
                    if 'period' in recommendations.columns and '0m' in recommendations['period'].values:
                         latest_row_df_rec = recommendations[recommendations['period'] == '0m']
                         if not latest_row_df_rec.empty:
                            latest_row_rec = latest_row_df_rec.iloc[-1] 
                    
                    if latest_row_rec is None and not recommendations.empty: 
                        latest_row_rec = recommendations.iloc[-1]

                    if latest_row_rec is not None:
                        latest_recoms_data_rec = {}
                        for col_name_rec_bar_tab in actual_cols_present_rec: 
                            if col_name_rec_bar_tab in latest_row_rec and pd.notna(latest_row_rec[col_name_rec_bar_tab]) and latest_row_rec[col_name_rec_bar_tab] > 0:
                                latest_recoms_data_rec[col_name_rec_bar_tab] = latest_row_rec[col_name_rec_bar_tab]
                        
                        if latest_recoms_data_rec:
                            latest_recoms_series_rec = pd.Series(latest_recoms_data_rec)
                            fig_recom_bar = px.bar(latest_recoms_series_rec, x=latest_recoms_series_rec.index, y=latest_recoms_series_rec.values, 
                                                   title="最新分析師建議數量", labels={'index':'建議', 'y':'數量'})
                            st.plotly_chart(fig_recom_bar, use_container_width=True)
                        else:
                            st.info("最新分析師建議評級數量均為0或無效。")
                    else:
                        st.info("無法確定最新的分析師建議行。")
                else:
                    st.info("無足夠數據生成分析師建議圖表 (預期摘要欄位不完整或數據為空)。")
        else:
            st.info("無分析師建議數據。")
        st.markdown("---")
        
        st.subheader(f"相關新聞 (來自 yfinance - {current_ticker})")
        if news_yf and isinstance(news_yf, list) and len(news_yf) > 0:
            news_items_to_display_yf = [] 
            for item_outer_news in news_yf: 
                if isinstance(item_outer_news, dict) and 'content' in item_outer_news and isinstance(item_outer_news['content'], dict):
                    item_content_news = item_outer_news['content'] 
                    news_link_url_yf = None
                    if 'clickThroughUrl' in item_content_news and isinstance(item_content_news['clickThroughUrl'], dict) and \
                       'url' in item_content_news['clickThroughUrl'] and item_content_news['clickThroughUrl']['url']:
                        news_link_url_yf = str(item_content_news['clickThroughUrl']['url']).strip()
                    elif 'canonicalUrl' in item_content_news and isinstance(item_content_news['canonicalUrl'], dict) and \
                         'url' in item_content_news['canonicalUrl'] and item_content_news['canonicalUrl']['url']:
                        news_link_url_yf = str(item_content_news['canonicalUrl']['url']).strip()
                    
                    if news_link_url_yf and news_link_url_yf != '#': 
                        title_news = item_content_news.get('title', '(無標題)') 
                        if not title_news or not str(title_news).strip(): 
                            title_news = '(無標題)'
                        else:
                            title_news = str(title_news).strip()
                        publisher_name_news = '來源不明'
                        if 'provider' in item_content_news and isinstance(item_content_news['provider'], dict) and \
                           'displayName' in item_content_news['provider'] and item_content_news['provider']['displayName']:
                            publisher_name_news = item_content_news['provider']['displayName']
                        publish_time_raw_news = item_content_news.get('pubDate')
                        news_items_to_display_yf.append({ 
                            'title': title_news,
                            'link': news_link_url_yf,
                            'publisher': publisher_name_news,
                            'providerPublishTime': publish_time_raw_news 
                        })
            
            if news_items_to_display_yf: 
                for news_item_yf_disp in news_items_to_display_yf[:5]: 
                    st.markdown(f"**<a href='{news_item_yf_disp['link']}' target='_blank'>{news_item_yf_disp['title']}</a>** - *{news_item_yf_disp['publisher']}*", unsafe_allow_html=True)
                    ts_str_news = news_item_yf_disp.get('providerPublishTime')
                    if ts_str_news and isinstance(ts_str_news, str):
                        try:
                            dt_object_news = datetime.fromisoformat(ts_str_news.replace('Z', '+00:00'))
                            st.caption(f"發布: {dt_object_news.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M %Z')}")
                        except ValueError: 
                            st.caption(f"發布時間: {ts_str_news}") 
                        except Exception: 
                             st.caption(f"發布時間處理出錯")
                    elif ts_str_news and isinstance(ts_str_news, (int, float)): 
                        try:
                            dt_object_news_ts = datetime.fromtimestamp(ts_str_news, tz=pytz.UTC)
                            st.caption(f"發布: {dt_object_news_ts.strftime('%Y-%m-%d %H:%M %Z')}")
                        except Exception:
                            st.caption("發布時間格式錯誤")
                    st.markdown("---")
            else:
                st.info(f"yfinance 為 {current_ticker} 提供的所有新聞項目中，均未找到包含有效連結的內容，或內容結構不符合預期。")
        else:
            st.info(f"yfinance 未找到 {current_ticker} 的相關新聞數據 (來源未提供任何新聞條目)。")
        
        st.markdown("---")
        st.subheader(f"外部財經新聞搜尋 (SERP API - {info.get('longName', current_ticker)})")
        
        if serpapi_error:
            st.caption(serpapi_error)
        if serpapi_results:
            for item_serp_news in serpapi_results[:5]: 
                title_serp = item_serp_news.get('title', '無標題')
                link_serp = item_serp_news.get('link', '#')
                source_dict_serp = item_serp_news.get('source', {}) 
                source_name_serp = source_dict_serp.get('name', '未知來源') 
                date_str_serp = item_serp_news.get('date', '')

                if link_serp and link_serp != '#': 
                    st.markdown(f"**<a href='{link_serp}' target='_blank'>{title_serp}</a>** - *{source_name_serp}*", unsafe_allow_html=True)
                    if date_str_serp:
                        st.caption(f"發布: {date_str_serp}") 
                    st.markdown("---")
        elif serp_api_key_input and not serpapi_error: 
            st.info("SERP API 未找到相關財經新聞。")


    with tab_ai_chat:
        st.subheader(f"🤖 與 Gemini AI 針對 {company_name} 的進階對話")

        if not google_api_key_input:
            st.warning("請在左側邊欄輸入 Google AI API 金鑰以啟用 AI 分析與對話功能。")
        else:
            if not st.session_state.initial_ai_analysis_done:
                with st.spinner("🧠 Gemini 正在生成初始分析，請稍候..."):
                    prompt_parts = [
                        f"你是一位專業的金融分析師。請針對以下公司 {company_name} ({current_ticker}) 進行基本面分析。\n",
                        f"公司概況:\n- 產業: {info.get('sector', 'N/A')}\n- 行業: {info.get('industry', 'N/A')}\n- 市值: {info.get('marketCap', 'N/A')}\n- Beta: {info.get('beta', 'N/A')}\n",
                        f"- 主要業務: {info.get('longBusinessSummary', 'N/A')[:700]}...\n"
                    ]
                    if not financials.empty:
                        latest_income = financials.iloc[0]
                        prompt_parts.extend(["\n最新年度損益表摘要:", f"- 總營收: {latest_income.get('Total Revenue', 'N/A')}", f"- 毛利: {latest_income.get('Gross Profit', 'N/A')}", f"- 淨利: {latest_income.get('Net Income', 'N/A')}"])
                    
                    yf_op_cash_col_ai = 'Total Cash From Operating Activities'; yf_capex_col1_ai = 'Capital Expenditures'; yf_capex_col2_ai = 'Capital Expenditure'; yf_fcf_col_direct_ai = 'Free Cash Flow'
                    if not cashflow.empty:
                        latest_cf_ai_prompt = cashflow.iloc[0]
                        prompt_parts.extend(["\n最新年度現金流量表摘要:", f"- 營業現金流: {latest_cf_ai_prompt.get(yf_op_cash_col_ai, 'N/A')}"])
                        fcf_for_ai_prompt = "N/A"
                        if yf_fcf_col_direct_ai in latest_cf_ai_prompt and pd.notna(latest_cf_ai_prompt[yf_fcf_col_direct_ai]): fcf_for_ai_prompt = latest_cf_ai_prompt[yf_fcf_col_direct_ai]
                        elif yf_op_cash_col_ai in latest_cf_ai_prompt:
                            op_c_ai_val_prompt = latest_cf_ai_prompt.get(yf_op_cash_col_ai)
                            cap_ex_ai_val_prompt = latest_cf_ai_prompt.get(yf_capex_col1_ai) if yf_capex_col1_ai in latest_cf_ai_prompt and pd.notna(latest_cf_ai_prompt.get(yf_capex_col1_ai)) else latest_cf_ai_prompt.get(yf_capex_col2_ai)
                            if pd.notna(op_c_ai_val_prompt) and pd.notna(cap_ex_ai_val_prompt): fcf_for_ai_prompt = op_c_ai_val_prompt + cap_ex_ai_val_prompt 
                        prompt_parts.append(f"- 自由現金流: {fcf_for_ai_prompt}")

                    prompt_parts.append("\n近期關鍵財務比率:")
                    for name_ratio_prompt, val_info_key_prompt in [("本益比(TTM)", 'trailingPE'), ("股價淨值比", 'priceToBook')]: prompt_parts.append(f"- {name_ratio_prompt}: {info.get(val_info_key_prompt, 'N/A')}")
                    for name_ratio_pct_prompt, val_info_key_pct_prompt, is_pct_flag_prompt in [("股息殖利率", 'dividendYield', True), ("ROE(TTM)", 'returnOnEquity', True)]:
                        val_pct_prompt = info.get(val_info_key_pct_prompt)
                        disp_pct_prompt = "N/A"
                        if pd.notna(val_pct_prompt) and isinstance(val_pct_prompt, (float,int)):
                            if is_pct_flag_prompt:
                                # Consistent heuristic for dividend yield in prompt
                                if name_ratio_pct_prompt == "股息殖利率":
                                    if val_pct_prompt == 0: disp_pct_val_prompt = 0.0
                                    elif val_pct_prompt >= 1.0: disp_pct_val_prompt = val_pct_prompt # Already a percent
                                    else: disp_pct_val_prompt = val_pct_prompt * 100.0 # Is a ratio
                                    disp_pct_prompt = f"{disp_pct_val_prompt:.2f}%"
                                else: # For ROE etc., assume it's a ratio
                                    disp_pct_prompt = f"{val_pct_prompt*100:.2f}%"
                            else: # Not a percentage value
                                disp_pct_prompt = str(val_pct_prompt)
                        prompt_parts.append(f"- {name_ratio_pct_prompt}: {disp_pct_prompt}")


                    if news_yf and isinstance(news_yf, list) and len(news_yf) > 0:
                        prompt_parts.append("\n\n近期相關內部財經新聞摘要 (來自 yfinance):")
                        yf_news_count_for_ai_prompt = 0
                        for item_outer_ai_news in news_yf:
                            if yf_news_count_for_ai_prompt >= 3: break
                            if isinstance(item_outer_ai_news, dict) and 'content' in item_outer_ai_news and isinstance(item_outer_ai_news['content'], dict):
                                item_content_ai_news = item_outer_ai_news['content']
                                news_link_url_for_check_ai_news = None
                                if 'clickThroughUrl' in item_content_ai_news and isinstance(item_content_ai_news['clickThroughUrl'], dict) and 'url' in item_content_ai_news['clickThroughUrl'] and item_content_ai_news['clickThroughUrl']['url']: news_link_url_for_check_ai_news = str(item_content_ai_news['clickThroughUrl']['url']).strip()
                                elif 'canonicalUrl' in item_content_ai_news and isinstance(item_content_ai_news['canonicalUrl'], dict) and 'url' in item_content_ai_news['canonicalUrl'] and item_content_ai_news['canonicalUrl']['url']: news_link_url_for_check_ai_news = str(item_content_ai_news['canonicalUrl']['url']).strip()
                                if news_link_url_for_check_ai_news and news_link_url_for_check_ai_news != '#':
                                    title_ai_news = item_content_ai_news.get('title'); publisher_name_ai_news = item_content_ai_news.get('provider', {}).get('displayName', '來源不明'); pub_date_str_ai_news = item_content_ai_news.get('pubDate') 
                                    display_title_for_ai_news = str(title_ai_news).strip() if title_ai_news and str(title_ai_news).strip() else "(無標題)"
                                    ai_news_line_prompt = f"{yf_news_count_for_ai_prompt + 1}. 標題: {display_title_for_ai_news} (來源: {publisher_name_ai_news})"
                                    if pub_date_str_ai_news and isinstance(pub_date_str_ai_news, str):
                                        try: ai_news_line_prompt += f" (發布時間: {datetime.fromisoformat(pub_date_str_ai_news.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M UTC')})"
                                        except: pass 
                                    prompt_parts.append(ai_news_line_prompt); yf_news_count_for_ai_prompt += 1
                    
                    if serpapi_results:
                        prompt_parts.append("\n\n近期相關外部財經新聞摘要 (來自 SERP API):")
                        for i_serp_prompt, item_serp_prompt_news in enumerate(serpapi_results[:3]): 
                            title_for_ai_serp_prompt = item_serp_prompt_news.get('title', 'N/A'); source_name_for_ai_serp_prompt = item_serp_prompt_news.get('source', {}).get('name', 'N/A'); date_str_for_ai_serp_prompt = item_serp_prompt_news.get('date', '')
                            ai_news_line_serp_prompt = f"{i_serp_prompt+1}. 標題: {title_for_ai_serp_prompt} (來源: {source_name_for_ai_serp_prompt})"
                            if date_str_for_ai_serp_prompt: ai_news_line_serp_prompt += f" (發布日期: {date_str_for_ai_serp_prompt})"
                            prompt_parts.append(ai_news_line_serp_prompt)
                    elif serpapi_error and "未提供 SERP API 金鑰" not in serpapi_error : 
                        prompt_parts.append(f"\n\n外部財經新聞搜尋提示: {serpapi_error}")
                    
                    prompt_instruction = (
                        "\n\n任務指示:\n"
                        "1. 基於以上提供的公司基本資料、最新的年度財務摘要、關鍵比率、以及來自 yfinance 和 SERP API 的近期相關財經新聞摘要（如果有的話），用繁體中文分析這家公司的基本面情況。\n"
                        "2. 分析應包括公司的主要優勢、潛在風險和挑戰，並結合所有提供的新聞資訊進行綜合評估。\n"
                        "3. 提供一個完整的總結性評價和未來展望。\n"
                        "4. 分析應客觀且基於數據，段落分明，易於理解。避免提供直接的投資建議（買入/賣出）。\n"
                        "5. 你的回答將作為後續對話的初始上下文。"
                    )
                    full_initial_prompt = "\n".join(str(p_part) for p_part in prompt_parts) + prompt_instruction
                    
                    genai.configure(api_key=google_api_key_input)
                    model_for_initial = genai.GenerativeModel('gemini-1.5-flash-latest')
                    initial_response = model_for_initial.generate_content(full_initial_prompt)
                    initial_analysis_text = initial_response.text if initial_response.parts else "AI 分析無法生成初始內容。"
                    
                    st.markdown(initial_analysis_text)
                    st.session_state.initial_analysis_context = initial_analysis_text
                    st.session_state.chat_messages.append({"role": "assistant", "content": initial_analysis_text})
                    st.session_state.gemini_chat_history.append({'role': 'model', 'parts': [initial_analysis_text]})
                    st.session_state.initial_ai_analysis_done = True
            
            if st.session_state.initial_ai_analysis_done: 
                for message_chat in st.session_state.chat_messages:
                    with st.chat_message(message_chat["role"]):
                        st.markdown(message_chat["content"])

            if prompt_chat_input := st.chat_input("針對以上分析，您想問什麼？", key="ai_chat_input"):
                if not st.session_state.initial_ai_analysis_done:
                    st.warning("請等待初始分析完成後再提問。")
                elif not google_api_key_input: 
                    st.error("請先提供 Google AI API 金鑰。")
                else:
                    st.session_state.chat_messages.append({"role": "user", "content": prompt_chat_input})
                    with st.chat_message("user"):
                        st.markdown(prompt_chat_input)

                    with st.spinner("🤖 AI 正在思考中..."):
                        ai_response_text_chat, updated_gemini_history_chat = get_ai_chat_response_from_gemini(
                            google_api_key_input,
                            prompt_chat_input, 
                            st.session_state.gemini_chat_history
                        )
                        st.session_state.gemini_chat_history = updated_gemini_history_chat 
                        
                        with st.chat_message("assistant"):
                            st.markdown(ai_response_text_chat)
                        st.session_state.chat_messages.append({"role": "assistant", "content": ai_response_text_chat})


elif analyze_button and not ticker_symbol_input:
    st.sidebar.error("🚨 請輸入股票代碼。")
elif st.session_state.get('stock_data_loaded') is False and st.session_state.get('current_ticker'): 
    st.error(f"加載 {st.session_state.current_ticker} 的數據失敗。請檢查股票代碼或網絡，然後重試。")
else: 
    st.info("👋 歡迎使用 Fin Agent 進階股票分析工具！請在左側輸入股票代碼、您的 Google AI API 金鑰以及 SERP API 金鑰（可選），然後點擊 '分析股票' 按鈕開始。")