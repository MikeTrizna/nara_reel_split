"""Add a NAID column to nara_reel_tif_image_dimensions.csv.

Adds NAID and pageNumber (the PDF page). Joins on pageObjectId using nara_revolutionary_war_pension_microfilm_reels.csv
and writes nara_reel_image_dimensions.csv.
"""
import pandas as pd

DIMS_CSV = "nara_reel_tif_image_dimensions.csv"
REELS_CSV = "nara_revolutionary_war_pension_microfilm_reels.csv"
OUT_CSV = "nara_reel_image_dimensions.csv"

dims = pd.read_csv(DIMS_CSV)
reels = pd.read_csv(REELS_CSV, usecols=["NAID", "pageObjectId", "pageNumber"]).drop_duplicates()

merged = dims.merge(reels, on="pageObjectId", how="left", validate="many_to_one")
missing = merged["NAID"].isna().sum()
if missing:
    print(f"WARNING: {missing} rows have no NAID match")

cols = ["NAID", "pageNumber"] + [c for c in merged.columns if c not in ("NAID", "pageNumber")]
merged[cols].to_csv(OUT_CSV, index=False)
print(f"Wrote {len(merged)} rows to {OUT_CSV}")
