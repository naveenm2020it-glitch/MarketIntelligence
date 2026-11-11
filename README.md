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
