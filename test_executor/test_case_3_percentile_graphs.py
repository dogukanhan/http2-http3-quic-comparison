import contextlib
import csv
import io
import math
import tempfile
import unittest
from pathlib import Path

from test_executor.case_3_percentile_graph_creator import METRICS, create_figure, case_3_percentile_graph_creator, read_groups


class Case3PercentileGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_3"
        self.case_dir.mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def rows(self):
        return [{"test_id": f"run/case_3/{protocol}-20-5000-20-10-{delay}-0",
                 "payload": "20", "protocol": protocol, "total_requests": "5000", "clients": "20",
                 "max_stream": "10", "delay": str(delay), "packet_loss": "0",
                 METRICS[0][1]: str(delay * factor), METRICS[1][1]: str(delay * factor + 10)}
                for protocol, factor in (("h2", 2), ("h3", 3)) for delay in (20, 1, 5)]

    def write(self, rows):
        path = self.case_dir / "trimmed_mean.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_two_panels_plot_csv_percentiles_in_delay_order_without_rescaling(self):
        key, group = next(iter(read_groups(self.write(self.rows())).items()))
        figure = create_figure(key, group)
        self.addCleanup(figure.clear)
        self.assertEqual(len(figure.axes), 2)
        for index, axis in enumerate(figure.axes):
            self.assertEqual([line.get_label() for line in axis.lines], ["h2", "h3"])
            self.assertEqual(axis.get_xlabel(), "Delay (ms)")
            self.assertEqual(axis.get_ylabel(), "Request latency (ms)")
            for line, factor in zip(axis.lines, (2, 3)):
                self.assertEqual(list(line.get_xdata()), [1, 5, 20])
                self.assertEqual(list(line.get_ydata()), [delay * factor + 10 * index for delay in (1, 5, 20)])

    def test_missing_percentiles_leave_gaps_and_different_settings_stay_separate(self):
        rows = self.rows()
        rows[0][METRICS[0][1]] = ""
        groups = read_groups(self.write(rows + [{**rows[1], "payload": "10"}]))
        self.assertEqual(len(groups), 2)
        group = groups[("case_3", 5000, 20, 20, 10, 0)]
        self.assertTrue(math.isnan(group["h2"][20][METRICS[0][1]]))
        self.assertEqual(group["h2"][20][METRICS[1][1]], 50)

    def test_png_export_keeps_csv_unchanged(self):
        source = self.write(self.rows())
        original = source.read_bytes()
        outputs = case_3_percentile_graph_creator(self.root)
        self.assertTrue(all(path.is_relative_to(self.case_dir / "percentile_graphs") for path in outputs))
        self.assertFalse((self.root / "percentile_graphs").exists())
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].name, "percentile_summary.png")
        data = outputs[0].read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")), (2340, 1044))
        self.assertEqual(source.read_bytes(), original)

    def test_case_1_rows_are_ignored(self):
        rows = self.rows()
        case_1 = [{**row, "test_id": row["test_id"].replace("/case_3/", "/case_1/")} for row in rows]
        self.assertEqual(len(read_groups(self.write(rows + case_1))), 1)
        self.assertEqual(read_groups(self.write(case_1)), {})


if __name__ == "__main__":
    unittest.main()
