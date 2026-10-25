# Building the dictionaries

Sources, downloaded tools, and generated dictionaries are not included in Git.

## Setup

Use Windows, Python 3.14, JDK 21, and Zemberek 0.17.1. Yomitan checks also need Node, Edge, and Yomitan. KOReader checks need sdcv.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock
.venv/Scripts/python scripts/setup_java.py
.venv/Scripts/python -m turkdict fetch
.venv/Scripts/python -m turkdict build
.venv/Scripts/python -m unittest discover -s tests
```

The commands above build a small sample. Continue below for the full dictionaries.

Source URLs and checksums are in `config/sources.lock.json`. Keep the downloaded files: some URLs may change.

## Complete inventory

```powershell
Copy-Item build/latest.json build/macocu-baseline-latest.json
.venv/Scripts/python scripts/import_macocu.py --archive MaCoCu-tr-2.0.xml.zip
.venv/Scripts/python scripts/build_full_inventory.py
.venv/Scripts/python scripts/export_yomitan.py build/yomitan-full-inventory/sample-17386877
.venv/Scripts/python scripts/extend_dictionary_forms.py
.venv/Scripts/python scripts/package_yomitan.py --export-directory build/yomitan-dictionary-plus-corpus --destination build/releases/complete
```

Download MaCoCu-tr-2.0 from the [publisher](https://www.clarin.si/repository/xmlui/handle/11356/1802) before running the importer. Let it finish before building the inventory. Use a new output directory if one already exists.

## Application exports

Use `--help` for each script's options. Set input and output paths explicitly; some defaults refer to older builds.

| Stage | Script | Input |
| --- | --- | --- |
| Compact presentation and frequency | `scripts/refresh_presentation.py` | Completed Yomitan release, canonical definitions, observed inventory |
| Separate source dictionaries | `scripts/separate_dictionaries.py` | Refreshed release and canonical export report |
| Add English-to-Turkish Tureng | `scripts/add_english_tureng.py` | Separated release and pinned Tureng snapshot |
| Remove redundant headings | `scripts/remove_source_headings.py` | Separated Yomitan release |
| Export KOReader | `scripts/export_koreader.py` | Completed Yomitan release |
| Build optional morphology | `scripts/export_koreader_morphology.py` | Matching KOReader base, canonical forms, original mappings |
| Package downloads | `scripts/package_release.py` | Matching application releases |

The morphology exporter needs the forms database, its `dictionary_evidence` table, `mappings.jsonl`, and the matching definitions database. Keep these files if you plan to rebuild.

## Validation

```powershell
node scripts/check_yomitan_browser.mjs PATH_TO_YOMITAN_RELEASE build/browser-validation
.venv/Scripts/python scripts/validate_koreader.py PATH_TO_KOREADER_RELEASE --sdcv PATH_TO_SDCV
.venv/Scripts/python scripts/package_release.py --yomitan PATH_TO_YOMITAN_RELEASE --koreader PATH_TO_KOREADER_RELEASE --destination build/releases/bundles
```

Some checks also need the matching Yomitan source checkout in `research/yomitan`. Browser checks use a separate profile. Keep it to use `--resume` or `--reuse-from`; otherwise, start a fresh import.

Build reports contain absolute paths. Update those paths if you move the data. Re-run checks after changing exported dictionaries.
