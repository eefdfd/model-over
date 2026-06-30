from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_SCORE_WINDOW = 252
DEFAULT_SHORT_WINDOW = 20


def _require_columns(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def _safe_mean(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    _require_columns(df, columns)
    return df[columns].mean(axis=1)


def _top_n_mean(df: pd.DataFrame, columns: list[str], n: int = 3) -> pd.Series:
    _require_columns(df, columns)
    n = min(n, len(columns))
    return df[columns].apply(lambda row: row.nlargest(n).mean(), axis=1)


def delta(series: pd.Series, periods: int = DEFAULT_SHORT_WINDOW) -> pd.Series:
    return series - series.shift(periods)


def pct_change(series: pd.Series, periods: int = DEFAULT_SHORT_WINDOW) -> pd.Series:
    return series.pct_change(periods=periods)


def moving_average_ratio(series: pd.Series, window: int = DEFAULT_SHORT_WINDOW) -> pd.Series:
    ma = series.rolling(window=window, min_periods=max(3, window // 3)).mean()
    return series / ma.replace(0, np.nan)


def rolling_zscore(series: pd.Series, window: int = DEFAULT_SCORE_WINDOW) -> pd.Series:
    mean = series.rolling(window=window, min_periods=max(20, window // 4)).mean()
    std = series.rolling(window=window, min_periods=max(20, window // 4)).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def rolling_rank_pct(series: pd.Series, window: int = DEFAULT_SCORE_WINDOW) -> pd.Series:
    min_periods = max(20, window // 4)
    return series.rolling(window=window, min_periods=min_periods).apply(
        lambda values: pd.Series(values).rank(pct=True).iloc[-1],
        raw=False,
    )


def score_series(
    series: pd.Series,
    *,
    window: int = DEFAULT_SCORE_WINDOW,
    lower_is_better: bool = False,
) -> pd.Series:
    score = 100 * rolling_rank_pct(series, window=window)
    if lower_is_better:
        score = 100 - score
    return score.clip(0, 100)


def score_series_z(
    series: pd.Series,
    *,
    window: int = DEFAULT_SCORE_WINDOW,
    lower_is_better: bool = False,
) -> pd.Series:
    z = rolling_zscore(series, window=window)
    if lower_is_better:
        z = -z
    return (50 + 15 * z).clip(0, 100)


def choose_score_method(
    series: pd.Series,
    *,
    method: str = "rank",
    window: int = DEFAULT_SCORE_WINDOW,
    lower_is_better: bool = False,
) -> pd.Series:
    if method == "rank":
        return score_series(series, window=window, lower_is_better=lower_is_better)
    if method == "zscore":
        return score_series_z(series, window=window, lower_is_better=lower_is_better)
    raise ValueError(f"Unsupported score method: {method}")


def weighted_average_series(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    cols = list(weights.keys())
    _require_columns(df, cols)

    weighted_sum = pd.Series(0.0, index=df.index, dtype=float)
    weight_sum = pd.Series(0.0, index=df.index, dtype=float)

    for col, weight in weights.items():
        valid = df[col].notna()
        weighted_sum = weighted_sum + df[col].fillna(0.0) * weight
        weight_sum = weight_sum + valid.astype(float) * weight

    result = weighted_sum / weight_sum.replace(0.0, np.nan)
    return result


@dataclass
class MarketRegimeScorer:
    score_window: int = DEFAULT_SCORE_WINDOW
    short_window: int = DEFAULT_SHORT_WINDOW
    score_method: str = "rank"

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()

        market_ret_5d_cols = [
            "raw_ret_5d_csi300",
            "raw_ret_5d_cyb",
        ]
        market_ret_20d_cols = [
            "raw_ret_20d_csi300",
            "raw_ret_20d_cyb",
        ]
        trend_cols = [
            "raw_above_ma20_csi300",
            "raw_above_ma20_cyb",
            "raw_above_ma60_csi300",
            "raw_above_ma60_cyb",
        ]
        out["feat_leader_ret_5d_t"] = _top_n_mean(out, market_ret_5d_cols, n=3)
        out["feat_leader_ret_20d_t"] = _top_n_mean(out, market_ret_20d_cols, n=3)
        out["feat_trend_strength_t"] = _safe_mean(out, trend_cols)

        out["feat_style_momo_cyb_sz50_t"] = pct_change(out["raw_ratio_cyb_sz50"], self.short_window)
        out["feat_style_momo_kc50_hongli_t"] = pct_change(out["raw_ratio_kc50_hongli"], self.short_window)
        out["feat_style_strength_t"] = _safe_mean(
            out,
            [
                "feat_style_momo_cyb_sz50_t",
                "feat_style_momo_kc50_hongli_t",
            ],
        )

        industry_ret_5d_cols = [
            "raw_ret_5d_ai",
            "raw_ret_5d_semis",
            "raw_ret_5d_software",
            "raw_ret_5d_ev",
            "raw_ret_5d_energy",
            "raw_ret_5d_finance",
            "raw_ret_5d_consumer",
            "raw_ret_5d_defense",
        ]
        out["feat_industry_concentration_t"] = (
            _top_n_mean(out, industry_ret_5d_cols, n=3) - _safe_mean(out, industry_ret_5d_cols)
        )
        out["feat_leader_persistence_t"] = out["raw_leader_industry_days"]
        out["feat_leader_breakout_t"] = out["raw_new_high_count_leaders"]
        out["feat_theme_diffusion_t"] = _safe_mean(
            out,
            [
                "raw_theme_breadth_ai",
                "raw_theme_breadth_semis",
                "raw_theme_breadth_software",
            ],
        )

        breadth_cols = [
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
        ]
        concentration_cols = [
            "raw_top10_weight_contrib_us",
            "raw_top10_weight_contrib_cn",
            "raw_top10_weight_contrib_hk",
            "raw_top3_industry_turnover_share",
        ]
        out["feat_market_breadth_t"] = _safe_mean(out, breadth_cols)
        out["feat_over_concentration_t"] = _safe_mean(out, concentration_cols)

        out["feat_fed_easing_t"] = out["raw_fed_cut_prob"]
        out["feat_us2y_trend_t"] = -delta(out["raw_us2y_yield"], self.short_window)
        out["feat_us10y_trend_t"] = -delta(out["raw_us10y_yield"], self.short_window)
        out["feat_real_yield_trend_t"] = -delta(out["raw_us10y_real_yield"], self.short_window)
        out["feat_dxy_trend_t"] = -pct_change(out["raw_dxy"], self.short_window)

        out["feat_policy_easing_cn_t"] = out["raw_pbo_c_easing_flag"]
        out["feat_dr007_trend_t"] = -delta(out["raw_dr007"], self.short_window)
        out["feat_shibor_trend_t"] = -delta(out["raw_shibor_3m"], self.short_window)
        out["feat_cn10y_trend_t"] = -delta(out["raw_cn10y_yield"], self.short_window)
        out["feat_credit_impulse_t"] = out["raw_credit_impulse"]
        out["feat_cnh_stability_t"] = -rolling_zscore(
            pct_change(out["raw_usdcnh"], self.short_window),
            window=self.score_window,
        ).abs()

        out["feat_a_share_turnover_ratio_t"] = moving_average_ratio(
            out["raw_turnover_a_share"],
            self.short_window,
        )
        out["feat_hk_turnover_ratio_t"] = moving_average_ratio(
            out["raw_turnover_hk"],
            self.short_window,
        )
        out["feat_us_turnover_ratio_t"] = moving_average_ratio(
            out["raw_turnover_us_etf"],
            self.short_window,
        )
        out["feat_cross_border_flow_t"] = _safe_mean(
            out,
            ["raw_northbound_net_inflow", "raw_southbound_net_inflow"],
        )
        out["feat_us_etf_flow_t"] = out["raw_us_equity_etf_flow"]
        out["feat_margin_risk_appetite_t"] = out["raw_margin_balance_change"]

        out["feat_gold_context_t"] = out["raw_gold_ret_20d"] - pct_change(
            out["raw_dxy"],
            self.short_window,
        )
        out["feat_btc_risk_on_t"] = out["raw_btc_ret_20d"]
        out["feat_oil_growth_signal_t"] = out["raw_wti_ret_20d"]
        out["feat_credit_spread_proxy_t"] = pct_change(
            out["raw_hyg_lqd_ratio"],
            self.short_window,
        )
        out["feat_vol_suppression_t"] = -pct_change(out["raw_vix"], self.short_window)

        return out

    def _score(self, series: pd.Series, *, lower_is_better: bool = False) -> pd.Series:
        return choose_score_method(
            series,
            method=self.score_method,
            window=self.score_window,
            lower_is_better=lower_is_better,
        )

    def score_components(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self.build_features(df)

        out["score_c1_leader_ret_5d"] = self._score(out["feat_leader_ret_5d_t"])
        out["score_c1_leader_ret_20d"] = self._score(out["feat_leader_ret_20d_t"])
        out["score_c1_trend_strength"] = self._score(out["feat_trend_strength_t"])
        out["score_c_market_leadership"] = weighted_average_series(
            out,
            {
                "score_c1_leader_ret_5d": 0.4,
                "score_c1_leader_ret_20d": 0.4,
                "score_c1_trend_strength": 0.2,
            },
        )

        out["score_c_style_leadership"] = self._score(out["feat_style_strength_t"])

        out["score_c3_industry_concentration"] = self._score(out["feat_industry_concentration_t"])
        out["score_c3_leader_persistence"] = self._score(out["feat_leader_persistence_t"])
        out["score_c3_leader_breakout"] = self._score(out["feat_leader_breakout_t"])
        out["score_c3_theme_diffusion"] = self._score(out["feat_theme_diffusion_t"])
        out["score_c_theme_clarity"] = weighted_average_series(
            out,
            {
                "score_c3_industry_concentration": 0.30,
                "score_c3_leader_persistence": 0.25,
                "score_c3_leader_breakout": 0.25,
                "score_c3_theme_diffusion": 0.20,
            },
        )

        out["score_c4_market_breadth"] = self._score(out["feat_market_breadth_t"])
        out["score_c4_over_concentration"] = self._score(
            out["feat_over_concentration_t"],
            lower_is_better=True,
        )
        out["score_c_breadth"] = weighted_average_series(
            out,
            {
                "score_c4_market_breadth": 0.7,
                "score_c4_over_concentration": 0.3,
            },
        )

        out["score_competition"] = weighted_average_series(
            out,
            {
                "score_c_market_leadership": 0.25,
                "score_c_style_leadership": 0.20,
                "score_c_theme_clarity": 0.30,
                "score_c_breadth": 0.25,
            },
        )

        out["score_l1_fed_easing"] = self._score(out["feat_fed_easing_t"])
        out["score_l1_us2y_trend"] = self._score(out["feat_us2y_trend_t"])
        out["score_l1_us10y_trend"] = self._score(out["feat_us10y_trend_t"])
        out["score_l1_real_yield_trend"] = self._score(out["feat_real_yield_trend_t"])
        out["score_l1_dxy_trend"] = self._score(out["feat_dxy_trend_t"])
        out["score_l_global_liquidity"] = weighted_average_series(
            out,
            {
                "score_l1_fed_easing": 0.25,
                "score_l1_us10y_trend": 0.35,
                "score_l1_real_yield_trend": 0.15,
                "score_l1_dxy_trend": 0.25,
            },
        )

        out["score_l2_policy_easing_cn"] = self._score(out["feat_policy_easing_cn_t"])
        out["score_l2_dr007_trend"] = self._score(out["feat_dr007_trend_t"])
        out["score_l2_shibor_trend"] = self._score(out["feat_shibor_trend_t"])
        out["score_l2_cn10y_trend"] = self._score(out["feat_cn10y_trend_t"])
        out["score_l2_credit_impulse"] = self._score(out["feat_credit_impulse_t"])
        out["score_l2_cnh_stability"] = self._score(out["feat_cnh_stability_t"])
        out["score_l_china_liquidity"] = weighted_average_series(
            out,
            {
                "score_l2_policy_easing_cn": 0.20,
                "score_l2_dr007_trend": 0.25,
                "score_l2_shibor_trend": 0.20,
                "score_l2_credit_impulse": 0.20,
                "score_l2_cnh_stability": 0.15,
            },
        )

        out["score_l3_a_share_turnover"] = self._score(out["feat_a_share_turnover_ratio_t"])
        out["score_l3_hk_turnover"] = self._score(out["feat_hk_turnover_ratio_t"])
        out["score_l3_us_turnover"] = self._score(out["feat_us_turnover_ratio_t"])
        out["score_l3_cross_border_flow"] = self._score(out["feat_cross_border_flow_t"])
        out["score_l3_us_etf_flow"] = self._score(out["feat_us_etf_flow_t"])
        out["score_l3_margin_risk_appetite"] = self._score(out["feat_margin_risk_appetite_t"])
        out["score_l_trading_liquidity"] = weighted_average_series(
            out,
            {
                "score_l3_a_share_turnover": 0.20,
                "score_l3_hk_turnover": 0.15,
                "score_l3_us_turnover": 0.15,
                "score_l3_cross_border_flow": 0.20,
                "score_l3_us_etf_flow": 0.15,
                "score_l3_margin_risk_appetite": 0.15,
            },
        )

        out["score_l4_gold_context"] = self._score(out["feat_gold_context_t"])
        out["score_l4_btc_risk_on"] = self._score(out["feat_btc_risk_on_t"])
        out["score_l4_oil_growth_signal"] = self._score(out["feat_oil_growth_signal_t"])
        out["score_l4_credit_spread_proxy"] = self._score(out["feat_credit_spread_proxy_t"])
        out["score_l4_vol_suppression"] = self._score(out["feat_vol_suppression_t"])
        out["score_l_cross_asset"] = weighted_average_series(
            out,
            {
                "score_l4_gold_context": 0.15,
                "score_l4_btc_risk_on": 0.30,
                "score_l4_oil_growth_signal": 0.15,
                "score_l4_credit_spread_proxy": 0.20,
                "score_l4_vol_suppression": 0.20,
            },
        )

        out["score_liquidity"] = weighted_average_series(
            out,
            {
                "score_l_global_liquidity": 0.30,
                "score_l_china_liquidity": 0.20,
                "score_l_cross_asset": 0.20,
            },
        )

        out["score_total"] = weighted_average_series(
            out,
            {
                "score_competition": 0.55,
                "score_liquidity": 0.45,
            },
        )
        out["regime_label"] = out["score_total"].apply(self._regime_label)
        out["lead_market_label"] = out.apply(self._lead_market_label, axis=1)
        out["lead_style_label"] = out.apply(self._lead_style_label, axis=1)
        out["lead_market_label"] = out["lead_market_label"].replace("未知", np.nan).ffill().fillna("未知")
        out["lead_style_label"] = out["lead_style_label"].replace("未知", np.nan).ffill().fillna("未知")
        out["position_suggestion"] = out.apply(self._position_suggestion, axis=1)
        out["risk_note"] = out.apply(self._risk_note, axis=1)

        return out

    def final_output(self, df: pd.DataFrame) -> pd.DataFrame:
        scored = self.score_components(df)
        desired_columns = [
            "score_c_market_leadership",
            "score_c_style_leadership",
            "score_c_theme_clarity",
            "score_c_breadth",
            "score_competition",
            "score_l_global_liquidity",
            "score_l_china_liquidity",
            "score_l_trading_liquidity",
            "score_l_cross_asset",
            "score_liquidity",
            "score_total",
            "regime_label",
            "lead_market_label",
            "lead_style_label",
            "risk_note",
        ]
        existing_columns = [col for col in desired_columns if col in scored.columns]
        if "date" in scored.columns:
            return scored[["date", *existing_columns]]
        return scored[existing_columns]

    @staticmethod
    def _regime_label(score_total: float) -> str:
        if pd.isna(score_total):
            return "数据不足"
        if score_total >= 75:
            return "风险扩张"
        if score_total >= 60:
            return "偏强震荡"
        if score_total >= 45:
            return "中性"
        if score_total >= 30:
            return "偏弱"
        return "风险收缩"

    @staticmethod
    def _lead_market_label(row: pd.Series) -> str:
        # 仅比较 美股 vs A 股 的 20 日相对收益，取更强一侧作为「主导市场」（不含港股）
        us_cols = ["raw_ret_20d_spx", "raw_ret_20d_ndx"]
        cn_cols = ["raw_ret_20d_csi300", "raw_ret_20d_cyb"]

        def _mean(cols: list[str]) -> float | None:
            values = [row.get(c) for c in cols if pd.notna(row.get(c))]
            if not values:
                return None
            return float(np.mean(values))

        scores: dict[str, float] = {}
        s_us = _mean(us_cols)
        if s_us is not None:
            scores["美股"] = s_us
        s_cn = _mean(cn_cols)
        if s_cn is not None:
            scores["A股"] = s_cn

        if not scores:
            return "未知"
        return max(scores, key=scores.get)

    @staticmethod
    def _lead_style_label(row: pd.Series) -> str:
        style_cols = [
            "feat_style_momo_cyb_sz50_t",
            "feat_style_momo_kc50_hongli_t",
        ]
        values = [row.get(col) for col in style_cols if pd.notna(row.get(col))]
        if not values:
            return "未知"
        avg_value = float(np.mean(values))
        if avg_value > 0.03:
            return "成长"
        if avg_value < -0.03:
            return "红利/防御"
        return "均衡"

    @staticmethod
    def _position_suggestion(row: pd.Series) -> str:
        score_competition = row.get("score_competition", np.nan)
        score_liquidity = row.get("score_liquidity", np.nan)
        if pd.isna(score_competition) or pd.isna(score_liquidity):
            return "等待数据"
        if score_competition >= 70 and score_liquidity >= 70:
            return "高仓，聚焦主线"
        if score_competition >= 70 and score_liquidity < 55:
            return "中仓，聚焦龙头"
        if score_competition < 55 and score_liquidity >= 65:
            return "中低仓，偏交易型"
        return "低仓或防守"

    @staticmethod
    def _risk_note(row: pd.Series) -> str:
        dxy_trend = row.get("feat_dxy_trend_t", np.nan)
        us10y_trend = row.get("feat_us10y_trend_t", np.nan)
        breadth = row.get("score_c_breadth", np.nan)

        notes: list[str] = []
        if pd.notna(dxy_trend) and dxy_trend < 0:
            notes.append("美元走强压制风险偏好")
        if pd.notna(us10y_trend) and us10y_trend < 0:
            notes.append("美债上行抬升全球资金成本")
        if pd.notna(breadth) and breadth < 40:
            notes.append("市场广度偏弱，需防止抱团松动")
        if not notes:
            return "暂无显著系统性风险提示"
        return "；".join(notes)


def score_market_regime(
    df: pd.DataFrame,
    *,
    score_window: int = DEFAULT_SCORE_WINDOW,
    short_window: int = DEFAULT_SHORT_WINDOW,
    score_method: str = "rank",
    keep_intermediate: bool = False,
) -> pd.DataFrame:
    scorer = MarketRegimeScorer(
        score_window=score_window,
        short_window=short_window,
        score_method=score_method,
    )
    if keep_intermediate:
        return scorer.score_components(df)
    return scorer.final_output(df)


if __name__ == "__main__":
    sample_columns = [
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
    sample = pd.DataFrame(
        np.random.randn(400, len(sample_columns)),
        columns=sample_columns,
        index=pd.date_range("2024-01-01", periods=400, freq="B"),
    ).reset_index(names="date")
    result = score_market_regime(sample)
    print(result.tail(3))
