from __future__ import annotations

import json
import logging
import sys

from generator import run_generation_pipeline
from scraper import scrape_lottery_history


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    configure_logging()

    try:
        LOGGER.info("Step 1/2: Running scraper module")
        output_path = scrape_lottery_history()
        LOGGER.info("Scraper completed successfully: %s", output_path)

        LOGGER.info("Step 2/2: Running generator module")
        result = run_generation_pipeline()
        LOGGER.info("Generator completed successfully")

        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        LOGGER.error("Execution failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
