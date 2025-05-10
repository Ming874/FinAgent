# Fin AIgent: Stock Analysis & AI Insights Platform

Fin AIgent is a Streamlit web application designed for comprehensive stock analysis. It integrates financial data from Yahoo Finance, technical indicators, real-time news aggregation (via yfinance and SerpAPI), and AI-powered fundamental analysis and interactive chat using Google's Gemini API. This tool aims to provide investors and analysts with a consolidated platform for making informed investment decisions.

![Fin AIgent Screenshot](platform_demo.png)

## ✨ Features

*   **Comprehensive Stock Data:** Fetches detailed stock information, historical prices (up to 5 years), financials (income statement, balance sheet, cash flow), dividend history, major holders, institutional holders, and analyst recommendations via `yfinance`.
*   **Interactive Price Charts:**
    *   Candlestick charts with customizable time periods (1M, 3M, 6M, YTD, 1Y, 2Y, 5Y, All).
    *   Volume charts.
*   **Technical Indicators:**
    *   Simple Moving Averages (SMA)
    *   Exponential Moving Averages (EMA)
    *   Relative Strength Index (RSI)
    *   Moving Average Convergence Divergence (MACD)
    *   Bollinger Bands (BB)
    *   All indicators are configurable through the sidebar.
*   **Financial Statement Analysis:** Displays and plots key metrics from annual income statements, balance sheets, and cash flow statements.
*   **Key Financial Ratios:** Presents a list of important financial ratios (P/E, P/B, EPS, ROE, ROA, Margins, etc.).
*   **Company Profile:**
    *   Business summary.
    *   Major and institutional shareholders.
    *   Analyst recommendation summaries (pie/bar charts).
*   **News Aggregation:**
    *   Recent news from `yfinance` specific to the ticker.
    *   Broader market news search for the company using `SerpAPI` (Google News).
*   **AI-Powered Analysis & Chat (Google Gemini):**
    *   Generates an initial fundamental analysis report based on the fetched company data and news.
    *   Provides an interactive chat interface to ask follow-up questions about the company, its financials, or market conditions, with conversation memory.
*   **User-Friendly Interface:** Built with Streamlit, featuring a sidebar for inputs and chart configurations, and tabbed navigation for different analysis sections.
*   **Data Caching:** Uses `st.cache_data` to speed up repeated data fetching.

## ⚙️ Prerequisites

*   Python 3.8+
*   pip (Python package installer)

## 🚀 Installation & Setup

1.  **Clone the repository (or download the files):**
    ```bash
    # If you have it in a git repo
    # git clone <your-repo-url>
    # cd <your-repo-name>
    ```
    If you only have the `app.py` file, create a new directory, place `app.py` inside it, and `cd` into that directory.

2.  **Create `requirements.txt`:**
    Create a file named `requirements.txt` in the same directory as your `app.py` and paste the following content into it:
    ```txt
    streamlit
    yfinance
    pandas
    plotly
    google-generativeai
    google-search-results
    ta
    pytz
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **API Keys:**
    This application requires API keys for full functionality:
    *   **Google Gemini API Key:** For AI analysis and chat. Obtain it from [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   **SerpAPI Key:** For enhanced news search. Obtain it from [SerpApi](https://serpapi.com/).

    These keys are entered directly into the application's sidebar when you run it. They are not stored persistently by the app beyond the current session.

## 📊 Usage

1.  **Run the Streamlit application:**
    Open your terminal in the project directory and run:
    ```bash
    streamlit run app.py
    ```
    (Replace `app.py` with your Python script's filename if it's different).

2.  **Interact with the application:**
    *   The application will open in your web browser.
    *   In the sidebar:
        *   Enter a **Stock Ticker** (e.g., `AAPL`, `2330.TW`).
        *   Enter your **Google Gemini API Key**.
        *   Enter your **Serp API Key**.
        *   Select the desired **Time Period** for charts.
        *   Configure **Technical Indicators** visibility and parameters.
    *   Click the "**立即分析**" (Analyze Now) button.
    *   Navigate through the tabs ("Overview", "Price Analysis", "Financials", "Company Info", "AI Smart Analysis & Chat") to explore different aspects of the stock.

## 🛠️ Technologies Used

*   **Streamlit:** For building the web application interface.
*   **yfinance:** For fetching stock market data.
*   **Pandas:** For data manipulation and analysis.
*   **Plotly (Express & Graph Objects):** For creating interactive charts.
*   **Google Generative AI (Gemini API):** For AI-driven analysis and chat.
*   **SerpAPI (google-search-results):** For fetching external news results.
*   **TA-Lib (python wrapper `ta`):** For calculating technical analysis indicators.
*   **Pytz:** For timezone handling.

## 🧑‍💻 Author

Maintained by Tai-Ming Chen.