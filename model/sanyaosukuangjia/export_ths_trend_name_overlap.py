# -*- coding: utf-8 -*-
"""
对比 **同花顺热榜** ``ths_hot_top100_1h.csv`` 与 **宽松趋势结果**
``trend_up_loose_结果_按趋势强度.xlsx``，按 **股票名称** 取交集，写出单独工作簿。

默认路径（与 ``run_model`` 一致）::

  outputs/<日期>/ths_hot_top100_1h.csv
  outputs/<日期>/trend_up_loose_结果_按趋势强度.xlsx  （表「全量」）

例::

  python export_ths_trend_name_overlap.py
  python export_ths_trend_name_overlap.py --date 2026-04-27
  python export_ths_trend_name_overlap.py --ths path/to/1h.csv --trend path/to/trend.xlsx
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import date
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
# 与 run_model 中 MIRROR_NAME_OVERLAP_DIR 一致：名称重合表额外复制到该目录
MIRROR_NAME_OVERLAP_DIR = SCRIPT_DIR / "outputs" / "2026-04-27"


def _norm_name(s: object) -> str:
    t = str(s).strip()
    t = re.sub(r"\s+", "", t)
    return t


def run_overlap(
    *,
    ths_path: Path,
    trend_path: Path,
    out_path: Path,
    trend_sheet: str = "全量",
) -> Path:
    ths = pd.read_csv(ths_path, encoding="utf-8-sig")
    tr = pd.read_excel(trend_path, sheet_name=trend_sheet, engine="openpyxl")

    name_ths = "股票名称" if "股票名称" in ths.columns else "name"
    name_tr = "股票名称"
    if name_ths not in ths.columns or name_tr not in tr.columns:
        raise ValueError(
            f"需要列 股票名称；当前 ths: {list(ths.columns)} trend: {list(tr.columns)}"
        )

    ths = ths.copy()
    tr = tr.copy()
    ths["_name_key"] = ths[name_ths].map(_norm_name)
    tr["_name_key"] = tr[name_tr].map(_norm_name)
    ths = ths[ths["_name_key"] != ""].copy()
    tr = tr[tr["_name_key"] != ""].copy()

    merged = ths.merge(
        tr,
        on="_name_key",
        how="inner",
        suffixes=("_热榜", "_趋势表"),
    )
    merged = merged.drop(columns=["_name_key"], errors="ignore")

    if merged.empty:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        empty = pd.DataFrame(
            {
                "说明": [
                    "无名称交集：请确认两表已生成、名称一致（与 run_model 同目录、同日）。"
                ],
            }
        )
        with pd.ExcelWriter(out_path, engine="openpyxl") as w:
            empty.to_excel(w, sheet_name="重合明细", index=False)
            pd.DataFrame(
                {
                    "项": ["热榜行数", "趋势表行数", "名称交集行数"],
                    "值": [len(ths), len(tr), 0],
                }
            ).to_excel(w, sheet_name="摘要", index=False)
        return out_path

    n_overlap = len(merged)
    c_hot = f"{'股票代码' if '股票代码' in ths.columns else 'code'}_热榜"
    c_trc = f"{'股票代码' if '股票代码' in tr.columns else 'code'}_趋势表"
    n_hot2 = f"{name_ths}_热榜"
    n_tr2 = f"{name_tr}_趋势表"
    nm = (
        merged[n_hot2].combine_first(merged[n_tr2])
        if n_hot2 in merged.columns
        else merged[n_tr2]
    )
    head = pd.DataFrame({"股票名称": nm, "股票代码": merged[c_trc] if c_trc in merged.columns else None})
    drop_name_code = {n_hot2, n_tr2, c_hot, c_trc, "股票代码_热榜", "股票名称_热榜", "股票代码_趋势表", "股票名称_趋势表"}
    rest = merged.drop(columns=[c for c in drop_name_code if c in merged.columns], errors="ignore")
    merged = pd.concat([head, rest], axis=1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        merged.to_excel(w, sheet_name="重合明细", index=False)
        summary = pd.DataFrame(
            {
                "项": [
                    "热榜行数",
                    "趋势全量行数",
                    "名称交集行数(合并后)",
                ],
                "值": [len(ths), len(tr), n_overlap],
            }
        )
        summary.to_excel(w, sheet_name="摘要", index=False)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="热榜 ths 与 trend_up_loose 按股票名称取交集")
    ap.add_argument(
        "--date",
        type=str,
        default=date.today().strftime("%Y-%m-%d"),
        help="与 outputs/日期 子目录一致",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=SCRIPT_DIR / "outputs",
        help="输出根目录",
    )
    ap.add_argument("--ths", type=Path, default=None, help="热榜 csv，默认 日期下 ths_hot_top100_1h.csv")
    ap.add_argument("--trend", type=Path, default=None, help="趋势 xlsx，默认同目录 trend_up…xlsx")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="写出路径，默认同目录 热榜与宽松趋势_名称重合.xlsx",
    )
    ap.add_argument(
        "--trend-sheet",
        type=str,
        default="全量",
        help="趋势工作簿表名，默认 全量",
    )
    args = ap.parse_args()
    root = Path(args.output_root)
    if not root.is_absolute():
        root = (SCRIPT_DIR / root).resolve()
    day = args.date
    ddir = root / day
    ths = Path(args.ths) if args.ths else ddir / "ths_hot_top100_1h.csv"
    if not ths.is_file():
        raise FileNotFoundError(ths)
    trend = Path(args.trend) if args.trend else ddir / "trend_up_loose_结果_按趋势强度.xlsx"
    if not trend.is_file():
        raise FileNotFoundError(trend)
    out = Path(args.out) if args.out else ddir / "热榜与宽松趋势_名称重合.xlsx"
    if not out.is_absolute():
        out = (SCRIPT_DIR / out).resolve()
    p = run_overlap(
        ths_path=ths,
        trend_path=trend,
        out_path=out,
        trend_sheet=str(args.trend_sheet),
    )
    try:
        MIRROR_NAME_OVERLAP_DIR.mkdir(parents=True, exist_ok=True)
        m = MIRROR_NAME_OVERLAP_DIR / p.name
        if p.resolve() != m.resolve():
            shutil.copy2(p, m)
            print("已复制到:", m.resolve())
    except OSError as e:
        print("镜像目录写入失败:", e)
    nrows = len(pd.read_excel(p, sheet_name="重合明细", engine="openpyxl"))
    print("已写:", p.resolve(), "行数", nrows)


if __name__ == "__main__":
    main()
