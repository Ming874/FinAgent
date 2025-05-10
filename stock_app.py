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

def get_ai_analysis_from_gemini(prompt_text, api_key):
    if not api_key:
        return "錯誤：未提供 Google AI API 金鑰。"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt_text)
        return response.text if response.parts else "AI 分析無法生成內容，可能觸發了內容過濾或安全設定。"
    except Exception as e:
        return f"Gemini AI 分析出錯: {e}"

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
             return results["organic_results"], None # Should not happen with tbm=nws
        else:
            return None, f"SERP API 未返回預期的 'news_results'。收到: {list(results.keys())}"
            
    except Exception as e:
        return None, f"SERP API 搜尋出錯: {e}"

# --- 側邊欄 ---
st.sidebar.title("📈 Fin Agent 股票分析")
ticker_symbol_input = st.sidebar.text_input("輸入股票代碼 (例如：NVDA)", "NVDA").upper()
google_api_key_input = st.sidebar.text_input("輸入 Google AI API 金鑰", type="password", key="google_api_key")
serp_api_key_input = st.sidebar.text_input("輸入 SERP API 金鑰 (可選)", type="password", key="serp_api_key")

DEFAULT_PERIODS = ["1個月", "3個月", "6個月", "今年以來(YTD)", "1年", "2年", "5年", "全部"]
st.sidebar.subheader("股價圖表設定")
selected_period = st.sidebar.selectbox("選擇時間區間:", DEFAULT_PERIODS, index=5, key="sb_period_select")

analyze_button = st.sidebar.button("🚀 分析股票", key="btn_analyze")

# --- 主內容區 ---
st.title(f"【Fin Agent】AI 驅動進階股票分析 ({ticker_symbol_input or ''})")

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
                        # 修改搜尋查詢以包含財經相關關鍵字
                        search_query = f'"{company_name_for_search}" OR "{ticker_symbol_input}" 財經 OR 金融 OR 股票 OR 市場分析 新聞'
                        st.session_state.serpapi_results, st.session_state.serpapi_error = get_serpapi_news(search_query, serp_api_key_input, num_results=5)
                    elif not serp_api_key_input:
                        st.session_state.serpapi_error = "未提供 SERP API 金鑰，跳過外部新聞搜尋。"
                else:
                    st.session_state.stock_data_loaded = False
                    st.error(f"未能成功獲取 {ticker_symbol_input} 的歷史股價數據。請檢查股票代碼或稍後再試。")
                st.rerun()
            except Exception as e:
                st.error(f"獲取股票數據時發生嚴重錯誤: {e}")
                st.session_state.stock_data_loaded = False
                st.session_state.current_ticker = ticker_symbol_input

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

    tab_titles = ["📊 總覽", "📈 股價分析", " F 財務數據", "🏢 公司資訊", "🤖 AI 智能分析"]
    tab_overview, tab_price_analysis, tab_financials, tab_company_profile, tab_ai_analysis = st.tabs(tab_titles)

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

    with tab_overview:
        st.subheader("關鍵指標與股價摘要")
        col1, col2, col3, col4 = st.columns(4)
        current_price = info.get('currentPrice', info.get('regularMarketPreviousClose', 'N/A'))
        price_change = info.get('regularMarketChange', 0)
        price_change_percent = info.get('regularMarketChangePercent', 0) * 100 if isinstance(info.get('regularMarketChangePercent'), (int, float)) else 0

        with col1: st.metric(label="當前價格", value=f"{current_price:.2f}" if isinstance(current_price, (int,float)) else "N/A", delta=f"{price_change:.2f} ({price_change_percent:.2f}%)" if isinstance(price_change, (int,float)) else None)
        with col2: st.metric(label="市值", value=f"{info.get('marketCap', 0)/1_000_000_000_000:.2f} 兆" if isinstance(info.get('marketCap'), (int, float)) else "N/A")
        with col3: st.metric(label="本益比 (TTM)", value=f"{info.get('trailingPE'):.2f}" if isinstance(info.get('trailingPE'), (int, float)) else "N/A")
        with col4: st.metric(label="每股盈餘 (TTM)", value=f"{info.get('trailingEps'):.2f}" if isinstance(info.get('trailingEps'), (int, float)) else "N/A")

        col5, col6, col7, col8 = st.columns(4)
        with col5: st.metric(label="股價淨值比", value=f"{info.get('priceToBook'):.2f}" if isinstance(info.get('priceToBook'), (int, float)) else "N/A")
        with col6: st.metric(label="股息殖利率", value=f"{info.get('dividendYield', 0)*100:.2f}%" if isinstance(info.get('dividendYield'), (int, float)) else "N/A")
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
                if show_macd and len(hist_data_processed['Close'].dropna()) >= macd_slow:
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
                    financials_plot['日期'] = financials_plot['index'].astype(str).str.split('-').str[0]
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
            else: st.warning(f"無法獲取 {current_ticker} 的資產負債表數據。")

        with st.expander("現金流量表 (Cash Flow Statement) - 年度"):
            if not cashflow.empty:
                cashflow_display = pd.DataFrame(index=cashflow.index)
                yf_op_cash_col = 'Total Cash From Operating Activities'
                yf_inv_cash_col = 'Total Cashflows From Investing Activities'
                yf_fin_cash_col = 'Total Cash From Financing Activities'
                yf_fcf_col = 'Free Cash Flow'
                yf_capex_col1 = 'Capital Expenditures'
                yf_capex_col2 = 'Capital Expenditure'
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
                    if cap_ex_val is not None: cashflow_display[display_fcf_calc] = op_c + cap_ex_val

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
                        for col in cols_to_plot_cf: cf_plot_df[col] = pd.to_numeric(cf_plot_df[col], errors='coerce')
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
            ratios_data = { "本益比 (Trailing P/E)": info.get('trailingPE'), "預期本益比 (Forward P/E)": info.get('forwardPE'), "每股盈餘 (Trailing EPS)": info.get('trailingEps'), "預期每股盈餘 (Forward EPS)": info.get('forwardEps'), "股價淨值比 (P/B Ratio)": info.get('priceToBook'), "股價營收比 (P/S Ratio TTM)": info.get('priceToSalesTrailing12Months'), "股東權益報酬率 (ROE TTM)": info.get('returnOnEquity'), "資產報酬率 (ROA TTM)": info.get('returnOnAssets'), "毛利率 (Gross Margins)": info.get('grossMargins'), "營業利潤率 (Operating Margins)": info.get('operatingMargins'), "淨利率 (Profit Margins)": info.get('profitMargins'), "負債權益比 (Debt/Equity)": info.get('debtToEquity'), "流動比率 (Current Ratio)": info.get('currentRatio'), "速動比率 (Quick Ratio)": info.get('quickRatio'), "企業價值/營收 (EV/Revenue)": info.get('enterpriseToRevenue'), "企業價值/EBITDA (EV/EBITDA)": info.get('enterpriseToEbitda'), }
            for name, val in ratios_data.items():
                disp_val = "N/A"
                if pd.notna(val) and isinstance(val, (float, int)): disp_val = f"{val*100:.2f}%" if any(k in name for k in ["Margins", "ROE", "ROA", "殖利率", "支付率"]) else f"{val:.2f}"
                elif pd.notna(val): disp_val = str(val)
                st.write(f"- {name}: {disp_val}")
            st.subheader("股息資訊")
            if not dividends.empty:
                st.write("最近股息發放歷史:"); st.dataframe(dividends.tail().sort_index(ascending=False))
                div_rate, payout = info.get('dividendRate'), info.get('payoutRatio')
                st.write(f"年股息金額: {div_rate if pd.notna(div_rate) else 'N/A'}")
                st.write(f"股息支付率: {payout*100:.2f}%" if isinstance(payout, float) and pd.notna(payout) else 'N/A')
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
            st.dataframe(recommendations.tail().sort_index(ascending=False))
            if 'To Grade' in recommendations.columns and not recommendations['To Grade'].value_counts().empty:
                summary = recommendations['To Grade'].value_counts()
                fig_recom_pie = px.pie(summary, values=summary.values, names=summary.index, title="分析師建議分佈 (評級)")
                st.plotly_chart(fig_recom_pie, use_container_width=True)
            elif all(c in recommendations.columns for c in ['Strong Buy', 'Buy', 'Hold', 'Sell', 'Strong Sell']) and not recommendations.empty:
                recom_cat_cols = ['Strong Buy', 'Buy', 'Hold', 'Sell', 'Strong Sell']
                latest_recoms = recommendations[recom_cat_cols].iloc[-1][lambda x: x > 0]
                if not latest_recoms.empty:
                    fig_recom_bar = px.bar(latest_recoms, x=latest_recoms.index, y=latest_recoms.values, title="最新分析師建議數量", labels={'index':'建議', 'y':'數量'})
                    st.plotly_chart(fig_recom_bar, use_container_width=True)
                else: st.info("最新分析師建議評級數量均為0。")
            else: st.info("無足夠數據生成分析師建議圖表。")
        else: st.info("無分析師建議數據。")
        st.markdown("---")
        
        # --- YFinance 新聞顯示優化 ---
        st.subheader(f"相關新聞 (來自 yfinance - {current_ticker})")
        if news_yf and isinstance(news_yf, list) and len(news_yf) > 0:
            news_items_to_display = []
            for item in news_yf:
                if isinstance(item, dict):
                    title = item.get('title')
                    link = item.get('link')
                    publisher = item.get('publisher', '來源不明')

                    if title and title.strip() and title.lower() != 'n/a' and \
                       link and link.strip() and link != '#':
                        news_items_to_display.append({
                            'title': title,
                            'link': link,
                            'publisher': publisher,
                            'providerPublishTime': item.get('providerPublishTime')
                        })
            
            if news_items_to_display:
                for news_item in news_items_to_display[:5]: # 最多顯示5條有效新聞
                    st.markdown(f"**<a href='{news_item['link']}' target='_blank'>{news_item['title']}</a>** - *{news_item['publisher']}*", unsafe_allow_html=True)
                    ts = news_item.get('providerPublishTime')
                    if ts and isinstance(ts, (int, float)):
                        try:
                            dt_object = datetime.fromtimestamp(ts, tz=pytz.UTC)
                            st.caption(f"發布: {dt_object.strftime('%Y-%m-%d %H:%M %Z')}")
                        except Exception:
                            st.caption("發布時間格式錯誤")
                    st.markdown("---")
            else:
                st.info(f"yfinance 未能提供 {current_ticker} 的有效新聞標題或連結。")
        else:
            st.info(f"yfinance 未找到 {current_ticker} 的相關新聞數據。")
        
        st.markdown("---")
        # --- SERP API 新聞標題修改 ---
        st.subheader(f"外部財經新聞搜尋 (SERP API - {info.get('longName', current_ticker)})")
        if serpapi_error:
            st.caption(serpapi_error)
        if serpapi_results:
            for item in serpapi_results:
                title = item.get('title', '無標題')
                link = item.get('link', '#')
                source = item.get('source', '未知來源')
                snippet = item.get('snippet', '')
                date_str = item.get('date', '')

                st.markdown(f"**<a href='{link}' target='_blank'>{title}</a>** - *{source}*", unsafe_allow_html=True)
                if date_str:
                    st.caption(f"發布日期: {date_str}")
                if snippet:
                    st.caption(f"摘要: {snippet}")
                st.markdown("---")
        elif serp_api_key_input and not serpapi_error:
            st.info("SERP API 未找到相關財經新聞。")


    with tab_ai_analysis:
        st.subheader(f"🤖 Gemini AI 對 {company_name} 的智能分析")
        if not google_api_key_input: st.warning("請在左側邊欄輸入 Google AI API 金鑰以啟用 AI 分析功能。")
        else:
            prompt_parts = [
                f"你是一位專業的金融分析師。請針對以下公司 {company_name} ({current_ticker}) 進行基本面分析。\n",
                f"公司概況:\n- 產業: {info.get('sector', 'N/A')}\n- 行業: {info.get('industry', 'N/A')}\n- 市值: {info.get('marketCap', 'N/A')}\n- Beta: {info.get('beta', 'N/A')}\n",
                f"- 主要業務: {info.get('longBusinessSummary', 'N/A')[:700]}...\n"
            ]
            if not financials.empty:
                latest_income = financials.iloc[0]
                prompt_parts.extend(["\n最新年度損益表摘要:", f"- 總營收: {latest_income.get('Total Revenue', 'N/A')}", f"- 毛利: {latest_income.get('Gross Profit', 'N/A')}", f"- 淨利: {latest_income.get('Net Income', 'N/A')}"])
            
            yf_op_cash_col = 'Total Cash From Operating Activities'
            yf_capex_col1 = 'Capital Expenditures'
            yf_capex_col2 = 'Capital Expenditure'
            yf_fcf_col_direct = 'Free Cash Flow'

            if not cashflow.empty:
                latest_cf_ai = cashflow.iloc[0]
                prompt_parts.extend(["\n最新年度現金流量表摘要:", f"- 營業現金流: {latest_cf_ai.get(yf_op_cash_col, 'N/A')}"])
                fcf_for_ai = "N/A"
                if yf_fcf_col_direct in latest_cf_ai and pd.notna(latest_cf_ai[yf_fcf_col_direct]):
                    fcf_for_ai = latest_cf_ai[yf_fcf_col_direct]
                elif yf_op_cash_col in latest_cf_ai:
                    op_c_ai_val = latest_cf_ai.get(yf_op_cash_col)
                    cap_ex_ai_val = None
                    if yf_capex_col1 in latest_cf_ai: cap_ex_ai_val = latest_cf_ai.get(yf_capex_col1)
                    elif yf_capex_col2 in latest_cf_ai: cap_ex_ai_val = latest_cf_ai.get(yf_capex_col2)
                    if pd.notna(op_c_ai_val) and pd.notna(cap_ex_ai_val):
                        fcf_for_ai = op_c_ai_val + cap_ex_ai_val # Note: FCF is OpCash - CapEx. CapEx is usually negative from yf, so + is correct.
                prompt_parts.append(f"- 自由現金流: {fcf_for_ai}")

            prompt_parts.append("\n近期關鍵財務比率:")
            for name, val_info in [("本益比(TTM)", 'trailingPE'), ("股價淨值比", 'priceToBook')]:
                prompt_parts.append(f"- {name}: {info.get(val_info, 'N/A')}")
            for name, val_info, is_pct in [("股息殖利率", 'dividendYield', True), ("ROE(TTM)", 'returnOnEquity', True)]:
                val = info.get(val_info)
                disp = f"{val*100:.2f}%" if pd.notna(val) and isinstance(val, (float,int)) and is_pct else (val if pd.notna(val) else 'N/A')
                prompt_parts.append(f"- {name}: {disp}")

            if serpapi_results:
                prompt_parts.append("\n\n近期相關外部財經新聞摘要 (來自 SERP API):")
                for i, item in enumerate(serpapi_results[:3]):
                    title = item.get('title', 'N/A')
                    snippet = item.get('snippet', 'N/A')
                    source = item.get('source', 'N/A')
                    prompt_parts.append(f"{i+1}. 標題: {title} (來源: {source})\n   摘要: {snippet}")
            elif serpapi_error and "未提供 SERP API 金鑰" not in serpapi_error : # Only show search error if key was provided
                 prompt_parts.append(f"\n\n外部財經新聞搜尋提示: {serpapi_error}")
            
            prompt_instruction = ("\n\n任務指示:\n1. 基於以上提供的公司基本資料、最新的年度財務摘要、關鍵比率以及近期相關外部財經新聞摘要（如果有的話），用繁體中文分析這家公司的基本面情況。\n2. 分析應包括公司的主要優勢、潛在風險和挑戰，並結合外部新聞資訊（如果提供）。\n3. 提供一個簡短的總結性評價和未來展望（如果可能）。\n4. 分析應客觀且基於數據，段落分明，易於理解。避免提供直接的投資建議（買入/賣出）。")
            full_prompt = "\n".join(str(p) for p in prompt_parts) + prompt_instruction
            # st.text_area("Debug: AI Prompt", full_prompt, height=300)
            with st.spinner("🧠 Gemini AI 正在深度分析中，請稍候..."):
                st.markdown(get_ai_analysis_from_gemini(full_prompt, google_api_key_input))

elif analyze_button and not ticker_symbol_input:
    st.sidebar.error("🚨 請輸入股票代碼。")
elif st.session_state.get('stock_data_loaded') is False and st.session_state.get('current_ticker'):
    st.error(f"加載 {st.session_state.current_ticker} 的數據失敗。請檢查股票代碼或網絡，然後重試。")
else:
    st.info("👋 歡迎使用 Fin Agent 進階股票分析工具！請在左側輸入股票代碼、您的 Google AI API 金鑰以及 SERP API 金鑰（可選），然後點擊 '分析股票' 按鈕開始。")