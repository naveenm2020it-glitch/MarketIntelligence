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


## Scheduled archive monitoring

- The NSE downloader runs at 17:00 Asia/Kolkata, Monday-Friday.
- The archive monitor runs at 17:50 Asia/Kolkata, Monday-Friday. It checks that the scheduled downloader run completed successfully and downloads the current-day GitHub Release ZIP to verify ZIP integrity and the included validation summary.
- A manual monitor run can use `archive-only` mode to validate the current-day Release without requiring a scheduled event; `scheduled` mode checks both.

## Optional independent S3-compatible backup

The downloader workflow can mirror the verified daily ZIP to an independent S3-compatible object-storage bucket. Configure these as GitHub Actions repository secrets (Settings -> Secrets and variables -> Actions -> New repository secret):

- `S3_ENDPOINT` — provider endpoint URL (for Cloudflare R2, the account-specific R2 S3 API endpoint).
- `S3_BUCKET` — bucket name.
- `S3_ACCESS_KEY_ID` — access key ID with write/read-head permission for the backup bucket.
- `S3_SECRET_ACCESS_KEY` — matching secret access key.

The workflow uploads to `nse-market-data/YYYY-MM-DD/nse-market-data-YYYY-MM-DD.zip` and verifies the remote object size using `head-object`. If these secrets are absent, the independent backup step reports `NOT CONFIGURED` and does not block the GitHub Release archive. Do not put credentials in source files, issues, or chat messages.


## Agent 2: FII/DII Participant OI Intelligence

Consumes only the normalized NSE Participant-wise Open Interest report. It reports FII, DII, Client and Proprietary positions where present; computes index-futures net long/short and a transparent directional index-options net; and compares with the latest earlier valid normalized trading day when available. Missing columns stay unavailable. The output is EOD descriptive intelligence, not intraday flow or a standalone trade recommendation. Historical accuracy is not claimed.


## Agent 3: Equity Delivery Strength Intelligence

Uses the NSE Equity UDiFF Bhavcopy and Security-wise Delivery report to join symbols, rank delivery percentage and quantity, compare each stock with the current universe median, and calculate price change when source fields are available. Daily EOD snapshots support next-session comparisons. High delivery percentage alone does not prove accumulation or institutional buying.


## Agent 4: Futures Buildup Intelligence

Uses only FUTIDX and FUTSTK contracts from the normalized NSE F&O UDiFF Bhavcopy. It classifies each valid contract from the reported close-versus-previous-close price change and change in open interest: long buildup, short buildup, short covering, or long unwinding. Zero/missing inputs are labelled neutral or unavailable rather than forced into a directional class. Options and other instrument types are excluded. The output includes contract-level expiry detail and an underlying-level summary across included expiries. This is EOD descriptive analysis only, not an intraday signal or standalone trade recommendation; no prediction accuracy is claimed.


## Agent 5: NIFTY Option OI Support, Resistance and Max Pain

Uses only the NIFTY option contracts in the normalized NSE F&O UDiFF report. Contracts are grouped separately by expiry and strike. Put OI (then change in Put OI) ranks potential support zones; Call OI (then change in Call OI) ranks potential resistance zones. Max Pain is calculated independently for each expiry by minimizing aggregate intrinsic-value payout across observed strikes. Non-NIFTY options and futures are excluded. This is EOD descriptive analysis, not live option-chain data, a guaranteed support/resistance level, or a price forecast.


## Agent 6: EOD Market Direction and Confluence Intelligence

Consumes only the persisted EOD outputs from Agents 2-5. It keeps FII and DII positioning separate, uses NIFTY FUTIDX (not stock futures) for index-futures confirmation, compares top Put OI support with top Call OI resistance for the nearest listed expiry, and derives equity delivery breadth only from matched stock price/delivery rows. A weighted, signed evidence score is normalized over available inputs; missing inputs reduce coverage and confidence rather than being imputed. The output is a rule-based bullish/bearish/neutral-or-mixed EOD assessment, not a calibrated probability, intraday signal, or standalone trade recommendation. Chronological walk-forward backtesting is required before making any accuracy claim.
