"""
NSE Historical EOD Tracker
──────────────────────────
Bugs fixed vs previous version:
1. curl_cffi Session was passed into @st.cache_data → not picklable → crash.
   Fix: removed session param everywhere; yfinance 1.5+ auto-creates its own
   curl_cffi Chrome-impersonating session internally when the package is present.
2. time.sleep() in the main Streamlit thread → heartbeat missed → app restarted.
   Fix: removed all sleep() calls.
3. A/D section fetched 200 stocks one-by-one → Streamlit Cloud 60 s run timeout.
   Fix: uses yf.download() batch API (single HTTP round-trip for all tickers).
4. @st.cache_resource session object was never used correctly.
   Fix: removed entirely.
"""

from datetime import date
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="NSE EOD Tracker", layout="wide", page_icon="📈")

st.markdown("""
<style>
  .block-container{padding-top:.8rem;padding-bottom:.8rem}
  .metric-card{
    background:#111827;border:1px solid #1f2937;border-radius:10px;
    padding:14px 10px;text-align:center;
  }
  .metric-card .num{font-size:1.8rem;font-weight:700;line-height:1.1}
  .metric-card .lbl{font-size:.72rem;color:#9ca3af;margin-top:4px}
  .green{color:#22c55e} .red{color:#ef4444} .white{color:#f9fafb}
  div[data-testid="stDataFrame"] iframe{border-radius:8px}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTOR UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────
SECTORS = {
    "My Watchlist":    ["ASTRAL","TATAMOTORS","BANKBARODA","PFC","RECLTD","HUDCO","RVNL","GODREJIND"],
    "Nifty 50":        ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","BHARTIARTL","ITC","LT",
                        "HINDUNILVR","SBIN","BAJFINANCE","KOTAKBANK","AXISBANK","ASIANPAINT",
                        "MARUTI","HCLTECH","SUNPHARMA","TITAN","WIPRO","ONGC","NTPC","POWERGRID",
                        "ULTRACEMCO","NESTLEIND","TECHM","INDUSINDBK","ADANIENT","ADANIPORTS",
                        "BAJAJFINSV","DRREDDY","DIVISLAB","CIPLA","BPCL","COALINDIA","HEROMOTOCO",
                        "M&M","TATASTEEL","JSWSTEEL","EICHERMOT","GRASIM"],
    "Nifty Bank":      ["HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","PNB","INDUSINDBK",
                        "BANDHANBNK","FEDERALBNK","IDFCFIRSTB","AUBANK","BANKBARODA"],
    "Nifty IT":        ["TCS","INFY","HCLTECH","WIPRO","TECHM","LTIM","PERSISTENT","MPHASIS","COFORGE","OFSS"],
    "Nifty Auto":      ["MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT",
                        "BOSCHLTD","MRF","BALKRISIND","MOTHERSON","BHARATFORG","APOLLOTYRE"],
    "Nifty FMCG":      ["HINDUNILVR","ITC","NESTLEIND","BRITANNIA","DABUR","MARICO",
                        "COLPAL","GODREJCP","EMAMILTD","TATACONSUM","UBL","MCDOWELL-N"],
    "Nifty Pharma":    ["SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","APOLLOHOSP","TORNTPHARM",
                        "ALKEM","AUROPHARMA","LUPIN","BIOCON","IPCALAB","GLENMARK"],
    "Nifty Metal":     ["TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA","VEDL","SAIL",
                        "NMDC","APLAPOLLO","NATIONALUM","HINDCOPPER","MOIL","WELCORP"],
    "Nifty Realty":    ["DLF","GODREJPROP","OBEROIRLTY","PHOENIXLTD","PRESTIGE",
                        "BRIGADE","SOBHA","SUNTECK","KOLTEPATIL","MAHLIFE"],
    "Nifty Energy":    ["RELIANCE","ONGC","NTPC","POWERGRID","BPCL","IOC","GAIL",
                        "TATAPOWER","ADANIGREEN","ADANIPOWER","CESC"],
    "Nifty Infra":     ["LT","ADANIPORTS","POWERGRID","NTPC","BHARTIARTL","RVNL","IRFC",
                        "PFC","RECLTD","HUDCO","NBCC","IRB"],
    "Nifty PSU Bank":  ["SBIN","PNB","BANKBARODA","CANARABANK","UNIONBANK","BANKINDIA",
                        "CENTRALBK","UCOBANK","MAHABANK","INDIANB"],
    "Nifty Midcap":    ["PERSISTENT","POLYCAB","FEDERALBNK","LTTS","MPHASIS","COFORGE",
                        "ABCAPITAL","SUNDARMFIN","VOLTAS","ASTRAL","PIIND","ZYDUSLIFE",
                        "MAXHEALTH","CAMS","ANGELONE","BSE","MCX","DIXON","AMBER","TRENT"],
    "Nifty Fin Svcs":  ["HDFCBANK","ICICIBANK","BAJFINANCE","KOTAKBANK","AXISBANK","SBIN",
                        "BAJAJFINSV","HDFCAMC","MUTHOOTFIN","CHOLAFIN","M&MFIN","LICHSGFIN"],
    "Nifty Oil & Gas": ["RELIANCE","ONGC","BPCL","IOC","GAIL","HINDPETRO",
                        "MGL","IGL","PETRONET","GSPL","CASTROLIND"],
    "Custom Basket":   [],
}

SECTOR_INDEX = {
    "Nifty 50":       "^NSEI",
    "Nifty Bank":     "^NSEBANK",
    "Nifty IT":       "^CNXIT",
    "Nifty Auto":     "^CNXAUTO",
    "Nifty FMCG":     "^CNXFMCG",
    "Nifty Pharma":   "^CNXPHARMA",
    "Nifty Metal":    "^CNXMETAL",
    "Nifty Realty":   "^CNXREALTY",
    "Nifty Energy":   "^CNXENERGY",
    "Nifty Infra":    "^CNXINFRA",
    "Nifty PSU Bank": "^CNXPSUBANK",
    "Nifty Fin Svcs": "^CNXFIN",
    "Nifty Oil & Gas":"^CNXOILGAS",
}

AD_UNIVERSE = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","BHARTIARTL","ITC","LT",
    "HINDUNILVR","SBIN","BAJFINANCE","KOTAKBANK","AXISBANK","ASIANPAINT",
    "MARUTI","HCLTECH","SUNPHARMA","TITAN","WIPRO","ONGC","NTPC","POWERGRID",
    "ULTRACEMCO","NESTLEIND","TECHM","INDUSINDBK","ADANIENT","ADANIPORTS",
    "BAJAJFINSV","DRREDDY","DIVISLAB","CIPLA","BPCL","COALINDIA","HEROMOTOCO",
    "M&M","TATASTEEL","JSWSTEEL","EICHERMOT","GRASIM",
    "DMART","SIEMENS","HAVELLS","PIDILITIND","DABUR","MARICO","COLPAL",
    "GODREJCP","TATACONSUM","BRITANNIA","MUTHOOTFIN","CHOLAFIN",
    "SHREECEM","BERGEPAINT","TORNTPHARM","LUPIN","BIOCON","ALKEM","AUROPHARMA",
    "AMBUJACEM","GAIL","HINDPETRO","IOC","PETRONET","MGL","IGL",
    "DLF","GODREJPROP","OBEROIRLTY","PHOENIXLTD","PRESTIGE",
    "APOLLOHOSP","MAXHEALTH","FORTIS","LALPATHLAB",
    "PERSISTENT","POLYCAB","LTTS","MPHASIS","COFORGE","ZYDUSLIFE",
    "CAMS","ANGELONE","BSE","MCX","VOLTAS","ASTRAL","PIIND",
    "ABCAPITAL","SUNDARMFIN","FEDERALBNK","IDFCFIRSTB","AUBANK","BANDHANBNK",
    "PNB","BANKBARODA","CANARABANK","UNIONBANK","BANKINDIA","CENTRALBK","INDIANB",
    "TATAMOTORS","PFC","RECLTD","HUDCO","RVNL","IRFC","RAILTEL","IRCON",
    "RITES","NBCC","HFCL","SUZLON","NHPC","SJVN","TATAPOWER",
    "ADANIGREEN","ADANIPOWER","CESC","JSWENERGY","TORNTPOWER",
    "BAJAJ-AUTO","BOSCHLTD","MRF","BALKRISIND","MOTHERSON","BHARATFORG","APOLLOTYRE",
    "VEDL","NMDC","APLAPOLLO","NATIONALUM","HINDCOPPER","MOIL","SAIL",
    "HDFCAMC","HDFCLIFE","SBILIFE","M&MFIN","LICHSGFIN",
    "DIXON","AMBER","WHIRLPOOL","BLUESTAR","CROMPTON","VGUARD",
    "ZOMATO","NYKAA","DELHIVERY",
    "TRENT","RAYMOND","VEDANT","ABFRL",
    "UPL","COROMANDEL","CHAMBLFERT","DEEPAKNTR",
    "OFSS","KPITTECH","TATAELXSI","HAPPYMNDS","MASTEK","LTIM",
    "VARUNBEV","RADICO","UBL","MCDOWELL-N",
    "JUBLFOOD","DEVYANI","KAJARIACER","GRINDWELL",
    "GODREJIND","EMAMILTD","IPCALAB","GLENMARK","BRIGADE","SOBHA",
    "IRB","GSPL","HINDALCO","WELCORP","NMDC",
]
AD_UNIVERSE = list(dict.fromkeys(AD_UNIVERSE))   # deduplicate


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def drop_partial_today(df: pd.DataFrame) -> pd.DataFrame:
    """Remove today's intraday bar if market is still open."""
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index)
    if str(df.index[-1].date()) == date.today().isoformat():
        df = df.iloc[:-1]
    return df


def ns(sym: str) -> str:
    """Add .NS suffix."""
    s = sym.strip().upper().replace(".NS","")
    return f"{s}.NS"


def cell_bg(val, cap=20):
    """White=0, deep green=+cap, deep red=-cap."""
    if pd.isna(val):
        return ""
    i = min(abs(float(val)) / cap, 1.0)
    if val >= 0:
        r,g,b = int(255-i*195), int(255-i*55),  int(255-i*195)
    else:
        r,g,b = int(255-i*35),  int(255-i*205), int(255-i*205)
    return f"background-color:rgb({r},{g},{b});color:#000;font-weight:600;"


def apply_style(df, pct_cols, cap=20):
    existing = [c for c in pct_cols if c in df.columns]
    fmt = {c:"{:.2f}" for c in df.columns if c not in ("Symbol",)}
    s = df.style.format(fmt, na_rep="—")
    fn = s.map if hasattr(s,"map") else s.applymap
    return fn(lambda v: cell_bg(v, cap), subset=existing)


# ─────────────────────────────────────────────────────────────────────────────
# FETCH SINGLE TICKER  (for sector tracker)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=86400)
def fetch_ticker(symbol: str, is_index: bool = False) -> dict:
    """
    No session param → yfinance manages its own curl_cffi session internally.
    This is what makes it picklable / cacheable by Streamlit.
    """
    yf_sym = symbol if is_index else ns(symbol)
    try:
        raw = yf.Ticker(yf_sym).history(period="1y", interval="1d", auto_adjust=True)
    except Exception as e:
        return {"Symbol": symbol, "Error": str(e)}

    raw = drop_partial_today(raw)

    if raw.empty or len(raw) < 5:
        return {"Symbol": symbol, "Error": "No data / invalid ticker"}

    close = raw["Close"].astype(float)
    high  = raw["High"].astype(float)
    low   = raw["Low"].astype(float)
    ltp   = float(close.iloc[-1])

    # ── Returns: N-day = (ltp vs close N trading-days ago) ───────────────────
    def ret(n):
        # iloc[-1]=ltp, iloc[-(n+1)]=price n trading-days before ltp
        if len(close) <= n:
            return np.nan
        return round(((ltp - float(close.iloc[-(n+1)])) / float(close.iloc[-(n+1)]))*100, 2)

    # ── EMAs ─────────────────────────────────────────────────────────────────
    def ema(span):
        return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

    e4, e10, e20, e50, e100 = ema(4), ema(10), ema(20), ema(50), ema(100)

    # ── 52-week ──────────────────────────────────────────────────────────────
    h52 = round(float(high.max()), 2)
    l52 = round(float(low.min()),  2)
    # positive = above 52W high (breakout) → GREEN
    # negative = below 52W high            → RED
    vs_h = round(((ltp - h52) / h52)*100, 2)
    # positive = above 52W low (safe)      → GREEN
    vs_l = round(((ltp - l52) / l52)*100, 2)

    label = symbol if is_index else symbol.replace(".NS","").upper()
    return {
        "Symbol":     label,
        "LTP":        round(ltp, 2),
        "1D %":       ret(1),
        "3D %":       ret(3),
        "1W %":       ret(5),
        "2W %":       ret(10),
        "1M %":       ret(21),
        "2M %":       ret(42),
        "3M %":       ret(63),
        "6M %":       ret(126),
        "1Y %":       ret(251),
        "4 EMA":      e4,
        "10 EMA":     e10,
        "20 EMA":     e20,
        "50 EMA":     e50,
        "100 EMA":    e100,
        "52W High":   h52,
        "vs 52W H%":  vs_h,
        "52W Low":    l52,
        "vs 52W L%":  vs_l,
        "Error":      None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BATCH FETCH  (for Advance/Decline – uses yf.download, much faster)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_ad_batch(symbols: tuple) -> pd.DataFrame:
    """
    Downloads all tickers in one batch call.
    Returns DataFrame with MultiIndex columns (field, ticker).
    Uses tuple arg so Streamlit can hash it.
    """
    yf_syms = [ns(s) for s in symbols]
    try:
        raw = yf.download(
            tickers   = yf_syms,
            period    = "1y",
            interval  = "1d",
            auto_adjust = True,
            progress  = False,
            threads   = True,
            multi_level_index = True,
        )
    except Exception:
        return pd.DataFrame()

    raw = drop_partial_today(raw)
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR NAVIGATION
# ─────────────────────────────────────────────────────────────────────────────
if "custom_stocks" not in st.session_state:
    st.session_state.custom_stocks = []

st.sidebar.title("📈 NSE EOD Tracker")
page = st.sidebar.radio("", ["📊 Sector Tracker", "📉 Advance / Decline"], label_visibility="collapsed")

# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1 – SECTOR TRACKER
# ─────────────────────────────────────────────────────────────────────────────
if page == "📊 Sector Tracker":
    st.header("📊 Sector Tracker")

    sector_list = [s for s in SECTORS if s != "Custom Basket"] + ["Custom Basket"]
    sel_sector  = st.sidebar.selectbox("Sector / Basket", sector_list)
    def_stocks  = SECTORS.get(sel_sector, [])

    all_opts = sorted(set(def_stocks + st.session_state.custom_stocks))
    ms_def   = st.session_state.custom_stocks if sel_sector == "Custom Basket" else def_stocks

    sel_stocks = st.sidebar.multiselect("Stocks", options=all_opts, default=ms_def)

    new_s = st.sidebar.text_input("Add stock (e.g. ZOMATO)").upper().strip()
    ca, cb = st.sidebar.columns(2)
    with ca:
        if st.button("➕ Add") and new_s:
            if new_s not in st.session_state.custom_stocks:
                st.session_state.custom_stocks.append(new_s)
                st.rerun()
    with cb:
        if st.button("🗑 Clear"):
            st.session_state.custom_stocks = []
            st.rerun()

    final = list(dict.fromkeys(sel_stocks + st.session_state.custom_stocks))
    if not final:
        st.info("Select stocks from the sidebar.")
        st.stop()

    # ── Fetch ────────────────────────────────────────────────────────────────
    results, errors = [], []
    bar  = st.progress(0)
    note = st.empty()

    # Sector index
    idx_sym = SECTOR_INDEX.get(sel_sector)
    idx_row = None
    if idx_sym:
        note.text(f"Fetching sector index…")
        d = fetch_ticker(idx_sym, is_index=True)
        if d and not d.get("Error"):
            d.pop("Error", None)
            d["Symbol"] = f"▶ {sel_sector} INDEX"
            idx_row = d

    # Individual stocks
    for i, sym in enumerate(final):
        note.text(f"Fetching {sym}  ({i+1}/{len(final)})")
        d = fetch_ticker(sym)
        if d and not d.get("Error"):
            d.pop("Error", None)
            results.append(d)
        else:
            errors.append(f"**{sym}**: {d.get('Error','unknown')}")
        bar.progress((i+1) / len(final))

    bar.empty(); note.empty()

    if errors:
        with st.expander(f"⚠️ {len(errors)} ticker(s) failed"):
            for e in errors: st.write(e)

    if not results:
        st.warning("No data returned. Check tickers or try again.")
        st.stop()

    df = pd.DataFrame(results)
    num_cols = [c for c in df.columns if c != "Symbol"]

    # Average row
    avg = {"Symbol": f"📊 {sel_sector} AVG"}
    avg.update(df[num_cols].mean(numeric_only=True).round(2).to_dict())

    frames = []
    if idx_row:
        frames.append(pd.DataFrame([idx_row]))
    frames.append(pd.DataFrame([avg]))
    frames.append(df)
    df_all = pd.concat(frames, ignore_index=True)

    PCT_COLS = ["1D %","3D %","1W %","2W %","1M %","2M %","3M %","6M %","1Y %",
                "vs 52W H%","vs 52W L%"]

    st.caption(f"{sel_sector} · {len(final)} stocks · 🟢 positive  🔴 negative")
    st.dataframe(apply_style(df_all, PCT_COLS), use_container_width=True, height=600)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2 – ADVANCE / DECLINE
# ─────────────────────────────────────────────────────────────────────────────
else:
    st.header("📉 Market Advance / Decline")
    st.caption(f"Universe: {len(AD_UNIVERSE)} NSE stocks · market cap ≥ ₹1000 Cr")

    if st.button("🔄 Fetch / Refresh Market Data"):
        st.session_state.pop("ad_result", None)   # bust cache key

    if "ad_result" not in st.session_state:
        with st.spinner("Downloading batch data… (this takes ~30–60 seconds)"):
            raw = fetch_ad_batch(tuple(AD_UNIVERSE))
            st.session_state.ad_result = raw

    raw = st.session_state.get("ad_result", pd.DataFrame())

    if raw.empty:
        st.warning("Could not fetch data. Try refreshing.")
        st.stop()

    # ── Compute per-stock metrics ─────────────────────────────────────────────
    rows_ad = []
    for sym in AD_UNIVERSE:
        yf_sym = ns(sym)
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                close_s = raw["Close"][yf_sym].dropna()
                high_s  = raw["High"][yf_sym].dropna()
                low_s   = raw["Low"][yf_sym].dropna()
            else:
                close_s = raw["Close"].dropna()
                high_s  = raw["High"].dropna()
                low_s   = raw["Low"].dropna()

            if len(close_s) < 10:
                continue

            ltp  = float(close_s.iloc[-1])
            prev = float(close_s.iloc[-2])

            def ema(span):
                return float(close_s.ewm(span=span, adjust=False).mean().iloc[-1])

            e4, e10, e20, e50, e100 = ema(4), ema(10), ema(20), ema(50), ema(100)
            h52 = float(high_s.max())
            l52 = float(low_s.min())

            rows_ad.append({
                "Symbol":    sym,
                "LTP":       round(ltp,  2),
                "Day Chg%":  round(((ltp-prev)/prev)*100, 2),
                ">4EMA":     "✅" if ltp > e4   else "❌",
                "4 EMA":     round(e4,   2),
                ">10EMA":    "✅" if ltp > e10  else "❌",
                "10 EMA":    round(e10,  2),
                ">20EMA":    "✅" if ltp > e20  else "❌",
                "20 EMA":    round(e20,  2),
                ">50EMA":    "✅" if ltp > e50  else "❌",
                "50 EMA":    round(e50,  2),
                ">100EMA":   "✅" if ltp > e100 else "❌",
                "100 EMA":   round(e100, 2),
                "vs 52W H%": round(((ltp-h52)/h52)*100, 2),
                "vs 52W L%": round(((ltp-l52)/l52)*100, 2),
                "52W High":  round(h52,  2),
                "52W Low":   round(l52,  2),
            })
        except Exception:
            continue

    if not rows_ad:
        st.warning("Could not parse downloaded data.")
        st.stop()

    df_ad = pd.DataFrame(rows_ad)
    total = len(df_ad)

    # ── Summary metrics ───────────────────────────────────────────────────────
    adv   = int((df_ad["Day Chg%"] > 0).sum())
    dec   = int((df_ad["Day Chg%"] < 0).sum())
    unch  = total - adv - dec

    a4   = int((df_ad[">4EMA"]   == "✅").sum())
    a10  = int((df_ad[">10EMA"]  == "✅").sum())
    a20  = int((df_ad[">20EMA"]  == "✅").sum())
    a50  = int((df_ad[">50EMA"]  == "✅").sum())
    a100 = int((df_ad[">100EMA"] == "✅").sum())

    at52h   = int((df_ad["vs 52W H%"] >= 0).sum())
    near52h = int((df_ad["vs 52W H%"] >= -5).sum())
    at52l   = int((df_ad["vs 52W L%"] <= 5).sum())
    near52l = int((df_ad["vs 52W L%"] <= 10).sum())

    def card(num, label, color="white"):
        return f'<div class="metric-card"><div class="num {color}">{num}</div><div class="lbl">{label}</div></div>'

    # Row 1 – Today's breadth
    st.markdown("#### Today's Breadth")
    c = st.columns(4)
    c[0].markdown(card(adv,  "Advancing",  "green"), unsafe_allow_html=True)
    c[1].markdown(card(dec,  "Declining",  "red"),   unsafe_allow_html=True)
    c[2].markdown(card(unch, "Unchanged",  "white"), unsafe_allow_html=True)
    c[3].markdown(card(total,"Total Tracked","white"),unsafe_allow_html=True)

    st.markdown("---")

    # Row 2 – EMA breadth
    st.markdown("#### Stocks Above EMA (Breadth)")
    c = st.columns(5)
    for col, above, label in zip(c, [a4,a10,a20,a50,a100], ["4 EMA","10 EMA","20 EMA","50 EMA","100 EMA"]):
        pct   = round(above/total*100,1) if total else 0
        color = "green" if above >= total/2 else "red"
        col.markdown(
            f'<div class="metric-card">'
            f'<div class="num {color}">{above}<span style="font-size:.9rem;color:#6b7280"> /{total}</span></div>'
            f'<div class="lbl">{label} · {pct}%</div>'
            f'</div>', unsafe_allow_html=True)

    st.markdown("---")

    # Row 3 – 52W extremes
    st.markdown("#### 52-Week Extremes")
    c = st.columns(4)
    c[0].markdown(card(at52h,   "At / Above 52W High", "green"), unsafe_allow_html=True)
    c[1].markdown(card(near52h, "Within 5% of 52W High","green"),unsafe_allow_html=True)
    c[2].markdown(card(at52l,   "Within 5% of 52W Low","red"),   unsafe_allow_html=True)
    c[3].markdown(card(near52l, "Within 10% of 52W Low","red"),  unsafe_allow_html=True)

    st.markdown("---")

    # ── Detail table ──────────────────────────────────────────────────────────
    st.markdown("#### Stock Detail")

    ad_pct_cols = ["Day Chg%", "vs 52W H%", "vs 52W L%"]
    bool_cols   = [">4EMA",">10EMA",">20EMA",">50EMA",">100EMA"]
    fmt_cols    = {c:"{:.2f}" for c in df_ad.columns
                   if c not in (["Symbol"]+bool_cols)}

    s = df_ad.style.format(fmt_cols)
    fn = s.map if hasattr(s,"map") else s.applymap
    s  = fn(lambda v: cell_bg(v, 10), subset=[c for c in ad_pct_cols if c in df_ad.columns])

    st.dataframe(s, use_container_width=True, height=580)
    st.caption("✅ = price above EMA  |  ❌ = price below EMA  |  Data refreshes every hour")
