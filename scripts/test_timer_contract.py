"""Verify the pinned SDK request/response contract without network or credentials."""
from unittest.mock import patch
from pathlib import Path
import sys
import pandas as pd
from timecho_ai import TimechoAIClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.forecast as provider


def main():
    captured = {}
    def fake_request(self, method, path, **kwargs):
        captured.update({"method": method, "path": path, "body": kwargs["json"]})
        return {"data": {"results": [{"columns": ["time", "target"],
                                      "data": [[f"2026-01-01T{hour:02d}:00:00", 100.0 + hour]
                                               for hour in range(16, 24)]}]}}
    series = pd.Series([float(i) for i in range(16)],
                       index=pd.date_range("2026-01-01", periods=16, freq="h", tz="UTC"))
    with patch.object(TimechoAIClient, "_request", fake_request):
        with patch.object(provider, "TIMECHO_API_KEY", "contract-test-only"):
            values = provider.timer_predict(series, 8, 3600)
    assert len(values) == 8 and values[0] == 116.0
    assert captured["method"] == "POST" and captured["path"] == "/forecast"
    assert captured["body"]["model_id"] == "Timer-3.5"
    assert captured["body"]["output_length"] == [8]
    print("Timer SDK 0.2.6 request/response contract: passed")


if __name__ == "__main__":
    main()

