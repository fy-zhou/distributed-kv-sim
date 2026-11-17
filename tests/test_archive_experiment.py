"""Archive exporter smoke tests: portable, isolated temporary files."""
import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.archive_experiment import RUN_FIELDS, AGG_FIELDS, summarize


class ArchiveExperimentTests(unittest.TestCase):
    def test_signed_outcomes(self):
        rows = [
            {"lambda": "0.0", "p95_improvement_pct": "0.0"},
            {"lambda": "1.0", "p95_improvement_pct": "10.0"},
            {"lambda": "1.0", "p95_improvement_pct": "-2.0"},
            {"lambda": "1.0", "p95_improvement_pct": "0.0"},
        ]
        agg = [{"seed_count": "3"}]
        pos, neutral, neg, breakdown, counts = summarize(rows, agg)
        self.assertEqual((pos, neutral, neg), (1, 1, 1))
        self.assertEqual(counts, [3])
        self.assertEqual(len(breakdown), 1)

    def test_end_to_end_small_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            source = root / "source"
            dest = root / "archive"
            source.mkdir()
            run = {key: "0" for key in RUN_FIELDS}
            run.update(seed="41", **{"lambda": "1"},
                       p95_improvement_pct="10", greedy_p95_ms="100",
                       aware_p95_ms="90")
            agg = {key: "0" for key in AGG_FIELDS}
            agg.update(seed_count="1", **{"lambda": "1"},
                       p95_improvement_mean_pct="10")
            for name, fields, row in (
                    ("runs.csv", RUN_FIELDS, run),
                    ("aggregated.csv", AGG_FIELDS, agg)):
                with (source / name).open("w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=fields)
                    w.writeheader()
                    w.writerow(row)
            (source / "config.json").write_text(
                json.dumps({"seeds": [41], "out": "/private/local/path"}))
            script = pathlib.Path(__file__).resolve().parents[1] / (
                "scripts/archive_experiment.py")
            subprocess.check_call(
                [sys.executable, str(script), "--source", str(source),
                 "--destination", str(dest)], cwd=str(root))
            self.assertEqual(
                sorted(p.name for p in dest.iterdir()),
                ["README.md", "aggregated_compact.csv",
                 "manifest.json", "paired_compact.csv"])
            manifest = json.loads((dest / "manifest.json").read_text())
            self.assertNotIn("out", manifest["parameters"])
            self.assertEqual(manifest["rows"], 1)
            self.assertIn("Improvement: **1**",
                          (dest / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
