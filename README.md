# reel_split

Splitting NARA's Revolutionary War pension microfilm reels (M804) into smaller PDFs.

Each reel is one PDF of roughly 1,000 pages, which is too big to process. The pension files on a reel are concatenated, and each file starts with a landscape "jacket" card. This repo is an exploratory exercise to find out whether **image dimensions alone** can locate those jackets, and so give us file boundaries to cut at. The target is pieces of at most ~100 pages.

The work builds on itself, so read it in this order:

1. [`get_image_dimensions.py`](get_image_dimensions.py): get width and height for every page
2. [`landscape_analysis.ipynb`](landscape_analysis.ipynb): what do the landscape pages look like?
3. [`cover_candidates.ipynb`](cover_candidates.ipynb): turn that into a rule and hand-label a sample to test it
4. [`determine_splits.ipynb`](determine_splits.ipynb): cut the reels and write the final split table

## Headline results

- 75,804 page images across 74 reels. 4.9% are landscape, and most of those are file jackets.
- A dimensions-only rule ("Tier A") finds jackets with about 96% precision in a blind hand-labeled sample (48 of 50).
- Cutting at Tier A covers alone gives 2,434 pieces, but 61 are still over 100 pages. A second, looser box ("Tier B+") adds 39 cuts. The result is **2,473 PDFs, the largest 199 pages, 39 still over 100**. We accepted that without further splitting.
- The final table is [`splits.csv`](splits.csv).

## Data sources

| File | Where it came from |
|---|---|
| `train-00000-of-00001.parquet` | Downloaded from the Hugging Face dataset [`RevolutionCrossroads/nara_revolutionary_war_pension_microfilm_reels`](https://huggingface.co/datasets/RevolutionCrossroads/nara_revolutionary_war_pension_microfilm_reels). One row per page image. It has the NARA catalog ID (`NAID`), page number, image URL, and any extracted or transcribed text. |
| `nara_revolutionary_war_pension_microfilm_reels.csv` | The parquet converted to CSV by [`parquet_to_csv.py`](parquet_to_csv.py). That script also rewrites each `pageImageURL` from the old `s3.amazonaws.com/NARAprodstorage/lz/` prefix to `catalog.archives.gov/medialz/`. |
| `nara_reel_tif_image_dimensions.csv` | Output of `get_image_dimensions.py` (step 1). |
| `labeling/` | Output of `cover_candidates.ipynb` (step 3), plus the hand labels. |
| `tier_b_plus_cuts.csv`, `splits.csv` | Outputs of `determine_splits.ipynb` (step 4). |

### Reel numbers versus NAIDs

The `reel` column in `splits.csv` is **not** a NAID. It is the M804 microfilm reel number (for example `1525`), parsed from the image URL:

```
.../M804/M804-1525/M804-1525-0009.tif   ->  reel 1525, page 9
```

The notebooks extract it with the regex `M804-(\d+)/M804-\d+-(\d+)`. In the source CSV, a `NAID` identifies a *file unit* (one catalog record, for example "Lara, James - Larkcom, Paul"), and one reel's pages are spread across many NAIDs. So `reel` and `NAID` are different identifiers, and you can't map between them with the split table alone. The source CSV has both, since each row carries a NAID and a `pageImageURL`.

## Step 1: `get_image_dimensions.py`

Reads `nara_revolutionary_war_pension_microfilm_reels.csv` and writes `nara_reel_tif_image_dimensions.csv` with columns `pageObjectId, pageImageURL, height, width`.

The TIFFs are about 30 MB each, so downloading all 75,804 of them is out of the question. Instead the script uses `fsspec` HTTP range requests and Pillow to read only the TIFF header, with a 64 KB block size. fsspec's default block size is 5 MB, which would pull far more than needed. It runs threaded, retries with backoff, and writes rows as they finish so partial output survives a crash.

It was run on an HPC cluster through [`get_image_dimensions.job`](get_image_dimensions.job) (an SGE job with 12 threads). [`get_image_dimensions.log`](get_image_dimensions.log) shows two runs. The first was a 120-image test, which took 6 seconds. The second was the full 75,804 images, which took about 27 minutes. Everything downstream depends on this CSV.

Usage: `python get_image_dimensions.py --threads 16`, or add `--limit 20` for a quick test.

## Step 2: `landscape_analysis.ipynb`

*Question: how many pages are landscape, and is anything else distinctive about them?*

Input is the dimensions CSV. The notebook derives `reel`, `page`, aspect ratio, megapixels and orientation.

Findings:

- **3,731 of 75,804 pages (4.9%) are landscape**, 2 are square, and the rest are portrait.
- The aspect ratio is **bimodal**, with a portrait peak at about 0.77 and a landscape peak at about 1.42. Landscape pages are a distinct kind of page, not rotated portraits.
- They are **bigger**: a median of 28.7 MP against 20.5 MP for portrait.
- They occur in **all 74 reels** at a steady 3-8% rate and are spread throughout each reel. 81% of landscape runs are a single page.
- A visual sample of 5 pages (saved as [`landscape_sample.png`](landscape_sample.png)) showed three **pension file jackets** (state, veteran's name, `S.`/`R.` claim number) and two wide interior documents.
- Gaps between consecutive landscape pages have a median of 16 pages, which is plausible for file lengths, but 26% of gaps are 3 pages or fewer. So landscape alone is a hint, not a clean boundary.
- 64% of landscape pages fall in a tight **6000-6700 x 4300-4700 px** band, probably the jackets.
- About 77 extreme images turned up (very small, very large, or very wide). One URL has a stray trailing letter (`-1069m.tif`), which the regex tolerates.

## Step 3: `cover_candidates.ipynb`

*Question: can dimensions alone find the cover cards?*

The notebook turns the size band into a tiered rule, draws a sample, and has a person label it blind.

| Tier | Rule | Pages |
|---|---|---|
| A | landscape, 6000-6700 x 4300-4700 px, aspect 1.38-1.47 | 2,362 |
| B | any other landscape page | 1,369 |
| C | portrait (the control group) | 72,073 |

It also notes that positions 1-2 of each reel are landscape 55% and 72% of the time. These are probably microfilm targets or title frames, not jackets.

**The labeling workflow.** The notebook draws a stratified random sample of 120 pages (50 A, 40 B, 30 C), with some deliberately taken from the first 5 positions of reels. It downloads full TIFFs, shrinks them to 1000 px thumbnails, and builds a self-contained labeling page. Sample IDs are neutral, so the tier is not revealed while labeling. Everything lands in [`labeling/`](labeling/):

- `thumbs/`: the 120 thumbnails (`s000.jpg` and so on)
- `label.html`: a browser labeling tool. Keys are `c` (cover), `f` (front_matter), `n` (not_cover) and `u` (unsure). It exports a CSV.
- `labels_template.csv`: a blank sheet for people who would rather use a spreadsheet
- `sample_key.csv`: the hidden tier and dimensions for each sample ID
- `labels.csv`: the hand labels exported from `label.html`

Results:

| Tier | Labeled | Cover | Front matter | Not cover |
|---|---|---|---|---|
| A | 50 | 48 | 2 | 0 |
| B | 40 | 6 | 8 | 26 |
| C | 30 | 0 | 7 | 23 |

Findings:

- **Tier A is a reliable cover detector.** Precision is about 96%, which is roughly 2,270 file boundaries, or about 30 per reel.
- **Both Tier A errors were page 2 of their reel**, so excluding positions 1-2 removes them.
- **Tier B is mostly noise.** Only 6 of 40 were covers, and adding all of B would drop precision to about 66%.
- **All 6 Tier B covers sit just outside the Tier A box.** They miss by a small margin on width, height or aspect, so the band is slightly too tight.
- **Recall is not established.** The ~92% estimate rests on finding 0 covers in 30 portrait pages. Treat it as an upper bound.

## Step 4: `determine_splits.ipynb`

*Question: if we cut at covers, how big are the pieces, and what else do we need to get under ~100 pages?*

1. **Cut at Tier A**, skipping reel positions 1-2. This gives 2,360 usable cuts and **2,434 pieces**, with a median of 24 pages. 61 pieces (2.5%) are over 100 pages, in 39 of the 74 reels. The largest is 258 pages. Together these oversize pieces hold 10.9% of all pages.
2. **Front matter lands in "piece 0" of each reel.** It is typically 16-17 pages. Six reels have a larger piece 0 (27-95 pages), probably because their first jacket is far from the start.
3. **Split the oversize pieces with a "Tier B+" near-miss box.** This is width 6250-6800, height 4550-4850, aspect 1.36-1.45, fitted to the 6 labeled Tier B covers. It separates the 40 labeled Tier B pages perfectly, but that is in-sample (the box was fitted to those same pages), so it is not a validated rule. It matches 200 pages overall, close to the ~205 covers estimated in Tier B. For each oversize piece the notebook greedily picks the candidate that leaves the most balanced halves, and recurses on halves still over the cap.
4. **Result:**

   | Cuts used | PDFs | Over 100 pages | Largest |
   |---|---|---|---|
   | Tier A only | 2,434 | 61 | 258 |
   | + Tier B+ (used) | 2,473 | 39 | 199 |
   | + any landscape page | 2,494 | 20 | 199 |

   Using any landscape page would leave fewer oversize pieces, but it brings in the noisy Tier B pages. We accepted the Tier B+ result and stopped.
5. **Spot-check.** The 39 chosen Tier B+ cuts are unlabeled. They are written to [`tier_b_plus_cuts.csv`](tier_b_plus_cuts.csv) with links to the TIFFs. The notebook builds a thumbnail collage of them in `labeling/cut_thumbs/` (named `<reel>_<page>.jpg`) so you can eyeball whether they look like jackets.

### Output: `splits.csv`

One row per PDF to produce, 2,473 rows covering 74 reels. Every page lands in exactly one PDF, and the notebook asserts this.

| Column | Meaning |
|---|---|
| `reel` | M804 reel number (see "Reel numbers versus NAIDs" above) |
| `split_id` | 1-based index of the PDF within its reel |
| `page_start`, `page_end` | Inclusive page numbers taken from the image filenames. These can have gaps, so a PDF should include whichever pages of that reel fall in the range. |
| `n_pages` | Number of pages in the PDF |
| `cut_type` | How the PDF's first page was chosen: `reel_start` (74, the front matter before the first jacket), `tier_a` (2,360) or `tier_b_plus` (39) |

## Caveats and open questions

- **Recall is unmeasured.** We know Tier A is precise, but not how many covers it misses among the portrait pages.
- **Tier B+ is unvalidated.** Its 39 cuts have not been labeled.
- **39 PDFs are still over 100 pages**, with the largest at 199. Possible next steps are labeling the pages in long gaps between Tier A hits, or using the `extractedText` column in the source CSV as an independent cover signal.
- **Pieces are not exact files.** A piece starts at a jacket, but some files have no landscape jacket and some have several, so a PDF may hold more or less than one pension file.
- Page positions come from the dimensions CSV, which has no file sizes, so piece sizes are in pages.

## Environment

The notebooks use `pandas`, `matplotlib`, `numpy`, `requests` and `Pillow`. The dimension script also needs `fsspec` and `aiohttp`. A local `.venv/` is not tracked.
