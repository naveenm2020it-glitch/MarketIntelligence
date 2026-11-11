# MarketIntelligence

Cloud-first NSE market intelligence project. The first milestone is to prove official NSE report downloads and file validation before any AI analysis is introduced.

## Current phase: downloader verification

The GitHub Actions workflow runs on weekdays at 17:00 India time and can also be started manually from **Actions → NSE Daily Downloader → Run workflow**.

It discovers report links from NSE's official report catalogues, downloads the latest available:
- CM UDiFF Common Bhavcopy ZIP
- FO UDiFF Common Bhavcopy ZIP
- F&O Participant-wise Open Interest CSV
- Full Bhavcopy and Security Deliverable Data CSV

The workflow validates ZIP integrity, extracts CSV members, checks CSV headers and data rows, and uploads raw files, extracted files, JSON summaries and logs as a 30-day Actions artifact. It does not commit market data into the repository.

## Run result is the proof

A successful code commit is **not** proof of successful NSE downloads. Verification is complete only when a GitHub Actions run finishes successfully and its artifact contains the four report categories with valid files. NSE may publish reports later than the scheduled time or block automated requests; failures are logged and the run fails visibly.

## Local execution (optional; not required for the user)

```bash
python -m pip install -r requirements.txt
python downloader/nse_downloader.py
```

No desktop installation is required for normal operation; use GitHub Actions from the GitHub mobile website/app.


## Verification trigger
A repository update is configured to start the NSE Daily Downloader workflow on the main branch.


## Reliability controls added

- Transient NSE GET failures (HTTP 429/5xx and connection/read failures) are retried up to five times with exponential backoff and Retry-After support.
- data/history/manifest.json is committed by the workflow and records each report's trading date, source key, byte size, SHA-256 checksum, first/last seen timestamps and observation count.
- A repeated report is identified by report category + trading date + SHA-256, so duplicate detection survives GitHub's temporary runner filesystem.
- Raw report files and extracted CSVs remain in the dated Actions artifact (30-day retention); the committed manifest is durable metadata, not a permanent raw-data archive.


## Permanent daily archive and completeness checks

- Each successful trading-date collection is packaged as one ZIP containing the raw NSE report files, extracted CSVs, JSON validation summary, dated logs and checksum history.
- The ZIP is published as a GitHub Release asset tagged `nse-data-YYYY-MM-DD`. Release assets do not use the 30-day Actions artifact retention window; they remain until manually deleted or repository/account limits or policy changes apply.
- Permanent release publication is gated: all four configured report categories must be present, and their NSE catalogue trading dates must agree. Incomplete or mixed-date collections are not published as verified daily archives; the Actions artifact/logs remain available for diagnosis.
- The downloader summary now explicitly lists missing report categories and trading-date consistency status.

The GitHub Releases archive is a durable project archive, but it is still hosted in the same GitHub repository/account. A separate object-storage backup is a future resilience improvement, not yet configured.
