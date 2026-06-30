# -*- coding: utf-8 -*-
"""从解压后的 Excel sheet1.xml 提取前 200 行，写入 _top200_stocks.json（供 build_top200_surge_narrative.py 使用）。"""
import json
import xml.etree.ElementTree as ET
from collections import Counter

path = r"c:\Users\admin\Desktop\fenxi\_xlsx_extract\xl\worksheets\sheet1.xml"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
root = ET.parse(path).getroot()
sheetData = root.find("m:sheetData", NS)
rows = []
for row in sheetData.findall("m:row", NS):
    cells = {}
    for c in row.findall("m:c", NS):
        ref = c.get("r") or ""
        col = "".join(filter(str.isalpha, ref))
        is_el = c.find("m:is", NS)
        if is_el is not None:
            ts = is_el.findall(".//m:t", NS)
            cells[col] = "".join((x.text or "") for x in ts)
        else:
            v = c.find("m:v", NS)
            cells[col] = v.text if v is not None and v.text else ""
    rows.append(cells)

header = rows[0]
body = rows[1:201]  # first 200 data rows


def board(code: str) -> str:
    c = code.strip().zfill(6)
    if c.startswith("688"):
        return "科创板"
    if c.startswith("689"):
        return "科创板(存托等)"
    if c.startswith("60"):
        return "沪市主板"
    if c.startswith("68") and len(c) == 6:
        return "科创板"
    if c.startswith("000") or c.startswith("001"):
        return "深市主板"
    if c.startswith("002"):
        return "中小板"
    if c.startswith("003"):
        return "深市主板"
    if c.startswith("300"):
        return "创业板"
    if c.startswith("301"):
        return "创业板"
    if c.startswith("8") or c.startswith("4"):
        return "北交所"
    return "其他"


out = []
for cells in body:
    code = cells.get("A", "")
    out.append(
        {
            "代码": code,
            "名称": cells.get("B", ""),
            "截止日": cells.get("C", ""),
            "趋势强度": cells.get("D", ""),
            "收盘": cells.get("E", ""),
            "趋势成立_宽松": cells.get("F", ""),
            "原因(表内)": cells.get("G", ""),
            "板块": board(code),
        }
    )

out_path = r"c:\Users\admin\Desktop\fenxi\_top200_stocks.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"表头": header, "前200条": out}, f, ensure_ascii=False, indent=2)

bc = Counter(x["板块"] for x in out)
print("WROTE", out_path)
print("BOARD_COUNTS", dict(bc))
print("N", len(out))
print("FIRST3", out[:3])
print("LAST3", out[-3:])
