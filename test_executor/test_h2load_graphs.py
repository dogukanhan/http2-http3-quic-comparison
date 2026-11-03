import contextlib
import csv
import io
import math
import tempfile
import unittest
from pathlib import Path

from test_executor.case_1_h2load_graph_creator import METRICS, create_figure, case_1_h2load_graph_creator, read_groups


class H2loadGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_1"
        self.case_dir.mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def rows(self):
        rows = []
        for protocol in ("h2", "h3"):
            for payload in (20, 1, 5):
                row = dict(test_id=f"run/case_1/{protocol}-{payload}-5000-20-10-0-0",
                           payload=str(payload), protocol=protocol, total_requests="5000", clients="20",
                           max_stream="10", delay="0", packet_loss="0", h2load_req_per_sec="99999")
                for index, (_, _, mean) in enumerate(METRICS, 1):
                    row[mean] = str(index * payload)
                    row[mean.replace("_mean", "_sd")] = "0.5"
                rows.append(row)
        return rows

    def write(self, rows):
        path = self.case_dir / "trimmed_mean.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_four_panels_use_sorted_payloads_correct_means_without_sd_bands(self):
        key, group = next(iter(read_groups(self.write(self.rows())).items()))
        figure = create_figure(key, group)
        self.addCleanup(figure.clear)
        self.assertEqual(len(figure.axes), 4)
        for index, axis in enumerate(figure.axes, 1):
            self.assertEqual(len(axis.lines), 2)
            self.assertEqual(len(axis.collections), 0)
            self.assertEqual([line.get_label() for line in axis.lines], ["h2", "h3"])
            self.assertEqual(list(axis.lines[0].get_xdata()), [1, 5, 20])
            self.assertEqual(list(axis.lines[0].get_ydata()), [index, index * 5, index * 20])
            self.assertEqual(axis.get_xlabel(), "Payload (KB)")
        self.assertIn("req/s", figure.axes[0].get_ylabel())
        self.assertTrue(all("ms" in axis.get_ylabel() for axis in figure.axes[1:]))

    def test_missing_sd_keeps_mean_and_missing_mean_leaves_gap(self):
        rows = self.rows()
        for row in rows:
            for field in list(row):
                if "_sd" in field:
                    del row[field]
        rows[1][METRICS[1][2]] = "NaN"
        key, group = next(iter(read_groups(self.write(rows)).items()))
        self.assertEqual(group["h2"][20][METRICS[0][2]], 20)
        figure = create_figure(key, group)
        self.addCleanup(figure.clear)
        self.assertTrue(math.isnan(figure.axes[1].lines[0].get_ydata()[0]))

    def test_separate_settings_and_duplicate_detection(self):
        rows = self.rows()
        self.assertEqual(len(read_groups(self.write(rows + [{**rows[0], "delay": "10"}]))), 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            read_groups(self.write(rows + [rows[0]]))

    def test_case_2_rows_are_ignored(self):
        rows = self.rows()
        case_2 = [{**row, "test_id": row["test_id"].replace("/case_1/", "/case_2/")} for row in rows]
        self.assertEqual(len(read_groups(self.write(rows + case_2))), 1)
        self.assertEqual(read_groups(self.write(case_2)), {})

    def test_one_png_contains_all_panels_and_preserves_csv(self):
        source = self.write(self.rows())
        original = source.read_bytes()
        outputs = case_1_h2load_graph_creator(self.root)
        self.assertTrue(all(path.is_relative_to(self.case_dir / "h2load_graphs") for path in outputs))
        self.assertFalse((self.root / "h2load_graphs").exists())
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].name, "h2load_summary.png")
        data = outputs[0].read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")), (2340, 1710))
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
