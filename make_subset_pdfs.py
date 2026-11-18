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

How it runs: each worker takes one reel, opens it once, and writes all of that
reel's pieces. Opening is the slow part (several minutes per ~12 GB reel on the
HPC) while copying a piece takes seconds, so a reel must never be opened more
than once. Pages are copied with pikepdf (the qpdf C++ library), which copies
the scanned images byte for byte without holding the whole reel in memory.

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


def process_reel(src, pieces):
    """Open src once and write each (first, last, out) piece (1-based, inclusive pages).

    Returns (seconds to open, [(out name, error or None), ...]).
    """
    t0 = time.time()
    results = []
    with pikepdf.open(src) as reel:
        t_open = time.time() - t0
        print(f"opened {src.name} in {t_open:.0f}s", flush=True)
        n = len(reel.pages)
        for first, last, out in pieces:
            if last > n:
                results.append((out.name, f"{src.name} has {n} pages, need {first}-{last}"))
                continue
            subset = pikepdf.new()
            subset.pages.extend(reel.pages[first - 1 : last])
            tmp = out.with_suffix(".tmp")
            # Copy the image streams as-is: no decompressing and recompressing.
            subset.save(tmp, stream_decode_level=pikepdf.StreamDecodeLevel.none)
            tmp.replace(out)  # so an interrupted job never leaves a truncated PDF
            results.append((out.name, None))
    return t_open, results


def plan_jobs(args):
    """Return ({src: [(first, last, out), ...]} still to do, n skipped, [missing reels])."""
    naid_of, page_map = load_reel_maps(args.dims_csv)

    by_reel = defaultdict(list)
    with open(args.splits, newline="") as f:
        for row in csv.DictReader(f):
            if row["cut_type"] != SKIP_CUT_TYPE:
                by_reel[int(row["reel"])].append(row)

    jobs, skipped, missing = {}, 0, []
    for reel in sorted(by_reel)[: args.limit]:
        naid = naid_of[reel]
        src = args.pdf_dir / f"{naid}.pdf"
        if not src.exists():
            print(f"MISSING {src} (reel {reel})", file=sys.stderr)
            missing.append(reel)
            continue
        pieces = []
        for row in by_reel[reel]:
            first = page_map[reel][int(row["page_start"])]
            last = page_map[reel][int(row["page_end"])]
            out = args.out_dir / f"{naid}_{first}-{last}.pdf"
            if out.exists() and not args.overwrite:
                skipped += 1
            else:
                pieces.append((first, last, out))
        if pieces:
            jobs[src] = pieces
    return jobs, skipped, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True, type=Path, help="directory holding <NAID>.pdf full reel files")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--splits", default=SPLITS_CSV)
    ap.add_argument("--dims-csv", default=DIMS_CSV)
    ap.add_argument("--threads", type=int, default=1,
                    help="number of parallel worker processes, one reel each (default 1)")
    ap.add_argument("--limit", type=int, help="only process the first N reels (for testing)")
    ap.add_argument("--overwrite", action="store_true", help="redo files that already exist")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    jobs, skipped, missing = plan_jobs(args)
    n_pieces = sum(len(p) for p in jobs.values())
    print(f"{len(jobs)} reels / {n_pieces} PDFs to write, {skipped} already exist, "
          f"{len(missing)} reels missing, {args.threads} workers", flush=True)

    start = time.time()
    written, failed = 0, []
    with ProcessPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(process_reel, src, pieces): src for src, pieces in jobs.items()}
        for i, future in enumerate(as_completed(futures), 1):
            src = futures[future]
            try:
                t_open, results = future.result()
            except Exception as e:  # e.g. an unreadable reel; rerun to retry just the failures
                failed += [out.name for _, _, out in jobs[src]]
                print(f"[reel {i}/{len(jobs)} {time.time() - start:.0f}s] {src.name} FAILED: {e}", flush=True)
                continue
            for name, error in results:
                if error:
                    failed.append(name)
                    print(f"  {name} FAILED: {error}", flush=True)
                else:
                    written += 1
            ok = sum(error is None for _, error in results)
            print(f"[reel {i}/{len(jobs)} {time.time() - start:.0f}s] {src.name}: "
                  f"{ok}/{len(results)} written (open took {t_open:.0f}s)", flush=True)

    print(f"Wrote {written}, skipped {skipped} existing, {len(failed)} failed, "
          f"{len(missing)} reels missing a PDF")
    sys.exit(1 if failed or missing else 0)


if __name__ == "__main__":
    main()
