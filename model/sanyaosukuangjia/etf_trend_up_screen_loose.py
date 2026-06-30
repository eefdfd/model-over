# -*- coding: utf-8 -*-
"""
全市场 ETF：拉取东财「ETF 板块」名单 + 日 K，按与 A 股一致的 **宽松趋势强度** 降序排列。

规则与 ``trend_up_screen_loose.eval_one`` 相同（MA20/MA60、MACD、L 区间等），
**趋势强度** = (收盘-MA20)/MA20 + (收盘-MA60)/MA60 + (收盘-L)/L + (DIF-DEA)/收盘。

日 K 默认走东财 ``push2his`` 接口（不依赖 akshare）；可选 ``--try-akshare`` 回退新浪。

输出 xlsx：工作表「全量」「仅符合」（趋势成立_宽松=是），按趋势强度从高到低。

示例::

  python etf_trend_up_screen_loose.py
  python etf_trend_up_screen_loose.py --limit 50 --workers 8
  python -u etf_trend_up_screen_loose.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from trend_up_screen_loose import eval_one, trend_loose_dataframe_for_xlsx

SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT_DIR = SCRIPT_DIR / "outputs" / date.today().strftime("%Y-%m-%d")
DEFAULT_OUT_XLSX = _DEFAULT_OUT_DIR / "etf_trend_up_loose_全市场_按趋势强度.xlsx"
_ETF_UNIVERSE_CACHE = SCRIPT_DIR / "outputs" / "cache" / "etf_universe_em.csv"
_ETF_KLINE_CACHE_DIR = SCRIPT_DIR / "outputs" / "cache" / "etf_kline"

_EM_ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"
_EM_LIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs="
    + _EM_ETF_FS
    + "&fields=f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f20,f21"
)
_EM_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    "&klt=101&fqt={fqt}&secid={secid}&beg={beg}&end={end}"
)

_COL_STRENGTH = "趋势强度"
_COL_OK = "趋势成立_宽松"
_COL_DATE = "数据截止日"
_COL_REASON = "原因"


def _em_secid(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("5", "6")):
        return f"1.{c}"
    return f"0.{c}"


def _http_json(url: str, *, timeout: float = 25.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_etf_universe_em(
    *,
    cache_hours: float = 6.0,
    force_refresh: bool = False,
) -> pd.DataFrame:
    p = _ETF_UNIVERSE_CACHE
    if (
        not force_refresh
        and cache_hours > 0
        and p.is_file()
        and (time.time() - p.stat().st_mtime) / 3600.0 < cache_hours
    ):
        df = pd.read_csv(p, dtype={"code": str, "name": str}, encoding="utf-8-sig")
        print(f"[ETF筛] 使用本地名单缓存 {p.name}，共 {len(df)} 只", flush=True)
        return df

    print("[ETF筛] 正在拉取东财 ETF 板块名单 …", flush=True)
    all_items: list[dict] = []
    pn = 1
    total = 0
    while True:
        url = _EM_LIST_URL.format(pn=pn)
        data = _http_json(url)
        d = data.get("data") or {}
        total = int(d.get("total") or 0)
        diff = d.get("diff") or []
        if not diff:
            break
        all_items.extend(diff)
        if len(all_items) >= total:
            break
        pn += 1
        time.sleep(0.05)

    rows = [
        {
            "code": str(x["f12"]).zfill(6),
            "name": str(x.get("f14") or "").strip(),
            "最新价": x.get("f2"),
            "涨跌幅": x.get("f3"),
        }
        for x in all_items
        if x.get("f12")
    ]
    df = pd.DataFrame(rows).drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"[ETF筛] 名单已写入 {p.name}，共 {len(df)} 只", flush=True)
    return df


def _parse_em_klines(klines: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "日期": parts[0],
                "开盘": float(parts[1]),
                "收盘": float(parts[2]),
                "最高": float(parts[3]),
                "最低": float(parts[4]),
                "成交量": float(parts[5]) if parts[5] else np.nan,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    return out.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)


def fetch_etf_daily_em(
    code: str,
    *,
    lookback_days: int = 200,
    adjust: str = "qfq",
    max_retries: int = 3,
) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=int(lookback_days))
    secid = _em_secid(code)
    fqt = "1" if adjust == "qfq" else ("2" if adjust == "hfq" else "0")
    url = _EM_KLINE_URL.format(
        fqt=fqt,
        secid=secid,
        beg=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            data = _http_json(url, timeout=30.0)
            kl = (data.get("data") or {}).get("klines") or []
            return _parse_em_klines(kl)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            time.sleep(0.3 * (attempt + 1))
    raise RuntimeError(f"东财日K失败 {code}: {last_err!r}")


def _kline_cache_path(code: str) -> Path:
    return _ETF_KLINE_CACHE_DIR / f"{str(code).zfill(6)}.csv"


def _load_or_fetch_kline(
    code: str,
    *,
    lookback_days: int,
    adjust: str,
    kline_cache_hours: float,
    try_akshare: bool,
) -> tuple[pd.DataFrame, str]:
    cp = _kline_cache_path(code)
    if (
        kline_cache_hours > 0
        and cp.is_file()
        and (time.time() - cp.stat().st_mtime) / 3600.0 < kline_cache_hours
    ):
        try:
            df = pd.read_csv(cp, encoding="utf-8-sig")
            if len(df) >= 65:
                return df, "缓存"
        except Exception:  # noqa: BLE001
            pass

    df = pd.DataFrame()
    source = "无"
    try:
        df = fetch_etf_daily_em(code, lookback_days=lookback_days, adjust=adjust)
        if len(df) >= 65:
            source = "东财"
    except Exception:  # noqa: BLE001
        df = pd.DataFrame()

    if len(df) < 65 and try_akshare:
        try:
            from screen_matrix_short_trend import fetch_daily_hist

            df2, src2 = fetch_daily_hist(
                code,
                lookback_days=lookback_days,
                adjust=adjust,
                skip_em=True,
            )
            if len(df2) >= 65:
                df, source = df2, src2
        except Exception:  # noqa: BLE001
            pass

    if kline_cache_hours > 0 and len(df) >= 65 and source in ("东财", "新浪", "新浪(回退)", "缓存"):
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cp, index=False, encoding="utf-8-sig")
        except OSError:
            pass
    return df, source


def _eval_etf_one(
    code: str,
    name: str,
    *,
    lookback_days: int,
    buffer_pct: float,
    adjust: str,
    kline_cache_hours: float,
    try_akshare: bool,
) -> dict[str, object]:
    rec: dict[str, object] = {"code": str(code).zfill(6), "name": name, "K线源": ""}
    try:
        df, src = _load_or_fetch_kline(
            code,
            lookback_days=lookback_days,
            adjust=adjust,
            kline_cache_hours=kline_cache_hours,
            try_akshare=try_akshare,
        )
        rec["K线源"] = src
        if df is None or df.empty or len(df) < 65:
            rec[_COL_DATE] = ""
            rec[_COL_OK] = "否"
            rec[_COL_STRENGTH] = None
            rec[_COL_REASON] = f"日K不足(需>=65) 当前{0 if df is None else len(df)}"
            return rec
        rec.update(eval_one(df, buffer_pct=float(buffer_pct)))
    except Exception as e:  # noqa: BLE001
        rec[_COL_DATE] = ""
        rec[_COL_OK] = "否"
        rec[_COL_STRENGTH] = None
        rec[_COL_REASON] = repr(e)
    return rec


def _format_result_df(res: pd.DataFrame) -> pd.DataFrame:
    if res.empty:
        return res
    out = res.copy()
    if "code" in out.columns:
        out["ETF代码"] = out["code"].astype(str).str.zfill(6)
        out = out.drop(columns=["code"])
    if "name" in out.columns:
        out = out.rename(columns={"name": "ETF名称"})
    first = ["ETF代码", "ETF名称"]
    rest = [c for c in out.columns if c not in first]
    return out[first + rest]


def run_etf_screen(
    *,
    out: Path,
    buffer_pct: float = 0.0,
    lookback_days: int = 200,
    adjust: str = "qfq",
    limit: int = 0,
    workers: int = 12,
    universe_cache_hours: float = 6.0,
    force_refresh_universe: bool = False,
    kline_cache_hours: float = 12.0,
    try_akshare: bool = False,
) -> Path:
    uni = fetch_etf_universe_em(
        cache_hours=universe_cache_hours,
        force_refresh=force_refresh_universe,
    )
    if limit and limit > 0:
        uni = uni.head(int(limit)).copy()
        print(f"[ETF筛] --limit {limit}，仅处理前 {len(uni)} 只", flush=True)

    out = Path(out)
    if not out.is_absolute():
        out = (SCRIPT_DIR / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    n = len(uni)
    print(
        f"[ETF筛] 待拉日 K 共 {n} 只，workers={workers}，回溯约 {lookback_days} 自然日",
        flush=True,
    )
    rows: list[dict[str, object]] = []
    done = 0
    t0 = time.perf_counter()

    def _task(row: pd.Series) -> dict[str, object]:
        return _eval_etf_one(
            str(row["code"]),
            str(row.get("name") or ""),
            lookback_days=lookback_days,
            buffer_pct=buffer_pct,
            adjust=adjust,
            kline_cache_hours=kline_cache_hours,
            try_akshare=try_akshare,
        )

    w = max(1, int(workers))
    if w == 1:
        for _, r in uni.iterrows():
            rows.append(_task(r))
            done += 1
            if done % 50 == 0 or done == n:
                print(f"[ETF筛] 进度 {done}/{n}", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=w) as ex:
            futs = {ex.submit(_task, r): i for i, (_, r) in enumerate(uni.iterrows())}
            for fut in as_completed(futs):
                rows.append(fut.result())
                done += 1
                if done % 100 == 0 or done == n:
                    elapsed = time.perf_counter() - t0
                    print(f"[ETF筛] 进度 {done}/{n}，已用 {elapsed:.0f}s", flush=True)

    res = pd.DataFrame(rows)
    if not res.empty and _COL_STRENGTH in res.columns:
        res[_COL_STRENGTH] = pd.to_numeric(res[_COL_STRENGTH], errors="coerce")
        res = res.sort_values(
            _COL_STRENGTH, ascending=False, na_position="last", kind="mergesort"
        )
    res = _format_result_df(res)
    ok = res[res.get(_COL_OK) == "是"].copy() if _COL_OK in res.columns else pd.DataFrame()
    res_x = trend_loose_dataframe_for_xlsx(res)
    ok_x = trend_loose_dataframe_for_xlsx(ok)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        res_x.to_excel(w, sheet_name="全量", index=False)
        ok_x.to_excel(w, sheet_name="仅符合", index=False)
    dt = time.perf_counter() - t0
    print(
        f"[ETF筛] 已写: {out.resolve()} | 全量 {len(res)} | 符合 {len(ok)} | 用时 {dt:.0f}s",
        flush=True,
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="全市场 ETF 宽松趋势强度排序")
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_XLSX,
        help=f"输出 xlsx，默认 {DEFAULT_OUT_XLSX}",
    )
    ap.add_argument("--buffer-pct", type=float, default=0.0, help="收盘高于 L 的额外比例(%%)")
    ap.add_argument("--lookback-days", type=int, default=200, help="日 K 回溯自然日")
    ap.add_argument("--adjust", choices=("qfq", "hfq", ""), default="qfq", help="复权，默认前复权")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只，0=全部")
    ap.add_argument("--workers", type=int, default=12, help="并发拉 K 线程数")
    ap.add_argument("--universe-cache-hours", type=float, default=6.0, help="ETF 名单缓存小时")
    ap.add_argument("--refresh-universe", action="store_true", help="强制重拉 ETF 名单")
    ap.add_argument("--kline-cache-hours", type=float, default=12.0, help="单只日 K 本地缓存小时")
    ap.add_argument("--try-akshare", action="store_true", help="东财不足时尝试 akshare 新浪")
    args = ap.parse_args()
    run_etf_screen(
        out=args.out,
        buffer_pct=float(args.buffer_pct),
        lookback_days=int(args.lookback_days),
        adjust=args.adjust if args.adjust else "",
        limit=int(args.limit),
        workers=int(args.workers),
        universe_cache_hours=float(args.universe_cache_hours),
        force_refresh_universe=bool(args.refresh_universe),
        kline_cache_hours=float(args.kline_cache_hours),
        try_akshare=bool(args.try_akshare),
    )


if __name__ == "__main__":
    main()
