import contextlib
import csv
from importlib import import_module
import io
import math
from pathlib import Path
import tempfile
import unittest

from test_executor.h2load_comparison_graph_creator import CASES, h2load_comparison_graph_creator, line_figure, bar_figure
from test_executor.case_1_h2load_graph_creator import METRICS


class ComparisonGraphTests(unittest.TestCase):
    def test_all_cases_preserve_axes_and_plot_four_correct_series(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            for case, (module_name, x_axis) in CASES.items():
                with self.subTest(case=case):
                    directory = root / case
                    directory.mkdir()
                    for filename, base in (("trimmed_mean.csv", 10), ("average.csv", 30)):
                        rows = []
                        for protocol, offset in (("h2", 0), ("h3", 10)):
                            for point in (1, 2):
                                row = dict(test_id=f"run/{case}/{protocol}-{point}", protocol=protocol,
                                           total_requests=5000, payload=20, clients=20, max_stream=10,
                                           delay=10, packet_loss=0, h2load_req_per_sec=999999)
                                row[x_axis or "packet_loss"] = point
                                for _title, _ylabel, mean in METRICS:
                                    row[mean] = base + offset + point
                                    row[mean.replace("_mean", "_sd")] = 1
                                rows.append(row)
                        with (directory / filename).open("w", newline="") as file:
                            writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter=";")
                            writer.writeheader()
                            writer.writerows(reversed(rows) if filename == "average.csv" else rows)
                    reader = import_module(f"test_executor.{module_name}").read_groups
                    trimmed = reader(directory / "trimmed_mean.csv")
                    averaged = reader(directory / "average.csv")
                    key = next(iter(trimmed))
                    render = bar_figure if x_axis is None else line_figure
                    options = {} if x_axis is None else {"x_axis": x_axis}
                    original = render(key, trimmed[key], **options)
                    figure = render(key, trimmed[key], average_group=averaged[key], **options)
                    try:
                        for old_axis, axis in zip(original.axes, figure.axes):
                            self.assertEqual(axis.get_xlabel(), old_axis.get_xlabel())
                            self.assertEqual(axis.get_ylabel(), old_axis.get_ylabel())
                            self.assertEqual(axis.get_title(loc="left"), old_axis.get_title(loc="left"))
                            self.assertEqual(len(axis.collections), 0)
                            self.assertNotIn("±", " ".join(text.get_text() for text in figure.texts))
                            series = axis.containers if x_axis is None else axis.lines
                            self.assertEqual([item.get_label() for item in series],
                                             ["h2 trimmed", "h3 trimmed", "h2 average", "h3 average"])
                            values = ([[bar.get_height() for bar in bars] for bars in series] if x_axis is None
                                      else [list(line.get_ydata()) for line in series])
                            self.assertEqual(values, [[11, 12], [21, 22], [31, 32], [41, 42]])
                    finally:
                        original.clear()
                        figure.clear()
                    sources = {p: p.read_bytes() for p in directory.glob("*.csv")}
                    outputs = h2load_comparison_graph_creator(directory)
                    self.assertEqual(len(outputs), 1)
                    self.assertEqual(outputs[0].name, "h2load_summary_comparison.png")
                    self.assertTrue(outputs[0].read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                    original_png = outputs[0].with_name("h2load_summary.png")
                    original_png.write_bytes(b"existing graph")
                    h2load_comparison_graph_creator(directory)
                    self.assertEqual(original_png.read_bytes(), b"existing graph")
                    for path, contents in sources.items():
                        self.assertEqual(path.read_bytes(), contents)

    def test_missing_series_stays_a_gap_and_x_values_are_aligned(self):
        key = ("case_1", 5000, 20, 10, 0, 0)
        metrics = {field: 5 for metric in METRICS for field in metric[2:]}
        group = {"h2": {1: metrics}, "h3": {}}
        averaged = {"h2": {5: metrics}, "h3": {}}
        figure = line_figure(key, group, average_group=averaged)
        try:
            for axis in figure.axes:
                self.assertEqual(list(axis.lines[0].get_xdata()), [1, 5])
                self.assertTrue(math.isnan(axis.lines[0].get_ydata()[1]))
                self.assertTrue(math.isnan(axis.lines[2].get_ydata()[0]))
                self.assertTrue(all(math.isnan(v) for v in axis.lines[3].get_ydata()))
        finally:
            figure.clear()


if __name__ == "__main__":
    unittest.main()
