from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests


def _ak():
    import akshare as ak  # noqa: PLC0415 — 可选依赖，仅拉行情/券表时加载

    return ak


LOGGER = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
_EM_A_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="筛选全A股票池")
    parser.add_argument("--market-cap-threshold-yi", type=float, default=200.0, help="总市值下限，单位亿元")
    parser.add_argument("--top-amount", type=int, default=100, help="按当日成交额筛选前N只股票")
    parser.add_argument("--stock-limit", type=int, default=0, help="仅处理前N只股票，0表示全部")
    parser.add_argument("--output-dir", default="outputs", help="输出目录")
    parser.add_argument(
        "--top-only",
        action="store_true",
        help="仅输出成交额前N股票池，不输出全市场市值与市值筛选表",
    )
    return parser.parse_args()


def ensure_output_dir(output_dir: str) -> Path:
    path = Path(output_dir)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def fallback_output_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{timestamp}{path.suffix}")


def save_dataframe_with_fallback(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        alt_path = fallback_output_path(path)
        df.to_csv(alt_path, index=False, encoding="utf-8-sig")
        LOGGER.warning("目标文件被占用，已改存到 %s", alt_path)
        return alt_path


def cache_dir(output_dir: Path) -> Path:
    path = output_dir / "trend_screener_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def turnover_top100_asof_cache_dir(output_dir: Path) -> Path:
    p = cache_dir(output_dir) / "turnover_top100_asof"
    p.mkdir(parents=True, exist_ok=True)
    return p


def trading_date_on_or_before(ts: pd.Timestamp) -> date:
    """<= ts 的最近一个交易日（自然日，含 ts 当日为交易日时取当日）。"""
    cal = _ak().tool_trade_date_hist_sina()
    cal["d"] = pd.to_datetime(cal["trade_date"], errors="coerce").dt.tz_localize(None)
    end_n = pd.Timestamp(ts).normalize()
    sub = cal[cal["d"].notna() & (cal["d"] <= end_n)].drop_duplicates("d").sort_values("d")
    if sub.empty:
        return end_n.date()
    return pd.Timestamp(sub["d"].iloc[-1]).date()


def _sina_market_prefix(code: str) -> str:
    """新浪/腾讯日K 用：sh600000、sz000001、bj920000 形式。"""
    c = str(code).zfill(6)
    if c.startswith("6"):
        return f"sh{c}"
    if c.startswith(("8", "4", "9")):
        return f"bj{c}"
    return f"sz{c}"


def _fetch_em_hist_one_day(code: str, trade_d: date, s_ymd: str) -> dict[str, object] | None:
    last_err: Exception | None = None
    for _ in range(3):
        try:
            df = _ak().stock_zh_a_hist(
                symbol=str(code).zfill(6),
                start_date=s_ymd,
                end_date=s_ymd,
                adjust="",
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.25)
            continue
        if df is None or df.empty:
            return None
        close_c = "收盘" if "收盘" in df.columns else None
        amt_c = "成交额" if "成交额" in df.columns else None
        if not close_c or not amt_c:
            return None
        h = df.iloc[-1]
        amt = pd.to_numeric(h[amt_c], errors="coerce")
        if pd.isna(amt) or float(amt) <= 0:
            return None
        cl = pd.to_numeric(h[close_c], errors="coerce")
        if pd.isna(cl):
            cl = 0.0
        return {
            "latest_close": float(cl),
            "turnover_amount": float(amt),
            "turnover_amount_yi": float(amt) / 1.0e8,
        }
    if last_err is not None:
        LOGGER.debug("东财日K失败 %s %s: %s", code, trade_d, last_err)
    return None


def _fetch_sina_hist_one_day(code: str, trade_d: date, s_ymd: str) -> dict[str, object] | None:
    """新浪 stock_zh_a_daily：amount 为成交额(元)，与东财同口径可排序。"""
    last_err: Exception | None = None
    for _ in range(3):
        try:
            df = _ak().stock_zh_a_daily(
                symbol=_sina_market_prefix(code),
                start_date=s_ymd,
                end_date=s_ymd,
                adjust="",
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.25)
            continue
        if df is None or df.empty or "amount" not in df.columns or "close" not in df.columns:
            return None
        h = df.iloc[0]
        dcol = "date" if "date" in df.columns else df.columns[0]
        row_d = pd.to_datetime(h[dcol], errors="coerce")
        if pd.isna(row_d) or row_d.date() != trade_d:
            return None
        amt = pd.to_numeric(h["amount"], errors="coerce")
        if pd.isna(amt) or float(amt) <= 0:
            return None
        cl = pd.to_numeric(h["close"], errors="coerce")
        if pd.isna(cl):
            cl = 0.0
        return {
            "latest_close": float(cl),
            "turnover_amount": float(amt),
            "turnover_amount_yi": float(amt) / 1.0e8,
        }
    if last_err is not None:
        LOGGER.debug("新浪日K失败 %s %s: %s", code, trade_d, last_err)
    return None


def _fetch_hist_one_day_row(
    code: str,
    trade_d: date,
    *,
    kline_source: str = "auto",
) -> dict[str, object] | None:
    """
    单日 A 股成交额(元) + 收盘。kline_source: em=仅东财, sina=仅新浪, auto=东财失败再试新浪。
    同花顺 A 股日 K 在 akshare 中无与东财/新浪等价的「全市场统一、逐股 成交额」单接口，故此处未接。
    """
    s = trade_d.strftime("%Y%m%d")
    if kline_source not in ("auto", "em", "sina"):
        kline_source = "auto"
    if kline_source == "em":
        return _fetch_em_hist_one_day(code, trade_d, s)
    if kline_source == "sina":
        return _fetch_sina_hist_one_day(code, trade_d, s)
    r = _fetch_em_hist_one_day(code, trade_d, s)
    if r is not None:
        return r
    return _fetch_sina_hist_one_day(code, trade_d, s)


def normalize_universe_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0]
    df["name"] = df["name"].astype(str).str.strip()
    df = df.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"]).reset_index(drop=True)
    df = df[~df["name"].str.contains("ST|退", case=False, na=False)].copy()
    return df.reset_index(drop=True)


def load_universe(output_dir: Path, stock_limit: int) -> pd.DataFrame:
    universe_path = cache_dir(output_dir) / "a_share_universe.csv"
    bundled_path = SCRIPT_DIR / "data" / "a_share_universe.csv"
    if universe_path.exists():
        df = pd.read_csv(universe_path, dtype={"code": str})
        df = normalize_universe_df(df)
    elif bundled_path.is_file():
        df = pd.read_csv(bundled_path, dtype={"code": str})
        df = normalize_universe_df(df)
        LOGGER.info("使用内置券表: %s", bundled_path)
    else:
        df = normalize_universe_df(_ak().stock_info_a_code_name())
        save_dataframe_with_fallback(df, universe_path)

    if stock_limit > 0:
        df = df.head(stock_limit).copy()
    return df.reset_index(drop=True)


def code_to_em_symbol(code: str) -> str:
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("8", "4", "9")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def code_to_tx_symbol(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("8", "4", "9")):
        return f"bj{code}"
    return f"sz{code}"


def safe_float(value: str) -> float | None:
    try:
        if value in {"", "-", "None"}:
            return None
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def fetch_tencent_quotes(symbols: list[str], retries: int = 3) -> str:
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    headers = {"User-Agent": "Mozilla/5.0"}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"腾讯批量行情获取失败: {last_error}") from last_error


def parse_tencent_quote_line(line: str) -> dict[str, object] | None:
    if "=\"" not in line:
        return None
    body = line.split("=\"", 1)[1].rstrip("\"")
    parts = body.split("~")
    if len(parts) < 73:
        return None

    code = parts[2].strip()
    name = parts[1].strip()
    latest_close = safe_float(parts[3])
    market_cap_yi = safe_float(parts[45])
    total_shares = safe_float(parts[72])
    turnover_amount = None
    turnover_amount_wan = safe_float(parts[37]) if len(parts) > 37 else None
    if turnover_amount_wan is not None:
        turnover_amount = turnover_amount_wan * 10000
    elif len(parts) > 35 and "/" in parts[35]:
        detail_parts = parts[35].split("/")
        if len(detail_parts) >= 3:
            turnover_amount = safe_float(detail_parts[2])

    if not code or not name or latest_close is None or market_cap_yi is None:
        return None

    market_cap = market_cap_yi * 1e8
    return {
        "code": code,
        "quote_name": name,
        "latest_close": latest_close,
        "total_shares": total_shares,
        "market_cap": market_cap,
        "market_cap_yi": market_cap_yi,
        "turnover_amount": turnover_amount,
        "turnover_amount_yi": (turnover_amount / 1e8) if turnover_amount is not None else None,
    }


def fetch_a_share_spot_eastmoney(*, page_size: int = 100) -> pd.DataFrame:
    """东财沪深京 A 股实时行情（分页拉全量），含市值与收盘。"""
    url = "https://82.push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0"}
    all_rows: list[dict[str, object]] = []
    pn = 1
    total = 0
    last_err: Exception | None = None
    while True:
        params = {
            "pn": str(pn),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f20",
            "fs": _EM_A_FS,
            "fields": "f12,f14,f20,f2",
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=40)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or {}
            total = int(data.get("total") or 0)
            diff = data.get("diff") or []
            if not diff:
                break
            for item in diff:
                code = str(item.get("f12", "")).strip().zfill(6)
                name = str(item.get("f14", "")).strip()
                mkt = safe_float(str(item.get("f20", "")) if item.get("f20") is not None else "")
                close = safe_float(str(item.get("f2", "")) if item.get("f2") is not None else "")
                if not code or mkt is None or mkt <= 0:
                    continue
                mkt_yi = mkt / 1e8
                all_rows.append(
                    {
                        "code": code,
                        "quote_name": name,
                        "latest_close": float(close) if close is not None else 0.0,
                        "total_shares": None,
                        "market_cap": mkt,
                        "market_cap_yi": mkt_yi,
                        "turnover_amount": None,
                        "turnover_amount_yi": None,
                    }
                )
            if len(all_rows) >= total or len(diff) < page_size:
                break
            pn += 1
            time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.5 + pn * 0.1)
            if pn > 3 and not all_rows:
                raise RuntimeError(f"东财 A 股全市场行情请求失败: {last_err}") from last_err
            pn += 1
    if not all_rows:
        raise ValueError("东财 A 股全市场行情未返回可用数据。")
    return pd.DataFrame(all_rows).drop_duplicates(subset=["code"]).reset_index(drop=True)


def evaluate_universe_with_eastmoney(universe: pd.DataFrame) -> pd.DataFrame:
    spot = fetch_a_share_spot_eastmoney()
    df = universe.merge(spot, on=["code"], how="inner")
    if "name_x" in df.columns:
        df["name"] = df["name_x"]
        drop_cols = [col for col in ["name_x", "name_y", "quote_name"] if col in df.columns]
        df = df.drop(columns=drop_cols)
    return df


def evaluate_universe_with_tencent(universe: pd.DataFrame, batch_size: int = 200) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    symbols = [code_to_tx_symbol(code) for code in universe["code"].astype(str).tolist()]

    for start in range(0, len(symbols), batch_size):
        batch_symbols = symbols[start : start + batch_size]
        text = fetch_tencent_quotes(batch_symbols)
        lines = [item for item in text.split(";") if item.strip()]
        for line in lines:
            parsed = parse_tencent_quote_line(line)
            if parsed is not None:
                rows.append(parsed)

    if not rows:
        raise ValueError("腾讯批量行情未返回可用股票数据。")

    df = pd.DataFrame(rows).drop_duplicates(subset=["code"]).reset_index(drop=True)
    df = universe.merge(df, on=["code"], how="inner")
    if "name_x" in df.columns:
        df["name"] = df["name_x"]
        drop_cols = [col for col in ["name_x", "name_y", "quote_name"] if col in df.columns]
        df = df.drop(columns=drop_cols)
    return df


def _compute_top_turnover_pool_eastmoney_clist(
    top_amount: int,
) -> pd.DataFrame:
    """
    东财「沪深京 A 股」行情列表，单次 HTTP：按**成交额 f6 降序**取前 N，与网站排序一致，无需全 A 逐股日 K。

    注意：为**拉取时点的榜单**（东财 API 无历史 as_of 参数）。若需严格「某一过去交易日」的日 K 成交额，请用
    _compute_top_turnover_pool_historical（--top100-historical-k）。
    """
    url = "https://82.push2.eastmoney.com/api/qt/clist/get"
    n = max(1, min(int(top_amount), 500))
    params = {
        "pn": "1",
        "pz": str(n),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f6",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f22,f23,f24,f25",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=40)
            r.raise_for_status()
            payload = r.json()
            data = (payload.get("data") or {}).get("diff")
            if not data:
                raise ValueError("东财 clist 返回空 diff")
            rows: list[dict[str, object]] = []
            for item in data[:n]:
                code = str(item.get("f12", "")).strip().zfill(6)
                name = str(item.get("f14", "")).strip()
                to_amt = safe_float(str(item.get("f6", "")) if item.get("f6") is not None else "")
                close = safe_float(str(item.get("f2", "")) if item.get("f2") is not None else "")
                mkt = safe_float(str(item.get("f20", "")) if item.get("f20") is not None else "")
                if not code or to_amt is None or to_amt <= 0:
                    continue
                mkt_yi = mkt / 1e8 if mkt is not None and mkt > 0 else None
                mcap = mkt * 1e8 if mkt is not None else None
                rows.append(
                    {
                        "code": code,
                        "name": name,
                        "quote_name": name,
                        "latest_close": float(close) if close is not None else 0.0,
                        "total_shares": None,
                        "market_cap": mcap,
                        "market_cap_yi": mkt_yi,
                        "turnover_amount": to_amt,
                        "turnover_amount_yi": to_amt / 1e8,
                    }
                )
            if not rows:
                raise ValueError("东财 clist 未解析到有效成交额行")
            LOGGER.info("东财 clist 一次请求取成交额前 %s 只（fid=f6）", len(rows))
            return pd.DataFrame(rows)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.6 + attempt * 0.4)
    raise RuntimeError(f"东财 A 股成交额榜请求失败: {last_err}")


def _compute_top_turnover_pool_tencent(
    output_dir: Path,
    stock_limit: int,
    top_amount: int,
) -> pd.DataFrame:
    """腾讯全行情快照的当日/最近（实时接口）成交额排序；与 as_of 无关。"""
    universe = load_universe(output_dir=output_dir, stock_limit=stock_limit)
    all_df = evaluate_universe_with_tencent(universe).sort_values("market_cap_yi", ascending=False).reset_index(drop=True)
    return (
        all_df.dropna(subset=["turnover_amount"])
        .sort_values(["turnover_amount", "market_cap_yi"], ascending=[False, False])
        .head(top_amount)
        .reset_index(drop=True)
    )


def _compute_top_turnover_pool_historical(
    output_dir: Path,
    stock_limit: int,
    top_amount: int,
    as_of: pd.Timestamp,
    refresh_cache: bool = False,
    sleep_seconds: float = 0.04,
    hist_kline_source: str = "auto",
) -> pd.DataFrame:
    """
    按「as_of 对应交易日」的**日K 成交额(元)** 做全A 排序取前 N（东财/新浪，auto 为东财失败再新浪）。
    结果按交易日缓存: trend_screener_cache/turnover_top100_asof/<date>.csv
    """
    trade_d = trading_date_on_or_before(as_of)
    cache_p = turnover_top100_asof_cache_dir(output_dir) / f"{trade_d.isoformat()}.csv"
    if not refresh_cache and cache_p.is_file():
        out = pd.read_csv(cache_p, dtype={"code": str}, encoding="utf-8-sig")
        if len(out) >= min(top_amount, 1) and "turnover_amount_yi" in out.columns:
            LOGGER.info("使用缓存的历史成交额前%s: %s", top_amount, cache_p)
            return out.head(top_amount).copy()

    if hist_kline_source == "em":
        src_desc = "东财(ak.stock_zh_a_hist)"
    elif hist_kline_source == "sina":
        src_desc = "新浪(ak.stock_zh_a_daily)"
    else:
        src_desc = "东财→新浪自动回退"
    universe = load_universe(output_dir=output_dir, stock_limit=stock_limit)
    rows: list[dict[str, object]] = []
    n_u = len(universe)
    LOGGER.info(
        "按日K(%s)统计 %s 成交额并取前 %s 只（全市场 %s 只，约需数分钟）",
        src_desc,
        trade_d,
        top_amount,
        n_u,
    )
    for i, u in enumerate(universe.itertuples(index=False), 1):
        code = str(u.code).zfill(6)
        name = str(getattr(u, "name", "") or "")
        rowd = _fetch_hist_one_day_row(code, trade_d, kline_source=hist_kline_source)
        if rowd is None:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "quote_name": name,
                "latest_close": rowd["latest_close"],
                "total_shares": None,
                "market_cap": None,
                "market_cap_yi": None,
                "turnover_amount": rowd["turnover_amount"],
                "turnover_amount_yi": rowd["turnover_amount_yi"],
            }
        )
        if sleep_seconds > 0 and i < n_u:
            time.sleep(sleep_seconds)
        if i % 400 == 0 or i == 1:
            LOGGER.info("日K拉取进度: %s / %s — %s", i, n_u, code)

    if not rows:
        raise ValueError(
            f"未从日K 得到 {trade_d} 的成交额（东财/新浪），请检查网络/交易日/或稍后重试。"
        )
    all_df = pd.DataFrame(rows)
    out = (
        all_df.dropna(subset=["turnover_amount"])
        .sort_values(["turnover_amount", "code"], ascending=[False, True])
        .head(top_amount)
        .reset_index(drop=True)
    )
    save_dataframe_with_fallback(out, cache_p)
    LOGGER.info("已写入按日 top100 缓存: %s", cache_p)
    return out


def compute_top_turnover_pool(
    output_dir: Path,
    stock_limit: int,
    top_amount: int,
    *,
    as_of: pd.Timestamp | None = None,
    use_spot_tencent: bool = False,
    use_historical_daily_k: bool = False,
    refresh_top100_cache: bool = False,
    hist_sleep_seconds: float = 0.04,
    hist_kline_source: str = "auto",
) -> pd.DataFrame:
    """
    - as_of 为 None，或 use_spot_tencent：腾讯**快照**全 A 池再排序（与 as_of 日无严格对应）。
    - 否则默认：**东财 clist 单次请求** `fid=成交额` 取前 N，与东财网站「按成交额」一致、**不扫全 A 日 K**；榜单为
      **拉取时刻**的东财数据（接口不带历史 as_of）。
    - use_historical_daily_k 为 True：按 as_of 对应交易日 **逐股**拉东财日 K（慢，可落盘到 turnover_top100_asof/ 缓存）。

    若你要「end-date=很久以前」的**当日**日 K 成交额，请加 ``--top100-historical-k``；否则以「当前东财榜单」
    为主，**不再**为默认路径扫 5000+ 次日 K。
    """
    if use_spot_tencent or as_of is None:
        return _compute_top_turnover_pool_tencent(output_dir, stock_limit, top_amount)
    if use_historical_daily_k:
        return _compute_top_turnover_pool_historical(
            output_dir,
            stock_limit,
            top_amount,
            as_of,
            refresh_cache=refresh_top100_cache,
            sleep_seconds=hist_sleep_seconds,
            hist_kline_source=hist_kline_source,
        )
    t_as = trading_date_on_or_before(as_of)
    t_now = trading_date_on_or_before(pd.Timestamp.now())
    if t_as < t_now:
        LOGGER.warning(
            "as-of 交易日 %s 早于「当前最近交易日」%s；本次仍用东财**实时**成交额榜（1 次请求），"
            "**不是** %s 的历史日 K 数值。需严格该日日 K 时请加 run_model: --top100-historical-k。",
            t_as,
            t_now,
            t_as,
        )
    return _compute_top_turnover_pool_eastmoney_clist(top_amount)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    output_dir = ensure_output_dir(args.output_dir)
    universe = load_universe(output_dir=output_dir, stock_limit=args.stock_limit)
    LOGGER.info("待筛选股票数: %s", len(universe))

    all_df = evaluate_universe_with_tencent(universe).sort_values("market_cap_yi", ascending=False).reset_index(drop=True)
    filtered_df = all_df[all_df["market_cap_yi"] > args.market_cap_threshold_yi].copy().reset_index(drop=True)
    top_amount_df = compute_top_turnover_pool(output_dir=output_dir, stock_limit=args.stock_limit, top_amount=args.top_amount)

    top_amount_path = save_dataframe_with_fallback(
        top_amount_df,
        output_dir / f"a_share_top_{int(args.top_amount)}_by_turnover_amount.csv",
    )
    LOGGER.info("成交额前%s股票池已保存: %s", args.top_amount, top_amount_path)

    if args.top_only:
        return

    all_path = save_dataframe_with_fallback(all_df, output_dir / "a_share_market_cap_all.csv")
    filtered_path = save_dataframe_with_fallback(
        filtered_df,
        output_dir / f"a_share_non_st_market_cap_gt_{int(args.market_cap_threshold_yi)}yi.csv",
    )

    LOGGER.info("全量市值表已保存: %s", all_path)
    LOGGER.info("筛选后股票池已保存: %s", filtered_path)
    LOGGER.info("筛选后数量: %s", len(filtered_df))


if __name__ == "__main__":
    main()
