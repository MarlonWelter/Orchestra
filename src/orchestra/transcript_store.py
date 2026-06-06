"""
Transcript storage for Orchestra runs.

Writes run state and turn records to .orchestra/runs/<run_id>/ as JSON files.
Every write is incremental — the run directory remains a valid partial transcript
even if the engine crashes mid-run.

Directory layout:
    .orchestra/runs/<run_id>/
    ├── run.json           # index and summary; updated after every turn
    └── turns/
        ├── 001-george.json
        ├── 002-warren.json
        └── ...

WARNING: turn files contain full assembled prompts and may include sensitive
content. Add .orchestra/ to .gitignore and treat it as sensitive.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from orchestra.errors import TranscriptWriteError
from orchestra.schemas import RunState, RunStatus, TurnRecord, TurnSummary


# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------


def generate_run_id() -> str:
    """
    Generate a unique, human-readable run ID.

    Format: run_YYYYMMDD_HHMMSS_<4hex>
    Example: run_20260606_140501_ab12
    """
    now = datetime.now(timezone.utc)
    suffix = secrets.token_hex(2)
    return f"run_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


# ---------------------------------------------------------------------------
# TranscriptStore
# ---------------------------------------------------------------------------


class TranscriptStore:
    """
    Owns all file I/O for run transcripts.

    Pass a custom base_dir in tests to avoid writing to the real filesystem.
    In production the default is Path.cwd() / ".orchestra".
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.cwd() / ".orchestra")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_run(self, run_state: RunState) -> None:
        """
        Create the run directory tree and write the initial run.json.

        Must be called once before any other method on this run.
        """
        run_dir = self._run_dir(run_state.run_id)
        turns_dir = self._turns_dir(run_state.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        turns_dir.mkdir(parents=True, exist_ok=True)
        self._write_run_json(run_state)

    def write_turn(self, turn_record: TurnRecord) -> None:
        """
        Write a completed turn record to its turn file.

        The file is written atomically to the turns/ subdirectory. Call this
        as soon as a turn completes so the transcript stays up to date.
        """
        path = self._turn_file_path(turn_record)
        self._safe_write(path, turn_record.model_dump_json(indent=2))

    def update_run(self, run_state: RunState) -> None:
        """
        Overwrite run.json with the current run state.

        Call after every turn (after updating run_state.turns with the new
        TurnSummary) so the index stays in sync with the turn files.
        """
        self._write_run_json(run_state)

    def complete_run(self, run_state: RunState, final_answer: str) -> None:
        """Mark the run as completed and record the final answer."""
        run_state.status = RunStatus.completed
        run_state.final_answer = final_answer
        run_state.completed_at = _now_iso()
        self._write_run_json(run_state)

    def fail_run(self, run_state: RunState, error: str) -> None:
        """Mark the run as failed and record the error summary."""
        run_state.status = RunStatus.failed
        run_state.error = error
        run_state.completed_at = _now_iso()
        self._write_run_json(run_state)

    def exhaust_run(self, run_state: RunState) -> None:
        """Mark the run as exhausted (energy budget reached zero)."""
        run_state.status = RunStatus.exhausted
        run_state.completed_at = _now_iso()
        self._write_run_json(run_state)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        return self.base_dir / "runs" / run_id

    def _turns_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "turns"

    def _run_json_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _turn_file_path(self, turn_record: TurnRecord) -> Path:
        filename = f"{turn_record.index:03d}-{turn_record.agent_id}.json"
        return self._turns_dir(turn_record.run_id) / filename

    # ------------------------------------------------------------------
    # Internal write helpers
    # ------------------------------------------------------------------

    def _write_run_json(self, run_state: RunState) -> None:
        path = self._run_json_path(run_state.run_id)
        self._safe_write(path, run_state.model_dump_json(indent=2))

    def _safe_write(self, path: Path, content: str) -> None:
        """Write content to path, raising TranscriptWriteError on failure."""
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise TranscriptWriteError(
                f"Failed to write transcript file: {path}",
                path=str(path),
                original_error=exc,
            ) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
