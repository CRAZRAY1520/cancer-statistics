# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas"]
# ///
"""
驗證 data/cancer_deaths.csv 是否和原始檔一致。

不使用 work/etl_cancer.py 的任何邏輯：直接重讀 raw/extracted/opendata*.csv，
縣市對應只用 data/county_mapping.csv。

用法：uv run scripts/validate_cancer.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "extracted"
OUT = pd.read_csv(ROOT / "data" / "cancer_deaths.csv", encoding="utf-8-sig")
CMAP = pd.read_csv(ROOT / "data" / "county_mapping.csv", encoding="utf-8-sig", dtype={"county_code": str})

COLS = ["year", "county", "cancer", "sex", "age_group", "deaths"]
EXPECTED_YEARS = list(range(2008, 2026))
ok = True


def check(cond: bool, msg: str):
    global ok
    ok &= bool(cond)
    print(f"{'✅' if cond else '❌'} {msg}")


# 原始檔：cause 007～043
files = sorted(RAW.glob("opendata*.csv"), key=lambda p: int(re.search(r"\d+", p.stem).group()))
raw = pd.concat([pd.read_csv(f, dtype=str, encoding="utf-8") for f in files], ignore_index=True)
raw["N"] = raw.N.astype(int)
raw["year"] = raw.year.astype(int) + 1911
raw = raw[(raw.cause >= "007") & (raw.cause <= "043")]

# 1. 欄位、空值、負數
check(list(OUT.columns) == COLS, f"欄位為 {COLS}")
check(OUT.isna().sum().sum() == 0, "沒有空值")
check((OUT.deaths >= 0).all(), "沒有負數")
check((OUT.deaths == OUT.deaths.round()).all(), "deaths 都是整數")
check(not OUT.duplicated(["year", "county", "cancer", "sex", "age_group"]).any(), "沒有重複的 (year, county, cancer, sex, age_group)")

# 2. 年份、縣市
check(sorted(OUT.year.unique()) == EXPECTED_YEARS, f"年份 2008～2025 齊全（{OUT.year.nunique()} 年）")
check(OUT.county.nunique() == 22, f"縣市剛好 22 個（實際 {OUT.county.nunique()}）")
check(not OUT.county.str.contains("台").any(), "縣市名稱一律用「臺」")
legacy = {"臺中縣", "臺南縣", "高雄縣", "臺北縣", "桃園縣"}
check(not (set(OUT.county) & legacy), "輸出沒有舊縣名（臺中縣、臺南縣、高雄縣、臺北縣、桃園縣）")

# 3. 每一年總和
raw_year = raw.groupby("year").N.sum()
out_year = OUT.groupby("year").deaths.sum()
diff = (raw_year - out_year).fillna(raw_year)
check((diff == 0).all() and list(raw_year.index) == list(out_year.index),
      "每年 deaths 總和 = 原始檔 cause 007～043 的 N 總和")
if not (diff == 0).all():
    print(diff[diff != 0].to_string())

# 4. 每個縣市（合併前 → 合併後）每年總和
m = CMAP.assign(period=CMAP.period_roc)
def period_of(roc):
    return "97-99" if roc <= 99 else "100-103" if roc <= 103 else "104-"
r = raw.copy()
r["period_roc"] = [period_of(y - 1911) for y in r.year]
r = r.merge(m[["period_roc", "county_code", "county_unified"]],
            left_on=["period_roc", "county"], right_on=["period_roc", "county_code"], how="left")
check(r.county_unified.notna().all(), "原始檔每個縣市代碼都在 county_mapping.csv 找得到")
before = r.groupby(["year", "county_unified"]).N.sum()
after = OUT.groupby(["year", "county"]).deaths.sum()
after.index.names = before.index.names
cmp = pd.concat([before.rename("原始"), after.rename("輸出")], axis=1).fillna(0).astype(int)
bad = cmp[cmp["原始"] != cmp["輸出"]]
check(bad.empty, f"每個縣市每年合併前後人數一致（{len(cmp)} 組）")
if not bad.empty:
    print(bad.head(10).to_string())
# 合併縣市另外列出
for city in ["臺中市", "臺南市", "高雄市", "新北市", "桃園市"]:
    c = cmp.xs(city, level="county_unified")
    check((c["原始"] == c["輸出"]).all(), f"{city}：2008～2025 每年一致")

# 5. 癌別：依 cancer_mapping.csv 把原始檔對應後，每年每個癌別的人數要一致
AMAP = pd.read_csv(ROOT / "data" / "cancer_mapping.csv", encoding="utf-8-sig", dtype={"cause": str})
check(sorted(AMAP.cause) == [f"{i:03d}" for i in range(7, 44)], "cancer_mapping.csv 涵蓋 cause 007～043 共 37 個代碼，沒有 044")
check(AMAP.loc[AMAP.cause == "043", "short_name"].eq("其他癌症").all(), "043（其他惡性腫瘤）歸入其他癌症")
check(set(AMAP.loc[AMAP.cause.isin(["014", "015", "016"]), "short_name"]) == {"肝癌"}, "014、015、016 合併為肝癌")
check(AMAP.short_name.nunique() == 11, f"癌別為前 10 大 + 其他癌症共 11 類（實際 {AMAP.short_name.nunique()}）")
check(set(AMAP.short_name) == set(OUT.cancer), "輸出的癌別和 cancer_mapping.csv 一致")
rc = raw.merge(AMAP[["cause", "short_name"]], on="cause", how="left")
check(rc.short_name.notna().all(), "原始檔每個癌症 cause 都在 cancer_mapping.csv 找得到")
b = rc.groupby(["year", "short_name"]).N.sum()
a = OUT.groupby(["year", "cancer"]).deaths.sum()
a.index.names = b.index.names
cc = pd.concat([b.rename("原始"), a.rename("輸出")], axis=1).fillna(0).astype(int)
check((cc["原始"] == cc["輸出"]).all(), f"每年每個癌別的人數與原始檔一致（{len(cc)} 組）")

# 6. 其他維度總和
check(OUT.sex.isin(["男", "女"]).all(), "sex 只有 男、女")
check(set(OUT.age_group) == {"0-14", "15-44", "45-64", "65-74", "75-84", "85+"}, "age_group 為 6 組")
last = OUT[OUT.year == max(EXPECTED_YEARS)].groupby("cancer").deaths.sum().sort_values(ascending=False)
print(f"\n{max(EXPECTED_YEARS)} 年（民國 {max(EXPECTED_YEARS) - 1911}）各癌別死亡人數：")
for name, n in last.items():
    print(f"  {name:　<10} {n:>7,}")

# 7. 每年癌症死亡總數
print("\n每年癌症死亡總數（請和衛福部公布的「惡性腫瘤」死亡人數核對）：")
print(f"{'西元':>6} {'民國':>5} {'死亡人數':>10}")
for y, n in out_year.items():
    print(f"{y:>6} {y - 1911:>5} {n:>10,}")

print("\n🎉 全部通過" if ok else "\n⚠️ 有項目未通過")
sys.exit(0 if ok else 1)
