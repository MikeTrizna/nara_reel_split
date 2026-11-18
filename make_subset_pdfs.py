"""Cut the full reel PDFs into the subset PDFs listed in splits.csv.

Full reel PDFs are expected at <pdf_dir>/<NAID>.pdf. Output is written to
<out_dir>/<NAID>_<page_start>-<page_end>.pdf. Rows with cut_type "reel_start"
(administrative front matter) are skipped.

The "reel" column in splits.csv is the microfilm reel number taken from the image
URLs (M804-<reel>-<page>.tif), not a NAID. nara_reel_image_dimensions.csv (made by
add_naid.py) supplies the NAID and the PDF page number (pageNumber) for each image.

Page numbers in splits.csv are TIFF filename numbers. They equal the PDF page
number everywhere except reel 1824, where the filenames skip one number, so
this script looks each one up to get the PDF's own page number (and names the
output files with those).

How it runs: every output PDF is its own task, so all workers stay busy even
when only one or two reels are left. Tasks are started largest first so a big
piece doesn't end up running alone at the end. Pages are copied with pikepdf
(the qpdf C++ library), which copies the scanned images byte for byte without
holding the whole reel in memory.

Requires: pip install pikepdf

Usage:
    python make_subset_pdfs.py --pdf-dir /path/to/reel_pdfs --out-dir /path/to/subsets
    python make_subset_pdfs.py --pdf-dir ... --out-dir ... --threads 12
    python make_subset_pdfs.py --pdf-dir ... --out-dir ... --limit 2   # quick test
"""
import argparse
import csv
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pikepdf

SPLITS_CSV = "splits.csv"
DIMS_CSV = "nara_reel_image_dimensions.csv"
SKIP_CUT_TYPE = "reel_start"
URL_RE = re.compile(r"M804-(\d+)/M804-\d+-(\d+)")


def load_reel_maps(path):
    """Return ({reel: NAID}, {reel: {tiff_filename_page: pdf_page}})."""
    naid = {}
    page_map = defaultdict(dict)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            m = URL_RE.search(row["pageImageURL"])
            reel = int(m.group(1))
            naid[reel] = row["NAID"]
            page_map[reel][int(m.group(2))] = int(row["pageNumber"])
    return naid, page_map


def write_subset(src, first, last, out):
    """Copy pages first..last (1-based, inclusive) of src into out. Returns out."""
    with pikepdf.open(src) as reel:
        n = len(reel.pages)
        if last > n:
            raise ValueError(f"{src.name} has {n} pages, need {first}-{last}")
        subset = pikepdf.new()
        subset.pages.extend(reel.pages[first - 1 : last])
        tmp = out.with_suffix(".tmp")
        subset.save(tmp)
    tmp.replace(out)  # so an interrupted job never leaves a truncated PDF
    return out


def plan_tasks(args):
    """Build the list of (src, first, last, out) still to do, plus skip/missing counts."""
    naid_of, page_map = load_reel_maps(args.dims_csv)

    by_reel = defaultdict(list)
    with open(args.splits, newline="") as f:
        for row in csv.DictReader(f):
            if row["cut_type"] != SKIP_CUT_TYPE:
                by_reel[int(row["reel"])].append(row)

    tasks, skipped, missing = [], 0, []
    for reel in sorted(by_reel)[: args.limit]:
        naid = naid_of[reel]
        src = args.pdf_dir / f"{naid}.pdf"
        if not src.exists():
            print(f"MISSING {src} (reel {reel})", file=sys.stderr)
            missing.append(reel)
            continue
        for row in by_reel[reel]:
            first = page_map[reel][int(row["page_start"])]
            last = page_map[reel][int(row["page_end"])]
            out = args.out_dir / f"{naid}_{first}-{last}.pdf"
            if out.exists() and not args.overwrite:
                skipped += 1
            else:
                tasks.append((src, first, last, out))

    # Largest pieces first, so the slowest ones don't straggle at the end.
    tasks.sort(key=lambda t: t[2] - t[1], reverse=True)
    return tasks, skipped, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True, type=Path, help="directory holding <NAID>.pdf full reel files")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--splits", default=SPLITS_CSV)
    ap.add_argument("--dims-csv", default=DIMS_CSV)
    ap.add_argument("--threads", type=int, default=1,
                    help="number of parallel worker processes, one output PDF each (default 1)")
    ap.add_argument("--limit", type=int, help="only process the first N reels (for testing)")
    ap.add_argument("--overwrite", action="store_true", help="redo files that already exist")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tasks, skipped, missing = plan_tasks(args)
    print(f"{len(tasks)} PDFs to write, {skipped} already exist, {len(missing)} reels missing, "
          f"{args.threads} workers", flush=True)

    start = time.time()
    written, failed = 0, []
    with ProcessPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(write_subset, *task): task for task in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            out = futures[future][3]
            try:
                future.result()
                written += 1
                status = "ok"
            except Exception as e:  # report and keep going; rerun to retry just the failures
                failed.append(out.name)
                status = f"FAILED: {e}"
            print(f"[{i}/{len(tasks)} {time.time() - start:.0f}s] {out.name} {status}", flush=True)

    print(f"Wrote {written}, skipped {skipped} existing, {len(failed)} failed, "
          f"{len(missing)} reels missing a PDF")
    sys.exit(1 if failed or missing else 0)


if __name__ == "__main__":
    main()
