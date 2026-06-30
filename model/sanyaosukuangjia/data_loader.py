from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)


try:
    import akshare as ak  # type: ignore
except ImportError:  # pragma: no cover
    ak = None

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None


MODEL_RAW_COLUMNS = [
    "raw_ret_5d_spx",
    "raw_ret_5d_ndx",
    "raw_ret_5d_hsi",
    "raw_ret_5d_hstech",
    "raw_ret_5d_csi300",
    "raw_ret_5d_cyb",
    "raw_ret_5d_rut",
    "raw_ret_20d_spx",
    "raw_ret_20d_ndx",
    "raw_ret_20d_hsi",
    "raw_ret_20d_hstech",
    "raw_ret_20d_csi300",
    "raw_ret_20d_cyb",
    "raw_ret_20d_rut",
    "raw_above_ma20_spx",
    "raw_above_ma20_ndx",
    "raw_above_ma20_hsi",
    "raw_above_ma20_csi300",
    "raw_above_ma60_spx",
    "raw_above_ma60_ndx",
    "raw_above_ma60_hsi",
    "raw_above_ma60_csi300",
    "raw_ratio_qqq_dia",
    "raw_ratio_qqq_iwm",
    "raw_ratio_cyb_sz50",
    "raw_ratio_kc50_hongli",
    "raw_ratio_hstech_hshighdiv",
    "raw_ret_5d_ai",
    "raw_ret_5d_semis",
    "raw_ret_5d_software",
    "raw_ret_5d_ev",
    "raw_ret_5d_energy",
    "raw_ret_5d_finance",
    "raw_ret_5d_consumer",
    "raw_ret_5d_defense",
    "raw_leader_industry_days",
    "raw_new_high_count_leaders",
    "raw_theme_breadth_ai",
    "raw_theme_breadth_semis",
    "raw_theme_breadth_software",
    "raw_adv_decline_ratio_us",
    "raw_adv_decline_ratio_cn",
    "raw_adv_decline_ratio_hk",
    "raw_newhigh_newlow_ratio_us",
    "raw_newhigh_newlow_ratio_cn",
    "raw_pct_above_ma20_us",
    "raw_pct_above_ma20_cn",
    "raw_pct_above_ma20_hk",
    "raw_pct_above_ma60_us",
    "raw_pct_above_ma60_cn",
    "raw_pct_above_ma60_hk",
    "raw_top10_weight_contrib_us",
    "raw_top10_weight_contrib_cn",
    "raw_top10_weight_contrib_hk",
    "raw_top3_industry_turnover_share",
    "raw_fed_cut_prob",
    "raw_us2y_yield",
    "raw_us10y_yield",
    "raw_us10y_real_yield",
    "raw_dxy",
    "raw_pbo_c_easing_flag",
    "raw_dr007",
    "raw_shibor_3m",
    "raw_cn10y_yield",
    "raw_credit_impulse",
    "raw_usdcnh",
    "raw_turnover_a_share",
    "raw_turnover_hk",
    "raw_turnover_us_etf",
    "raw_northbound_net_inflow",
    "raw_southbound_net_inflow",
    "raw_us_equity_etf_flow",
    "raw_margin_balance_change",
    "raw_gold_ret_20d",
    "raw_btc_ret_20d",
    "raw_wti_ret_20d",
    "raw_hyg_lqd_ratio",
    "raw_vix",
]


@dataclass(frozen=True)
class SeriesSpec:
    name: str
    loader: str
    symbol: str
    close_alias: str | None = None


def _to_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.sort_index()
        out.index.name = "date"
        return out
    date_candidates = ["date", "日期", "trade_date", "datetime"]
    date_col = next((col for col in date_candidates if col in out.columns), None)
    if date_col is None and not out.empty:
        first_col = out.columns[0]
        parsed = pd.to_datetime(out[first_col], errors="coerce")
        if parsed.notna().sum() >= max(3, len(out) // 2):
            out[first_col] = parsed
            date_col = first_col
    if date_col is None:
        raise ValueError(f"Cannot find date column in {list(out.columns)}")
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.sort_values(date_col).set_index(date_col)
    out.index.name = "date"
    return out


def _pick_first_existing(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    for col in columns:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    raise ValueError(f"Cannot find any of columns {columns} in {list(df.columns)}")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = _to_datetime_index(df)
    normalized = pd.DataFrame(index=out.index)
    normalized["open"] = _pick_first_existing(out, ["open", "开盘"])
    normalized["high"] = _pick_first_existing(out, ["high", "最高"])
    normalized["low"] = _pick_first_existing(out, ["low", "最低"])
    normalized["close"] = _pick_first_existing(out, ["close", "收盘", "最新价"])

    volume_candidates = ["volume", "成交量"]
    amount_candidates = ["amount", "成交额", "turnover"]

    normalized["volume"] = (
        _pick_first_existing(out, volume_candidates) if any(c in out.columns for c in volume_candidates) else np.nan
    )
    normalized["amount"] = (
        _pick_first_existing(out, amount_candidates) if any(c in out.columns for c in amount_candidates) else np.nan
    )
    return normalized


def _merge_series(frames: dict[str, pd.Series]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, axis=1).sort_index()
    merged.index.name = "date"
    return merged


def _find_column_by_keywords(df: pd.DataFrame, keywords: list[str]) -> str:
    normalized = {
        col: str(col)
        .replace("�", "")
        .replace(" ", "")
        .replace(":", "")
        .replace("-", "")
        .lower()
        for col in df.columns
    }
    target_keywords = [kw.replace(" ", "").replace("-", "").lower() for kw in keywords]

    for col, norm in normalized.items():
        if all(keyword in norm for keyword in target_keywords):
            return col

    raise ValueError(f"Cannot find column with keywords {keywords} in {list(df.columns)}")


def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    if series.dtype == object:
        cleaned = (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.strip()
            .replace({"None": np.nan, "nan": np.nan, "": np.nan})
        )
        return pd.to_numeric(cleaned, errors="coerce")
    return pd.to_numeric(series, errors="coerce")


def _business_calendar(start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start_date, end_date, freq="B")


def _align_to_business_days(
    series: pd.Series,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    *,
    method: str = "ffill",
) -> pd.Series:
    base_index = _business_calendar(start_date, end_date)
    out = series.sort_index().reindex(base_index)
    if method == "ffill":
        out = out.ffill()
    elif method == "none":
        pass
    else:
        raise ValueError(f"Unsupported align method: {method}")
    out.name = series.name
    return out


class SinaDataLoader:
    """
    新浪优先的数据加载器。

    设计思路：
    1. A 股、港股、美股指数和部分外盘期货优先用 AKShare 的新浪接口
    2. 新浪不稳定或覆盖不足的字段，允许回退到 yfinance
    3. 第一版先把 `scoring.py` 需要的原始字段尽量补齐，缺失项保留 NaN
    """

    CORE_SERIES_SPECS = [
        SeriesSpec("spx_close", "sina_us_index", ".INX"),
        SeriesSpec("ndx_close", "sina_us_index", ".NDX"),
        SeriesSpec("rut_close", "sina_us_stock", "IWM"),
        SeriesSpec("hsi_close", "sina_hk_index", "HSI"),
        SeriesSpec("csi300_close", "sina_zh_index", "sh000300"),
        SeriesSpec("cyb_close", "sina_zh_index", "sz399006"),
        SeriesSpec("sz50_close", "sina_zh_index", "sh000016"),
        SeriesSpec("kc50_close", "sina_zh_index", "sh000688"),
        SeriesSpec("hongli_close", "sina_zh_index", "sh000015"),
        SeriesSpec("dxy_close", "sina_us_stock", "UUP"),
        SeriesSpec("gold_close", "sina_us_stock", "GLD"),
        SeriesSpec("wti_close", "sina_us_stock", "USO"),
        SeriesSpec("btc_close", "sina_us_stock", "BITO"),
        SeriesSpec("vix_close", "sina_us_stock", "VXX"),
        SeriesSpec("hyg_close", "sina_us_stock", "HYG"),
        SeriesSpec("lqd_close", "sina_us_stock", "LQD"),
        SeriesSpec("qqq_close", "sina_us_stock", "QQQ"),
        SeriesSpec("dia_close", "sina_us_stock", "DIA"),
        SeriesSpec("iwm_close", "sina_us_stock", "IWM"),
    ]

    def __init__(self, start_date: str, end_date: str, *, use_yfinance_fallback: bool = True) -> None:
        self.start_date = pd.Timestamp(start_date)
        self.end_date = pd.Timestamp(end_date)
        self.use_yfinance_fallback = use_yfinance_fallback

    def _fetch_sina_zh_index(self, symbol: str) -> pd.DataFrame:
        if ak is None:
            raise ImportError("akshare is required for sina_zh_index loader")
        return _normalize_ohlcv(ak.stock_zh_index_daily(symbol=symbol))

    def _fetch_sina_hk_index(self, symbol: str) -> pd.DataFrame:
        if ak is None:
            raise ImportError("akshare is required for sina_hk_index loader")
        return _normalize_ohlcv(ak.stock_hk_index_daily_sina(symbol=symbol))

    def _fetch_sina_us_index(self, symbol: str) -> pd.DataFrame:
        if ak is None:
            raise ImportError("akshare is required for sina_us_index loader")
        return _normalize_ohlcv(ak.index_us_stock_sina(symbol=symbol))

    def _fetch_sina_us_stock(self, symbol: str) -> pd.DataFrame:
        if ak is None:
            raise ImportError("akshare is required for sina_us_stock loader")
        return _normalize_ohlcv(ak.stock_us_daily(symbol=symbol, adjust=""))

    def _fetch_yfinance(self, symbol: str) -> pd.DataFrame:
        if yf is None:
            raise ImportError("yfinance is required for yf loader")
        data = yf.download(
            symbol,
            start=self.start_date.strftime("%Y-%m-%d"),
            end=(self.end_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=False,
            progress=False,
        )
        if data.empty:
            raise ValueError(f"Empty yfinance data for {symbol}")
        data = data.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        data.index = pd.to_datetime(data.index)
        data.index.name = "date"
        return data[["open", "high", "low", "close", "volume"]]

    def _fetch_one_series(self, spec: SeriesSpec) -> pd.Series:
        loader_map: dict[str, Callable[[str], pd.DataFrame]] = {
            "sina_zh_index": self._fetch_sina_zh_index,
            "sina_hk_index": self._fetch_sina_hk_index,
            "sina_us_index": self._fetch_sina_us_index,
            "sina_us_stock": self._fetch_sina_us_stock,
            "yf": self._fetch_yfinance,
        }
        if spec.loader not in loader_map:
            raise ValueError(f"Unsupported loader type: {spec.loader}")

        try:
            df = loader_map[spec.loader](spec.symbol)
        except Exception as exc:
            if spec.loader != "yf" and self.use_yfinance_fallback and yf is not None:
                LOGGER.warning("Primary loader failed for %s, fallback to yfinance: %s", spec.name, exc)
                df = self._fetch_yfinance(spec.symbol)
            else:
                raise

        df = df.loc[(df.index >= self.start_date) & (df.index <= self.end_date)]
        if "close" not in df.columns:
            raise ValueError(f"No close column for {spec.name}")
        series = pd.to_numeric(df["close"], errors="coerce")
        series.name = spec.name
        return series

    def load_core_series(self) -> pd.DataFrame:
        frames: dict[str, pd.Series] = {}
        for spec in self.CORE_SERIES_SPECS:
            try:
                frames[spec.name] = self._fetch_one_series(spec)
                LOGGER.info("Loaded %s", spec.name)
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Failed to load %s: %s", spec.name, exc)

        df = _merge_series(frames)
        if df.empty:
            raise ValueError("No market series loaded. Check akshare/yfinance availability or symbols.")
        return df

    def _load_shibor_3m(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for Shibor data")
        df = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月")
        df = df.copy()
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        df = df.dropna(subset=[df.columns[0]]).sort_values(df.columns[0]).set_index(df.columns[0])
        df.index.name = "date"
        value = _coerce_numeric_series(df.iloc[:, 1])
        value = _coerce_numeric_series(value)
        value.name = "raw_shibor_3m"
        return _align_to_business_days(value, self.start_date, self.end_date)

    def _load_dr007(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for DR007 data")
        df = ak.repo_rate_hist(
            start_date=self.start_date.strftime("%Y%m%d"),
            end_date=self.end_date.strftime("%Y%m%d"),
        )
        df = _to_datetime_index(df)
        value = _pick_first_existing(df, ["FDR007", "DR007", "FR007"])
        value = _coerce_numeric_series(value)
        value.name = "raw_dr007"
        return _align_to_business_days(value, self.start_date, self.end_date)

    def _load_cn10y_yield(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for CN10Y data")
        df = ak.bond_zh_us_rate(start_date=self.start_date.strftime("%Y%m%d"))
        df = df.copy()
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        df = df.dropna(subset=[df.columns[0]]).sort_values(df.columns[0]).set_index(df.columns[0])
        df.index.name = "date"
        try:
            col = _find_column_by_keywords(df, ["中国", "10"])
        except ValueError:
            col = df.columns[2]
        value = _coerce_numeric_series(df[col])
        value.name = "raw_cn10y_yield"
        return _align_to_business_days(value, self.start_date, self.end_date)

    def _load_us_yields(self) -> pd.DataFrame:
        if ak is None:
            raise ImportError("akshare is required for US yields data")
        df = ak.bond_zh_us_rate(start_date=self.start_date.strftime("%Y%m%d"))
        df = df.copy()
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        df = df.dropna(subset=[df.columns[0]]).sort_values(df.columns[0]).set_index(df.columns[0])
        df.index.name = "date"

        out = pd.DataFrame(index=df.index)
        try:
            us2y_col = _find_column_by_keywords(df, ["美国", "2"])
        except ValueError:
            us2y_col = df.columns[6]
        try:
            us10y_col = _find_column_by_keywords(df, ["美国", "10"])
        except ValueError:
            us10y_col = df.columns[8]

        out["raw_us2y_yield"] = _coerce_numeric_series(df[us2y_col])
        out["raw_us10y_yield"] = _coerce_numeric_series(df[us10y_col])
        return out

    def _load_credit_impulse(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for social financing data")
        df = ak.macro_china_shrzgm()
        df = df.copy()
        date_col = "月份" if "月份" in df.columns else "date"
        value_col = next(
            (
                col
                for col in ["社会融资规模增量", "社融增量", "当月"]
                if col in df.columns
            ),
            None,
        )
        if value_col is None:
            raise ValueError(f"Cannot find social financing column in {list(df.columns)}")
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col)
        series = _coerce_numeric_series(df[value_col])
        # 用 12 个月同比变化近似信用脉冲，先求同比，再映射到日频。
        credit_impulse = series.pct_change(12)
        credit_impulse.index = df[date_col]
        credit_impulse.name = "raw_credit_impulse"
        return _align_to_business_days(credit_impulse, self.start_date, self.end_date)

    def _load_usdcnh_proxy(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for RMB FX proxy")
        df = ak.currency_boc_sina(
            symbol="美元",
            start_date=self.start_date.strftime("%Y%m%d"),
            end_date=self.end_date.strftime("%Y%m%d"),
        )
        df = df.copy()
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        df = df.dropna(subset=[df.columns[0]]).sort_values(df.columns[0]).set_index(df.columns[0])
        df.index.name = "date"

        # 中行牌价通常以 100 外币折人民币报价，换算到 USD/CNY 需除以 100。
        raw_value = _coerce_numeric_series(df.iloc[:, -1]) / 100.0
        raw_value.name = "raw_usdcnh"
        return _align_to_business_days(raw_value, self.start_date, self.end_date)

    def _load_a_share_turnover(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for A-share turnover data")
        sh_df = self._fetch_sina_zh_index("sh000001")
        sz_df = self._fetch_sina_zh_index("sz399001")
        sh_amount = _coerce_numeric_series(sh_df["amount"]) if "amount" in sh_df.columns else sh_df["close"]
        sz_amount = _coerce_numeric_series(sz_df["amount"]) if "amount" in sz_df.columns else sz_df["close"]
        value = sh_amount.add(sz_amount, fill_value=np.nan)
        value.name = "raw_turnover_a_share"
        return _align_to_business_days(value, self.start_date, self.end_date)

    def _load_hk_turnover(self) -> pd.Series:
        if ak is None:
            raise ImportError("akshare is required for HK turnover data")
        df = self._fetch_sina_hk_index("HSI")
        value = _coerce_numeric_series(df["amount"]) if "amount" in df.columns else df["close"]
        value.name = "raw_turnover_hk"
        return _align_to_business_days(value, self.start_date, self.end_date)

    def _load_us_turnover_proxy(self) -> pd.Series:
        df = self._fetch_sina_us_stock("QQQ")
        volume = _coerce_numeric_series(df["volume"]) if "volume" in df.columns else np.nan
        close = _coerce_numeric_series(df["close"])
        value = close * volume
        value.name = "raw_turnover_us_etf"
        return _align_to_business_days(value, self.start_date, self.end_date, method="none")

    def _load_macro_frames(self) -> dict[str, pd.Series]:
        loaders: dict[str, Callable[[], pd.Series]] = {
            "raw_dr007": self._load_dr007,
            "raw_shibor_3m": self._load_shibor_3m,
            "raw_cn10y_yield": self._load_cn10y_yield,
            "raw_credit_impulse": self._load_credit_impulse,
            "raw_usdcnh": self._load_usdcnh_proxy,
            "raw_turnover_a_share": self._load_a_share_turnover,
            "raw_turnover_hk": self._load_hk_turnover,
            "raw_turnover_us_etf": self._load_us_turnover_proxy,
        }
        out: dict[str, pd.Series] = {}
        try:
            us_yields_df = self._load_us_yields()
            for col in us_yields_df.columns:
                out[col] = _align_to_business_days(
                    us_yields_df[col],
                    self.start_date,
                    self.end_date,
                )
                LOGGER.info("Loaded %s", col)
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Failed to load US yields block: %s", exc)

        for name, func in loaders.items():
            try:
                out[name] = func()
                LOGGER.info("Loaded %s", name)
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Failed to load %s: %s", name, exc)
        return out

    def load_optional_macro_series(self) -> pd.DataFrame:
        """
        这些字段很多不完全来自新浪，但对模型有效，所以这里优先接入
        AkShare 能稳定提供的宏观/流动性数据；其余字段后续继续扩展。
        """
        index = _business_calendar(self.start_date, self.end_date)
        base = pd.DataFrame(index=index)
        macro_frames = self._load_macro_frames()
        if not macro_frames:
            return base

        merged = _merge_series(macro_frames).reindex(index).sort_index().ffill()
        merged.index.name = "date"

        if "raw_dr007" in merged.columns and "raw_credit_impulse" in merged.columns:
            merged["raw_pbo_c_easing_flag"] = (
                (merged["raw_dr007"] < merged["raw_dr007"].rolling(20, min_periods=5).mean())
                | (merged["raw_credit_impulse"] > 0)
            ).astype(float)
        elif "raw_dr007" in merged.columns:
            merged["raw_pbo_c_easing_flag"] = (
                merged["raw_dr007"] < merged["raw_dr007"].rolling(20, min_periods=5).mean()
            ).astype(float)
        else:
            merged["raw_pbo_c_easing_flag"] = np.nan

        return merged

    @staticmethod
    def _return_feature(series: pd.Series, periods: int) -> pd.Series:
        return series.pct_change(periods=periods)

    @staticmethod
    def _above_ma_feature(series: pd.Series, window: int) -> pd.Series:
        ma = series.rolling(window=window, min_periods=max(5, window // 3)).mean()
        return (series > ma).astype(float)

    def build_model_input(self) -> pd.DataFrame:
        core = self.load_core_series()
        macro = self.load_optional_macro_series()
        df = core.join(macro, how="outer").sort_index()
        out = pd.DataFrame(index=df.index)
        out.index.name = "date"

        self._fill_market_return_fields(out, df)
        self._fill_style_ratio_fields(out, df)
        self._fill_macro_and_cross_asset_fields(out, df)
        self._fill_placeholder_fields(out)

        result = out.reset_index()
        return result

    def _fill_market_return_fields(self, out: pd.DataFrame, df: pd.DataFrame) -> None:
        market_map = {
            "spx": "spx_close",
            "ndx": "ndx_close",
            "hsi": "hsi_close",
            "hstech": "hstech_close",
            "csi300": "csi300_close",
            "cyb": "cyb_close",
            "rut": "rut_close",
        }
        for suffix, close_col in market_map.items():
            if close_col not in df.columns:
                continue
            close = df[close_col]
            out[f"raw_ret_5d_{suffix}"] = self._return_feature(close, 5)
            out[f"raw_ret_20d_{suffix}"] = self._return_feature(close, 20)

        above_ma_map = {
            "csi300": "csi300_close",
            "cyb": "cyb_close",
        }
        for suffix, close_col in above_ma_map.items():
            if close_col not in df.columns:
                continue
            close = df[close_col]
            out[f"raw_above_ma20_{suffix}"] = self._above_ma_feature(close, 20)
            out[f"raw_above_ma60_{suffix}"] = self._above_ma_feature(close, 60)

    def _fill_style_ratio_fields(self, out: pd.DataFrame, df: pd.DataFrame) -> None:
        ratio_specs = {
            "raw_ratio_qqq_dia": ("qqq_close", "dia_close"),
            "raw_ratio_qqq_iwm": ("qqq_close", "iwm_close"),
            "raw_ratio_cyb_sz50": ("cyb_close", "sz50_close"),
            "raw_ratio_kc50_hongli": ("kc50_close", "hongli_close"),
            "raw_ratio_hstech_hshighdiv": ("hstech_close", None),
        }
        for raw_col, (num_col, den_col) in ratio_specs.items():
            if num_col not in df.columns:
                continue
            if den_col is None or den_col not in df.columns:
                out[raw_col] = np.nan
            else:
                denominator = df[den_col].replace(0, np.nan)
                out[raw_col] = df[num_col] / denominator

    def _fill_macro_and_cross_asset_fields(self, out: pd.DataFrame, df: pd.DataFrame) -> None:
        if "us2y_yield_close" in df.columns:
            out["raw_us2y_yield"] = df["us2y_yield_close"]
        if "us10y_yield_close" in df.columns:
            out["raw_us10y_yield"] = df["us10y_yield_close"]
        if "us_real_yield_close" in df.columns:
            out["raw_us10y_real_yield"] = df["us_real_yield_close"]
        if "dxy_close" in df.columns:
            out["raw_dxy"] = df["dxy_close"]
        if "usdcnh_close" in df.columns:
            out["raw_usdcnh"] = df["usdcnh_close"]
        if "gold_close" in df.columns:
            out["raw_gold_ret_20d"] = self._return_feature(df["gold_close"], 20)
        if "btc_close" in df.columns:
            out["raw_btc_ret_20d"] = self._return_feature(df["btc_close"], 20)
        if "wti_close" in df.columns:
            out["raw_wti_ret_20d"] = self._return_feature(df["wti_close"], 20)
        if "vix_close" in df.columns:
            out["raw_vix"] = df["vix_close"]
        if {"hyg_close", "lqd_close"}.issubset(df.columns):
            out["raw_hyg_lqd_ratio"] = df["hyg_close"] / df["lqd_close"].replace(0, np.nan)

        passthrough_cols = [
            "raw_us2y_yield",
            "raw_us10y_yield",
            "raw_dr007",
            "raw_shibor_3m",
            "raw_cn10y_yield",
            "raw_credit_impulse",
            "raw_usdcnh",
            "raw_turnover_a_share",
            "raw_turnover_hk",
            "raw_turnover_us_etf",
            "raw_pbo_c_easing_flag",
        ]
        for col in passthrough_cols:
            if col in df.columns:
                out[col] = df[col]

    def _fill_placeholder_fields(self, out: pd.DataFrame) -> None:
        """
        第一版先把字段补齐，方便 `scoring.py` 直接运行。
        后续可以继续接入：
        - 行业主线、广度、集中度
        """
        for col in MODEL_RAW_COLUMNS:
            if col not in out.columns:
                out[col] = np.nan

        out["raw_fed_cut_prob"] = out["raw_fed_cut_prob"].fillna(np.nan)
        out["raw_pbo_c_easing_flag"] = out["raw_pbo_c_easing_flag"].fillna(0.0)


def build_model_input(
    start_date: str,
    end_date: str,
    *,
    use_yfinance_fallback: bool = True,
) -> pd.DataFrame:
    loader = SinaDataLoader(
        start_date=start_date,
        end_date=end_date,
        use_yfinance_fallback=use_yfinance_fallback,
    )
    return loader.build_model_input()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sample = build_model_input("2024-01-01", "2025-12-31")
    print(sample.tail(3).T)
