# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas"]
# ///
"""
把 data/cancer_deaths.csv 整理成網頁可直接載入的 docs/data.js。

只讀癌症資料（cancer_deaths.csv、cancer_mapping.csv），不碰東華的資料。
輸出格式（window.CANCER_DATA）：
  meta   資料來源、期間、癌別對照、限制說明
  dims   各維度的值（years, counties, cancers, sexes, ageGroups）
  cols   欄式資料，每個陣列長度相同，第 i 筆是 (y[i], c[i], k[i], s[i], a[i], n[i])：
           y/c/k/s/a 是 dims 對應陣列的索引，n 是死亡人數

用法：uv run work/build_data.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.js"

# ---------- 顯示順序與說明文字（呈現用，不影響數字） ----------

COUNTY_ORDER = [
    "臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市",
    "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣",
]
AGE_ORDER = ["0-14", "15-44", "45-64", "65-74", "75-84", "85+"]
SEX_ORDER = ["男", "女"]
OTHER_CANCER = "其他癌症"

# 這兩類由多個 ICD-10 範圍組成，顯示用文字另外寫（其餘癌別直接用 cancer_mapping.csv 的 ICD-10）
ICD_DISPLAY = {
    "肝癌": "C22",
    OTHER_CANCER: "C00–C97 中未單獨列出者（含「其他惡性腫瘤」）",
}

META = {
    "title": "台灣癌症死亡人數與趨勢",
    "source": {
        "name": "衛生福利部統計處「死因統計(138分類)」按縣市及五齡分",
        "agency": "衛生福利部統計處",
        "datasetUrl": "https://data.gov.tw/dataset/46077",
        "downloadUrl": "https://www.mohw.gov.tw/dl-83420-8ac74f07-080f-4b94-8453-ce2a8ee99f8b.html",
        "downloadedOn": "2026-10-12",
        "license": "政府資料開放授權條款－第1版",
    },
    "period": {"from": 2008, "to": 2025, "rocFrom": 97, "rocTo": 114},
    "notes": [
        "只計算死亡人數，未計算死亡率；各縣市人口結構不同，不宜直接以人數比較風險高低。",
        "癌症指死因 138 分類中的惡性腫瘤（代碼 007～043，ICD-10 C00–C97），不含「其餘腫瘤」(D00–D48)。",
        "肝癌 = 肝細胞癌、肝內膽管癌、其他肝及肝內膽管惡性腫瘤三項合計。",
        "前 10 大癌別以 2025 年（民國 114 年）死亡人數排名，「其他惡性腫瘤」(043) 為雜項不參與排名，與其餘癌別合稱「其他癌症」。",
        "原始資料 2008～2010 年（民國 97～99 年）沿用縣市改制前的行政區，臺中縣與臺中市、臺南縣與臺南市、高雄縣與高雄市分開統計，本資料已合併為現行的臺中市、臺南市、高雄市；臺北縣統一稱新北市，桃園縣統一稱桃園市。",
        "縣市代碼對照是從官方對照表的鄉鎮代碼前兩碼推得，並非官方縣市對照表。",
        "年齡組：0-14 歲、15-44 歲、45-64 歲、65-74 歲、75-84 歲、85 歲以上。",
        "年度為西元年，由原始資料的民國年加 1911 轉換。",
    ],
}


def main():
    df = pd.read_csv(ROOT / "data" / "cancer_deaths.csv", encoding="utf-8-sig")
    cmap = pd.read_csv(ROOT / "data" / "cancer_mapping.csv", encoding="utf-8-sig", dtype={"cause": str})

    years = sorted(df.year.unique().tolist())
    if set(df.county) != set(COUNTY_ORDER):
        sys.exit(f"縣市和 COUNTY_ORDER 不一致：{set(df.county) ^ set(COUNTY_ORDER)}")
    if set(df.age_group) != set(AGE_ORDER) or set(df.sex) != set(SEX_ORDER):
        sys.exit("age_group 或 sex 和預期的順序清單不一致")
    if set(df.cancer) != set(cmap.short_name):
        sys.exit("cancer_deaths.csv 和 cancer_mapping.csv 的癌別不一致")

    # 癌別順序：依最後一年死亡人數由高到低，其他癌症放最後
    last = df[df.year == years[-1]].groupby("cancer").deaths.sum()
    named = [c for c in last.sort_values(ascending=False).index if c != OTHER_CANCER]
    cancers = named + [OTHER_CANCER]

    cancer_meta = []
    for name in cancers:
        g = cmap[cmap.short_name == name]
        icd = ICD_DISPLAY.get(name) or "、".join(g.icd10.tolist())
        cancer_meta.append({
            "name": name,
            "icd10": icd,
            "causes": g.cause.tolist(),
            "originalNames": g.original_name.tolist(),
        })

    dims = {"years": years, "counties": COUNTY_ORDER, "cancers": cancers,
            "sexes": SEX_ORDER, "ageGroups": AGE_ORDER}

    def idx(values):
        return {v: i for i, v in enumerate(values)}
    cols = {
        "y": df.year.map(idx(years)).tolist(),
        "c": df.county.map(idx(COUNTY_ORDER)).tolist(),
        "k": df.cancer.map(idx(cancers)).tolist(),
        "s": df.sex.map(idx(SEX_ORDER)).tolist(),
        "a": df.age_group.map(idx(AGE_ORDER)).tolist(),
        "n": df.deaths.astype(int).tolist(),
    }

    payload = {"meta": {**META, "cancers": cancer_meta, "rows": len(df)}, "dims": dims, "cols": cols}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.CANCER_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    size = OUT.stat().st_size
    print(f"輸出 {OUT.relative_to(ROOT)}：{len(df):,} 筆，{size:,} bytes（{size / 1024:.1f} KB）")

    verify(df)


def verify(csv_df: pd.DataFrame):
    """重新讀回 data.js，還原成表格，和 cancer_deaths.csv 逐格比對。"""
    text = OUT.read_text(encoding="utf-8")
    prefix, suffix = "window.CANCER_DATA = ", ";\n"
    if not (text.startswith(prefix) and text.endswith(suffix)):
        sys.exit("data.js 格式不是預期的 window.CANCER_DATA = {...};")
    data = json.loads(text[len(prefix):-len(suffix)])
    d, c = data["dims"], data["cols"]
    lengths = {k: len(v) for k, v in c.items()}
    ok = len(set(lengths.values())) == 1
    back = pd.DataFrame({
        "year": [d["years"][i] for i in c["y"]],
        "county": [d["counties"][i] for i in c["c"]],
        "cancer": [d["cancers"][i] for i in c["k"]],
        "sex": [d["sexes"][i] for i in c["s"]],
        "age_group": [d["ageGroups"][i] for i in c["a"]],
        "deaths": c["n"],
    })

    print("\n核對：")
    print(f"{'✅' if ok else '❌'} 各欄長度一致：{lengths}")
    key = ["year", "county", "cancer", "sex", "age_group"]
    same = back.sort_values(key).reset_index(drop=True).equals(csv_df.sort_values(key).reset_index(drop=True))
    print(f"{'✅' if same else '❌'} 還原後與 cancer_deaths.csv 逐格相同（{len(back):,} 筆）")
    ok &= same

    by_js = back.groupby("year").deaths.sum()
    by_csv = csv_df.groupby("year").deaths.sum()
    year_ok = by_js.equals(by_csv)
    print(f"{'✅' if year_ok else '❌'} 每年加總 = cancer_deaths.csv（{len(by_js)} 年）")
    ok &= year_ok

    for y, expect in [(2025, 54838), (2008, 38913)]:
        got = int(by_js[y])
        print(f"{'✅' if got == expect else '❌'} {y} 年：{got:,}（預期 {expect:,}）")
        ok &= got == expect

    n_cancers = len(data["meta"]["cancers"])
    meta_ok = (data["meta"]["period"]["from"] == min(d["years"]) and data["meta"]["period"]["to"] == max(d["years"])
               and len(d["counties"]) == 22 and n_cancers == len(d["cancers"]) == 11)
    print(f"{'✅' if meta_ok else '❌'} metadata：期間 {data['meta']['period']['from']}–{data['meta']['period']['to']}、22 縣市、{n_cancers} 類癌別")
    ok &= meta_ok

    print("\n每年癌症死亡總數：" + "、".join(f"{y}:{int(n):,}" for y, n in by_js.items()))
    print("\n🎉 核對通過" if ok else "\n⚠️ 核對未通過")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
