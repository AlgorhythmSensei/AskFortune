from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import pandas as pd
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from groq import Groq


# Tries preferred modern models first; can be overridden by GROQ_MODEL.
MODEL_CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]
RECENT_DRAW_COUNT = 17
MAX_OUTPUT_LINES = 1200
GAME_ENDPOINTS: dict[str, str] = {
    "Powerball": "https://www.lotterywest.wa.gov.au/api/games/5132/results-csv",
    "Oz Lotto": "https://www.lotterywest.wa.gov.au/api/games/5130/results-csv",
    "Saturday Lotto": "https://www.lotterywest.wa.gov.au/api/games/5127/results-csv",
}


@dataclass
class AnalysisBundle:
    game: str
    total_rows: int
    recent_rows: pd.DataFrame
    hot_numbers: list[int]
    cold_numbers: list[int]
    all_frequency: Counter[int]
    main_number_count: int
    expected_pick_count: int
    include_powerball_pick: bool


def _candidate_env_paths() -> list[Path]:
    candidates: list[Path] = []

    explicit = os.getenv("ASKFORTUNE_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    # Prefer user-level secrets over repository placeholders.
    candidates.append(Path.home() / ".askfortune" / ".env")
    candidates.append(Path.home() / ".askfortune" / ".env.local")

    candidates.append(Path.cwd() / ".env")
    candidates.append(Path.cwd() / ".env.local")
    candidates.append(Path(__file__).resolve().parent / ".env")
    candidates.append(Path(__file__).resolve().parent / ".env.local")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env")
        candidates.append(exe_dir.parent / ".env")
        candidates.append(exe_dir.parent.parent / ".env")

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def _is_placeholder_key_value(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized == "your_groq_api_key_here"
        or normalized.startswith("your_")
        or "replace" in normalized
    )


def load_env_file() -> str | None:
    selected_data: dict[str, str] | None = None
    selected_path: str | None = None
    fallback_data: dict[str, str] | None = None
    fallback_path: str | None = None

    for env_path in _candidate_env_paths():
        try:
            if not env_path.exists():
                continue

            parsed: dict[str, str] = {}
            with env_path.open("r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue

                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        parsed[key] = value

            if not parsed:
                continue

            if fallback_data is None:
                fallback_data = parsed
                fallback_path = str(env_path)

            key_candidate = parsed.get("GROQ_API_KEY", "")
            if key_candidate and not _is_placeholder_key_value(key_candidate):
                selected_data = parsed
                selected_path = str(env_path)
                break
        except OSError:
            continue

    data_to_apply = selected_data if selected_data is not None else fallback_data
    path_to_apply = selected_path if selected_path is not None else fallback_path

    if data_to_apply is None:
        return None

    for key, value in data_to_apply.items():
        if key and key not in os.environ:
            os.environ[key] = value

    return path_to_apply


class LotteryPredictorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.loaded_env_path = load_env_file()
        self.root = root
        self.root.title("AskFortune Lottery Probability Analyzer")
        self.root.geometry("980x700")
        self.root.minsize(900, 620)

        self.result_queue: Queue[tuple[str, Any]] = Queue()
        self.worker_thread: threading.Thread | None = None

        self.selected_game = tk.StringVar(value="Powerball")

        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            outer,
            text="Lottery Data Fetch + Groq Prediction",
            font=("Helvetica", 16, "bold"),
        )
        title.pack(anchor="w", pady=(0, 12))

        controls = ttk.LabelFrame(outer, text="Inputs", padding=12)
        controls.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(controls, text="Game:").grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")
        game_menu = ttk.OptionMenu(
            controls,
            self.selected_game,
            self.selected_game.get(),
            *GAME_ENDPOINTS.keys(),
        )
        game_menu.grid(row=0, column=1, padx=(0, 20), pady=6, sticky="w")

        self.fetch_button = ttk.Button(
            controls,
            text="Fetch & Predict",
            command=self.on_fetch_predict,
        )
        self.fetch_button.grid(row=0, column=2, padx=(6, 0), pady=6, sticky="e")

        controls.columnconfigure(1, weight=1)

        output_frame = ttk.LabelFrame(outer, text="Results", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True)

        self.output_box = ScrolledText(
            output_frame,
            wrap=tk.WORD,
            width=120,
            height=30,
            font=("Menlo", 11),
        )
        self.output_box.pack(fill=tk.BOTH, expand=True)
        self.output_box.configure(state=tk.DISABLED)
        self.output_box.tag_configure("normal_number", foreground="#1f9d55")
        self.output_box.tag_configure("special_number", foreground="#d62828")
        self.output_box.tag_configure("section_header", font=("Menlo", 11, "bold"))

        self._append_output("Ready. Select a game, then click Fetch & Predict.")

    def _append_output(self, text: str) -> None:
        self.output_box.configure(state=tk.NORMAL)
        self.output_box.insert(tk.END, text + "\n")
        self._trim_output_buffer_locked()
        self.output_box.see(tk.END)
        self.output_box.configure(state=tk.DISABLED)

    def _trim_output_buffer_locked(self) -> None:
        total_lines = int(float(self.output_box.index("end-1c").split(".")[0]))
        if total_lines > MAX_OUTPUT_LINES:
            excess = total_lines - MAX_OUTPUT_LINES
            self.output_box.delete("1.0", f"{excess + 1}.0")

    def _set_busy(self, busy: bool) -> None:
        self.fetch_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _is_placeholder_key(self, value: str) -> bool:
        return _is_placeholder_key_value(value)

    def on_fetch_predict(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Processing", "A request is already running. Please wait.")
            return

        game = self.selected_game.get().strip()
        api_key = os.getenv("GROQ_API_KEY", "").strip()

        if not game:
            messagebox.showerror("Validation Error", "Please select a lottery game.")
            return

        if self._is_placeholder_key(api_key):
            checked_paths = "\n".join(str(path) for path in _candidate_env_paths())
            messagebox.showerror(
                "Validation Error",
                "GROQ_API_KEY is missing or still a placeholder.\n\nChecked:\n" + checked_paths,
            )
            return

        self._append_output(f"\n--- Running analysis for {game} ---")
        self._set_busy(True)

        self.worker_thread = threading.Thread(
            target=self._run_pipeline,
            args=(game, api_key),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_pipeline(self, game: str, api_key: str) -> None:
        try:
            bundle = self._collect_and_analyze(game)
            prompt = self._build_prompt(bundle)
            llm_data = self._call_groq(api_key=api_key, prompt=prompt)
            self._validate_llm_output(
                llm_data,
                expected_count=bundle.expected_pick_count,
                include_powerball_pick=bundle.include_powerball_pick,
                main_count=bundle.main_number_count,
            )
            self.result_queue.put(("success", {"bundle": bundle, "llm_data": llm_data}))
        except Exception as exc:
            detail = "\n".join(
                [
                    f"Error: {exc}",
                    "",
                    "Diagnostic traceback:",
                    traceback.format_exc(),
                ]
            )
            self.result_queue.put(("error", detail))

    def _is_special_column(self, column_name: str) -> bool:
        lowered = column_name.lower()
        keywords = ("powerball", "supplementary", "bonus", "special")
        return any(keyword in lowered for keyword in keywords)

    def _insert_colored_numbers(self, numbers: list[int], tag: str) -> None:
        for index, number in enumerate(numbers):
            if index > 0:
                self.output_box.insert(tk.END, ", ")
            self.output_box.insert(tk.END, str(number), tag)

    def _render_colored_result(self, bundle: AnalysisBundle, llm_data: dict[str, Any]) -> None:
        columns = list(bundle.recent_rows.columns)
        draw_col = columns[0]
        date_col = columns[1]
        value_cols = columns[2:]

        special_cols = [col for col in value_cols if self._is_special_column(col)]
        normal_cols = [col for col in value_cols if col not in special_cols]
        has_special_numbers = bool(special_cols)

        self.output_box.configure(state=tk.NORMAL)
        self.output_box.insert(tk.END, f"Game: {bundle.game}\n")
        self.output_box.insert(tk.END, f"Rows analyzed: {bundle.total_rows}\n")

        self.output_box.insert(tk.END, "Hot numbers: ")
        self._insert_colored_numbers(bundle.hot_numbers, "normal_number")
        self.output_box.insert(tk.END, "\n")

        self.output_box.insert(tk.END, "Cold numbers: ")
        self._insert_colored_numbers(bundle.cold_numbers, "normal_number")
        self.output_box.insert(tk.END, "\n\n")

        self.output_box.insert(tk.END, "Most recent draws:\n", "section_header")
        self.output_box.insert(
            tk.END,
            f"{'Draw':<8} {'Date':<12} {'Normal numbers':<38} {'Special':<20}\n",
        )
        self.output_box.insert(tk.END, "-" * 86 + "\n")

        for _, row in bundle.recent_rows.iterrows():
            draw = str(row[draw_col])
            date = str(row[date_col])
            normal_numbers = [
                int(row[col])
                for col in normal_cols
                if pd.notna(row[col])
            ]
            special_numbers = [
                int(row[col])
                for col in special_cols
                if pd.notna(row[col])
            ]

            self.output_box.insert(tk.END, f"{draw:<8} {date:<12} ")
            if normal_numbers:
                self._insert_colored_numbers(normal_numbers, "normal_number")
            else:
                self.output_box.insert(tk.END, "-")

            self.output_box.insert(tk.END, " " * 5)
            if special_numbers:
                self._insert_colored_numbers(special_numbers, "special_number")
            else:
                self.output_box.insert(tk.END, "-")
            self.output_box.insert(tk.END, "\n")

        self.output_box.insert(tk.END, "\nHeuristic analysis:\n", "section_header")
        self.output_box.insert(tk.END, str(llm_data.get("heuristic_analysis", "")) + "\n\n")

        self.output_box.insert(tk.END, "Suggested lines:\n", "section_header")
        suggested_lines = llm_data.get("suggested_lines", [])
        suggested_count = len(suggested_lines[0]) if suggested_lines else bundle.expected_pick_count
        header_numbers = [f"N{i}" for i in range(1, suggested_count + 1)]
        if bundle.include_powerball_pick and header_numbers:
            header_numbers[-1] = "PB"
        self.output_box.insert(tk.END, f"{'Line':<6} {'  '.join(header_numbers)}\n")
        self.output_box.insert(tk.END, "-" * 60 + "\n")

        for idx, line in enumerate(suggested_lines, start=1):
            self.output_box.insert(tk.END, f"{idx:<6} ")
            for num_idx, number in enumerate(line):
                if num_idx > 0:
                    self.output_box.insert(tk.END, "  ")
                is_powerball_pick = bundle.include_powerball_pick and num_idx == len(line) - 1
                self.output_box.insert(
                    tk.END,
                    f"{number:>2}",
                    "special_number" if is_powerball_pick else "normal_number",
                )
            self.output_box.insert(tk.END, "\n")

        self.output_box.insert(tk.END, "\n")
        self._trim_output_buffer_locked()
        self.output_box.see(tk.END)
        self.output_box.configure(state=tk.DISABLED)

    def _collect_and_analyze(self, game: str) -> AnalysisBundle:
        endpoint = GAME_ENDPOINTS.get(game)
        if endpoint is None:
            raise ValueError(f"Unsupported game selected: {game}")

        try:
            df = pd.read_csv(endpoint)
        except Exception as exc:
            raise RuntimeError(f"Failed to download or parse CSV for {game}: {exc}") from exc

        if df.empty:
            raise RuntimeError(f"The dataset for {game} is empty.")

        draw_col = self._first_present_column(df, ["Draw number", "Draw Number", "Draw", "draw_number"])
        date_col = self._first_present_column(df, ["Draw date", "Draw Date", "Date", "draw_date"])

        if not draw_col or not date_col:
            raise ValueError("Required draw/date columns were not found in the dataset.")

        if "Draw date" in df.columns:
            df["_parsed_date"] = pd.to_datetime(df["Draw date"], dayfirst=True, errors="coerce")
        elif "Draw Date" in df.columns:
            df["_parsed_date"] = pd.to_datetime(df["Draw Date"], dayfirst=True, errors="coerce")
        elif "draw_date" in df.columns:
            df["_parsed_date"] = pd.to_datetime(df["draw_date"], errors="coerce")
        else:
            df["_parsed_date"] = pd.to_datetime(df[date_col], errors="coerce")

        df = df.sort_values(by="_parsed_date", ascending=False, na_position="last").reset_index(drop=True)

        recent = df.head(RECENT_DRAW_COUNT).copy()
        if recent.empty:
            raise RuntimeError(f"Could not isolate the most recent {RECENT_DRAW_COUNT} rows.")

        win_cols = [
            col
            for col in df.columns
            if col.strip().lower().startswith("winning number")
        ]
        if not win_cols:
            raise ValueError("No winning number columns were found in the dataset.")

        special_cols_all = [col for col in df.columns if self._is_special_column(col)]
        powerball_cols = [col for col in special_cols_all if "powerball" in col.strip().lower()]
        include_powerball_pick = game == "Powerball" and bool(powerball_cols)

        all_numbers: list[int] = []
        for _, row in df.iterrows():
            for col in win_cols:
                value = row.get(col)
                if pd.notna(value):
                    try:
                        all_numbers.append(int(value))
                    except (TypeError, ValueError):
                        continue

        if not all_numbers:
            raise RuntimeError("No numeric winning numbers were extracted from the dataset.")

        freq = Counter(all_numbers)
        unique_numbers = sorted(set(all_numbers))

        ranked_hot = sorted(unique_numbers, key=lambda n: (-freq[n], n))
        ranked_cold = sorted(unique_numbers, key=lambda n: (freq[n], n))

        hot_numbers = ranked_hot[:5]
        cold_numbers = ranked_cold[:5]

        display_special_cols = powerball_cols if include_powerball_pick else special_cols_all
        display_recent = recent[[draw_col, date_col] + win_cols + display_special_cols].copy()

        return AnalysisBundle(
            game=game,
            total_rows=len(df),
            recent_rows=display_recent,
            hot_numbers=hot_numbers,
            cold_numbers=cold_numbers,
            all_frequency=freq,
            main_number_count=len(win_cols),
            expected_pick_count=len(win_cols) + (1 if include_powerball_pick else 0),
            include_powerball_pick=include_powerball_pick,
        )

    def _first_present_column(self, df: pd.DataFrame, candidates: list[str]) -> str | None:
        lowered = {c.lower(): c for c in df.columns}
        for candidate in candidates:
            if candidate.lower() in lowered:
                return lowered[candidate.lower()]
        return None

    def _build_prompt(self, bundle: AnalysisBundle) -> str:
        recent_lines: list[str] = []
        for _, row in bundle.recent_rows.iterrows():
            draw = row.iloc[0]
            date = row.iloc[1]
            nums = [
                int(row[col])
                for col in bundle.recent_rows.columns[2:]
                if pd.notna(row[col])
            ]
            recent_lines.append(f"Draw {draw} | Date {date} | Numbers {nums}")

        now = datetime.now().isoformat(timespec="seconds")
        rule_three = (
            "3) Include the Powerball number as the last value in each line.\n"
            if bundle.include_powerball_pick
            else "3) Include only main winning numbers; do not include supplementary/bonus numbers.\n"
        )

        prompt = (
            f"Timestamp: {now}\n"
            f"Game: {bundle.game}\n"
            f"Total rows analyzed: {bundle.total_rows}\n"
            f"Top 5 hot numbers: {bundle.hot_numbers}\n"
            f"Top 5 cold numbers: {bundle.cold_numbers}\n"
            f"Most recent {RECENT_DRAW_COUNT} draws:\n"
            + "\n".join(recent_lines)
            + "\n\n"
            "Generate a compact, data-driven response as strict JSON only. "
            "No markdown and no extra keys.\n"
            "Required JSON schema:\n"
            "{\n"
            '  "heuristic_analysis": "Explain odd/even balance, spacing, and clustering logic",\n'
            '  "suggested_lines": [[...], [...], [...]]\n'
            "}\n"
            "Rules:\n"
            "1) suggested_lines must contain exactly 3 arrays.\n"
            f"2) Each line must contain exactly {bundle.expected_pick_count} distinct integers.\n"
            f"{rule_three}"
            "4) Keep explanation concise but informative.\n"
            "5) Return JSON only."
        )
        return prompt

    def _call_groq(self, api_key: str, prompt: str) -> dict[str, Any]:
        client = Groq(api_key=api_key)

        requested_model = os.getenv("GROQ_MODEL", "").strip()
        candidates = [requested_model] if requested_model else []
        candidates.extend(model for model in MODEL_CANDIDATES if model not in candidates)

        response = None
        last_error: Exception | None = None
        attempted: list[str] = []

        for model_name in candidates:
            attempted.append(model_name)
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                    response_format={"type": "json_object"},
                )
                break
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "decommissioned" in message or "model_decommissioned" in message or "not found" in message:
                    continue
                raise RuntimeError(f"Groq API request failed using model '{model_name}': {exc}") from exc

        if response is None:
            raise RuntimeError(
                "Groq API request failed for all candidate models "
                f"{attempted}. Last error: {last_error}"
            ) from last_error

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise RuntimeError("Groq returned an empty response.")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Groq response is not valid JSON: {exc}") from exc

    def _validate_llm_output(
        self,
        data: dict[str, Any],
        expected_count: int,
        include_powerball_pick: bool,
        main_count: int,
    ) -> None:
        expected_keys = {"heuristic_analysis", "suggested_lines"}
        if set(data.keys()) != expected_keys:
            raise ValueError(
                f"Unexpected JSON keys returned: {sorted(data.keys())}. Expected exactly {sorted(expected_keys)}"
            )

        analysis = data.get("heuristic_analysis")
        lines = data.get("suggested_lines")

        if not isinstance(analysis, str) or not analysis.strip():
            raise ValueError("heuristic_analysis must be a non-empty string.")

        if not isinstance(lines, list) or len(lines) != 3:
            raise ValueError("suggested_lines must be a list of exactly 3 lines.")

        for idx, line in enumerate(lines, start=1):
            if not isinstance(line, list) or len(line) != expected_count:
                raise ValueError(f"suggested_lines[{idx}] must contain exactly {expected_count} numbers.")
            if any(not isinstance(n, int) for n in line):
                raise ValueError(f"suggested_lines[{idx}] contains non-integer values.")

            if include_powerball_pick:
                main_numbers = line[:main_count]
                if len(set(main_numbers)) != len(main_numbers):
                    raise ValueError(f"suggested_lines[{idx}] contains duplicate main numbers.")
            else:
                if len(set(line)) != expected_count:
                    raise ValueError(f"suggested_lines[{idx}] contains duplicate numbers.")

    def _format_result(self, bundle: AnalysisBundle, llm_data: dict[str, Any]) -> str:
        recent_table = bundle.recent_rows.to_string(index=False)

        suggested_lines = llm_data.get("suggested_lines", [])
        lines_table = pd.DataFrame(
            [
                {
                    "Line": idx + 1,
                    **{f"N{num_idx + 1}": value for num_idx, value in enumerate(line)},
                }
                for idx, line in enumerate(suggested_lines)
            ]
        ).to_string(index=False)

        lines = [
            f"Game: {bundle.game}",
            f"Rows analyzed: {bundle.total_rows}",
            f"Hot numbers: {bundle.hot_numbers}",
            f"Cold numbers: {bundle.cold_numbers}",
            "",
            "Most recent draws:",
            recent_table,
            "",
            "Heuristic analysis:",
            str(llm_data.get("heuristic_analysis", "")),
            "",
            "Suggested lines:",
            lines_table,
        ]
        return "\n".join(lines)

    def _poll_queue(self) -> None:
        try:
            while True:
                status, payload = self.result_queue.get_nowait()
                self._set_busy(False)
                if status == "success":
                    bundle = payload["bundle"]
                    llm_data = payload["llm_data"]
                    self._render_colored_result(bundle=bundle, llm_data=llm_data)
                else:
                    self._append_output("Execution failed.")
                    self._append_output(payload)
                    messagebox.showerror("Execution Failed", "See output panel for details.")
        except Empty:
            pass
        finally:
            self.root.after(150, self._poll_queue)


def main() -> None:
    root = tk.Tk()
    app = LotteryPredictorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
