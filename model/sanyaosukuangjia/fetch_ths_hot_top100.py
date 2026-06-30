"""
同花顺 App/网页「热榜」A 股列表（公开 JSON 接口，无需登录）。

默认与热榜页「热股 → 大家都在看、1 小时」一致（`--type day` 为 24 小时），单次约 100 条（接口本身上限 100）。

示例:
  python fetch_ths_hot_top100.py                    # 默认 1 小时榜（--type hour）
  python fetch_ths_hot_top100.py --type day         # 24 小时榜
  python fetch_ths_hot_top100.py --type hour        # 显式 1 小时榜
  python fetch_ths_hot_top100.py --type hour --list-type skyrocket
  python fetch_ths_hot_top100.py -o output/ths_hot_top100.csv
"""

from __future__ import annotations

import argparse
import json
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

# 同花顺 dq 接口，参数说明见同花顺热榜页前端请求（type / list_type 可换榜种）
BASE = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"


def _flatten_tag(tag: Any) -> str:
    if not tag or not isinstance(tag, dict):
        return ""
    parts: list[str] = []
    ct = tag.get("concept_tag")
    if isinstance(ct, list):
        parts.extend(str(x) for x in ct)
    pt = tag.get("popularity_tag")
    if pt:
        parts.append(str(pt))
    return "、".join(parts)


def fetch_ths_hot_stock_list(
    *,
    stock_type: str = "a",
    time_type: str = "hour",
    list_type: str = "normal",
    timeout: float = 20.0,
) -> pd.DataFrame:
    """
    拉取同花顺热股榜 JSON，解析为表。

    :param stock_type: 市场，默认 ``a`` = A 股
    :param time_type: ``hour`` = 近 1 小时热度更新；``day`` = 近 24 小时（与网页「1小时/24小时」对应）
    :param list_type: 榜单种类，常见：
        ``normal`` 大家都在看；``skyrocket`` 快速飙升；
        ``tech`` / ``value`` / ``trend`` 技术/价值/趋势投资派（多为 day）
    :return: 列：rank, code, name, hot, pct_change, hot_rank_chg, concept_tags, market
    """
    q = urlencode(
        {
            "stock_type": stock_type,
            "type": time_type,
            "list_type": list_type,
        }
    )
    url = f"{BASE}?{q}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://eq.10jqka.com.cn/",
        },
    )
    ctx = ssl.create_default_context()
    with urlopen(req, context=ctx, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    payload = json.loads(raw)
    if payload.get("status_code") != 0:
        raise RuntimeError(f"接口返回异常: {payload!r}")
    rows = payload.get("data", {}).get("stock_list")
    if not rows:
        return pd.DataFrame(
            columns=[
                "rank",
                "code",
                "name",
                "hot",
                "pct_change",
                "hot_rank_chg",
                "concept_tags",
                "market",
            ]
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        tag = r.get("tag")
        out.append(
            {
                "rank": int(r.get("order", 0)),
                "code": str(r.get("code", "")).zfill(6),
                "name": str(r.get("name", "")),
                "hot": r.get("rate"),  # 接口字段名为 rate，实为热度/人气量级
                "pct_change": r.get("rise_and_fall"),
                "hot_rank_chg": r.get("hot_rank_chg"),
                "concept_tags": _flatten_tag(tag),
                "market": r.get("market"),
            }
        )
    return pd.DataFrame(out)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="拉取同花顺热股榜（约前100条，视接口）")
    p.add_argument(
        "--type",
        dest="time_type",
        default="hour",
        choices=["day", "hour"],
        help="时间维度：hour=1小时榜（默认），day=24小时榜",
    )
    p.add_argument(
        "--list-type",
        default="normal",
        help="榜单：normal=大家都在看, skyrocket=快速飙升, tech/value/trend=投资派榜等",
    )
    p.add_argument(
        "--top",
        type=int,
        default=100,
        help="只保留前 N 行（默认 100，接口一般最多 100）",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="保存为 CSV（utf-8-sig），默认只打印不落地",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    dim = "24小时榜" if args.time_type == "day" else "1小时榜"
    print(f"时间维度: {dim}  |  list_type={args.list_type}\n")
    df = fetch_ths_hot_stock_list(
        time_type=args.time_type,
        list_type=args.list_type,
    )
    n = max(0, int(args.top))
    if n > 0 and len(df) > n:
        df = df.head(n).copy()
    print(df.to_string(index=False))
    print(f"\n行数: {len(df)}")
    if args.output is not None:
        args.output = Path(args.output)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"已写入: {args.output.resolve()}")


if __name__ == "__main__":
    main()
