"""Cut the full reel PDFs into the subset PDFs listed in splits.csv.

Full reel PDFs are expected at <pdf_dir>/<NAID>.pdf. Output is written to
<out_dir>/<NAID>_<page_start>-<page_end>.pdf. Rows with cut_type "reel_start"
(administrative front matter) are skipped.

The "reel" column in splits.csv is the microfilm reel number taken from the image
URLs (M804-<reel>-<page>.tif), not a NAID. The reel -> NAID and image-page ->
PDF-page mappings are rebuilt from the source reels CSV.

Page numbers in splits.csv are TIFF filename numbers. They equal the PDF page
number everywhere except reel 1824, where the filenames skip one number, so
this script converts them to the PDF's own page numbers (and names the output
files with those).

Requires: pip install pypdf

Usage:
    python make_subset_pdfs.py --pdf-dir /path/to/reel_pdfs --out-dir /path/to/subsets
    python make_subset_pdfs.py --pdf-dir ... --out-dir ... --threads 32
    python make_subset_pdfs.py --pdf-dir ... --out-dir ... --limit 5   # quick test
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pypdf import PdfReader, PdfWriter

SPLITS_CSV = "splits.csv"
REELS_CSV = "nara_revolutionary_war_pension_microfilm_reels.csv"
SKIP_CUT_TYPE = "reel_start"
URL_RE = re.compile(r"M804-(\d+)/M804-\d+-(\d+)")


def load_reel_maps(path):
    """Return ({reel: NAID}, {reel: {tiff_filename_page: pdf_page}})."""
    naid = {}
    page_map = defaultdict(dict)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            m = URL_RE.search(row["pageImageURL"])
            if not m:
                continue
            reel = int(m.group(1))
            if naid.setdefault(reel, row["NAID"]) != row["NAID"]:
                sys.exit(f"Reel {reel} maps to more than one NAID")
            page_map[reel][int(m.group(2))] = int(row["pageNumber"])
    return naid, page_map


def process_reel(job):
    """Write every pending subset PDF for one reel. Returns (reel, naid, n_written, had_problem)."""
    reel, naid, src, todo = job
    reader = PdfReader(src)
    n = len(reader.pages)
    written, bad = 0, False
    for a, b, out in todo:
        if b > n:
            print(f"{src.name} has {n} pages, need {a}-{b}; skipping", file=sys.stderr)
            bad = True
            continue
        w = PdfWriter()
        for i in range(a - 1, b):  # PDF pages are 1-based here, pypdf is 0-based
            w.add_page(reader.pages[i])
        tmp = out.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            w.write(f)
        tmp.replace(out)  # so an interrupted job never leaves a truncated PDF
        written += 1
    return reel, naid, written, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True, type=Path, help="directory holding <NAID>.pdf full reel files")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--splits", default=SPLITS_CSV)
    ap.add_argument("--reels-csv", default=REELS_CSV)
    ap.add_argument("--threads", type=int, default=1,
                    help="number of parallel workers, one reel each (default 1). Implemented as processes, "
                         "since pypdf is CPU-bound")
    ap.add_argument("--limit", type=int, help="only process the first N reels (for testing)")
    ap.add_argument("--overwrite", action="store_true", help="redo files that already exist")
    args = ap.parse_args()

    naid_of, page_map = load_reel_maps(args.reels_csv)

    by_reel = defaultdict(list)
    with open(args.splits, newline="") as f:
        for row in csv.DictReader(f):
            if row["cut_type"] != SKIP_CUT_TYPE:
                by_reel[int(row["reel"])].append(row)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    failed = []
    jobs = []
    for reel in sorted(by_reel)[: args.limit]:
        naid = naid_of[reel]
        src = args.pdf_dir / f"{naid}.pdf"
        if not src.exists():
            print(f"MISSING {src} (reel {reel})", file=sys.stderr)
            failed.append(reel)
            continue

        todo = []
        for row in by_reel[reel]:
            a = page_map[reel][int(row["page_start"])]
            b = page_map[reel][int(row["page_end"])]
            out = args.out_dir / f"{naid}_{a}-{b}.pdf"
            if out.exists() and not args.overwrite:
                skipped += 1
            else:
                todo.append((a, b, out))
        if not todo:
            continue

        jobs.append((reel, naid, src, todo))

    if args.threads > 1:
        with ProcessPoolExecutor(max_workers=args.threads) as pool:
            results = pool.map(process_reel, jobs)
            for reel, naid, n_written, bad in results:
                written += n_written
                failed += [reel] if bad else []
                print(f"reel {reel} ({naid}): done", flush=True)
    else:
        for job in jobs:
            reel, naid, n_written, bad = process_reel(job)
            written += n_written
            failed += [reel] if bad else []
            print(f"reel {reel} ({naid}): done", flush=True)

    print(f"Wrote {written}, skipped {skipped} existing, {len(set(failed))} reels with problems")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
