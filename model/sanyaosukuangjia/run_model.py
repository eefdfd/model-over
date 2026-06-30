from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_loader import build_model_input
from fetch_ths_hot_top100 import fetch_ths_hot_stock_list
from scoring import score_market_regime
from export_ths_trend_name_overlap import run_overlap as run_ths_trend_overlap
from trend_up_screen_loose import run_trend_up_screen_loose


SCRIPT_DIR = Path(__file__).resolve().parent
# 热榜/趋势「名称重合」表除写入本次 run 的 output_dir 外，另复制一份到该日期目录（便于固定路径引用）
MIRROR_NAME_OVERLAP_DIR = SCRIPT_DIR / "outputs" / "2026-04-27"
LOGGER = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行跨市场联动模型")
    parser.add_argument(
        "--start-date",
        default="2024-01-01",
        help="开始日期，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        default=pd.Timestamp.today().strftime("%Y-%m-%d"),
        help="结束日期，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--score-method",
        default="rank",
        choices=["rank", "zscore"],
        help="评分标准化方法",
    )
    parser.add_argument(
        "--score-window",
        type=int,
        default=252,
        help="滚动评分窗口",
    )
    parser.add_argument(
        "--short-window",
        type=int,
        default=20,
        help="短周期特征窗口",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="输出根目录，默认在脚本目录下的 outputs",
    )
    parser.add_argument(
        "--no-date-subdir",
        dest="date_subdir",
        action="store_false",
        help="不创建以日期命名的子目录（默认会创建 YYYY-MM-DD 子目录再写入主输出）",
    )
    parser.set_defaults(date_subdir=True)
    parser.add_argument(
        "--skip-ths-hot-top100",
        action="store_true",
        help="跳过同花顺热榜前100导出（默认导出）",
    )
    parser.add_argument(
        "--ths-hot-type",
        default="hour",
        choices=["day", "hour"],
        help="同花顺热榜时间维度：hour=1小时榜（默认），day=24小时榜",
    )
    parser.add_argument(
        "--ths-hot-list-type",
        default="normal",
        help="同花顺热榜榜单：normal=大家都在看, skyrocket=快速飙升, tech/value/trend 等",
    )
    parser.add_argument(
        "--ths-hot-top",
        type=int,
        default=100,
        help="同花顺热榜保留前 N 行（默认 100）",
    )
    parser.add_argument(
        "--skip-trend-up-screen",
        action="store_true",
        help="跳过 quana 日K 宽松趋势筛（默认在跑完后写出 trend_up_loose_结果_按趋势强度.xlsx 到本 run 的日期子目录）",
    )
    parser.add_argument(
        "--trend-match-default-export-universe",
        action="store_true",
        help="宽松趋势筛仅处理 quana/_universe_mcap_gt200yi.csv 中的代码（与 export_all_a_daily_k_quana 默认>200亿导出一致，避免 quana 内未刷新小票旧 csv 混入）",
    )
    parser.add_argument(
        "--trend-universe-csv",
        type=Path,
        default=None,
        help="宽松趋势筛仅处理该 csv 的 code 列与 quana 的交集；若指定则优先于 --trend-match-default-export-universe",
    )
    parser.add_argument(
        "--skip-ths-trend-overlap",
        action="store_true",
        help="跳过「热榜与 trend_up 按股票名称重合」表（默认在热榜与趋势均成功时写出 热榜与宽松趋势_名称重合.xlsx）",
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
        LOGGER.warning("Target file is busy, saved to %s instead", alt_path)
        return alt_path


def save_text_with_fallback(text: str, path: Path) -> Path:
    try:
        path.write_text(text, encoding="utf-8")
        return path
    except PermissionError:
        alt_path = fallback_output_path(path)
        alt_path.write_text(text, encoding="utf-8")
        LOGGER.warning("Target file is busy, saved to %s instead", alt_path)
        return alt_path


def save_excel_with_fallback(frames: dict[str, pd.DataFrame], path: Path) -> Path:
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet, frame in frames.items():
                frame.to_excel(writer, sheet_name=sheet, index=False)
        return path
    except PermissionError:
        alt_path = fallback_output_path(path)
        with pd.ExcelWriter(alt_path, engine="openpyxl") as writer:
            for sheet, frame in frames.items():
                frame.to_excel(writer, sheet_name=sheet, index=False)
        LOGGER.warning("Target Excel is busy, saved to %s instead", alt_path)
        return alt_path


def export_competition_liquidity_last_month(scored: pd.DataFrame) -> pd.DataFrame:
    subset = recent_one_month_scores(scored)
    base_columns = [
        "date",
        "score_competition",
        "score_liquidity",
        "score_total",
        "regime_label",
        "lead_market_label",
        "lead_style_label",
        "risk_note",
    ]
    columns = [col for col in base_columns if col in subset.columns]
    out = subset[columns].copy()
    if not out.empty:
        out["auto_interpretation"] = out.apply(interpret_scores, axis=1)
    return out


def latest_snapshot(df: pd.DataFrame) -> pd.Series:
    latest = df.dropna(subset=["score_total"], how="any")
    if latest.empty:
        raise ValueError("No valid scored rows found. 数据长度可能不足，或关键字段仍然缺失。")
    return latest.iloc[-1]


def recent_one_month_scores(df: pd.DataFrame) -> pd.DataFrame:
    valid = df.dropna(subset=["score_total"], how="any").copy()
    if valid.empty:
        return valid
    valid["date"] = pd.to_datetime(valid["date"])
    end_date = valid["date"].max()
    start_date = end_date - pd.DateOffset(months=1)
    return valid[valid["date"] >= start_date].copy()


def export_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "score_c_market_leadership",
        "score_c_style_leadership",
        "score_c_theme_clarity",
        "score_c_breadth",
        "score_competition",
        "score_l_global_liquidity",
        "score_l_china_liquidity",
        "score_l_cross_asset",
        "score_liquidity",
        "score_total",
        "regime_label",
        "lead_market_label",
        "lead_style_label",
        "risk_note",
    ]
    existing = [col for col in columns if col in df.columns]
    return df[existing].copy()


def latest_valid_value(df: pd.DataFrame, column: str) -> tuple[str, object]:
    if column not in df.columns:
        return ("无", "NA")
    subset = df[["date", column]].dropna()
    if subset.empty:
        return ("无", "NA")
    row = subset.iloc[-1]
    return (str(row["date"]), row[column])


def format_number(value: object, digits: int = 2) -> str:
    if pd.isna(value):
        return "NA"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def explain_weighted_components(row: pd.Series, components: list[tuple[str, float, str]]) -> tuple[str, str]:
    available = [(col, weight, label) for col, weight, label in components if pd.notna(row.get(col))]
    if not available:
        return ("无有效子项", "NA")

    weight_sum = sum(weight for _, weight, _ in available)
    parts = []
    total = 0.0
    for col, weight, label in available:
        normalized_weight = weight / weight_sum
        value = float(row[col])
        total += value * normalized_weight
        parts.append(f"{label}({value:.2f} × {normalized_weight:.2f})")
    return (" + ".join(parts), f"{total:.2f}")


def interpret_scores(row: pd.Series) -> str:
    competition = row.get("score_competition", float("nan"))
    liquidity = row.get("score_liquidity", float("nan"))
    total = row.get("score_total", float("nan"))
    style = row.get("lead_style_label", "未知")

    if pd.isna(competition) or pd.isna(liquidity) or pd.isna(total):
        return "当前有效数据不足，先以观察为主。"

    if competition >= 75 and liquidity >= 70:
        core = "A股结构强、流动性也偏强，市场接近风险扩张区间。"
    elif competition >= 75 and liquidity >= 55:
        core = "A股结构偏强，但资金环境仍偏中性，适合聚焦主线而不是全面铺开。"
    elif competition >= 60 and liquidity >= 60:
        core = "A股处于偏强震荡，结构和环境都不差，但还没有进入极强单边。"
    elif competition >= 60:
        core = "A股结构强于资金，适合盯住强势方向，仓位不宜过激。"
    elif liquidity >= 65:
        core = "流动性条件尚可，但A股内部结构一般，更像交易型行情。"
    else:
        core = "A股结构和资金都不够强，当前更适合防守或等待。"

    if style == "成长":
        style_text = "当前风格偏成长。"
    elif style == "红利/防御":
        style_text = "当前风格偏红利和防御。"
    else:
        style_text = "当前风格没有明显单边倾向。"

    if total >= 75:
        regime_text = "模型把当前状态定义为风险扩张。"
    elif total >= 60:
        regime_text = "模型把当前状态定义为偏强震荡。"
    elif total >= 45:
        regime_text = "模型把当前状态定义为中性。"
    elif total >= 30:
        regime_text = "模型把当前状态定义为偏弱。"
    else:
        regime_text = "模型把当前状态定义为风险收缩。"

    return f"{core}{style_text}{regime_text}"


def build_detailed_daily_report(row: pd.Series, full_df: pd.DataFrame) -> str:
    competition_formula, competition_calc = explain_weighted_components(
        row,
        [
            ("score_c_market_leadership", 0.25, "市场主导权"),
            ("score_c_style_leadership", 0.20, "风格主导权"),
            ("score_c_theme_clarity", 0.30, "主线清晰度"),
            ("score_c_breadth", 0.25, "广度/集中度"),
        ],
    )
    liquidity_formula, liquidity_calc = explain_weighted_components(
        row,
        [
            ("score_l_global_liquidity", 0.30, "全球流动性"),
            ("score_l_china_liquidity", 0.20, "中国流动性"),
            ("score_l_cross_asset", 0.20, "跨资产验证"),
        ],
    )

    total_formula = (
        f"竞争格局({format_number(row.get('score_competition'))} × 0.55) + "
        f"流动性({format_number(row.get('score_liquidity'))} × 0.45)"
    )

    raw_fields = [
        ("沪深300 20日涨幅", "raw_ret_20d_csi300", 4),
        ("创业板 20日涨幅", "raw_ret_20d_cyb", 4),
        ("创业板/上证50", "raw_ratio_cyb_sz50", 4),
        ("科创50/红利", "raw_ratio_kc50_hongli", 4),
        ("美债10Y", "raw_us10y_yield", 2),
        ("美元代理(UUP)", "raw_dxy", 2),
        ("人民币汇率代理", "raw_usdcnh", 4),
        ("Shibor 3M", "raw_shibor_3m", 4),
    ]

    raw_lines = []
    for label, col, digits in raw_fields:
        date, value = latest_valid_value(full_df, col)
        raw_lines.append(f"- {label}: {format_number(value, digits)} ({date})")

    return "\n".join(
        [
            "A股日度模型解读",
            f"日期: {row.get('date')}",
            f"竞争格局分数: {row.get('score_competition', float('nan')):.2f}",
            f"流动性分数: {row.get('score_liquidity', float('nan')):.2f}",
            f"总分: {row.get('score_total', float('nan')):.2f}",
            f"市场状态: {row.get('regime_label', '未知')}",
            f"主导市场: {row.get('lead_market_label', '未知')}",
            f"主导风格: {row.get('lead_style_label', '未知')}",
            f"风险提示: {row.get('risk_note', '暂无')}",
            "",
            "分数怎么来",
            f"竞争格局 = {competition_formula} = {competition_calc}",
            f"流动性 = {liquidity_formula} = {liquidity_calc}",
            f"总分 = {total_formula} = {format_number(row.get('score_total'))}",
            "",
            "当天/最新有效原始数据",
            *raw_lines,
            "",
            "自动解读",
            interpret_scores(row),
        ]
    )


def print_summary(row: pd.Series) -> None:
    summary_lines = [
        "",
        "===== 最新模型结果 =====",
        f"日期: {row.get('date')}",
        f"竞争格局分数: {row.get('score_competition', float('nan')):.2f}",
        f"流动性分数: {row.get('score_liquidity', float('nan')):.2f}",
        f"总分: {row.get('score_total', float('nan')):.2f}",
        f"市场状态: {row.get('regime_label', '未知')}",
        f"主导市场: {row.get('lead_market_label', '未知')}",
        f"主导风格: {row.get('lead_style_label', '未知')}",
        f"风险提示: {row.get('risk_note', '暂无')}",
        f"自动解读: {interpret_scores(row)}",
    ]
    print("\n".join(summary_lines))


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    LOGGER.info("Loading model input from %s to %s", args.start_date, args.end_date)
    model_input = build_model_input(
        start_date=args.start_date,
        end_date=args.end_date,
        use_yfinance_fallback=True,
    )

    LOGGER.info("Scoring market regime")
    scored_full = score_market_regime(
        model_input,
        score_window=args.score_window,
        short_window=args.short_window,
        score_method=args.score_method,
        keep_intermediate=True,
    )
    scored = export_columns(scored_full)

    summary = latest_snapshot(scored_full)
    as_of = pd.to_datetime(summary.get("date"), errors="coerce")
    if pd.isna(as_of):
        as_of = pd.Timestamp.today().normalize()
    else:
        as_of = as_of.normalize()

    base_output = ensure_output_dir(args.output_dir)
    if args.date_subdir:
        output_dir = (base_output / as_of.strftime("%Y-%m-%d")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = base_output
    LOGGER.info("Writing outputs to %s", output_dir)

    month_df = export_competition_liquidity_last_month(scored)
    month_file = output_dir / "competition_liquidity_last_month.csv"
    saved_month_file = save_dataframe_with_fallback(month_df, month_file)
    LOGGER.info("Saved last-month competition/liquidity scores to %s", saved_month_file)

    report_file = output_dir / "daily_market_report.txt"
    report_text = build_detailed_daily_report(summary, scored_full)
    saved_report_file = save_text_with_fallback(report_text, report_file)
    LOGGER.info("Saved daily report to %s", saved_report_file)

    if not args.skip_ths_hot_top100:
        try:
            hot_df = fetch_ths_hot_stock_list(
                time_type=str(args.ths_hot_type or "hour"),
                list_type=str(args.ths_hot_list_type or "normal"),
            )
            n = max(0, int(args.ths_hot_top))
            if n > 0 and len(hot_df) > n:
                hot_df = hot_df.head(n).copy()
            if "code" in hot_df.columns and "name" in hot_df.columns:
                hot_df = hot_df.rename(
                    columns={"code": "股票代码", "name": "股票名称"}
                )
            hot_filename = (
                "ths_hot_top100_1h.csv"
                if str(args.ths_hot_type or "hour") == "hour"
                else "ths_hot_top100_24h.csv"
            )
            hot_path = output_dir / hot_filename
            saved_hot_file = save_dataframe_with_fallback(hot_df, hot_path)
            LOGGER.info(
                "Saved THS hot rank to %s (type=%s, list_type=%s, rows=%s)",
                saved_hot_file,
                args.ths_hot_type,
                args.ths_hot_list_type,
                len(hot_df),
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to export THS hot top list")

    if not args.skip_trend_up_screen:
        try:
            trend_out = output_dir / "trend_up_loose_结果_按趋势强度.xlsx"
            LOGGER.info(
                "Running trend_up_screen_loose (quana -> %s) …",
                trend_out.name,
            )
            trend_uni_path: Path | None = None
            if args.trend_universe_csv is not None:
                trend_uni_path = Path(args.trend_universe_csv)
                if not trend_uni_path.is_absolute():
                    trend_uni_path = SCRIPT_DIR / trend_uni_path
            elif bool(getattr(args, "trend_match_default_export_universe", False)):
                cand = SCRIPT_DIR / "quana" / "_universe_mcap_gt200yi.csv"
                if cand.is_file():
                    trend_uni_path = cand
                else:
                    LOGGER.warning(
                        "未找到 %s，宽松趋势将扫描 quana 下全部 csv（与仅导出>200亿时可能日期混杂）",
                        cand,
                    )

            p = run_trend_up_screen_loose(
                kline_dir=SCRIPT_DIR / "quana",
                out=trend_out,
                buffer_pct=0.0,
                limit=0,
                universe_csv=trend_uni_path,
            )
            LOGGER.info("Saved trend up screen: %s", p)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed trend_up_screen_loose")

    if not args.skip_ths_trend_overlap:
        try:
            hot_fn = (
                "ths_hot_top100_1h.csv"
                if str(args.ths_hot_type or "hour") == "hour"
                else "ths_hot_top100_24h.csv"
            )
            ths_p = output_dir / hot_fn
            tr_p = output_dir / "trend_up_loose_结果_按趋势强度.xlsx"
            ov = output_dir / "热榜与宽松趋势_名称重合.xlsx"
            if ths_p.is_file() and tr_p.is_file():
                run_ths_trend_overlap(
                    ths_path=ths_p,
                    trend_path=tr_p,
                    out_path=ov,
                    trend_sheet="全量",
                )
                LOGGER.info("Saved THS / trend name overlap: %s", ov)
                try:
                    MIRROR_NAME_OVERLAP_DIR.mkdir(parents=True, exist_ok=True)
                    mirror = MIRROR_NAME_OVERLAP_DIR / ov.name
                    if ov.resolve() != mirror.resolve():
                        shutil.copy2(ov, mirror)
                        LOGGER.info("Name overlap also copied to %s", mirror)
                except OSError as e:
                    LOGGER.warning("Could not mirror overlap to %s: %s", MIRROR_NAME_OVERLAP_DIR, e)
            else:
                LOGGER.warning(
                    "Skip THS/trend overlap: missing %s or %s",
                    ths_p.name,
                    tr_p.name,
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed THS/trend name overlap export")

    stale_input_file = output_dir / "model_input.csv"
    if stale_input_file.exists():
        try:
            stale_input_file.unlink()
            LOGGER.info("Removed stale input export %s", stale_input_file)
        except PermissionError:
            LOGGER.warning("Stale input export is busy, skipped removal: %s", stale_input_file)

    print_summary(summary)


if __name__ == "__main__":
    main()
