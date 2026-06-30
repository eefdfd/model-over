# -*- coding: utf-8 -*-
"""
按此前约定的 **宽松档**「日线趋势偏多」规则，批量扫本地日 K 目录（默认 quana 下 ``{code}.csv``）。

规则摘要（前复权、用收盘为主；与聊天中「一、二」一致）：

**趋势成立（须同时满足）**
1) 收盘 > MA20 且 收盘 > MA60
2) MA20 今日 ≥ 5 个交易日前的 MA20（均线抬头）
3) 今日收盘 > 近 20 根「已走完」K 的区间下沿 L（默认用「最低」列在 ``[-21:-1]`` 共 20 根；可用 --buffer-pct 要求高于 L 一小段）
4) MACD: DIF > DEA

**否定（任一条则判为「非趋势成立」）**
- MA5 下穿 MA20（观察最近一根是否发生死叉）
- 或连续 2 日收盘在 MA20 之下
- 或今日收盘 < MA60
- 或今日收盘 ≤ L（含 buffer 后）

输出：xlsx（全量 + 仅符合），按 **趋势强度** 降序（强在前）。导出时省略均线/MACD/分项条件等
中间列（见 ``TREND_LOOSE_XLSX_OMIT_COLUMNS``），逻辑仍以 ``eval_one`` 为准。

若仅用 ``export_all_a_daily_k_quana.py``（默认总市值 >200 亿）更新日 K，``quana`` 里**未在本次导出名单中的**旧 csv 不会被刷新；
此时应用 ``--universe-csv quana/_universe_mcap_gt200yi.csv`` 做趋势筛，使「数据截止日」与本次导出一致。

**趋势强度**（可排序、非建议涨幅）：  
``(收盘-MA20)/MA20 + (收盘-MA60)/MA60 + (收盘-L)/L + (DIF-DEA)/收盘``；无效行置空、排在末尾。

例::

  python trend_up_screen_loose.py
  # 默认: outputs/YYYY-MM-DD/trend_up_loose_结果_按趋势强度.xlsx
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from screen_matrix_short_trend import _name_cache_path, build_name_to_code

SCRIPT_DIR = Path(__file__).resolve().parent
# 默认写入 outputs/当日日期/，便于按自然日归档
_DEFAULT_OUT_DIR = SCRIPT_DIR / "outputs" / date.today().strftime("%Y-%m-%d")
DEFAULT_TREND_XLSX = _DEFAULT_OUT_DIR / "trend_up_loose_结果_按趋势强度.xlsx"

# 写入 xlsx 时不导出的列（eval_one 仍计算，仅简化表头）
TREND_LOOSE_XLSX_OMIT_COLUMNS: tuple[str, ...] = (
    "MA5",
    "MA20",
    "MA60",
    "L_近20已走完最低",
    "DIF",
    "DEA",
    "条件1_价在MA20MA60上",
    "条件2_MA20抬头",
    "条件3_价高于L",
    "条件4_DIF大于DEA",
    "否定触发",
    "否定说明",
)


def trend_loose_dataframe_for_xlsx(df: pd.DataFrame) -> pd.DataFrame:
    """去掉过细列后的副本，供「全量」「仅符合」工作表导出。"""
    out = df.copy()
    if out.empty:
        return out
    drop = [c for c in TREND_LOOSE_XLSX_OMIT_COLUMNS if c in out.columns]
    if drop:
        out = out.drop(columns=drop)
    return out


def _load_code_to_name() -> dict[str, str]:
    """6 位代码 -> 名称，来自全 A 券表缓存（与短线脚本同文件）。"""
    build_name_to_code()
    p = _name_cache_path()
    u = pd.read_csv(p, dtype={"code": str, "name": str}, encoding="utf-8-sig")
    u["code"] = u["code"].astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6)
    u = u.dropna(subset=["code", "name"])
    m: dict[str, str] = {}
    for _, r in u.iterrows():
        m[str(r["code"])] = str(r["name"]).strip()
    return m


def _add_stock_name_and_code_columns(res: pd.DataFrame) -> pd.DataFrame:
    cn = _load_code_to_name()
    if "code" not in res.columns:
        return res
    c = res["code"].astype(str).str.zfill(6)
    res = res.copy()
    res["股票名称"] = c.map(lambda x: cn.get(x, ""))
    res = res.rename(columns={"code": "股票代码"})
    first = ["股票代码", "股票名称"]
    rest = [x for x in res.columns if x not in first]
    return res[first + rest]


def _codes_from_universe_csv(path: Path) -> set[str]:
    """读取 export 写出的名单 csv（须含 code 列），返回 6 位代码集合。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"找不到 universe 文件: {p.resolve()}")
    df = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
    if "code" not in df.columns:
        raise ValueError(f"universe csv 缺少 code 列: {p.resolve()} 列={list(df.columns)}")
    s = (
        df["code"]
        .astype(str)
        .str.extract(r"(\d{6})", expand=False)
        .dropna()
        .str.zfill(6)
    )
    return set(s.tolist())


def _close_col(df: pd.DataFrame) -> str:
    for c in ("收盘", "close", "Close"):
        if c in df.columns:
            return c
    raise ValueError(f"无收盘列: {list(df.columns)}")


def _low_col(df: pd.DataFrame) -> str:
    for c in ("最低", "low", "Low"):
        if c in df.columns:
            return c
    return _close_col(df)


def _date_col(df: pd.DataFrame) -> str:
    for c in ("日期", "date", "Date"):
        if c in df.columns:
            return c
    return str(df.columns[0])


def macd_dif_dea(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    c = pd.to_numeric(close, errors="coerce")
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif, dea


def eval_one(df: pd.DataFrame, *, buffer_pct: float) -> dict[str, object]:
    dc = _date_col(df)
    cc = _close_col(df)
    lc = _low_col(df)
    d = df.copy()
    d[dc] = pd.to_datetime(d[dc], errors="coerce")
    d = d.dropna(subset=[dc]).sort_values(dc).reset_index(drop=True)
    c = pd.to_numeric(d[cc], errors="coerce")
    low = pd.to_numeric(d[lc], errors="coerce")
    if len(d) < 65:
        return {
            "数据截止日": "",
            "趋势强度": None,
            "原因": f"行数不足(需>=65) 当前{len(d)}",
            "趋势成立_宽松": "否",
        }

    ma5 = c.rolling(5, min_periods=5).mean()
    ma20 = c.rolling(20, min_periods=20).mean()
    ma60 = c.rolling(60, min_periods=60).mean()
    dif, dea = macd_dif_dea(c)

    last = d.index[-1]
    prev = last - 1
    # L: 近 20 根不含当日 -> [-21:-1]
    if last < 21:
        return {
            "数据截止日": "",
            "趋势强度": None,
            "原因": "内部索引异常",
            "趋势成立_宽松": "否",
        }
    L = float(low.iloc[-21:-1].min())

    c0 = float(c.iloc[last])
    m5, m5p = float(ma5.iloc[last]), float(ma5.iloc[prev]) if last > 0 else np.nan
    m20_0, m20_p = float(ma20.iloc[last]), float(ma20.iloc[prev]) if last > 0 else np.nan
    m20_5ago = float(ma20.iloc[-6])  # 5 交易日前相对最后一根
    m60_0 = float(ma60.iloc[last])
    c_prev = float(c.iloc[prev])
    m20_at_prev = float(ma20.iloc[prev])
    dif0, dea0 = float(dif.iloc[last]), float(dea.iloc[last])

    buf = 1.0 + max(0.0, float(buffer_pct)) / 100.0
    L_th = L * buf

    r1 = c0 > m20_0 and c0 > m60_0
    r2 = m20_0 >= m20_5ago
    r3 = c0 > L_th
    r4 = dif0 > dea0

    # 否定
    cross_down = m5p >= m20_p and m5 < m20_0 if (np.isfinite(m5p) and np.isfinite(m20_p)) else False
    two_below_20 = c_prev < m20_at_prev and c0 < m20_0
    below60 = c0 < m60_0
    below_L = c0 <= L_th
    neg = cross_down or two_below_20 or below60 or below_L
    neg_reasons: list[str] = []
    if cross_down:
        neg_reasons.append("MA5死叉MA20")
    if two_below_20:
        neg_reasons.append("连续2日收在MA20下")
    if below60:
        neg_reasons.append("收盘<MA60")
    if below_L:
        neg_reasons.append("收盘未高于L(含buffer)")

    ok = r1 and r2 and r3 and r4 and not neg
    asof = pd.Timestamp(d[dc].iloc[last]).strftime("%Y-%m-%d")

    # 趋势强度（只用于排序，无量纲加总，越大表示相对空间越足）
    if m20_0 > 0 and m60_0 > 0 and L > 0 and c0 > 0:
        strength = float(
            (c0 - m20_0) / m20_0
            + (c0 - m60_0) / m60_0
            + (c0 - L) / L
            + (dif0 - dea0) / c0
        )
    else:
        strength = float("nan")

    return {
        "数据截止日": asof,
        "趋势强度": round(strength, 6) if np.isfinite(strength) else None,
        "收盘": round(c0, 4),
        "MA5": round(m5, 4),
        "MA20": round(m20_0, 4),
        "MA60": round(m60_0, 4),
        "L_近20已走完最低": round(L, 4),
        "DIF": round(dif0, 6),
        "DEA": round(dea0, 6),
        "条件1_价在MA20MA60上": "是" if r1 else "否",
        "条件2_MA20抬头": "是" if r2 else "否",
        "条件3_价高于L": "是" if r3 else "否",
        "条件4_DIF大于DEA": "是" if r4 else "否",
        "否定触发": "是" if neg else "否",
        "否定说明": "；".join(neg_reasons) if neg_reasons else "",
        "趋势成立_宽松": "是" if ok else "否",
        "原因": "",
    }


def run_trend_up_screen_loose(
    *,
    kline_dir: Path | str | None = None,
    out: Path | str | None = None,
    buffer_pct: float = 0.0,
    limit: int = 0,
    universe_csv: Path | str | None = None,
) -> Path:
    """
    扫 ``kline_dir`` 下 ``{6位}.csv``，写出宽松趋势结果 xlsx。

    :param universe_csv: 若给定，仅处理该 csv 中 ``code`` 列与目录下 csv 文件名的交集
        （用于与 ``export_all_a_daily_k_quana.py`` 默认筛市值导出名单一致，避免未导出的旧文件混入）。
    :return: 输出 xlsx 绝对路径
    """
    kdir = Path(kline_dir) if kline_dir is not None else SCRIPT_DIR / "quana"
    if not kdir.is_absolute():
        kdir = (SCRIPT_DIR / kdir).resolve()
    outp = Path(out) if out is not None else DEFAULT_TREND_XLSX
    if not outp.is_absolute():
        outp = (SCRIPT_DIR / outp).resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(kdir.glob("[0-9][0-9][0-9][0-9][0-9][0-9].csv"))
    if universe_csv is not None:
        ucsv = Path(universe_csv)
        if not ucsv.is_absolute():
            ucsv = (SCRIPT_DIR / ucsv).resolve()
        want = _codes_from_universe_csv(ucsv)
        before = len(files)
        files = [p for p in files if p.stem in want]
        print(
            f"[趋势筛] universe={ucsv.name} 名单 {len(want)} 只；quana 交集 {len(files)}/{before} 个 csv",
            flush=True,
        )
    if limit and limit > 0:
        files = files[: int(limit)]
    rows: list[dict[str, object]] = []
    for p in files:
        code = p.stem
        rec: dict[str, object] = {"code": code}
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
            rec.update(eval_one(df, buffer_pct=float(buffer_pct)))
        except Exception as e:  # noqa: BLE001
            rec["数据截止日"] = ""
            rec["趋势成立_宽松"] = "否"
            rec["趋势强度"] = None
            rec["原因"] = repr(e)
        rows.append(rec)

    res = pd.DataFrame(rows)
    st_col = "趋势强度"
    if st_col in res.columns:
        res[st_col] = pd.to_numeric(res[st_col], errors="coerce")
    res = res.sort_values(
        st_col, ascending=False, na_position="last", kind="mergesort"
    )
    res = _add_stock_name_and_code_columns(res)
    ok = res[res.get("趋势成立_宽松") == "是"].copy()
    res_x = trend_loose_dataframe_for_xlsx(res)
    ok_x = trend_loose_dataframe_for_xlsx(ok)
    with pd.ExcelWriter(outp, engine="openpyxl") as w:
        res_x.to_excel(w, sheet_name="全量", index=False)
        ok_x.to_excel(w, sheet_name="仅符合", index=False)
    print("已写:", outp.resolve(), "全量", len(res), "符合", len(ok))
    return outp


def main() -> None:
    ap = argparse.ArgumentParser(description="宽松档日线趋势偏多筛选（日K目录）")
    ap.add_argument("--kline-dir", type=Path, default=SCRIPT_DIR / "quana", help="日K csv 目录")
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_TREND_XLSX,
        help=f"默认: {DEFAULT_TREND_XLSX}",
    )
    ap.add_argument(
        "--buffer-pct",
        type=float,
        default=0.0,
        help="要求收盘高于 L 的额外比例(%%，如 0.5 表示 +0.5%%)",
    )
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件，0=全部")
    ap.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="仅筛该 csv 的 code 列与 quana 中 6 位 csv 的交集；"
        "与默认 export_all_a_daily_k_quana（>200亿）配套时可填：quana/_universe_mcap_gt200yi.csv",
    )
    args = ap.parse_args()
    run_trend_up_screen_loose(
        kline_dir=args.kline_dir,
        out=args.out,
        buffer_pct=float(args.buffer_pct),
        limit=int(args.limit),
        universe_csv=args.universe_csv,
    )


if __name__ == "__main__":
    main()
