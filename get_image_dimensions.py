"""One-time script: get width/height of each TIFF in the CSV without downloading the whole file.

fsspec fetches only the bytes Pillow asks for (HTTP range requests), and Pillow reads
the dimensions from the TIFF header.

Requires: pip install pillow fsspec aiohttp

Usage:
    python get_image_dimensions.py --threads 16
    python get_image_dimensions.py --threads 2 --limit 20   # quick test
"""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import fsspec
from PIL import Image

INPUT_CSV = "nara_revolutionary_war_pension_microfilm_reels.csv"
OUTPUT_CSV = "image_dimensions.csv"
MAX_RETRIES = 4

Image.MAX_IMAGE_PIXELS = None  # these are big scans; we never decode pixels, so skip the warning

# IMPORTANT: fsspec's default block size is 5 MB, which would download 5 MB per image.
# A 64 KB block is plenty for a TIFF header.
fs = fsspec.filesystem("http", block_size=64 * 1024)


def get_dimensions(url):
    """Return (width, height) of the image at url, downloading only the header bytes."""
    with fs.open(url, mode="rb", cache_type="readahead") as f:
        return Image.open(f).size


def process_row(row):
    """Look up one image, retrying on errors. Returns an output row (blank dims on failure)."""
    url = row["pageImageURL"]
    width = height = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            width, height = get_dimensions(url)
            break
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"FAILED {url}: {e}", flush=True)
            else:
                time.sleep(2 ** attempt)  # back off: 2s, 4s, 8s
    return {
        "pageObjectId": row["pageObjectId"],
        "pageImageURL": url,
        "height": height,
        "width": width,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threads", type=int, default=8, help="number of worker threads")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N rows (for testing)")
    args = parser.parse_args()

    # Load rows that have an image URL
    csv.field_size_limit(10**9)  # the CSV has some very large text fields
    with open(INPUT_CSV, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["pageImageURL"]]
    if args.limit:
        rows = rows[:args.limit]
    print(f"Processing {len(rows)} images with {args.threads} threads", flush=True)

    # Fetch in parallel; write each result as it finishes so partial output exists if the job dies
    with open(OUTPUT_CSV, "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=["pageObjectId", "pageImageURL", "height", "width"])
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = [pool.submit(process_row, r) for r in rows]
            for n, future in enumerate(as_completed(futures), start=1):
                writer.writerow(future.result())
                if n % 1000 == 0:
                    print(f"{n}/{len(rows)} done", flush=True)
                    out.flush()

    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
