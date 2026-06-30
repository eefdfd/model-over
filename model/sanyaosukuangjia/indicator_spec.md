# 跨市场联动模型：数据字段定义与指标公式

## 1. 模型目标

这个模型用于做**日度市场状态判断**，不是直接预测单只股票明天涨跌。

核心框架只有两层：

- `C_t`：竞争格局分数
- `L_t`：流动性分数

总分定义为：

```text
S_t = 0.55 * C_t + 0.45 * L_t
```

状态映射建议：

- `S_t >= 75`：风险扩张，偏进攻
- `60 <= S_t < 75`：偏强震荡，精选主线
- `45 <= S_t < 60`：中性，控制仓位
- `30 <= S_t < 45`：偏弱，防守为主
- `S_t < 30`：风险收缩，降低风险暴露

---

## 2. 字段设计原则

所有指标分三层：

1. `raw_`：原始字段
2. `feat_`：加工后的特征字段
3. `score_`：标准化后的评分字段

建议统一规则：

- 原始数据频率尽量使用日频
- 月频数据使用最新值向后填充到日频
- 所有评分字段统一映射到 `0-100`
- 默认用最近 `252` 个交易日做滚动分位数标准化

标准化公式建议：

```text
score(x_t) = 100 * rank_pct(x_t, window=252)
```

其中：

- `rank_pct` 表示该值在过去 252 个交易日中的分位数
- 若指标是“越低越好”，则使用：

```text
score_low_better(x_t) = 100 * (1 - rank_pct(x_t, window=252))
```

为了避免极端值影响，也可以使用 z-score 版本：

```text
z_t = (x_t - mean(x, window=252)) / std(x, window=252)
score_z_t = min(100, max(0, 50 + 15 * z_t))
```

---

## 3. 竞争格局 C：字段定义与公式

竞争格局用于回答：**谁在主导市场，主线是否清晰，强势是否可持续。**

总公式建议：

```text
C_t = 0.25 * C1_t + 0.20 * C2_t + 0.30 * C3_t + 0.25 * C4_t
```

其中：

- `C1_t`：市场主导权
- `C2_t`：风格主导权
- `C3_t`：行业主线清晰度
- `C4_t`：市场广度与集中度

### 3.1 市场主导权 C1

#### 原始字段

- `raw_ret_5d_spx`
- `raw_ret_5d_ndx`
- `raw_ret_5d_hsi`
- `raw_ret_5d_hstech`
- `raw_ret_5d_csi300`
- `raw_ret_5d_cyb`
- `raw_ret_5d_rut`
- `raw_ret_20d_spx`
- `raw_ret_20d_ndx`
- `raw_ret_20d_hsi`
- `raw_ret_20d_hstech`
- `raw_ret_20d_csi300`
- `raw_ret_20d_cyb`
- `raw_ret_20d_rut`
- `raw_above_ma20_spx`
- `raw_above_ma20_ndx`
- `raw_above_ma20_hsi`
- `raw_above_ma20_csi300`
- `raw_above_ma60_spx`
- `raw_above_ma60_ndx`
- `raw_above_ma60_hsi`
- `raw_above_ma60_csi300`

#### 特征字段

```text
feat_leader_ret_5d_t = mean(top3(5日收益率排名))
feat_leader_ret_20d_t = mean(top3(20日收益率排名))
feat_trend_strength_t = mean(站上20日均线指标, 站上60日均线指标)
```

#### 子分公式

```text
C1_t = 0.4 * score(feat_leader_ret_5d_t)
     + 0.4 * score(feat_leader_ret_20d_t)
     + 0.2 * score(feat_trend_strength_t)
```

#### 解释

- 看哪个市场在短中期领跑
- 看主导市场是不是已经形成趋势

### 3.2 风格主导权 C2

#### 原始字段

- `raw_ratio_qqq_dia`
- `raw_ratio_qqq_iwm`
- `raw_ratio_cyb_sz50`
- `raw_ratio_kc50_hongli`
- `raw_ratio_hstech_hshighdiv`

#### 特征字段

```text
feat_style_momo_qqq_dia_t = pct_change(raw_ratio_qqq_dia, 20)
feat_style_momo_qqq_iwm_t = pct_change(raw_ratio_qqq_iwm, 20)
feat_style_momo_cyb_sz50_t = pct_change(raw_ratio_cyb_sz50, 20)
feat_style_momo_kc50_hongli_t = pct_change(raw_ratio_kc50_hongli, 20)
feat_style_momo_hstech_hshighdiv_t = pct_change(raw_ratio_hstech_hshighdiv, 20)
feat_style_strength_t = mean(以上五项)
```

#### 子分公式

```text
C2_t = score(feat_style_strength_t)
```

#### 解释

- 风格比值上行，说明成长和进攻资产占优
- 风格比值下行，说明防御和红利占优

### 3.3 行业主线清晰度 C3

#### 原始字段

- `raw_ret_5d_ai`
- `raw_ret_5d_semis`
- `raw_ret_5d_software`
- `raw_ret_5d_ev`
- `raw_ret_5d_energy`
- `raw_ret_5d_finance`
- `raw_ret_5d_consumer`
- `raw_ret_5d_defense`
- `raw_leader_industry_days`
- `raw_new_high_count_leaders`
- `raw_theme_breadth_ai`
- `raw_theme_breadth_semis`
- `raw_theme_breadth_software`

#### 特征字段

```text
feat_industry_concentration_t = 前三强行业5日涨幅均值 - 全市场行业5日涨幅均值
feat_leader_persistence_t = raw_leader_industry_days
feat_leader_breakout_t = raw_new_high_count_leaders
feat_theme_diffusion_t = mean(raw_theme_breadth_ai, raw_theme_breadth_semis, raw_theme_breadth_software)
```

#### 子分公式

```text
C3_t = 0.30 * score(feat_industry_concentration_t)
     + 0.25 * score(feat_leader_persistence_t)
     + 0.25 * score(feat_leader_breakout_t)
     + 0.20 * score(feat_theme_diffusion_t)
```

#### 解释

- 看主线是否稳定
- 看龙头是否创新高
- 看主线是否从少数票扩散到板块内部

### 3.4 市场广度与集中度 C4

#### 原始字段

- `raw_adv_decline_ratio_us`
- `raw_adv_decline_ratio_cn`
- `raw_adv_decline_ratio_hk`
- `raw_newhigh_newlow_ratio_us`
- `raw_newhigh_newlow_ratio_cn`
- `raw_pct_above_ma20_us`
- `raw_pct_above_ma20_cn`
- `raw_pct_above_ma20_hk`
- `raw_pct_above_ma60_us`
- `raw_pct_above_ma60_cn`
- `raw_pct_above_ma60_hk`
- `raw_top10_weight_contrib_us`
- `raw_top10_weight_contrib_cn`
- `raw_top10_weight_contrib_hk`
- `raw_top3_industry_turnover_share`

#### 特征字段

```text
feat_market_breadth_t = mean(
  raw_adv_decline_ratio_us,
  raw_adv_decline_ratio_cn,
  raw_adv_decline_ratio_hk,
  raw_newhigh_newlow_ratio_us,
  raw_newhigh_newlow_ratio_cn,
  raw_pct_above_ma20_us,
  raw_pct_above_ma20_cn,
  raw_pct_above_ma20_hk,
  raw_pct_above_ma60_us,
  raw_pct_above_ma60_cn,
  raw_pct_above_ma60_hk
)

feat_over_concentration_t = mean(
  raw_top10_weight_contrib_us,
  raw_top10_weight_contrib_cn,
  raw_top10_weight_contrib_hk,
  raw_top3_industry_turnover_share
)
```

#### 子分公式

```text
C4_t = 0.7 * score(feat_market_breadth_t)
     + 0.3 * score_low_better(feat_over_concentration_t)
```

#### 解释

- 广度越强越好
- 过度集中通常意味着行情脆弱，要适度扣分

---

## 4. 流动性 L：字段定义与公式

流动性用于回答：**资金成本是否友好，增量资金是否进场，跨资产是否支持风险扩张。**

总公式建议：

```text
L_t = 0.30 * L1_t + 0.20 * L2_t + 0.30 * L3_t + 0.20 * L4_t
```

其中：

- `L1_t`：全球货币流动性
- `L2_t`：中国本地流动性
- `L3_t`：交易流动性
- `L4_t`：跨资产验证

### 4.1 全球货币流动性 L1

#### 原始字段

- `raw_fed_cut_prob`
- `raw_us2y_yield`
- `raw_us10y_yield`
- `raw_us10y_real_yield`
- `raw_dxy`

#### 特征字段

```text
feat_fed_easing_t = raw_fed_cut_prob
feat_us2y_trend_t = -delta(raw_us2y_yield, 20)
feat_us10y_trend_t = -delta(raw_us10y_yield, 20)
feat_real_yield_trend_t = -delta(raw_us10y_real_yield, 20)
feat_dxy_trend_t = -pct_change(raw_dxy, 20)
```

#### 子分公式

```text
L1_t = 0.20 * score(feat_fed_easing_t)
     + 0.20 * score(feat_us2y_trend_t)
     + 0.25 * score(feat_us10y_trend_t)
     + 0.15 * score(feat_real_yield_trend_t)
     + 0.20 * score(feat_dxy_trend_t)
```

#### 解释

- 降息预期增强、美债回落、美元走弱，通常对应更友好的全球流动性

### 4.2 中国本地流动性 L2

#### 原始字段

- `raw_pbo_c_easing_flag`
- `raw_dr007`
- `raw_shibor_3m`
- `raw_cn10y_yield`
- `raw_credit_impulse`
- `raw_usdcnh`

#### 特征字段

```text
feat_policy_easing_cn_t = raw_pbo_c_easing_flag
feat_dr007_trend_t = -delta(raw_dr007, 20)
feat_shibor_trend_t = -delta(raw_shibor_3m, 20)
feat_cn10y_trend_t = -delta(raw_cn10y_yield, 20)
feat_credit_impulse_t = raw_credit_impulse
feat_cnh_stability_t = -abs(zscore(pct_change(raw_usdcnh, 20)))
```

#### 子分公式

```text
L2_t = 0.15 * score(feat_policy_easing_cn_t)
     + 0.20 * score(feat_dr007_trend_t)
     + 0.15 * score(feat_shibor_trend_t)
     + 0.15 * score(feat_cn10y_trend_t)
     + 0.20 * score(feat_credit_impulse_t)
     + 0.15 * score(feat_cnh_stability_t)
```

#### 解释

- 降息降准、短端利率回落、信用改善、汇率稳定，通常对 A 股和港股更友好

### 4.3 交易流动性 L3

#### 原始字段

- `raw_turnover_a_share`
- `raw_turnover_hk`
- `raw_turnover_us_etf`
- `raw_northbound_net_inflow`
- `raw_southbound_net_inflow`
- `raw_us_equity_etf_flow`
- `raw_margin_balance_change`

#### 特征字段

```text
feat_a_share_turnover_ratio_t = raw_turnover_a_share / ma(raw_turnover_a_share, 20)
feat_hk_turnover_ratio_t = raw_turnover_hk / ma(raw_turnover_hk, 20)
feat_us_turnover_ratio_t = raw_turnover_us_etf / ma(raw_turnover_us_etf, 20)
feat_cross_border_flow_t = mean(raw_northbound_net_inflow, raw_southbound_net_inflow)
feat_us_etf_flow_t = raw_us_equity_etf_flow
feat_margin_risk_appetite_t = raw_margin_balance_change
```

#### 子分公式

```text
L3_t = 0.20 * score(feat_a_share_turnover_ratio_t)
     + 0.15 * score(feat_hk_turnover_ratio_t)
     + 0.15 * score(feat_us_turnover_ratio_t)
     + 0.20 * score(feat_cross_border_flow_t)
     + 0.15 * score(feat_us_etf_flow_t)
     + 0.15 * score(feat_margin_risk_appetite_t)
```

#### 解释

- 这部分看“有没有钱真正进场”
- 相比单日值，更关注是否持续高于 20 日均值

### 4.4 跨资产验证 L4

#### 原始字段

- `raw_gold_ret_20d`
- `raw_btc_ret_20d`
- `raw_wti_ret_20d`
- `raw_hyg_lqd_ratio`
- `raw_vix`

#### 特征字段

```text
feat_gold_context_t = raw_gold_ret_20d - pct_change(raw_dxy, 20)
feat_btc_risk_on_t = raw_btc_ret_20d
feat_oil_growth_signal_t = raw_wti_ret_20d
feat_credit_spread_proxy_t = pct_change(raw_hyg_lqd_ratio, 20)
feat_vol_suppression_t = -pct_change(raw_vix, 20)
```

#### 子分公式

```text
L4_t = 0.15 * score(feat_gold_context_t)
     + 0.30 * score(feat_btc_risk_on_t)
     + 0.15 * score(feat_oil_growth_signal_t)
     + 0.20 * score(feat_credit_spread_proxy_t)
     + 0.20 * score(feat_vol_suppression_t)
```

#### 解释

- 比特币、高收益信用、VIX 对风险流动性的确认更直接
- 黄金必须结合美元和美债一起看，不能单独机械解读

---

## 5. 最终输出字段定义

建议每天输出以下字段：

- `date`
- `score_c_market_leadership`
- `score_c_style_leadership`
- `score_c_theme_clarity`
- `score_c_breadth`
- `score_competition`
- `score_l_global_liquidity`
- `score_l_china_liquidity`
- `score_l_trading_liquidity`
- `score_l_cross_asset`
- `score_liquidity`
- `score_total`
- `regime_label`
- `lead_market_label`
- `lead_style_label`
- `position_suggestion`
- `risk_note`

标签生成建议：

```text
if score_total >= 75:
    regime_label = "风险扩张"
elif score_total >= 60:
    regime_label = "偏强震荡"
elif score_total >= 45:
    regime_label = "中性"
elif score_total >= 30:
    regime_label = "偏弱"
else:
    regime_label = "风险收缩"
```

仓位建议示例：

```text
if score_competition >= 70 and score_liquidity >= 70:
    position_suggestion = "高仓，聚焦主线"
elif score_competition >= 70 and score_liquidity < 55:
    position_suggestion = "中仓，聚焦龙头"
elif score_competition < 55 and score_liquidity >= 65:
    position_suggestion = "中低仓，偏交易型"
else:
    position_suggestion = "低仓或防守"
```

---

## 6. 推荐数据源映射

可按“先能跑，再优化”的原则选择数据源。

### 全球市场

- 指数和 ETF：`yfinance`
- 美债收益率、美元指数、VIX：`FRED` + `yfinance`
- 美联储降息预期：CME FedWatch 手工抓取或替代为 `2Y` 利率趋势
- 黄金、原油、比特币：`yfinance`

### 中国市场

- A 股指数、成交额、北向资金：`akshare`
- 港股指数、南向资金、主板成交额：`akshare`
- DR007、Shibor、国债收益率：`akshare` 或官方公开数据
- 社融、信贷：`akshare`
- 人民币汇率：`yfinance` 或外汇公开接口

---

## 7. 第一版实现建议

第一版不要追求复杂，先完成这 4 件事：

1. 拉取所有 `raw_` 字段
2. 生成所有 `feat_` 字段
3. 把特征映射成 `score_` 字段
4. 输出 `C_t`、`L_t`、`S_t` 和标签

推荐代码结构：

```text
sanyaosukuangjia/
├─ indicator_spec.md
├─ data_loader.py
├─ feature_engineering.py
├─ scoring.py
└─ report.py
```

一句话总结：

**竞争格局解决“谁在主导”，流动性解决“钱是否支持”，最后共同决定市场处于什么状态。**
