# -*- coding: utf-8 -*-
"""
先按总市值筛股（默认：>200 亿元，与 ``filter_a_share_universe`` 同口径：腾讯批量行情 field 总市值亿元），
再对筛后列表逐只拉日 K（``screen_matrix_short_trend.fetch_daily_hist``，默认新浪前复权），
每只好转立即写入 ``{code}.csv``，并**实时打一行进度**（stdout 带 flush）。

筛后名单会写出 ``_universe_mcap_gt{阈值}yi.csv`` 便于核对；结束后再写 ``_export_log.csv``。

用法::

    python export_all_a_daily_k_quana.py --limit 5
    python export_all_a_daily_k_quana.py
    # 不筛市值、全A（原行为，耗时长）
    python export_all_a_daily_k_quana.py --skip-mcap-filter
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

# Windows 上 akshare/py_mini_racer(V8) 与多线程并发不兼容；多进程每进程独立 V8，可并行。
_WIN32 = sys.platform == "win32"

import akshare as ak
import pandas as pd

from filter_a_share_universe import ensure_output_dir, evaluate_universe_with_tencent, load_universe
from screen_matrix_short_trend import fetch_daily_hist

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_QUANA = SCRIPT_DIR / "quana"


def load_all_codes() -> pd.DataFrame:
    u = ak.stock_info_a_code_name()
    c0, c1 = u.columns[0], u.columns[1]
    df = u.rename(columns={c0: "code", c1: "name"}).copy()
    df["name"] = df["name"].astype(str).str.strip()
    df["code"] = df["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    df = df.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"])
    return df.reset_index(drop=True)


def one_stock(
    code: str,
    name: str,
    *,
    out_dir: Path,
    lookback: int,
    adjust: str,
    skip_em: bool,
    skip_existing: bool,
    em_timeout: float,
) -> dict[str, object]:
    code = str(code).zfill(6)
    p = out_dir / f"{code}.csv"
    if skip_existing and p.is_file() and p.stat().st_size > 0:
        return {"code": code, "name": name, "ok": True, "skipped": True, "rows": -1, "source": "skip", "err": ""}
    try:
        df, ksrc = fetch_daily_hist(
            code,
            lookback_days=lookback,
            adjust=adjust,
            kline_cache_dir=None,
            kline_cache_hours=0.0,
            skip_em=skip_em,
            em_timeout=em_timeout,
        )
    except Exception as e:  # noqa: BLE001
        return {"code": code, "name": name, "ok": False, "skipped": False, "rows": 0, "source": "", "err": repr(e)}
    if df is None or df.empty:
        return {"code": code, "name": name, "ok": False, "skipped": False, "rows": 0, "source": ksrc, "err": "empty"}
    try:
        df.to_csv(p, index=False, encoding="utf-8-sig")
    except OSError as e:
        return {"code": code, "name": name, "ok": False, "skipped": False, "rows": len(df), "source": ksrc, "err": repr(e)}
    return {"code": code, "name": name, "ok": True, "skipped": False, "rows": len(df), "source": ksrc, "err": ""}


def _mp_one_stock(
    payload: tuple[int, str, str, str, int, str, bool, bool, float],
) -> tuple[int, dict[str, object]]:
    """子进程入口：仅含可 pickle 的元组，供 ProcessPoolExecutor 使用。"""
    idx, out_dir_str, code, name, lookback, adjust, skip_em, skip_existing, em_timeout = payload
    r = one_stock(
        code,
        name,
        out_dir=Path(out_dir_str),
        lookback=lookback,
        adjust=adjust,
        skip_em=skip_em,
        skip_existing=skip_existing,
        em_timeout=em_timeout,
    )
    return idx, r


def main() -> None:
    ap = argparse.ArgumentParser(
        description="先筛市值(亿)，再将日K写入 quana；每只好一条进度",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_QUANA,
        help=f"日K 输出根目录，默认: {DEFAULT_QUANA}",
    )
    ap.add_argument(
        "--min-market-cap-yi",
        type=float,
        default=200.0,
        help="只处理总市值(亿元)严格大于该值的股票（与 filter 筛池一致，默认 200）",
    )
    ap.add_argument(
        "--skip-mcap-filter",
        action="store_true",
        help="不筛市值，对全A拉日K（与旧版行为相同）",
    )
    ap.add_argument("--lookback", type=int, default=500, help="日 K 回溯自然日")
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发数：Linux/macOS 用线程池；Windows 且 >1 时用子进程池（V8/mini_racer 与多线程不兼容）",
    )
    ap.add_argument("--limit", type=int, default=0, help="筛后仅处理前 N 只，0=全部")
    ap.add_argument("--skip-existing", action="store_true", help="已存在非空 csv 则跳过")
    ap.add_argument("--no-qfq", action="store_true", help="不复权（默认定前复权 qfq）")
    ap.add_argument(
        "--try-em",
        action="store_true",
        help="先东财、不足再新浪（默认只新浪）",
    )
    ap.add_argument("--em-timeout", type=float, default=6.0, help="东财请求超时(秒)")
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="每完成一只后额外休眠(秒)；仅与 --workers 1 配合限流",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (SCRIPT_DIR / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    adjust = "" if args.no_qfq else "qfq"
    mcap_yi: float = float(args.min_market_cap_yi)

    if not args.skip_mcap_filter:
        print(
            f"[1/2] 正在拉取腾讯行情并筛: 总市值(亿元) > {mcap_yi} ...",
            flush=True,
        )
        t_u = time.perf_counter()
        base = load_universe(ensure_output_dir("outputs"), 0)
        quoted = evaluate_universe_with_tencent(base)
        filtered = quoted[quoted["market_cap_yi"] > mcap_yi].copy()
        filtered = filtered.sort_values("market_cap_yi", ascending=False, kind="mergesort").reset_index(drop=True)
        list_path = out_dir / f"_universe_mcap_gt{mcap_yi:g}yi.csv"
        # 便于核对：含市值列
        save_cols = [c for c in ("code", "name", "market_cap_yi", "latest_close") if c in filtered.columns]
        filtered[save_cols].to_csv(list_path, index=False, encoding="utf-8-sig")
        u = filtered[["code", "name"]].copy() if "name" in filtered.columns else filtered[["code"]].copy()
        print(
            f"  筛完: 有效行情 {len(quoted)} 只 -> 高于阈值 {len(filtered)} 只，"
            f"名单: {list_path.resolve()} 用时 {time.perf_counter() - t_u:.1f}s",
            flush=True,
        )
    else:
        u = load_all_codes()
        print(f"[1/2] 未筛市值，全A: {len(u)} 只", flush=True)

    if args.limit and args.limit > 0:
        u = u.head(int(args.limit))
    n = len(u)
    if n == 0:
        print("无待处理股票，退出。", flush=True)
        sys.exit(0)

    w = max(1, int(args.workers))
    pool_kind = "子进程" if _WIN32 and w > 1 else "线程"
    print(
        f"[2/2] 开始拉日K -> {out_dir.resolve()}  共 {n} 只  复权={adjust or '无'}  "
        f"回溯日={args.lookback}  并发={w} ({pool_kind})",
        flush=True,
    )
    t0 = time.perf_counter()
    results: list[dict[str, object]] = []
    prog = threading.Lock()
    done = [0]

    def log_line(msg: str) -> None:
        print(msg, flush=True)

    def row_task(row: pd.Series) -> dict[str, object]:
        r = one_stock(
            str(row["code"]),
            str(row.get("name", "")),
            out_dir=out_dir,
            lookback=int(args.lookback),
            adjust=adjust,
            skip_em=not bool(args.try_em),
            skip_existing=bool(args.skip_existing),
            em_timeout=float(args.em_timeout),
        )
        if w == 1 and args.sleep and args.sleep > 0:
            time.sleep(float(args.sleep))
        with prog:
            done[0] += 1
            k = done[0]
            nm = str(row.get("name", ""))[:8]
            if r.get("skipped"):
                log_line(
                    f"  [{k}/{n}] {row['code']} {nm}  跳过(已存在文件)"
                )
            elif r.get("ok"):
                log_line(
                    f"  [{k}/{n}] {row['code']} {nm}  已写 rows={r['rows']} 源={r.get('source','')}"
                )
            else:
                log_line(
                    f"  [{k}/{n}] {row['code']} {nm}  失败 {r.get('err','')[:120]}"
                )
        return r

    rows_list = [row for _, row in u.iterrows()]
    out_dir_str = str(out_dir.resolve())
    if w == 1:
        for row in rows_list:
            results.append(row_task(row))
    elif _WIN32:
        payloads: list[tuple[int, str, str, str, int, str, bool, bool, float]] = []
        for idx, row in enumerate(rows_list):
            payloads.append(
                (
                    idx,
                    out_dir_str,
                    str(row["code"]),
                    str(row.get("name", "")),
                    int(args.lookback),
                    adjust,
                    not bool(args.try_em),
                    bool(args.skip_existing),
                    float(args.em_timeout),
                )
            )
        with ProcessPoolExecutor(max_workers=w) as ex:
            futs = {ex.submit(_mp_one_stock, p): p[0] for p in payloads}
            ordered_mp: list[dict[str, object] | None] = [None] * n
            for fut in as_completed(futs):
                idx, r = fut.result()
                ordered_mp[idx] = r
                with prog:
                    done[0] += 1
                    k = done[0]
                    row = rows_list[idx]
                    nm = str(row.get("name", ""))[:8]
                    if r.get("skipped"):
                        log_line(f"  [{k}/{n}] {row['code']} {nm}  跳过(已存在文件)")
                    elif r.get("ok"):
                        log_line(f"  [{k}/{n}] {row['code']} {nm}  已写 rows={r['rows']} 源={r.get('source','')}")
                    else:
                        log_line(f"  [{k}/{n}] {row['code']} {nm}  失败 {str(r.get('err',''))[:120]}")
            results = [r for r in ordered_mp if r is not None]
    else:
        with ThreadPoolExecutor(max_workers=w) as ex:
            futs = {ex.submit(row_task, row): idx for idx, row in enumerate(rows_list)}
            ordered: list[dict[str, object] | None] = [None] * n
            for fut in as_completed(futs):
                idx = futs[fut]
                ordered[idx] = fut.result()
            results = [r for r in ordered if r is not None]

    log = pd.DataFrame(results)
    log_path = out_dir / "_export_log.csv"
    log.to_csv(log_path, index=False, encoding="utf-8-sig")
    ok = int(log["ok"].sum()) if "ok" in log.columns else 0
    sk = int((log["skipped"] == True).sum()) if "skipped" in log.columns else 0
    print(
        f"完成 耗时: {time.perf_counter() - t0:.1f}s 成功写盘: {ok} 其中跳过: {sk} 明细: {log_path.resolve()}",
        flush=True,
    )


if __name__ == "__main__":
    main()
