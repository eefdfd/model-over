# -*- coding: utf-8 -*-
"""
美股：市值 >= 指定门槛（默认 20B USD，约 200 亿美元）的标的 + 与 A 股项目一致的
「日 K 宽松趋势（trend_up_loose）」技术面筛选。

数据来源（不访问维基百科；默认全程国内友好）
----------------------------------------
- **默认**：AkShare ``stock_us_spot``（新浪财经美股全市场行情，含市值字段）筛池；
  AkShare ``stock_us_daily``（新浪）拉日 K，``adjust=""`` 与 ``data_loader`` 一致。
- **可选**：``--universe-source em`` 时用东财 ``stock_us_spot_em`` 筛市值（需能访问东财）。

新浪全表 ``stock_us_spot`` 首次拉取较慢，解析后的结果会写入本地快照（见
``--sina-spot-cache-hours``），在有效期内直接读快照、不再请求全表。

导出 xlsx 时与 ``trend_up_screen_loose`` 相同，省略 ``TREND_LOOSE_XLSX_OMIT_COLUMNS`` 中的明细列。
涨跌基本面请按筛出结果自行检索；表内「原因」列留空（仅拉取失败时可能写入异常信息）。

依赖：pandas、openpyxl、akshare。

示例::

  python us_trend_up_screen_loose.py
  python us_trend_up_screen_loose.py --min-cap-b 20 --period 2y
  python us_trend_up_screen_loose.py --universe-source em
  python us_trend_up_screen_loose.py --tickers AAPL,MSFT --assume-min-cap-met
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import akshare as ak  # type: ignore
except ImportError as e:  # pragma: no cover
    raise SystemExit("请先安装 akshare: pip install akshare") from e

from trend_up_screen_loose import eval_one, trend_loose_dataframe_for_xlsx

SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT_DIR = SCRIPT_DIR / "outputs" / date.today().strftime("%Y-%m-%d")
DEFAULT_OUT_XLSX = _DEFAULT_OUT_DIR / "us_trend_up_loose_20Bplus_按趋势强度.xlsx"

_SINA_SPOT_NORM = SCRIPT_DIR / "outputs" / "cache" / "us_sina_spot_normalized.csv"

_COL_STRENGTH = "\u8d8b\u52bf\u5f3a\u5ea6"  # 趋势强度
_COL_OK = "\u8d8b\u52bf\u6210\u7acb_\u5bbd\u677e"  # 趋势成立_宽松
_COL_DATE = "\u6570\u636e\u622a\u6b62\u65e5"  # 数据截止日
_COL_REASON = "\u539f\u56e0"  # 原因


def _norm_symbol(sym: str) -> str:
    s = str(sym).strip()
    if not s:
        return s
    return s.replace(".", "-")


def _read_tickers_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(_norm_symbol(line.split()[0]))
    return sorted(set(out))


def _em_code_to_ticker(code: str) -> str:
    s = str(code).strip()
    if "." in s:
        return s.split(".", 1)[1].strip()
    return s


def _cols_lower_map(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower(): c for c in df.columns}


def _normalize_sina_spot_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """新浪 stock_us_spot 原始列名因接口版本可能略有差异，统一为 ticker/name/market_cap_usd。"""
    lm = _cols_lower_map(raw)
    sym_c = lm.get("symbol")
    if sym_c is None:
        for c in raw.columns:
            if str(c).strip().lower() in ("symbol", "code", "ticker"):
                sym_c = c
                break
    if sym_c is None:
        raise ValueError(f"新浪 spot 无 symbol 列: {list(raw.columns)}")

    name_c = lm.get("cname") or lm.get("name") or lm.get("名称")
    mcap_c = lm.get("mktcap") or lm.get("mkt_cap") or lm.get("marketcap")
    if mcap_c is None:
        for c in raw.columns:
            cl = str(c).lower()
            if "mkt" in cl or "cap" in cl or "\u5e02\u503c" in str(c):
                mcap_c = c
                break
    if mcap_c is None:
        raise ValueError(
            "新浪 spot 未识别到市值列，请升级 akshare 或改用 --universe-source em / --assume-min-cap-met。\n"
            f"当前列: {list(raw.columns)}"
        )

    tickers = raw[sym_c].astype(str).str.strip().map(_norm_symbol)
    names = raw[name_c].astype(str) if name_c else pd.Series([""] * len(raw), index=raw.index)
    mc = pd.to_numeric(raw[mcap_c], errors="coerce")
    # 常见：美元整数；或「十亿美元」量级小数。用分位数粗判量级（仅当明显偏小时放大）
    finite = mc.replace(0, np.nan).dropna()
    if len(finite) > 50:
        med = float(finite.median())
        if med < 1e6:
            mc = mc * 1.0e9
        elif med < 1e8:
            mc = mc * 1.0e6

    out = pd.DataFrame({"ticker": tickers, "name": names.values, "market_cap_usd": mc})
    out = out[out["ticker"].astype(str).str.len() > 0]
    out = out.drop_duplicates(subset=["ticker"], keep="first")
    return out.reset_index(drop=True)


def fetch_us_spot_sina_normalized(
    *,
    snapshot_max_age_hours: float,
    force_refresh_snapshot: bool,
) -> pd.DataFrame:
    p = _SINA_SPOT_NORM
    if (
        not force_refresh_snapshot
        and p.is_file()
        and snapshot_max_age_hours > 0
        and (time.time() - p.stat().st_mtime) / 3600.0 < snapshot_max_age_hours
    ):
        print(f"[美股筛] 使用新浪全表快照（{p.name}），跳过全市场分页拉取。", flush=True)
        df = pd.read_csv(p, dtype={"ticker": str, "name": str}, encoding="utf-8-sig")
        df["market_cap_usd"] = pd.to_numeric(df["market_cap_usd"], errors="coerce")
        return df

    print(
        "[美股筛] 正在请求 ak.stock_us_spot()（新浪全美股列表，分页很多，"
        "首次或 --refresh-sina-spot 时可能静默数分钟到十几分钟，并非卡死。）",
        flush=True,
    )
    sys.stdout.flush()
    raw = ak.stock_us_spot()
    if raw is None or raw.empty:
        raise ValueError("stock_us_spot 返回空表")
    norm = _normalize_sina_spot_raw(raw)
    print(f"[美股筛] 新浪全表已拉取，共 {len(norm)} 条；已写入快照。", flush=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    norm.to_csv(p, index=False, encoding="utf-8-sig")
    return norm


def fetch_us_spot_em(*, max_retries: int = 5, base_sleep_sec: float = 3.0) -> pd.DataFrame:
    """东财全表分页；对端常因限流/网络直接断连，这里做有限次退避重试。"""
    print("[美股筛] 正在请求 stock_us_spot_em（东财）…", flush=True)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            df = ak.stock_us_spot_em()
            if "代码" not in df.columns or "总市值" not in df.columns:
                raise ValueError(f"stock_us_spot_em 列异常: {list(df.columns)}")
            out = pd.DataFrame(
                {
                    "ticker": df["代码"].map(_em_code_to_ticker),
                    "name": df["名称"].astype(str) if "名称" in df.columns else "",
                    "market_cap_usd": pd.to_numeric(df["总市值"], errors="coerce"),
                }
            )
            out = out[out["ticker"].astype(str).str.len() > 0]
            out = out.drop_duplicates(subset=["ticker"], keep="first")
            return out.reset_index(drop=True)
        except Exception as e:  # noqa: BLE001 — urllib3/requests 断连等
            last_err = e
            if attempt + 1 >= max_retries:
                break
            wait = base_sleep_sec * (attempt + 1)
            print(
                f"[美股筛] stock_us_spot_em 失败（{type(e).__name__}），"
                f"{wait:.0f}s 后重试 ({attempt + 2}/{max_retries})…",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError(
        "东财 stock_us_spot_em 在多次重试后仍失败（多为限流、网络或对端主动断连）。"
        "请去掉 --universe-source em 使用默认新浪池（--universe-source sina），"
        "或换网络/稍后再试。"
    ) from last_err


def build_universe(
    *,
    source: str,
    min_cap_usd: float,
    tickers_subset: list[str] | None,
    sina_spot_cache_hours: float,
    force_refresh_sina_spot: bool,
) -> pd.DataFrame:
    if source == "sina":
        spot = fetch_us_spot_sina_normalized(
            snapshot_max_age_hours=float(sina_spot_cache_hours),
            force_refresh_snapshot=bool(force_refresh_sina_spot),
        )
    elif source == "em":
        spot = fetch_us_spot_em()
    else:
        raise ValueError(f"未知 universe-source: {source}")

    if tickers_subset:
        want = {_norm_symbol(x) for x in tickers_subset}
        spot = spot[spot["ticker"].isin(want)].copy()
    spot = spot[spot["market_cap_usd"].notna() & (spot["market_cap_usd"] >= float(min_cap_usd))]
    out = spot.sort_values("market_cap_usd", ascending=False).reset_index(drop=True)
    print(f"[美股筛] 市值过滤后剩余 {len(out)} 只。", flush=True)
    return out


def _period_to_lookback_days(period: str) -> int:
    p = str(period).strip().lower()
    if p in ("max", "all"):
        return 365 * 40
    m = re.match(r"^(\d+(?:\.\d+)?)\s*y$", p)
    if m:
        return int(float(m.group(1)) * 365) + 40
    m = re.match(r"^(\d+(?:\.\d+)?)\s*mo$", p)
    if m:
        return int(float(m.group(1)) * 31) + 10
    m = re.match(r"^(\d+)\s*d$", p)
    if m:
        return max(120, int(m.group(1)))
    return 600


def download_daily_sina(ticker: str, *, lookback_days: int) -> pd.DataFrame:
    raw = ak.stock_us_daily(symbol=ticker, adjust="")
    if raw is None or raw.empty:
        raise ValueError("empty history")
    d = raw.copy()
    if isinstance(d.index, pd.DatetimeIndex):
        d = d.reset_index()
    date_col = None
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]) or str(c).lower() in ("date", "\u65e5\u671f"):
            date_col = c
            break
    if date_col is None:
        date_col = d.columns[0]
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    close_c = "close" if "close" in d.columns else "收盘"
    low_c = "low" if "low" in d.columns else "最低"
    out = pd.DataFrame(
        {
            "日期": d[date_col],
            "收盘": pd.to_numeric(d[close_c], errors="coerce"),
            "最低": pd.to_numeric(d[low_c], errors="coerce"),
        }
    )
    out = out.dropna(subset=["日期", "收盘"])
    if out.empty:
        raise ValueError("empty after parse")
    end = out["日期"].max()
    start = end - pd.Timedelta(days=int(lookback_days))
    out = out[out["日期"] >= start].sort_values("日期").reset_index(drop=True)
    if len(out) < 65:
        raise ValueError(f"回溯窗口内行数不足(需>=65) 当前{len(out)}，可增大 --period 如 3y")
    return out


def run_us_screen(
    *,
    tickers_explicit: list[str] | None,
    min_cap_b_usd: float,
    buffer_pct: float,
    period: str,
    out: Path,
    refresh_universe: bool,
    universe_cache: Path | None,
    limit_universe: int = 0,
    assume_min_cap_met: bool = False,
    universe_source: str = "sina",
    sina_spot_cache_hours: float = 6.0,
    force_refresh_sina_spot: bool = False,
) -> Path:
    min_cap = float(min_cap_b_usd) * 1e9
    src = str(universe_source).strip().lower()
    if src not in ("sina", "em"):
        raise SystemExit("--universe-source 仅支持 sina 或 em")

    if universe_cache is None:
        cache = SCRIPT_DIR / "outputs" / "cache" / f"us_universe_ge_{int(min_cap_b_usd)}B_{src}.csv"
    else:
        cache = Path(universe_cache)

    print(
        f"[美股筛] 开始（市值>={min_cap_b_usd}B USD，universe={src}，筛后缓存文件={cache.name}）",
        flush=True,
    )

    force_sina_snap = bool(force_refresh_sina_spot) or (
        bool(refresh_universe) and src == "sina" and tickers_explicit is None
    )

    if assume_min_cap_met:
        if not tickers_explicit:
            raise SystemExit("--assume-min-cap-met 需配合 --tickers 或 --tickers-file")
        uni = pd.DataFrame(
            {
                "ticker": [_norm_symbol(x) for x in tickers_explicit],
                "market_cap_usd": np.nan,
                "name": "",
            }
        )
    elif tickers_explicit is not None:
        uni = build_universe(
            source=src,
            min_cap_usd=min_cap,
            tickers_subset=tickers_explicit,
            sina_spot_cache_hours=sina_spot_cache_hours,
            force_refresh_sina_spot=force_sina_snap,
        )
    elif not refresh_universe and cache.is_file() and not force_sina_snap:
        print(f"[美股筛] 从筛后缓存读取: {cache.resolve()}", flush=True)
        uni = pd.read_csv(cache, dtype={"ticker": str}, encoding="utf-8-sig")
        uni["ticker"] = uni["ticker"].map(_norm_symbol)
        if "market_cap_usd" not in uni.columns:
            raise ValueError(f"缓存缺少 market_cap_usd 列: {cache}")
        if "name" not in uni.columns:
            uni["name"] = ""
        uni["market_cap_usd"] = pd.to_numeric(uni["market_cap_usd"], errors="coerce")
        uni = uni[uni["market_cap_usd"].notna() & (uni["market_cap_usd"] >= float(min_cap))]
        print(f"[美股筛] 筛后缓存载入 {len(uni)} 只（已按市值下限再过滤）。", flush=True)
    else:
        uni = build_universe(
            source=src,
            min_cap_usd=min_cap,
            tickers_subset=None,
            sina_spot_cache_hours=sina_spot_cache_hours,
            force_refresh_sina_spot=force_sina_snap,
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        uni.to_csv(cache, index=False, encoding="utf-8-sig")

    if limit_universe and limit_universe > 0:
        uni = uni.head(int(limit_universe)).copy()

    if uni.empty:
        raise SystemExit(
            f"市值 >= {min_cap_b_usd}B USD 的标的为空（数据源={src}）。"
            "可试 --refresh-universe、--refresh-sina-spot（仅新浪快照）、"
            "--universe-source em、调整 --min-cap-b，或 --assume-min-cap-met。"
        )

    lookback_days = _period_to_lookback_days(period)
    print(
        f"[美股筛] 待拉日 K 共 {len(uni)} 只（数据源={src}），回溯约 {lookback_days} 自然日。",
        flush=True,
    )
    out = Path(out)
    if not out.is_absolute():
        out = (SCRIPT_DIR / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    n_uni = len(uni)
    for i, (_, r) in enumerate(uni.iterrows(), start=1):
        sym = str(r["ticker"])
        mc = float(r["market_cap_usd"]) if pd.notna(r.get("market_cap_usd")) else np.nan
        name = str(r.get("name", "") or "")
        rec: dict[str, object] = {
            "ticker": sym,
            "market_cap_usd": mc,
            "name": name,
        }
        try:
            t0 = time.perf_counter()
            print(f"[美股筛] 日K ({i}/{n_uni}) {sym} 请求新浪 …", flush=True)
            k = download_daily_sina(sym, lookback_days=lookback_days)
            rec.update(eval_one(k, buffer_pct=float(buffer_pct)))
            dt = time.perf_counter() - t0
            print(f"[美股筛] 日K ({i}/{n_uni}) {sym} 完成，用时 {dt:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            rec[_COL_OK] = "否"
            rec[_COL_STRENGTH] = None
            rec[_COL_DATE] = ""
            rec[_COL_REASON] = repr(e)
        rows.append(rec)

    res = pd.DataFrame(rows)
    if not res.empty and _COL_STRENGTH in res.columns:
        res[_COL_STRENGTH] = pd.to_numeric(res[_COL_STRENGTH], errors="coerce")
        res = res.sort_values(_COL_STRENGTH, ascending=False, na_position="last", kind="mergesort")
    ok = res[res.get(_COL_OK) == "是"].copy() if not res.empty and _COL_OK in res.columns else pd.DataFrame()
    print(f"[美股筛] 正在写入 Excel（{len(res)} 行）…", flush=True)
    res_x = trend_loose_dataframe_for_xlsx(res)
    ok_x = trend_loose_dataframe_for_xlsx(ok)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        res_x.to_excel(w, sheet_name="全量", index=False)
        ok_x.to_excel(w, sheet_name="仅符合", index=False)
    print("已写:", out.resolve(), "全量", len(res), "符合宽松趋势", len(ok), flush=True)
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="美股 >=N*B 市值 + 日K宽松趋势（默认新浪市值表+新浪日K）")
    ap.add_argument(
        "--min-cap-b",
        type=float,
        default=20.0,
        help="市值下限，单位：十亿美元（B USD），默认 20 即 >=20B USD",
    )
    ap.add_argument(
        "--universe-source",
        type=str,
        choices=("sina", "em"),
        default="sina",
        help="市值股票池来源：sina=stock_us_spot（默认）；em=stock_us_spot_em（东财）",
    )
    ap.add_argument(
        "--sina-spot-cache-hours",
        type=float,
        default=6.0,
        help="新浪全市场解析快照 us_sina_spot_normalized.csv 有效期(小时)；0=每次强制重拉全表",
    )
    ap.add_argument(
        "--refresh-sina-spot",
        action="store_true",
        help="忽略新浪快照，重新 stock_us_spot 并写快照（耗时长）",
    )
    ap.add_argument(
        "--tickers",
        type=str,
        default="",
        help="逗号分隔 ticker；指定后仅在该集合内按市值筛",
    )
    ap.add_argument(
        "--tickers-file",
        type=Path,
        default=None,
        help="每行一个 ticker；指定后仅在该集合内按市值筛",
    )
    ap.add_argument(
        "--refresh-universe",
        action="store_true",
        help="忽略筛后 universe 缓存 csv，重新算池子并写回（新浪源时会同时刷新快照）",
    )
    ap.add_argument(
        "--universe-cache",
        type=Path,
        default=None,
        help="筛后 universe 缓存路径（默认 outputs/cache/us_universe_ge_{N}B_{sina|em}.csv）",
    )
    ap.add_argument(
        "--period",
        type=str,
        default="2y",
        help="日 K 回溯：如 2y、1y、6mo（新浪拉全历史后按自然日截断）",
    )
    ap.add_argument(
        "--buffer-pct",
        type=float,
        default=0.0,
        help="与 trend_up_screen_loose 相同：收盘高于 L 的额外比例 %%",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_XLSX, help="输出 xlsx")
    ap.add_argument(
        "--limit-universe",
        type=int,
        default=0,
        help="只处理市值池中前 N 只（按市值降序），0=不截断",
    )
    ap.add_argument(
        "--assume-min-cap-met",
        action="store_true",
        help="与 --tickers 或 --tickers-file 合用：跳过市值表，仅拉日 K（市值列为 NaN）",
    )
    args = ap.parse_args()

    if args.assume_min_cap_met and not str(args.tickers).strip() and args.tickers_file is None:
        raise SystemExit("--assume-min-cap-met 必须与 --tickers 或 --tickers-file 同时使用")

    tickers_explicit: list[str] | None = None
    if str(args.tickers).strip():
        tickers_explicit = sorted(
            {_norm_symbol(x) for x in str(args.tickers).split(",") if str(x).strip()}
        )
    elif args.tickers_file is not None:
        tickers_explicit = _read_tickers_file(Path(args.tickers_file))

    run_us_screen(
        tickers_explicit=tickers_explicit,
        min_cap_b_usd=float(args.min_cap_b),
        buffer_pct=float(args.buffer_pct),
        period=str(args.period),
        out=Path(args.out),
        refresh_universe=bool(args.refresh_universe),
        universe_cache=Path(args.universe_cache) if args.universe_cache else None,
        limit_universe=int(args.limit_universe),
        assume_min_cap_met=bool(args.assume_min_cap_met),
        universe_source=str(args.universe_source),
        sina_spot_cache_hours=float(args.sina_spot_cache_hours),
        force_refresh_sina_spot=bool(args.refresh_sina_spot),
    )


if __name__ == "__main__":
    main()
