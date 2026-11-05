import pandas as pd

INPUT_FILE = "train-00000-of-00001.parquet"
OUTPUT_FILE = "nara_revolutionary_war_pension_microfilm_reels.csv"

df = pd.read_parquet(INPUT_FILE)
print(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

OLD_PREFIX = "https://s3.amazonaws.com/NARAprodstorage/lz/"
NEW_PREFIX = "https://catalog.archives.gov/medialz/"

original_urls = df["pageImageURL"]
df["pageImageURL"] = original_urls.str.replace(OLD_PREFIX, NEW_PREFIX, regex=False)

num_changed = (df["pageImageURL"] != original_urls).sum()
print(f"Changed {num_changed} of {len(df)} pageImageURL values")

df.to_csv(OUTPUT_FILE, index=False)
print(f"Wrote {OUTPUT_FILE}")
