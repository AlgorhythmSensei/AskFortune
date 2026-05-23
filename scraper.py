from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup


LOGGER = logging.getLogger(__name__)
OUTPUT_CSV = Path("lottery_history.csv")
REQUEST_TIMEOUT_SECONDS = 20
CSV_SOURCE_URLS = [
    "https://data.ny.gov/api/views/d6yy-54nr/rows.csv?accessType=DOWNLOAD",
]
USER_AGENT = (
    "AskFortuneBot/1.0 (+https://example.com/askfortune; "
    "compatible; DataIngestion/2026)"
)

MOCK_HTML = """
<html>
  <body>
    <table id="history">
      <thead>
        <tr><th>Draw</th><th>Date</th><th>Numbers</th></tr>
      </thead>
      <tbody>
        <tr><td>1001</td><td>2026-05-01</td><td>5, 12, 18, 26, 33, 41</td></tr>
        <tr><td>1002</td><td>2026-05-04</td><td>3, 11, 15, 24, 30, 44</td></tr>
        <tr><td>1003</td><td>2026-05-07</td><td>2, 9, 17, 22, 35, 40</td></tr>
        <tr><td>1004</td><td>2026-05-10</td><td>7, 14, 19, 23, 31, 45</td></tr>
        <tr><td>1005</td><td>2026-05-13</td><td>1, 8, 16, 25, 34, 42</td></tr>
      </tbody>
    </table>
  </body>
</html>
"""


@dataclass
class LotteryRecord:
    draw: str
    date: str
    numbers: list[int]


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def fetch_csv_from_endpoint(url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv,*/*;q=0.8"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.text
    except requests.Timeout as exc:
        raise RuntimeError(f"Timeout while requesting CSV data from {url}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error while requesting CSV data from {url}: {exc}") from exc


def normalize_date(raw_date: str) -> str:
    date_formats = [
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]

    cleaned = raw_date.strip()
    for fmt in date_formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    raise ValueError(f"Unsupported date format: {raw_date}")


def extract_numbers(raw: str, expected_count: int = 6) -> list[int]:
    values = [int(match) for match in re.findall(r"\d+", raw)]
    if len(values) < expected_count:
        raise ValueError(f"Could not parse at least {expected_count} numbers from: {raw}")
    return values[:expected_count]


def _first_existing_key(row: dict[str, str], keys: Iterable[str]) -> Optional[str]:
    lowered = {k.lower(): k for k in row.keys()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def parse_csv_records(csv_text: str) -> list[LotteryRecord]:
    records: list[LotteryRecord] = []
    reader = csv.DictReader(StringIO(csv_text))

    for index, row in enumerate(reader, start=1):
        try:
            draw_key = _first_existing_key(row, ["Draw", "Draw Number", "Draw #", "DrawNumber"])
            date_key = _first_existing_key(row, ["Date", "Draw Date", "draw_date"])
            numbers_key = _first_existing_key(row, ["Numbers", "Winning Numbers", "winning_numbers"])

            if not date_key or not numbers_key:
                raise ValueError("Missing required date or numbers columns")

            draw_value = (row.get(draw_key) if draw_key else "") or str(index)
            date_value = normalize_date(str(row[date_key]))
            numbers = extract_numbers(str(row[numbers_key]))

            records.append(LotteryRecord(draw=str(draw_value).strip(), date=date_value, numbers=numbers))
        except Exception as exc:
            LOGGER.warning("Skipping malformed row %s: %s", index, exc)

    if not records:
        raise RuntimeError("No valid lottery records were parsed from CSV source")

    return records


def parse_mock_html_records(html: str) -> list[LotteryRecord]:
    try:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", attrs={"id": "history"})
        if table is None:
            raise ValueError("Table with id='history' was not found")

        body = table.find("tbody")
        if body is None:
            raise ValueError("Table body is missing")

        records: list[LotteryRecord] = []
        for row in body.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            if len(cells) < 3:
                continue
            draw, raw_date, raw_numbers = cells[0], cells[1], cells[2]
            records.append(
                LotteryRecord(
                    draw=draw,
                    date=normalize_date(raw_date),
                    numbers=extract_numbers(raw_numbers),
                )
            )

        if not records:
            raise RuntimeError("No rows parsed from mock HTML")

        return records
    except Exception as exc:
        raise RuntimeError(f"Failed to parse mock HTML records: {exc}") from exc


def save_records_to_csv(records: list[LotteryRecord], output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["Draw", "Date", "Numbers"])
            for record in records:
                writer.writerow([record.draw, record.date, ",".join(str(num) for num in record.numbers)])
    except OSError as exc:
        raise RuntimeError(f"Unable to write output CSV to {output_path}: {exc}") from exc


def scrape_lottery_history(output_path: Path = OUTPUT_CSV) -> Path:
    configure_logging()

    records: list[LotteryRecord] = []
    last_error: Optional[Exception] = None

    for source_url in CSV_SOURCE_URLS:
        try:
            LOGGER.info("Fetching lottery data from CSV endpoint: %s", source_url)
            csv_text = fetch_csv_from_endpoint(source_url)
            records = parse_csv_records(csv_text)
            LOGGER.info("Parsed %s records from endpoint", len(records))
            break
        except Exception as exc:
            last_error = exc
            LOGGER.warning("Failed to parse CSV endpoint %s: %s", source_url, exc)

    if not records:
        LOGGER.info("Falling back to structured mock HTML source")
        records = parse_mock_html_records(MOCK_HTML)
        LOGGER.info("Parsed %s records from fallback HTML", len(records))

    save_records_to_csv(records, output_path)
    LOGGER.info("Saved cleaned lottery history to %s", output_path)

    if last_error:
        LOGGER.info("Note: CSV endpoint failed earlier and fallback was used: %s", last_error)

    return output_path


if __name__ == "__main__":
    try:
        path = scrape_lottery_history()
        print(f"Lottery history created at: {path}")
    except Exception as error:
        LOGGER.exception("Scraper execution failed: %s", error)
        raise
