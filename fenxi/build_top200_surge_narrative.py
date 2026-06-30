# -*- coding: utf-8 -*-
"""
根据 _top200_stocks.json 生成「大涨归纳」Markdown（两段：主线 + 个股映射），
并生成「主线按题材归并表」。
说明：六氟化钨/海外供应等表述来自 2026 年 6 月前后公开财经报道的常见叙事；
其余题材为行业层面归纳，非对每只股票的独立公告考证。投资请以上市公司披露为准。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JSON_PATH = ROOT / "_top200_stocks.json"
OUT_PATH = ROOT / "_前200只股票_大涨归纳_四段式.md"
TABLE_OUT = ROOT / "_前200只股票_主线按题材归纳表.md"

# 题材标签 → 中文简名（用于归并表）
TID_LABEL_ZH: dict[str, str] = {
    "wf6_specialty_gas": "电子特气/六氟化钨",
    "pcb_ccl": "PCB/覆铜板",
    "copper_foil": "铜箔",
    "semi_equipment": "半导体设备",
    "semi_materials_wafer": "半导体材料（硅片/化学品等）",
    "advanced_packaging": "先进封装",
    "optical_cpo": "光模块/CPO",
    "fiber_cable_grid": "光纤海缆/电网通信",
    "passive_mlcc": "被动元件/MLCC",
    "display_panel_optical": "面板与显示材料",
    "power_device_analog": "功率半导体/模拟芯片",
    "industrial_robot_automation": "工业机器人/工控",
    "lithium_battery_materials": "锂电材料",
    "coal_power_redividend": "煤炭/电力（高股息等）",
    "fluorine_silicone_chemical": "氟化工/有机硅",
    "rare_earth_magnetic": "稀土永磁/贵金属材料",
    "pharma_innovation": "医药创新",
    "auto_parts_motorcycle": "汽车零部件/动力",
    "broker_securities": "券商",
    "property_restructure": "地产转型/重组预期",
    "consumer_brand": "消费品牌",
    "military_electronics": "军工电子",
    "metal_processing_tool": "刀具/硬质合金",
    "powder_advanced_materials": "高端粉体材料",
    "xray_inspection_equipment": "X射线检测装备",
    "vacuum_film_equipment": "真空镀膜装备",
    "laser_photonics": "激光产业链",
    "ceramic_components": "先进陶瓷",
    "connector_distribution": "电子分销/连接器",
    "magnetic_components": "软磁/磁材",
    "medical_equipment_imaging": "医疗影像设备",
    "software_industrial_it": "工业软件/自动化信息化",
    "mining_polymetallic": "有色冶炼/小金属",
    "steel_processing": "铜加工/特钢",
    "construction_chemical": "建筑化工材料",
    "semiconductor_generic": "半导体（泛产业链）",
    "electronics_generic": "电子制造（泛产业链）",
    "chemical_materials_generic": "化工新材料（泛产业链）",
    "composite_fiberglass": "玻纤/复合材料",
    "machinery_equipment": "通用/专用装备",
    "utility_platform": "多元业务/平台控股",
    "default": "其他（未细分类）",
}

# 题材块：渲染时使用 main / why；risk、table 字段保留供日后扩展，当前不输出。
THEMES: dict[str, dict[str, str]] = {
    "wf6_specialty_gas": {
        "main": (
            "2026 年上半年以来，市场高度关注**六氟化钨（WF6）**等电子特气在存储/先进制程扩产下的需求；"
            "叠加**海外主要供应商排产与原料约束、对外涨价与供应预警**等报道，资金在「供给缺口 + 国产替代」框架下交易整条电子特气链条。"
        ),
        "why": (
            "**{name}（{code}）**主营或重要产品与**高纯电子气体/前驱体/配套特气**相关，与上述叙事高度同向；"
            "在题材发酵阶段，龙头及辨识度高的标的往往获得更高的成交与估值弹性。"
        ),
        "risk": (
            "多家公司已提示：**洽谈增加不等于已签约束性大单**，价格、份额与利润传导存在滞后与不确定；"
            "市盈率、市净率若显著高于历史与同业，易伴随**异常波动公告**与交易拥挤后的回撤风险。"
        ),
        "table": "您表中的**趋势强度**反映截止日价格趋势的量化得分；**宽松成立**为「是」表示模型侧「向上趋势」宽松口径成立。",
    },
    "pcb_ccl": {
        "main": (
            "全球 AI 服务器、交换机与高端消费电子对**高速多层板、HDI、封装基板**需求升温，"
            "**覆铜板（CCL）与高端 PCB** 环节在景气与涨价预期下常被资金集中交易。"
        ),
        "why": "**{name}（{code}）**处于 PCB/CCL/基材或上游材料环节，业务与高端产能、稼动率及价格预期挂钩，β 与题材弹性较大。",
        "risk": "需跟踪**铜价/加工费、客户认证、扩产节奏**；若预期抢跑而订单兑现偏慢，股价波动会放大。",
        "table": "表内**趋势强度**越高，通常表示该股在您的趋势模型中近期价格惯性更强；请结合基本面节奏解读。",
    },
    "copper_foil": {
        "main": (
            "锂电铜箔与电子铜箔在**新能源排产、加工费、高端铜箔供需**预期变化时易出现板块性行情；"
            "部分时段也与铜价、出口与产能爬坡叙事共振。"
        ),
        "why": "**{name}（{code}）**主营铜箔或紧密配套，价格与盈利预期对「量 × 价」敏感，因此在景气预期上行阶段弹性突出。",
        "risk": "加工费下行、同质化扩产、客户集中与减值风险需分季度验证；高涨幅后注意盈利预测与估值匹配度。",
        "table": "趋势指标刻画的是**价格路径**；与铜箔基本面并不同步时，以公司公告与季报为准。",
    },
    "semi_equipment": {
        "main": (
            "半导体资本开支预期、先进制程与存储扩产、国产设备渗透率等主题，常驱动**前道/检测设备、真空与沉积、清洗涂胶**等设备链共振。"
        ),
        "why": "**{name}（{code}）**属设备或关键零部件/系统供应商，订单可见性与下游 capex 预期高度相关。",
        "risk": "交付节奏、毛利率、出口管制与下游资本开支波动均可能使预期反复；注意大额解禁与股东减持计划。",
        "table": "设备股趋势强往往对应「预期先行」；模型趋势与订单拐点未必同时出现。",
    },
    "semi_materials_wafer": {
        "main": (
            "**大硅片、靶材、抛光垫、湿化学品、光刻胶配套**等材料环节，在「先进制程 + 供应链安全」叙事下易出现板块轮动。"
        ),
        "why": "**{name}（{code}）**业务落在晶圆制造材料或上游高纯材料，与国产替代与份额提升逻辑相关。",
        "risk": "认证周期长、客户导入节奏不一；部分品种价格周期性强，需防预期透支。",
        "table": "材料票的趋势强度常随「主题强弱」快速变化，宜对照公司指引与行业价格指标。",
    },
    "advanced_packaging": {
        "main": (
            "**先进封装、Chiplet、高密度互连**等方向提升对封测与晶圆级工艺的需求，相关设备、材料与封测龙头易受资金关注。"
        ),
        "why": "**{name}（{code}）**与封测链、晶圆级设备或载板材料等环节相关，受益于封装价值量提升预期。",
        "risk": "技术路线迭代快，客户项目制导致收入波动；注意竞争加剧对毛利的影响。",
        "table": "封装链个股波动往往大于指数；趋势模型提示的是价格惯性，不是封装渗透率结论。",
    },
    "optical_cpo": {
        "main": (
            "AI 算力集群建设带动**高速光模块、硅光/CPO、相干光**等需求预期；龙头业绩与指引变化时，板块易出现趋势性行情。"
        ),
        "why": "**{name}（{code}）**位于光通信器件/模块/组件或上游光芯片等环节，与数据中心 capex 预期同向。",
        "risk": "客户集中度、技术迭代（速率升级）、价格战与关税贸易政策均可能扰动预期与估值。",
        "table": "光模块龙头常呈现高趋势强度与高换手并存；注意模型信号与业绩兑现节奏差。",
    },
    "fiber_cable_grid": {
        "main": (
            "**海缆、特高压、电网投资、通信干线**等叙事，常带动光纤光缆、海缆、电力通信相关标的轮动；部分与海风与出口订单预期相关。"
        ),
        "why": "**{name}（{code}）**主营线缆、海缆、光纤或电网通信配套，对订单与招标节奏敏感。",
        "risk": "原材料波动、海上工程进度、区域招标价格与回款周期影响利润质量。",
        "table": "该类标的趋势常与「订单新闻/板块β」同步；单看趋势强度无法替代订单跟踪。",
    },
    "passive_mlcc": {
        "main": (
            "**MLCC、电感、电阻**等被动元件在汽车电子、AI 服务器与消费电子复苏预期下，易出现景气修复与补库交易。"
        ),
        "why": "**{name}（{code}）**为被动元件或陶瓷材料龙头/重要参与者，对稼动率与价格周期敏感。",
        "risk": "下游手机/PC 需求若低于预期，涨价与稼动率修复可能不及预期。",
        "table": "被动元件周期属性强；趋势强时也要看渠道库存与价格是否真拐点。",
    },
    "display_panel_optical": {
        "main": (
            "**面板价格周期、TV 备货、车载显示与 OLED 渗透**等，常驱动面板与显示材料、光学膜、玻璃加工等环节阶段性行情。"
        ),
        "why": "**{name}（{code}）**与面板厂稼动、显示材料或模组设备相关，对行业景气与价格战预期敏感。",
        "risk": "面板价格波动大，盈利弹性可能集中在少数季度；需警惕供给释放压制价格。",
        "table": "显示链条的趋势信号多与「面板报价/稼动」新闻同频，模型仅反映价格序列。",
    },
    "power_device_analog": {
        "main": (
            "**功率半导体、模拟与驱动 IC** 在汽车、光伏储能、工业电源等场景需求扩张预期下，易出现估值修复与趋势交易。"
        ),
        "why": "**{name}（{code}）**产品落在功率器件、模拟、驱动或配套封测环节，对下游景气与渠道补库敏感。",
        "risk": "周期下行时渠道去库存会压制营收与毛利；注意价格战与库存减值。",
        "table": "功率与模拟赛道个股分化大；趋势强度需结合细分品类景气验证。",
    },
    "industrial_robot_automation": {
        "main": (
            "制造业资本开支、机器换人、**工业机器人、精密减速器、工控自动化**等主题在景气预期改善时易共振。"
        ),
        "why": "**{name}（{code}）**位于工控自动化、机器人核心零部件或系统集成环节。",
        "risk": "下游制造业 capex 波动大；若宏观预期转弱，订单兑现可能滞后于股价。",
        "table": "自动化标的趋势常与制造业 PMI 及政策预期相关，属β+α混合驱动。",
    },
    "lithium_battery_materials": {
        "main": (
            "锂电产业链在**排产、碳酸锂价格、政策与储能需求**预期变化时，易出现材料与电池环节的波段行情。"
        ),
        "why": "**{name}（{code}）**与锂电材料、结构件或锂资源等环节相关，对价格与排产预期高度敏感。",
        "risk": "锂价与加工费波动剧烈，盈利预测分歧大；注意产能过剩与减值风险。",
        "table": "锂电材料趋势强时多为价格/排产预期驱动；与模型趋势同向不代表中期盈利已锁定。",
    },
    "coal_power_redividend": {
        "main": (
            "**煤价、电价、来水与火电利用小时**等因素影响火电与煤炭盈利；在「高股息/红利」与煤价阶段反弹叙事下，板块常出现趋势性行情。"
        ),
        "why": "**{name}（{code}）**为煤炭或电力运营企业，盈利对燃料价格、长协比例与政策敏感。",
        "risk": "煤价下行、利用小时波动、可再生能源挤压与政策调控均可能改变盈利与分红预期。",
        "table": "红利资产有时呈现「慢涨+趋势延续」；模型趋势与股息率变化需结合财报验证。",
    },
    "fluorine_silicone_chemical": {
        "main": (
            "**制冷剂配额、氟化工景气、有机硅价格**等周期因素，常驱动氟化工与有机硅龙头阶段性上涨。"
        ),
        "why": "**{name}（{code}）**主营含氟化学品、制冷剂或有机硅等，对价格周期与出口配额预期敏感。",
        "risk": "政策与环保核查、新增产能与下游需求波动会快速改变价格预期。",
        "table": "周期股趋势强往往对应「价格预期」阶段；注意商品价格是否已充分计价。",
    },
    "rare_earth_magnetic": {
        "main": (
            "**稀土价格、出口管制预期、永磁与机器人电机需求**等，常带动稀土与磁材链条交易活跃。"
        ),
        "why": "**{name}（{code}）**与稀土冶炼分离、磁材或相关资源品相关，对政策与商品价格敏感。",
        "risk": "政策与配额变化快，价格波动大；注意海外供应链替代叙事对情绪的反向冲击。",
        "table": "资源品趋势与商品价格高度相关；模型不区分政策驱动与基本面驱动。",
    },
    "pharma_innovation": {
        "main": (
            "**创新药临床数据、医保谈判、BD 出海授权、业绩超预期**等事件，常驱动医药子板块分化上涨。"
        ),
        "why": "**{name}（{code}）**属制药或创新药械相关标的，股价对管线进展与政策预期敏感。",
        "risk": "临床失败、集采降价、融资环境与情绪切换可导致剧烈回撤。",
        "table": "医药个股趋势常与「单事件」绑定；模型趋势强时尤需核对是否有未披露信息风险。",
    },
    "auto_parts_motorcycle": {
        "main": (
            "**汽车产销、零部件出海、智能化配置渗透率**以及摩托车等细分景气，常带动零部件与动力系统链条行情。"
        ),
        "why": "**{name}（{code}）**为零部件或动力系统相关供应商，对下游排产与客户项目节奏敏感。",
        "risk": "年降压力、客户集中、原材料与汇率波动影响毛利。",
        "table": "零部件趋势多与排产/政策（以旧换新等）相关；模型仅反映价格结果。",
    },
    "broker_securities": {
        "main": (
            "券商股常随**市场成交额、两融、政策预期（并购重组、衍生品、财富管理）**与β行情同步波动。"
        ),
        "why": "**{name}（{code}）**为证券公司标的，盈利与市场活跃度高度相关。",
        "risk": "市场若缩量，经纪与自营弹性会迅速回落；注意再融资与股东减持。",
        "table": "券商趋势强多为市场β；与您表中其他制造业逻辑不同，勿混用同一基本面叙事。",
    },
    "property_restructure": {
        "main": (
            "部分地产或转型类标的的行情，常来自**资产重组、壳资源、跨界转型预期**等事件型交易，基本面与股价可能短期脱钩。"
        ),
        "why": "**{name}（{code}）**若属此类，上涨驱动力多为预期博弈而非稳定现金流改善。",
        "risk": "方案不确定、监管问询与失败风险高；波动极大。",
        "table": "事件驱动股的趋势信号噪声高；务必以公司公告与交易所问询为准。",
    },
    "consumer_brand": {
        "main": (
            "**消费复苏、渠道改革、产品结构升级**或个别大单品放量，可能驱动饮料、包装等消费龙头趋势行情。"
        ),
        "why": "**{name}（{code}）**属大众品或包装等消费链条，对动销与成本（包材、运费）敏感。",
        "risk": "消费疲软、竞争加剧与成本上行会压制盈利弹性。",
        "table": "消费股趋势偏慢；若表中强度很高，多半叠加了题材或资金抱团因素。",
    },
    "military_electronics": {
        "main": (
            "**军工电子、特种材料、元器件自主可控**等主题在风险偏好上行阶段易出现板块性交易机会（仍以公告与订单为准）。"
        ),
        "why": "**{name}（{code}）**业务与军工电子或特种元器件相关，对订单节奏与主题风险偏好敏感。",
        "risk": "信息披露受限、估值波动大；注意合规与异常波动提示。",
        "table": "军工电子趋势常与风险偏好同向；模型无法反映保密订单信息。",
    },
    "metal_processing_tool": {
        "main": (
            "**刀具、硬质合金、精密加工**等环节在制造业景气与出口预期改善时，易出现盈利修复与估值扩张。"
        ),
        "why": "**{name}（{code}）**主营刀具或硬质材料加工，对制造业 capex 与原材料成本敏感。",
        "risk": "竞争加剧与价格战会侵蚀毛利；下游景气若转弱，订单能见度下降。",
        "table": "趋势强时建议对照制造业 PMI 与公司出货数据。",
    },
    "powder_advanced_materials": {
        "main": (
            "**高端粉体、纳米材料、靶材粉体**等在电子、新能源与催化领域有增量空间预期时，相关标的易被资金挖掘。"
        ),
        "why": "**{name}（{code}）**与先进粉体材料或上游金属粉体相关，对下游创新与认证进度敏感。",
        "risk": "下游验证周期长、客户导入不确定；注意概念炒作与基本面脱节。",
        "table": "小市值材料股趋势波动大；模型信号需配合客户与收入结构验证。",
    },
    "xray_inspection_equipment": {
        "main": (
            "**工业 X 射线检测、安检与半导体封装检测**等设备需求，受下游扩产与国产替代预期驱动。"
        ),
        "why": "**{name}（{code}）**位于检测装备或核心部件环节，对下游 capex 与渗透率预期敏感。",
        "risk": "项目制收入波动、竞争加剧与毛利率压力。",
        "table": "装备类趋势与订单节奏相关；强度变化快于收入确认。",
    },
    "vacuum_film_equipment": {
        "main": (
            "**真空镀膜、锂电铜箔设备、显示与半导体镀膜**等设备环节，在下游扩产预期升温时易出现趋势行情。"
        ),
        "why": "**{name}（{code}）**主营镀膜或真空相关装备/材料，对下游资本开支敏感。",
        "risk": "订单确认节奏与毛利率波动大；注意客户集中风险。",
        "table": "设备订单与股价常不同步；趋势强不等于在手订单已暴增。",
    },
    "laser_photonics": {
        "main": (
            "**激光器、激光装备与 3C/新能源加工**需求预期改善时，激光产业链易出现共振。"
        ),
        "why": "**{name}（{code}）**位于激光器、激光设备或光学器件环节。",
        "risk": "下游资本开支波动与价格战；出口与供应链扰动。",
        "table": "激光股趋势常与制造业景气绑定；模型为价格序列结果。",
    },
    "ceramic_components": {
        "main": (
            "**先进陶瓷、电子陶瓷结构件**等在半导体、通信与新能源场景的应用扩张预期，可能驱动细分龙头趋势行情。"
        ),
        "why": "**{name}（{code}）**与电子陶瓷或先进陶瓷材料/器件相关。",
        "risk": "认证与客户导入周期长；注意竞争与降价压力。",
        "table": "细分赛道容量有限，趋势强时更需核对收入占比与毛利。",
    },
    "connector_distribution": {
        "main": (
            "**电子分销、连接器、模组配套**等环节在下游补库与 AI 服务器等需求预期下，可能出现景气交易。"
        ),
        "why": "**{name}（{code}）**为分销或连接器/模组配套相关标的，对下游景气与库存周期敏感。",
        "risk": "毛利率薄、库存减值与应收风险；注意大客户项目节奏。",
        "table": "分销/模组β强；趋势信号需结合库存与周转指标。",
    },
    "magnetic_components": {
        "main": (
            "**软磁材料、电感磁芯、电力电子**等在新能源与高效率电源需求预期下，可能出现材料与器件共振。"
        ),
        "why": "**{name}（{code}）**与磁性材料或磁性器件相关。",
        "risk": "原材料价格与下游价格战；技术路线变化。",
        "table": "材料属性偏周期+成长混合；注意季度毛利变化。",
    },
    "medical_equipment_imaging": {
        "main": (
            "**医疗影像、放疗设备、高端医疗器械**国产替代与出海预期，可能驱动相关标的阶段性行情。"
        ),
        "why": "**{name}（{code}）**位于医疗影像或器械设备链条。",
        "risk": "集采、招标节奏与海外认证不确定。",
        "table": "医疗器械趋势常与政策/招标新闻同频。",
    },
    "software_industrial_it": {
        "main": (
            "**工业软件、流程自动化、信息化**在制造业升级与信创预期下可能出现主题与业绩双击交易。"
        ),
        "why": "**{name}（{code}）**主营工业软件或自动化信息化产品。",
        "risk": "项目制收入波动、回款周期长；竞争加剧。",
        "table": "软件股估值弹性大；趋势强时留意业绩兑现。",
    },
    "mining_polymetallic": {
        "main": (
            "**锗、锌铅、硫化工**等资源品在出口管制、供给约束或商品涨价预期下，可能出现阶段性行情。"
        ),
        "why": "**{name}（{code}）**与有色冶炼或资源综合利用相关，对金属价格与政策敏感。",
        "risk": "价格波动与环保政策；注意资源税与加工费变化。",
        "table": "资源冶炼股趋势与商品价格高度相关。",
    },
    "steel_processing": {
        "main": (
            "**特钢、铜加工、材料加工**等环节在制造业景气与原材料价差变化时，可能出现盈利修复交易。"
        ),
        "why": "**{name}（{code}）**主营铜加工或特钢等材料加工。",
        "risk": "加工费下行与库存风险；宏观需求波动。",
        "table": "加工环节利润薄；趋势强时重点看价差与开工率。",
    },
    "construction_chemical": {
        "main": (
            "**防水、涂料、建筑化学品**等在地产链政策预期改善时可能出现估值修复。"
        ),
        "why": "**{name}（{code}）**与建筑化学品或改性材料相关。",
        "risk": "地产需求疲软与应收风险；现金流质量关键。",
        "table": "地产链标的趋势多与政策预期相关。",
    },
    "semiconductor_generic": {
        "main": (
            "在半导体整体β上行阶段，**设计、制造、封测、材料、设备**等多环节可能出现轮动上涨，资金沿产业链挖掘补涨。"
        ),
        "why": "**{name}（{code}）**属半导体产业链相关标的，对行业景气与风险偏好敏感。",
        "risk": "个股分化大；注意业绩兑现、股东减持与估值位置。",
        "table": "泛半导体β行情下，趋势强度可能更多反映资金轮动而非公司α。",
    },
    "electronics_generic": {
        "main": (
            "电子制造与零部件在**AI 终端、汽车电子、服务器**等需求预期下，易出现板块性资金流入。"
        ),
        "why": "**{name}（{code}）**处于电子制造/零部件/模组等环节，对下游创新与补库预期敏感。",
        "risk": "年降、库存与汇率；客户集中度高时波动更大。",
        "table": "电子大板块内部分化显著；建议用公司主营细分去对照叙事。",
    },
    "chemical_materials_generic": {
        "main": (
            "**精细化工、功能材料**在下游（新能源、电子、汽车）景气预期上行时，可能出现订单与价差改善的交易机会。"
        ),
        "why": "**{name}（{code}）**主营化工新材料或功能化学品，对价格与产能利用率敏感。",
        "risk": "原材料波动、环保与安全监管、新增产能冲击价格。",
        "table": "化工材料趋势常与产品价格指标同向；模型不直接包含商品价格。",
    },
    "composite_fiberglass": {
        "main": (
            "**玻纤、复合材料**在风电、汽车轻量化与出口需求预期变化时，可能出现景气修复行情。"
        ),
        "why": "**{name}（{code}）**与玻纤或复合材料相关，对价格与库存周期敏感。",
        "risk": "价格战与库存高位会压制盈利；注意供给投放节奏。",
        "table": "玻纤周期属性强；趋势强时核对库存与价格是否同步改善。",
    },
    "machinery_equipment": {
        "main": (
            "**专用设备、工程机械、通用装备**在制造业投资预期改善时，易出现订单与估值共振。"
        ),
        "why": "**{name}（{code}）**位于装备制造或关键零部件环节。",
        "risk": "订单波动、交付风险与毛利率压力。",
        "table": "装备趋势与下游 capex 预期相关；模型为价格结果。",
    },
    "utility_platform": {
        "main": (
            "部分「平台/供应链/控股」类标的在**资产重组、业务转型或概念映射**下可能出现阶段性上涨，需以公告为准。"
        ),
        "why": "**{name}（{code}）**业务结构相对多元或对子公司并表预期敏感。",
        "risk": "透明度与治理结构风险；事件不确定高。",
        "table": "此类标的趋势噪声可能较大；务必核对公告与问询函。",
    },
    "default": {
        "main": (
            "A 股阶段性行情中，**行业β、资金轮动、主题映射**常共同推动个股趋势走强；"
            "若缺乏明确单点催化，更多体现为板块共振与流动性偏好。"
        ),
        "why": "**{name}（{code}）**在本批次列表中与您模型筛选出的强势趋势同向；具体驱动需结合公司近期公告、行业新闻与龙虎榜等核对。",
        "risk": "趋势交易拥挤后波动加剧；注意估值位置与业绩兑现。",
        "table": "您表中的**趋势强度**与**宽松成立**字段，说明该股在截止日被模型识别为趋势侧偏强样本；不构成买卖建议。",
    },
}


def assign_theme(code: str, name: str) -> str:
    c = code.strip().zfill(6)
    n = name.upper().replace("Ａ", "A").replace("XD", "").replace("DR", "").replace("XR", "")

    # 名称关键词优先（避免误伤可用更长短语）
    rules: list[tuple[str, str]] = [
        ("中船特气", "wf6_specialty_gas"),
        ("中巨芯", "wf6_specialty_gas"),
        ("昊华科技", "wf6_specialty_gas"),
        ("华特气体", "wf6_specialty_gas"),
        ("广钢气体", "wf6_specialty_gas"),
        ("南大光电", "wf6_specialty_gas"),
        ("雅克科技", "wf6_specialty_gas"),
        ("新莱应材", "wf6_specialty_gas"),
        ("三孚股份", "fluorine_silicone_chemical"),  # 有机硅/三氯氢硅等，偏氟硅周期
        ("彤程新材", "semi_materials_wafer"),
        ("飞凯材料", "semi_materials_wafer"),
        ("上海新阳", "semi_materials_wafer"),
        ("鼎龙股份", "semi_materials_wafer"),
        ("江丰电子", "semi_materials_wafer"),
        ("有研新材", "powder_advanced_materials"),
        ("博迁新材", "powder_advanced_materials"),
        ("风华高科", "passive_mlcc"),
        ("三环集团", "passive_mlcc"),
        ("顺络电子", "passive_mlcc"),
        ("金安国纪", "pcb_ccl"),
        ("南亚新材", "pcb_ccl"),
        ("华正新材", "pcb_ccl"),
        ("生益科技", "pcb_ccl"),
        ("鹏鼎控股", "pcb_ccl"),
        ("深南电路", "pcb_ccl"),
        ("沪电股份", "pcb_ccl"),
        ("中富电路", "pcb_ccl"),
        ("崇达技术", "pcb_ccl"),
        ("兴森科技", "pcb_ccl"),
        ("科翔股份", "pcb_ccl"),
        ("宏昌电子", "pcb_ccl"),
        ("铜冠铜箔", "copper_foil"),
        ("德福科技", "copper_foil"),
        ("嘉元科技", "copper_foil"),
        ("诺德股份", "copper_foil"),
        ("盛美上海", "semi_equipment"),
        ("拓荆科技", "semi_equipment"),
        ("中微公司", "semi_equipment"),
        ("北方华创", "semi_equipment"),
        ("芯源微", "semi_equipment"),
        ("微导纳米", "semi_equipment"),
        ("长川科技", "semi_equipment"),
        ("精测电子", "semi_equipment"),
        ("华峰测控", "semi_equipment"),
        ("富创精密", "semi_equipment"),
        ("芯碁微装", "semi_equipment"),
        ("金海通", "semi_equipment"),
        ("至纯", "semi_equipment"),
        ("日联科", "xray_inspection_equipment"),
        ("东威科技", "vacuum_film_equipment"),
        ("沪硅产业", "semi_materials_wafer"),
        ("有研硅", "semi_materials_wafer"),
        ("西安奕材", "semi_materials_wafer"),
        ("立昂微", "semi_materials_wafer"),
        ("中际旭创", "optical_cpo"),
        ("新易盛", "optical_cpo"),
        ("天孚通信", "optical_cpo"),
        ("联特科技", "optical_cpo"),
        ("太辰光", "optical_cpo"),
        ("源杰科技", "optical_cpo"),
        ("光迅科技", "optical_cpo"),
        ("光库科技", "optical_cpo"),
        ("亨通光电", "fiber_cable_grid"),
        ("亨通股份", "fiber_cable_grid"),
        ("中天科技", "fiber_cable_grid"),
        ("长飞光纤", "fiber_cable_grid"),
        ("烽火通信", "fiber_cable_grid"),
        ("永鼎股份", "fiber_cable_grid"),
        ("通鼎互联", "fiber_cable_grid"),
        ("杭电股份", "fiber_cable_grid"),
        ("京东方", "display_panel_optical"),
        ("彩虹股份", "display_panel_optical"),
        ("蓝思科技", "display_panel_optical"),
        ("海信视像", "display_panel_optical"),
        ("沃格光电", "display_panel_optical"),
        ("斯迪克", "display_panel_optical"),
        ("隆扬电子", "display_panel_optical"),
        ("汇成股份", "advanced_packaging"),  # 显示驱动芯片封测
        ("晶方科技", "advanced_packaging"),
        ("甬矽电子", "advanced_packaging"),
        ("长电科技", "advanced_packaging"),
        ("燕东微", "semiconductor_generic"),
        ("兆易创新", "semiconductor_generic"),
        ("佰维存储", "semiconductor_generic"),
        ("杰华特", "power_device_analog"),
        ("晶丰明源", "power_device_analog"),
        ("新洁能", "power_device_analog"),
        ("扬杰科技", "power_device_analog"),
        ("兴福电子", "semiconductor_generic"),
        ("华虹宏力", "semiconductor_generic"),
        ("强一股份", "semiconductor_generic"),
        ("石英股份", "semi_materials_wafer"),
        ("菲利华", "semi_materials_wafer"),
        ("厦门钨业", "rare_earth_magnetic"),
        ("中钨高新", "rare_earth_magnetic"),
        ("章源钨业", "rare_earth_magnetic"),
        ("盛和资源", "rare_earth_magnetic"),
        ("横店东磁", "rare_earth_magnetic"),
        ("云南锗业", "mining_polymetallic"),
        ("国城矿业", "mining_polymetallic"),
        ("楚江新材", "steel_processing"),
        ("海亮股份", "steel_processing"),
        ("潞安环能", "coal_power_redividend"),
        ("平煤股份", "coal_power_redividend"),
        ("晋控煤业", "coal_power_redividend"),
        ("大唐发电", "coal_power_redividend"),
        ("京能电力", "coal_power_redividend"),
        ("华电能源", "coal_power_redividend"),
        ("华电辽能", "coal_power_redividend"),
        ("豫能控股", "coal_power_redividend"),
        ("淮北矿", "coal_power_redividend"),
        ("巨化股份", "fluorine_silicone_chemical"),
        ("多氟多", "fluorine_silicone_chemical"),
        ("东岳硅材", "fluorine_silicone_chemical"),
        ("新宙邦", "lithium_battery_materials"),
        ("天华新能", "lithium_battery_materials"),
        ("盛新锂能", "lithium_battery_materials"),
        ("蔚蓝锂芯", "lithium_battery_materials"),
        ("TCL中环", "lithium_battery_materials"),
        ("科伦药业", "pharma_innovation"),
        ("贝达药业", "pharma_innovation"),
        ("通化金马", "pharma_innovation"),
        ("华兰股份", "pharma_innovation"),
        ("中鼎股份", "auto_parts_motorcycle"),
        ("岱美股份", "auto_parts_motorcycle"),
        ("宗申动力", "auto_parts_motorcycle"),
        ("华安证券", "broker_securities"),
        ("万通发展", "property_restructure"),
        ("百润股份", "consumer_brand"),
        ("火炬电子", "military_electronics"),
        ("宏达电子", "military_electronics"),
        ("欧科亿", "metal_processing_tool"),
        ("新锐股份", "metal_processing_tool"),
        ("国机精工", "metal_processing_tool"),
        ("埃斯顿", "industrial_robot_automation"),
        ("绿的谐波", "industrial_robot_automation"),
        ("恒立液压", "industrial_robot_automation"),
        ("昊志机电", "industrial_robot_automation"),
        ("中控技术", "software_industrial_it"),
        ("奕瑞科技", "medical_equipment_imaging"),
        ("铂科新材", "magnetic_components"),
        ("龙磁科技", "magnetic_components"),
        ("商络电子", "connector_distribution"),
        ("深科技", "electronics_generic"),
        ("罗博特科", "semi_equipment"),
        ("帝尔激光", "laser_photonics"),
        ("锐科激光", "laser_photonics"),
        ("大族数控", "laser_photonics"),
        ("联瑞新", "ceramic_components"),
        ("珂玛科技", "ceramic_components"),
        ("国瓷材料", "ceramic_components"),
        ("圣泉集团", "chemical_materials_generic"),
        ("东材科技", "chemical_materials_generic"),
        ("侨源股份", "chemical_materials_generic"),
        ("中国巨石", "composite_fiberglass"),
        ("国际复材", "composite_fiberglass"),
        ("贵研铂业", "rare_earth_magnetic"),
        ("博威合金", "steel_processing"),
        ("三祥新材", "ceramic_components"),
        ("江南新材", "copper_foil"),
        ("斯瑞新材", "powder_advanced_materials"),
        ("恒坤新材", "semi_materials_wafer"),
        ("麦格米特", "power_device_analog"),
        ("裕同科技", "electronics_generic"),
        ("海星股份", "chemical_materials_generic"),
        ("天承科技", "pcb_ccl"),
        ("科瑞技术", "machinery_equipment"),
        ("博杰股份", "electronics_generic"),
        ("华盛昌", "electronics_generic"),
        ("强瑞技术", "electronics_generic"),
        ("民爆光电", "electronics_generic"),
        ("弘信电子", "pcb_ccl"),
        ("粤桂股份", "mining_polymetallic"),
        ("国恩股份", "construction_chemical"),
        ("莲花控股", "utility_platform"),
        ("远东股份", "utility_platform"),
        ("行云科技", "software_industrial_it"),
        ("先导基电", "semiconductor_generic"),
        ("亚翔集成", "semiconductor_generic"),
        ("太极实业", "semiconductor_generic"),
        ("洁美科技", "pcb_ccl"),
        ("四方达", "metal_processing_tool"),
        ("奥比中光", "semiconductor_generic"),
        ("呈和科技", "chemical_materials_generic"),
        ("精智达", "semi_equipment"),
        ("海信家电", "consumer_brand"),
        ("东睦股份", "powder_advanced_materials"),
        ("安集", "semi_materials_wafer"),
        ("和林微纳", "semiconductor_generic"),
        ("长盈通", "military_electronics"),
        ("光智科技", "semiconductor_generic"),
        ("旭光电子", "semiconductor_generic"),
        ("华宏科技", "machinery_equipment"),
        ("中化国际", "chemical_materials_generic"),
    ]

    for key, tid in rules:
        if key in name:
            return tid

    # 代码集合补充
    if c.startswith("688") and any(
        x in name for x in ("微", "芯", "晶", "纳", "测", "装", "创", "虹")
    ):
        return "semiconductor_generic"
    if c.startswith("300") and ("电子" in name or "科技" in name):
        return "electronics_generic"
    if c.startswith("603") and "股份" in name and len(name) <= 5:
        return "chemical_materials_generic"

    return "default"


def render_stock(idx: int, row: dict) -> str:
    code = row["代码"]
    name = row["名称"]
    tid = assign_theme(code, name)
    blk = THEMES.get(tid, THEMES["default"])
    main = blk["main"]
    why = blk["why"].format(name=name, code=code)

    meta = (
        f"**序号 {idx}** ｜ **{code} {name}** ｜ 截止 {row['截止日']} 收盘 **{row['收盘']}** ｜ "
        f"趋势强度 **{row['趋势强度']}** ｜ 宽松成立 **{row['趋势成立_宽松']}** ｜ 题材标签 `{tid}`"
    )

    return "\n".join(
        [
            f"## {idx}. {code} {name}",
            "",
            meta,
            "",
            "### 1）主线：市场在交易什么？",
            "",
            main,
            "",
            "### 2）为什么这只股票容易「涨得多 / 涨得快」？",
            "",
            why,
            "",
            "---",
            "",
        ]
    )


def build_mainline_table_md(data: list[dict]) -> str:
    """按题材标签归并：主线正文相同的股票合并为一张总表 + 各组分节。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in data:
        tid = assign_theme(row["代码"], row["名称"])
        grouped[tid].append(row)
    items = sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0]))

    lines: list[str] = [
        "# 前 200 只个股 ·「主线：市场在交易什么？」按题材归并表",
        "",
        "与《大涨归纳》明细使用**同一套**题材规则：同一题材下「1）主线」正文相同，故合并展示。",
        "",
        "## 1. 总览表",
        "",
        "| 序号 | 题材简名 | 题材标签 | 个股数 | 涉及股票（代码+名称） |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for i, (tid, stocks) in enumerate(items, start=1):
        label = TID_LABEL_ZH.get(tid, tid)
        names = "、".join(f"{r['代码']} {r['名称']}" for r in stocks)
        lines.append(f"| {i} | {label} | `{tid}` | {len(stocks)} | {names} |")

    lines.extend(["", "---", "", "## 2. 各题材「主线」原文 + 个股清单", ""])
    for tid, stocks in items:
        label = TID_LABEL_ZH.get(tid, tid)
        main = THEMES.get(tid, THEMES["default"])["main"]
        lines.extend(
            [
                f"### {label}（`{tid}`，**{len(stocks)}** 只）",
                "",
                "**主线：市场在交易什么？**",
                "",
                main,
                "",
                "| # | 代码 | 名称 |",
                "| --- | --- | --- |",
            ]
        )
        for j, r in enumerate(stocks, 1):
            lines.append(f"| {j} | {r['代码']} | {r['名称']} |")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))["前200条"]
    parts = [
        "# 前 200 只个股「大涨」归纳（两段式）",
        "",
        "> **生成说明**：仅保留「主线 → 个股映射」两部分；已按要求不输出风险/降温与 Excel 字段解读。"
        "其中 **六氟化钨/电子特气海外供应** 等表述，与 2026 年 6 月前后公开财经报道中常见叙事一致；"
        "其余为**行业层面归纳**，不是对每只股票的独立公告考证。",
        "",
        "> **不构成投资建议**。个股催化请以交易所披露与上市公司公告为准。",
        "",
        "> **按「主线」归并的一览表**：同目录 **[_前200只股票_主线按题材归纳表.md](_前200只股票_主线按题材归纳表.md)**（总览表 + 各题材主线原文与个股清单）。",
        "",
        "---",
        "",
    ]
    for i, row in enumerate(data, start=1):
        parts.append(render_stock(i, row))
    OUT_PATH.write_text("\n".join(parts), encoding="utf-8")
    TABLE_OUT.write_text(build_mainline_table_md(data), encoding="utf-8")
    print("Wrote", OUT_PATH, "chars", OUT_PATH.stat().st_size)
    print("Wrote", TABLE_OUT, "chars", TABLE_OUT.stat().st_size)


if __name__ == "__main__":
    main()
