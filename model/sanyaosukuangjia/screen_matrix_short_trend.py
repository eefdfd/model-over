"""
从 top100 矩阵表 **按行顺序** 读取「股票名称」列，对每只股票拉日 K，筛「默认短线版」标的。

**默认输入**（可 ``--matrix`` 覆盖）：

  ``<项目>/outputs/four.xlsx/top100_matrix_0422_23_24_27_亿元.xlsx``（可用 ``--matrix`` 覆盖）

工作表为 ``成交额_亿元`` 或首表，须含列名 **股票名称**；重复名称会先去重、顺序保持表中首次出现。

**示例股票**：**宁德时代**（代码 ``300750``），名称会反查为深市代码后拉新浪日 K，用于自测与对照。

规则摘要：

- 收盘 > 近 5 根日 K 的「最低」之最小值（**不含**最新一日）；
- 且 收盘 > 10 日收盘均线（SMA10，含当日收盘参与计算）。

K 线：默认**只拉新浪** ``stock_zh_a_daily``（``sh/sz/bj`` 前缀，与 `filter_a_share_universe` 一致）；加 ``--try-em`` 时改为先东财、不足再新浪。前复权默认；``--no-qfq`` 不复权。``K线源``：缓存 / 东财 / 新浪 / 新浪(回退) / 无。

默认**多线程**拉取日 K（``--workers``，默认 12），可显著快于原逐股+sleep 串行。
若被接口限流，可 ``--workers 1 --sleep 0.15`` 改回慢速稳态。

**本地缓存**（见 ``--name-cache-hours`` / ``--kline-cache-hours``）：
- 全 A 券表：``outputs/trend_screener_cache/a_share_code_name_map.csv``，与 `filter_a_share_universe` 同目录、不同文件名（不筛 ST，供名称反查码）。
- 日 K：``outputs/trend_screener_cache/short_trend_kline/`` 下按代码+复权存 csv，在有效期内跳过东财拉取。

**输出**（默认与矩阵同目录）：``short_term_trend_筛选结果.xlsx``，含表「全量」「仅符合」。
"""
from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from filter_a_share_universe import _sina_market_prefix, cache_dir


def _ak():
    import akshare as ak  # noqa: PLC0415 — 可选依赖，仅拉 K/券表时加载

    return ak

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUTS = SCRIPT_DIR / "outputs"
NAME_CACHE_STEM = "a_share_code_name_map"
DEFAULT_MATRIX = (
    SCRIPT_DIR
    / "outputs"
    / "four.xlsx"
    / "top100_matrix_0422_23_24_27_亿元.xlsx"
)


def _ah_close_col(hist: pd.DataFrame) -> str:
    for c in ("收盘", "close", "Close"):
        if c in hist.columns:
            return c
    raise ValueError(f"无收盘列: {list(hist.columns)}")


def _ah_low_col(hist: pd.DataFrame) -> str:
    for c in ("最低", "low", "Low"):
        if c in hist.columns:
            return c
    raise ValueError(f"无最低列: {list(hist.columns)}")


def _ah_date_col(hist: pd.DataFrame) -> str:
    for c in ("日期", "date", "Date"):
        if c in hist.columns:
            return c
    return hist.columns[0]


def _name_cache_path() -> Path:
    return cache_dir(DEFAULT_OUTPUTS) / f"{NAME_CACHE_STEM}.csv"


def build_name_to_code(
    *,
    max_age_hours: float = 168.0,
    force_refresh: bool = False,
) -> dict[str, str]:
    """
    名称 -> 代码；全市场表可来自本地 ``a_share_code_name_map.csv``（不筛 ST，与全表接口一致）。
    """
    path = _name_cache_path()
    use_file = (
        not force_refresh
        and path.is_file()
        and (time.time() - path.stat().st_mtime) < max_age_hours * 3600.0
    )
    if use_file:
        u = pd.read_csv(path, dtype={"code": str, "name": str}, encoding="utf-8-sig")
        print("券表: 已读本地缓存", path, f"(时效<{max_age_hours}h)")
    else:
        u = _ak().stock_info_a_code_name()
        c0, c1 = u.columns[0], u.columns[1]
        u = u.rename(columns={c0: "code", c1: "name"}).copy()
        u["name"] = u["name"].astype(str).str.strip()
        u["code"] = u["code"].astype(str).str.extract(r"(\d{6})", expand=False)
        u = u.dropna(subset=["code", "name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        u.to_csv(path, index=False, encoding="utf-8-sig")
        print("券表: 已拉取并写入", path)
    u["name"] = u["name"].astype(str).str.strip()
    u["code"] = u["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    u = u.dropna(subset=["code", "name"])
    m: dict[str, str] = {}
    dups: list[str] = []
    for _, row in u.iterrows():
        n, c = row["name"], str(row["code"]).zfill(6)
        if n in m and m[n] != c:
            dups.append(n)
        m[n] = c
    if dups:
        print("提示: 以下名称在 A 股列表中重复/多次出现，将使用列表中最后一次映射:", len(set(dups)))
    return m


def _kline_cache_path(code: str, adjust: str, kline_dir: Path) -> Path:
    adj_key = (adjust or "none").replace(".", "_")
    return kline_dir / f"{str(code).zfill(6)}_{adj_key}.csv"


def _kline_cache_fresh(p: Path, max_age_hours: float) -> bool:
    if not p.is_file():
        return False
    return (time.time() - p.stat().st_mtime) < max_age_hours * 3600.0


def _sina_to_em_style(df: pd.DataFrame) -> pd.DataFrame:
    """新浪 stock_zh_a_daily 英文列名转为与东财一致（日期/开收高低）。"""
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    ren: dict[str, str] = {}
    if "date" in d.columns and "日期" not in d.columns:
        ren["date"] = "日期"
    for a, b in (("open", "开盘"), ("close", "收盘"), ("high", "最高"), ("low", "最低")):
        if a in d.columns and b not in d.columns:
            ren[a] = b
    d = d.rename(columns=ren)
    if "日期" not in d.columns:
        return pd.DataFrame()
    d["日期"] = pd.to_datetime(d["日期"], errors="coerce")
    d = d.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    return d


def _finalize_em_table(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    dc = _ah_date_col(d)
    d[dc] = pd.to_datetime(d[dc], errors="coerce")
    d = d.dropna(subset=[dc]).sort_values(dc).reset_index(drop=True)
    return d


def fetch_daily_hist(
    code: str,
    *,
    lookback_days: int = 120,
    adjust: str = "qfq",
    kline_cache_dir: Path | None = None,
    kline_cache_hours: float = 0.0,
    em_timeout: float = 6.0,
    skip_em: bool = True,
) -> tuple[pd.DataFrame, str]:
    """
    :return: (日 K 表, 数据来源：缓存/东财/新浪/新浪(回退)/无)
    :param em_timeout: 东财 ``stock_zh_a_hist`` 超时(秒)；仅 ``skip_em=False`` 时生效。
    :param skip_em: 默认 True，**不请求东财**、只拉新浪；为 False 时先东财再不足则新浪。
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    if kline_cache_dir is not None and kline_cache_hours > 0:
        kline_cache_dir = Path(kline_cache_dir)
        kline_cache_dir.mkdir(parents=True, exist_ok=True)
        cp = _kline_cache_path(code, adjust, kline_cache_dir)
        if _kline_cache_fresh(cp, kline_cache_hours):
            try:
                raw = pd.read_csv(cp, encoding="utf-8-sig")
                dc0 = _ah_date_col(raw)
                raw = raw.copy()
                raw[dc0] = pd.to_datetime(raw[dc0], errors="coerce")
                raw = raw.dropna(subset=[dc0]).sort_values(dc0).reset_index(drop=True)
                if len(raw) >= 11:
                    return raw, "缓存"
            except Exception:  # noqa: BLE001
                pass

    df_em = pd.DataFrame()
    if not skip_em:
        try:
            t = _ak().stock_zh_a_hist(
                symbol=str(code).zfill(6),
                period="daily",
                start_date=s,
                end_date=e,
                adjust=adjust,
                timeout=float(em_timeout),
            )
            if t is not None and not t.empty:
                df_em = _finalize_em_table(t)
        except Exception:  # noqa: BLE001
            pass

    df: pd.DataFrame
    source: str
    if len(df_em) >= 11:
        df = df_em
        source = "东财"
    else:
        df_sina = pd.DataFrame()
        try:
            t2 = _ak().stock_zh_a_daily(
                symbol=_sina_market_prefix(code),
                start_date=s,
                end_date=e,
                adjust=adjust or "",
            )
            if t2 is not None and not t2.empty:
                df_sina = _sina_to_em_style(t2)
        except Exception:  # noqa: BLE001
            pass
        if len(df_sina) >= 11:
            df = df_sina
            source = "新浪" if skip_em else "新浪(回退)"
        else:
            df = df_em if not df_em.empty else df_sina
            source = "无"

    if kline_cache_dir is not None and kline_cache_hours > 0 and len(df) >= 11 and source in (
        "东财",
        "新浪",
        "新浪(回退)",
    ):
        kline_cache_dir = Path(kline_cache_dir)
        kline_cache_dir.mkdir(parents=True, exist_ok=True)
        cp = _kline_cache_path(code, adjust, kline_cache_dir)
        try:
            df.to_csv(cp, index=False, encoding="utf-8-sig")
        except OSError:
            pass
    return df, source


def eval_short_trend(
    df: pd.DataFrame,
) -> dict[str, object] | None:
    """
    需至少 11 行：前 5 日低点窗口（不含当根需 5 行 + 当根 1 行 = 6），再加 SMA10 需前 9 根 + 当根（至少 10 行）；
    为稳妥要求 len >= 11（与 6 和 10 的较大者一致，并留余量）。
    """
    if len(df) < 11:
        return None
    ccol = _ah_close_col(df)
    lcol = _ah_low_col(df)
    close = pd.to_numeric(df[ccol], errors="coerce")
    low = pd.to_numeric(df[lcol], errors="coerce")
    if close.isna().all() or low.isna().all():
        return None
    # 前 5 个已完成交易日的最低（不含最新一根 K）
    min_low_5_wo_today = float(low.iloc[-6:-1].min())
    last_close = float(close.iloc[-1])
    ma10 = float(close.rolling(10, min_periods=10).mean().iloc[-1])
    dcol = _ah_date_col(df)
    asof = pd.Timestamp(df[dcol].iloc[-1])

    c1 = last_close > min_low_5_wo_today
    c2 = last_close > ma10
    ok = c1 and c2
    return {
        "数据截止日": asof.strftime("%Y-%m-%d"),
        "最新收盘": round(last_close, 4),
        "SMA10": round(ma10, 4),
        "前5日最低_不含当日K": round(min_low_5_wo_today, 4),
        "条件1_收在前5日低点之上": "是" if c1 else "否",
        "条件2_收在SMA10之上": "是" if c2 else "否",
        "符合默认短线": "是" if ok else "否",
    }


def read_matrix_stocks(xlsx: Path) -> list[str]:
    p = xlsx
    if not p.is_file() and p.suffix == "":
        p = p.with_suffix(".xlsx")
    if not p.is_file():
        raise FileNotFoundError(p)
    sheets: list | None = None
    try:
        xl = pd.ExcelFile(p, engine="openpyxl")
        sheets = xl.sheet_names
    except Exception:  # noqa: BLE001
        sheets = []
    sheet = 0
    for cand in ("成交额_亿元", "成交额_元"):
        if cand in (sheets or []):
            sheet = cand
            break
    df = pd.read_excel(p, sheet_name=sheet, engine="openpyxl")
    if "股票名称" not in df.columns:
        raise ValueError("矩阵表需含列 股票名称，当前为: " + str(list(df.columns)))
    names = (
        df["股票名称"]
        .astype(str)
        .str.strip()
        .replace({"nan": ""})
    )
    names = [n for n in names if n and n != "nan"]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


@dataclass(frozen=True)
class ScreenParams:
    adjust: str
    kline_cache_dir: Path | None
    kline_cache_hours: float
    skip_em: bool = True
    em_timeout: float = 6.0


def _screen_one_stock(
    name: str,
    *,
    name2code: dict[str, str],
    params: ScreenParams,
) -> dict[str, object]:
    rec: dict[str, object] = {"股票名称": name, "code": ""}
    try:
        code = name2code.get(name)
        if not code:
            rec["code"] = ""
            rec["原因"] = "未在A股名称表中匹配到代码"
            rec["符合默认短线"] = "否"
            return rec
        rec["code"] = code
        try:
            hist, ksrc = fetch_daily_hist(
                code,
                adjust=params.adjust,
                kline_cache_dir=params.kline_cache_dir,
                kline_cache_hours=params.kline_cache_hours,
                skip_em=params.skip_em,
                em_timeout=params.em_timeout,
            )
        except Exception as e:  # noqa: BLE001
            rec["原因"] = f"K线拉取失败: {e!r}"
            rec["符合默认短线"] = "否"
            return rec
        rec["K线源"] = ksrc
        ev = eval_short_trend(hist)
        if ev is None:
            rec["原因"] = "K线行数不足或列无效"
            rec["符合默认短线"] = "否"
        else:
            rec.update(ev)
            rec["原因"] = ""
        return rec
    except Exception as e:  # noqa: BLE001
        rec["原因"] = f"处理异常: {e!r}"
        rec["符合默认短线"] = "否"
        return rec


def main() -> None:
    ap = argparse.ArgumentParser(
        description="从矩阵表「股票名称」列拉日 K 并按默认短线规则筛选。",
        epilog=(
            "默认矩阵: outputs/four.xlsx/ 下导出的 top100_matrix_*.xlsx（含列「股票名称」）。"
            "示例（矩阵中仅一行「宁德时代」亦可）: python screen_matrix_short_trend.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="含列「股票名称」的 xlsx，默认: outputs/four.xlsx/top100_matrix_0422_23_24_27_亿元.xlsx",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 xlsx，默认在矩阵同目录 short_term_trend_筛选结果.xlsx",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=12,
        help="并发线程数拉取日 K，默认 12；被限流时改为 4～8 或 1",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="仅单线程时(--workers 1)在每只股处理后额外休眠(秒)，多线程时忽略",
    )
    ap.add_argument(
        "--no-qfq",
        action="store_true",
        help="不复权日 K（默认定前复权）",
    )
    ap.add_argument(
        "--name-cache-hours",
        type=float,
        default=168.0,
        help="全 A 券表本地文件缓存有效时长(小时)，超期才重新拉接口；默认 168=7 天",
    )
    ap.add_argument(
        "--refresh-name-cache",
        action="store_true",
        help="忽略券表本地缓存，强制从网络更新 a_share_code_name_map.csv",
    )
    ap.add_argument(
        "--kline-cache-hours",
        type=float,
        default=6.0,
        help="日 K 分文件缓存有效时长(小时)，期内直接读盘；0=不缓存日 K；默认 6",
    )
    ap.add_argument(
        "--try-em",
        action="store_true",
        help="日K先东财、不足再新浪；默认不连东财，只拉新浪",
    )
    ap.add_argument(
        "--em-timeout",
        type=float,
        default=6.0,
        help="与 --try-em 同用：东财请求超时(秒)",
    )
    args = ap.parse_args()
    matrix_path: Path = args.matrix
    if not matrix_path.is_absolute():
        matrix_path = (SCRIPT_DIR / matrix_path).resolve()
    out_path = args.out
    if out_path is None:
        base = matrix_path
        if not base.suffix:
            base = base.with_suffix(".xlsx")
        out_path = base.parent / "short_term_trend_筛选结果.xlsx"
    else:
        out_path = Path(out_path)
        if not out_path.is_absolute():
            out_path = (SCRIPT_DIR / out_path).resolve()

    names = read_matrix_stocks(matrix_path)
    adjust = "" if args.no_qfq else "qfq"
    w = max(1, int(args.workers or 1))
    ksrc_note = "先东财再新浪" if args.try_em else "仅新浪"
    print(
        "矩阵:",
        matrix_path,
        "共",
        len(names),
        "只（去重）, K线:",
        adjust or "不复权",
        f", 并发: {w}, 日K: {ksrc_note}",
    )
    t0 = time.perf_counter()
    name2code = build_name_to_code(
        max_age_hours=float(args.name_cache_hours),
        force_refresh=bool(args.refresh_name_cache),
    )
    kline_h = max(0.0, float(args.kline_cache_hours))
    kline_dir: Path | None = cache_dir(DEFAULT_OUTPUTS) / "short_trend_kline"
    if kline_h <= 0:
        kline_dir = None
        print("日 K: 不启用本地分股缓存( --kline-cache-hours 0 )")
    else:
        kline_dir.mkdir(parents=True, exist_ok=True)
        print("日 K: 分股缓存在", kline_dir, f" 时效<{kline_h}h")

    sp = ScreenParams(
        adjust=adjust,
        kline_cache_dir=kline_dir,
        kline_cache_hours=kline_h,
        skip_em=not bool(args.try_em),
        em_timeout=float(args.em_timeout),
    )
    rows: list[dict[str, object]] = []

    ntot = len(names)
    if w == 1:

        def one(n: str) -> dict[str, object]:
            r = _screen_one_stock(n, name2code=name2code, params=sp)
            if args.sleep > 0:
                time.sleep(args.sleep)
            return r

        for i, name in enumerate(names):
            rows.append(one(name))
            if (i + 1) % 25 == 0 or (i + 1) == ntot:
                print("已处理", i + 1, "/", ntot)
    else:
        prog = [0]
        plock = threading.Lock()

        def one_parallel(n: str) -> dict[str, object]:
            r = _screen_one_stock(n, name2code=name2code, params=sp)
            with plock:
                prog[0] += 1
                k = prog[0]
                if k % 25 == 0 or k == ntot:
                    print("已处理", k, "/", ntot)
            return r

        with ThreadPoolExecutor(max_workers=w) as ex:
            # 结果行序与 names 一致；完成顺序随机，进度为「已跑完 N 只」
            rows = list(ex.map(one_parallel, names, chunksize=1))

    print(f"总耗时: {time.perf_counter() - t0:.1f}s")

    res = pd.DataFrame(rows)
    ok = res[res.get("符合默认短线") == "是"].copy()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        res.to_excel(w, sheet_name="全量", index=False)
        ok.to_excel(w, sheet_name="仅符合", index=False)
    print("已写:", out_path, "全量行:", len(res), "符合行:", len(ok))


if __name__ == "__main__":
    main()
