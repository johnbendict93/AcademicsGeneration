import os
import sys

# Make App/ importable (pdf_render, qa_format, academicsgen_app) from App/tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _check_logs_to_temp(tmp_path_factory, monkeypatch):
    """Generation writes check logs (live run #3); tests must never write into App/run_logs."""
    if not os.environ.get("ACADEMICSGEN_RUN_LOG_DIR"):
        monkeypatch.setenv("ACADEMICSGEN_RUN_LOG_DIR", str(tmp_path_factory.mktemp("run_logs")))
