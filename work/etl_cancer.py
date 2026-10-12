# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas", "openpyxl"]
# ///
"""
台灣癌症死亡人數 ETL

讀 raw/extracted/opendata*.csv（衛福部死因統計 138 分類，按縣市及五齡分），
只保留 cause 007～043（惡性腫瘤），輸出：
  data/cancer_deaths.csv    year, county, cancer, sex, age_group, deaths
  data/county_mapping.csv   縣市代碼對照（代碼是從對照表鄉鎮代碼前兩碼推得）
  data/cancer_mapping.csv   癌別對照（原始 cause 代碼 → 輸出的癌別）

用法：uv run work/etl_cancer.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "extracted"
OUT = ROOT / "data"
LOOKUP = RAW / "對照表.xlsx"

# ---------- 規則（全部寫在這裡，不手打結果） ----------

CANCER_RANGE = ("007", "043")          # 惡性腫瘤；044（其餘腫瘤 D00-D48）不是惡性，排除
RANK_YEAR = 114                        # 用這一年的死亡人數排前 10 大
TOP_N = 10
OTHER_CANCER = "其他癌症"

# 合併規則：原始 cause 代碼 → 合併後的癌別名稱
CANCER_MERGE = {
    "014": "肝癌",   # 肝細胞癌
    "015": "肝癌",   # 肝內膽管癌
    "016": "肝癌",   # 其他肝及肝內膽管惡性腫瘤
}

# 不參加排名、直接歸入「其他癌症」的 cause（雜項桶）
RANK_EXCLUDE = {"043"}   # 其他惡性腫瘤（C26, C39, C51… 的雜項合計）

# 縮短名稱：原始 cause 代碼 → 常用名稱（只有進入前 10 大的才會用到；進榜卻沒有縮短名稱會報錯）
SHORT_NAMES = {
    "021": "肺癌",
    "013": "大腸直腸癌",
    "007": "口腔癌",
    "018": "胰臟癌",
    "027": "乳癌",
    "011": "胃癌",
    "010": "食道癌",
    "031": "攝護腺癌",
    "039": "非何杰金氏淋巴瘤",   # 不叫「淋巴瘤」，因為何杰金氏淋巴瘤（038）在「其他癌症」
}

# 縣市名稱統一：舊名 → 100 年後的名稱
COUNTY_RENAME = {
    "臺中縣": "臺中市",
    "臺南縣": "臺南市",
    "高雄縣": "高雄市",
    "臺北縣": "新北市",
    "桃園縣": "桃園市",
}

# 對照表 county 工作表的欄位位置：(代碼欄, 名稱欄)，依年份區間
COUNTY_SHEET_COLS = {
    "97-99": (6, 7),
    "100-103": (9, 10),
    "104-": (12, 13),
}

def county_period(roc_year: int) -> str:
    return "97-99" if roc_year <= 99 else "100-103" if roc_year <= 103 else "104-"

# 年齡代碼 → 年齡組（01 新生兒、02 嬰兒、03-06 一至四歲、07 起為五歲一組、26 為 100 歲以上）
AGE_GROUPS = {
    "0-14":  [f"{i:02d}" for i in range(1, 9)],     # 01-08：0 歲～14 歲
    "15-44": [f"{i:02d}" for i in range(9, 15)],    # 09-14：15～44 歲
    "45-64": [f"{i:02d}" for i in range(15, 19)],   # 15-18：45～64 歲
    "65-74": ["19", "20"],
    "75-84": ["21", "22"],
    "85+":   ["23", "24", "25", "26"],
}
AGE_TO_GROUP = {code: g for g, codes in AGE_GROUPS.items() for code in codes}


def clean(s) -> str:
    return re.sub(r"[\s　]+", "", str(s))


def read_causes() -> dict[str, tuple[str, str]]:
    """對照表 cause 工作表 → {三位數代碼: (中文名, ICD-10)}（沒有代碼的列是章標題，略過）"""
    d = pd.read_excel(LOOKUP, sheet_name="cause", header=None, dtype=str).iloc[1:]
    d = d[d[0].notna()]
    return {f"{int(t):03d}": (clean(name), clean(icd)) for t, name, icd in zip(d[0], d[1], d[3])}


def read_sex() -> dict[str, str]:
    d = pd.read_excel(LOOKUP, sheet_name="sex", header=None, dtype=str).iloc[1:]
    return {str(k).strip(): clean(v) for k, v in zip(d[0], d[1])}


def read_county_codes() -> dict[str, dict[str, str]]:
    """對照表 county 工作表是鄉鎮市區（4 碼）；取前兩碼推出縣市。
    回傳 {期間: {2 碼縣市代碼: 縣市名稱（當時的名稱）}}"""
    d = pd.read_excel(LOOKUP, sheet_name="county", header=None, dtype=str).iloc[1:]
    result = {}
    for period, (cc, nc) in COUNTY_SHEET_COLS.items():
        names: dict[str, set[str]] = {}
        for code, name in zip(d[cc], d[nc]):
            if pd.isna(code) or pd.isna(name):
                continue
            m = re.match(r"(.+?[縣市])", clean(name))
            names.setdefault(code[:2], set()).add(m.group(1))
        bad = {k: v for k, v in names.items() if len(v) != 1}
        if bad:
            sys.exit(f"縣市代碼前兩碼對應到多個縣市（{period}）：{bad}")
        result[period] = {k: next(iter(v)) for k, v in names.items()}
    return result


def unify_county(name: str) -> str:
    return COUNTY_RENAME.get(name, name).replace("台", "臺")


def load_raw() -> pd.DataFrame:
    files = sorted(RAW.glob("opendata*.csv"), key=lambda p: int(re.search(r"\d+", p.stem).group()))
    if not files:
        sys.exit(f"找不到 {RAW}/opendata*.csv")
    frames = []
    for f in files:
        d = pd.read_csv(f, dtype=str, encoding="utf-8")
        if list(d.columns) != ["year", "county", "cause", "sex", "age_code", "N"]:
            sys.exit(f"{f.name} 欄位不是預期的 year,county,cause,sex,age_code,N：{list(d.columns)}")
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d["N"] = d["N"].astype(int)
    d["year"] = d["year"].astype(int)
    return d


def main():
    raw = load_raw()
    cause_names = read_causes()
    sex_map = read_sex()
    county_codes = read_county_codes()
    print(f"讀入 {len(raw):,} 列，年份 {raw.year.min()}～{raw.year.max()}（民國）")

    # 1. 只保留惡性腫瘤 007～043
    lo, hi = CANCER_RANGE
    df = raw[(raw.cause >= lo) & (raw.cause <= hi)].copy()
    unknown = set(df.cause) - set(cause_names)
    if unknown:
        sys.exit(f"cause 代碼在對照表找不到：{sorted(unknown)}")
    print(f"癌症（cause {lo}～{hi}）：{len(df):,} 列")

    # 2. 癌別：先套合併規則（肝癌），排除雜項桶後依 RANK_YEAR 的死亡人數取前 TOP_N，
    #    進榜的用縮短名稱，其餘（含被排除的 043）歸其他癌症
    df["merged"] = [CANCER_MERGE.get(c, cause_names[c][0]) for c in df.cause]
    ranked = df[(df.year == RANK_YEAR) & ~df.cause.isin(RANK_EXCLUDE)]
    rank = ranked.groupby("merged").N.sum().sort_values(ascending=False)
    top_merged = list(rank.index[:TOP_N])
    merged_to_short = {}
    for c in df.cause.unique():
        merged = CANCER_MERGE.get(c, cause_names[c][0])
        if merged in top_merged:
            short = CANCER_MERGE.get(c) or SHORT_NAMES.get(c)
            if short is None:
                sys.exit(f"{merged}（cause {c}）進入前 {TOP_N} 大，但 SHORT_NAMES 沒有縮短名稱")
            merged_to_short[merged] = short
    top = [merged_to_short[m] for m in top_merged]
    print(f"{RANK_YEAR} 年前 {TOP_N} 大癌別（排除 {sorted(RANK_EXCLUDE)}）：")
    for i, m in enumerate(top_merged, 1):
        print(f"  {i:2d}. {merged_to_short[m]}（{m}）  {rank[m]:,}")
    df["cancer"] = [OTHER_CANCER if (c in RANK_EXCLUDE or m not in top_merged) else merged_to_short[m]
                    for c, m in zip(df.cause, df.merged)]

    cancer_map = pd.DataFrame({"cause": sorted(c for c in cause_names if lo <= c <= hi)})
    cancer_map["original_name"] = cancer_map.cause.map(lambda c: cause_names[c][0])
    cancer_map["icd10"] = cancer_map.cause.map(lambda c: cause_names[c][1])
    cancer_map["short_name"] = cancer_map.cause.map(df.drop_duplicates("cause").set_index("cause").cancer)
    cancer_map["note"] = [("未參加排名，歸入其他癌症" if c in RANK_EXCLUDE else
                           "與其他肝癌代碼合併為肝癌" if c in CANCER_MERGE else "")
                          for c in cancer_map.cause]

    # 3. 縣市：代碼 → 當時名稱 → 統一名稱
    for roc, g in df.groupby("year"):
        period = county_period(roc)
        for code in sorted(g.county.unique()):
            if code not in county_codes[period]:
                sys.exit(f"{roc} 年縣市代碼 {code} 在對照表找不到")
    def county_name(row_year, code):
        return county_codes[county_period(row_year)][code]
    df["county_src"] = [county_name(y, c) for y, c in zip(df.year, df.county)]
    df["county_unified"] = df.county_src.map(unify_county)

    rows = []
    for period, codes in county_codes.items():
        for code, name in sorted(codes.items()):
            note = f"{name}→{unify_county(name)}" if unify_county(name) != name else ""
            rows.append({"period_roc": period, "county_code": code,
                         "county_name_in_source": name,
                         "county_unified": unify_county(name), "note": note,
                         "code_source": "縣市代碼是從對照表.xlsx county 工作表的鄉鎮代碼前兩碼推得，非官方縣市對照表"})
    county_map = pd.DataFrame(rows)
    # 只列資料裡實際出現過的 (期間, 代碼)
    used = {(county_period(y), c) for y, c in zip(df.year, df.county)}
    county_map = county_map[[(p, c) in used for p, c in zip(county_map.period_roc, county_map.county_code)]]

    # 4. 性別、年齡、年度
    df["sex"] = df.sex.map(sex_map)
    if df.sex.isna().any():
        sys.exit("sex 有無法對照的值")
    df["age_group"] = df.age_code.map(AGE_TO_GROUP)
    if df.age_group.isna().any():
        sys.exit(f"age_code 無法歸組：{sorted(df[df.age_group.isna()].age_code.unique())}")
    df["year_ad"] = df.year + 1911

    out = (df.groupby(["year_ad", "county_unified", "cancer", "sex", "age_group"], as_index=False).N.sum()
             .rename(columns={"year_ad": "year", "county_unified": "county", "N": "deaths"}))
    age_order = {g: i for i, g in enumerate(AGE_GROUPS)}
    cancer_order = {n: i for i, n in enumerate(top + [OTHER_CANCER])}
    out = out.sort_values(
        ["year", "county", "cancer", "sex", "age_group"],
        key=lambda s: s.map(cancer_order) if s.name == "cancer"
        else s.map(age_order) if s.name == "age_group" else s,
    ).reset_index(drop=True)

    OUT.mkdir(exist_ok=True)
    out.to_csv(OUT / "cancer_deaths.csv", index=False, encoding="utf-8-sig")
    county_map.to_csv(OUT / "county_mapping.csv", index=False, encoding="utf-8-sig")
    cancer_map.to_csv(OUT / "cancer_mapping.csv", index=False, encoding="utf-8-sig")
    print(f"\n輸出 data/cancer_deaths.csv：{len(out):,} 列，deaths 合計 {out.deaths.sum():,}")
    print(f"輸出 data/county_mapping.csv：{len(county_map)} 列")
    print(f"輸出 data/cancer_mapping.csv：{len(cancer_map)} 列")


if __name__ == "__main__":
    main()
