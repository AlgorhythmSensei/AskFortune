from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from groq import Groq


LOGGER = logging.getLogger(__name__)
INPUT_CSV = Path("lottery_history.csv")
MODEL_NAME = "llama3-70b-8192"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def parse_number_cell(value: Any) -> list[int]:
    if pd.isna(value):
        return []
    raw = str(value)
    numbers: list[int] = []
    for token in raw.replace("|", ",").split(","):
        token = token.strip()
        if token.isdigit():
            numbers.append(int(token))
    return numbers


def load_history(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {csv_path}. Run scraper.py first to generate lottery_history.csv"
        )

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read CSV file {csv_path}: {exc}") from exc

    required_columns = {"Draw", "Date", "Numbers"}
    missing = required_columns.difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in CSV: {sorted(missing)}")

    return df


def compute_hot_cold(df: pd.DataFrame) -> tuple[list[int], list[int], Counter[int]]:
    all_numbers: list[int] = []
    for cell in df["Numbers"]:
        all_numbers.extend(parse_number_cell(cell))

    frequency: Counter[int] = Counter(num for num in all_numbers if 1 <= num <= 45)

    # Include zero-frequency values so cold numbers can be truly underrepresented.
    full_range = list(range(1, 46))
    ranked_hot = sorted(full_range, key=lambda n: (-frequency.get(n, 0), n))
    ranked_cold = sorted(full_range, key=lambda n: (frequency.get(n, 0), n))

    hot_numbers = ranked_hot[:5]
    cold_numbers = ranked_cold[:5]
    return hot_numbers, cold_numbers, frequency


def build_prompt(df: pd.DataFrame, hot_numbers: list[int], cold_numbers: list[int]) -> str:
    recent_rows = df.tail(5)
    recent_lines = [
        f"Draw {row['Draw']} | Date {row['Date']} | Numbers {row['Numbers']}"
        for _, row in recent_rows.iterrows()
    ]

    return (
        "You are an advanced lottery pattern analyst. "
        "Use only heuristic reasoning and do not claim certainty.\n\n"
        f"Dataset row count: {len(df)}\n"
        f"Top 5 hot numbers (most frequent): {hot_numbers}\n"
        f"Bottom 5 cold numbers (least frequent): {cold_numbers}\n"
        "Last 5 historical draws:\n"
        + "\n".join(recent_lines)
        + "\n\nReturn strictly valid JSON with this shape:\n"
        "{\n"
        '  "heuristic_analysis": "...",\n'
        '  "suggested_lines": [[n1,n2,n3,n4,n5,n6], [n1,n2,n3,n4,n5,n6], [n1,n2,n3,n4,n5,n6]]\n'
        "}\n\n"
        "Rules:\n"
        "1) suggested_lines must contain exactly 3 arrays.\n"
        "2) Each array must contain exactly 6 distinct integers.\n"
        "3) Every number must be between 1 and 45.\n"
        "4) heuristic_analysis must explain odd/even splits and number spacing strategy.\n"
        "5) Do not include any keys other than heuristic_analysis and suggested_lines."
    )


def get_groq_client() -> Groq:
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY is missing. Set it in the .env file.")
    return Groq(api_key=api_key)


def validate_model_output(payload: dict[str, Any]) -> dict[str, Any]:
    keys = set(payload.keys())
    expected = {"heuristic_analysis", "suggested_lines"}
    if keys != expected:
        raise ValueError(f"Unexpected output keys: {sorted(keys)}; expected {sorted(expected)}")

    analysis = payload.get("heuristic_analysis")
    lines = payload.get("suggested_lines")

    if not isinstance(analysis, str) or not analysis.strip():
        raise ValueError("heuristic_analysis must be a non-empty string")

    if not isinstance(lines, list) or len(lines) != 3:
        raise ValueError("suggested_lines must be an array with exactly 3 entries")

    for index, line in enumerate(lines, start=1):
        if not isinstance(line, list) or len(line) != 6:
            raise ValueError(f"Line {index} must have exactly 6 values")
        if any(not isinstance(num, int) for num in line):
            raise ValueError(f"Line {index} includes a non-integer value")
        if len(set(line)) != 6:
            raise ValueError(f"Line {index} contains duplicate numbers")
        if any(num < 1 or num > 45 for num in line):
            raise ValueError(f"Line {index} contains out-of-range numbers")

    return payload


def run_generation_pipeline(input_csv: Path = INPUT_CSV) -> dict[str, Any]:
    configure_logging()

    df = load_history(input_csv)
    hot_numbers, cold_numbers, _ = compute_hot_cold(df)
    prompt = build_prompt(df, hot_numbers, cold_numbers)

    client = get_groq_client()

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "You output strict JSON only.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise RuntimeError(f"Groq API request failed: {exc}") from exc

    message = response.choices[0].message.content if response.choices else None
    if not message:
        raise RuntimeError("Groq API returned an empty response")

    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response was not valid JSON: {exc}") from exc

    validated = validate_model_output(parsed)
    return validated


if __name__ == "__main__":
    try:
        result = run_generation_pipeline()
        print(json.dumps(result, indent=2))
    except Exception as error:
        LOGGER.exception("Generator execution failed: %s", error)
        raise
