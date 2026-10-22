import contextlib
import csv
import io
import math
import tempfile
import unittest
from pathlib import Path

from test_executor.case_4_h2load_graph_creator import METRICS, read_groups, create_figure, case_4_h2load_graph_creator


class Case4H2loadGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_dir = self.root / "case_4"
        self.case_dir.mkdir()
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def rows(self):
        rows = []
        for protocol in ("h3", "h2"):
            for delay in (100, 0, 50, 10):
                for loss in (2, 0, 1):
                    factor = 1 if protocol == "h2" else 1.2
                    row = dict(test_id=f"run/case_4/{protocol}-20-5000-20-10-{delay}-{loss}",
                               protocol=protocol, total_requests="5000", payload="20", clients="20",
                               max_stream="10", delay=str(delay), packet_loss=str(loss),
                               h2load_req_per_sec=str(5000 * factor / (1 + delay / 10 + loss)),
                               h2load_req_per_sec_mean=str(250 * factor / (1 + delay / 10 + loss)),
                               h2load_throughput="888888")
                    for index, (_title, _label, field) in enumerate(METRICS[1:], 1):
                        row[field] = str((delay + loss + index) * factor)
                    rows.append(row)
        return rows

    def write(self, rows):
        path = self.case_dir / "trimmed_mean.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_all_four_panels_use_delay_loss_pairs_and_correct_metrics(self):
        key, group = next(iter(read_groups(self.write(self.rows())).items()))
        figure = create_figure(key, group)
        self.addCleanup(figure.clear)
        pairs = [(delay, loss) for delay in (0, 10, 50, 100) for loss in (0, 1, 2)]
        self.assertEqual(len(figure.axes), 4)
        for axis, (_title, ylabel, field) in zip(figure.axes, METRICS):
            self.assertEqual(axis.get_ylabel(), ylabel)
            self.assertEqual(axis.get_xlabel(), "Packet loss (%) grouped by delay (ms)")
            self.assertEqual([tick.get_text() for tick in axis.get_xticklabels()],
                             [f"{loss}%" for delay, loss in pairs])
            self.assertEqual([text.get_text() for text in axis.texts], ["0 ms", "10 ms", "50 ms", "100 ms"])
            self.assertEqual([bars.get_label() for bars in axis.containers], ["h2", "h3"])
            for bars, protocol in zip(axis.containers, ("h2", "h3")):
                self.assertEqual([bar.get_height() for bar in bars],
                                 [group[protocol][pair][field] for pair in pairs])
            for h2, h3 in zip(*axis.containers):
                self.assertLess(h2.get_x() + h2.get_width(), h3.get_x())
        self.assertEqual(figure.axes[0].containers[0][0].get_height(), 250)

    def test_case_filter_fixed_settings_and_duplicate_detection(self):
        rows = self.rows()
        other_case = [{**row, "test_id": row["test_id"].replace("case_4", "case_3")} for row in rows]
        self.assertEqual(len(read_groups(self.write(rows + other_case))), 1)
        self.assertEqual(read_groups(self.write(other_case)), {})
        self.assertEqual(len(read_groups(self.write(rows + [{**rows[0], "payload": "40"}]))), 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            read_groups(self.write(rows + [rows[0]]))

    def test_missing_metric_and_protocol_remain_missing(self):
        rows = [row for row in self.rows() if row["protocol"] == "h2"]
        rows[0][METRICS[0][2]] = ""
        key, group = next(iter(read_groups(self.write(rows)).items()))
        figure = create_figure(key, group)
        self.addCleanup(figure.clear)
        bars = figure.axes[0].containers
        self.assertTrue(math.isnan(bars[0][-1].get_height()))
        self.assertTrue(all(math.isnan(bar.get_height()) for bar in bars[1]))
        self.assertEqual(sum(text.get_text() == "×" for text in figure.axes[0].texts), 13)

    def test_single_png_contains_four_panels_and_preserves_csv(self):
        source = self.write(self.rows())
        before = source.read_bytes()
        outputs = case_4_h2load_graph_creator(self.root)
        self.assertEqual(outputs, [self.case_dir / "h2load_graphs" / "n5000-p20-c20-m10" / "h2load_summary.png"])
        data = outputs[0].read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")), (2520, 1890))
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
