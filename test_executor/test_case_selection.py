import contextlib
import io
from itertools import product
import unittest
from unittest.mock import call, patch

import run_test
from test_executor.environment import CaseParameters, Environment
from test_executor import execute_case_1, execute_case_2, execute_case_3, execute_case_4


PARAMETERS = CaseParameters(protocols=("h3", "h2"), payloads=(5, 20), clients=(2, 7),
                            delays=(0, 13), packet_losses=(0, 2), total_requests=137,
                            max_streams=3, iterations=2)


class CaseSelectionTests(unittest.TestCase):
    def test_all_enable_combinations(self):
        for case_1, case_2, case_3, case_4 in product((False, True), repeat=4):
            with self.subTest(case_1=case_1, case_2=case_2, case_3=case_3, case_4=case_4), contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(patch.object(run_test, "pre_init_test", return_value=contextlib.nullcontext()))
                config = Environment(case_1_enabled=case_1, case_2_enabled=case_2, case_3_enabled=case_3, case_4_enabled=case_4,
                                     case_1=PARAMETERS, case_2=PARAMETERS, case_3=PARAMETERS, case_4=PARAMETERS)
                stack.enter_context(patch.object(run_test, "load_environment", return_value=config))
                connection = stack.enter_context(patch.object(run_test, "server_connection", return_value=contextlib.nullcontext()))
                names = ("pre_build_containers", "execute_case_1", "execute_case_2", "execute_case_3", "execute_case_4", "pre_analysis",
                         "execute_trimmed_mean", "execute_percentiles", "execute_average", "h2load_comparison_graph_creator", "percentile_summary_average_graph_creator", "case_1_summary_table", "case_2_summary_table", "case_3_summary_table", "case_4_summary_table", "case_4_all_delays_summary_table", "case_4_resource_summary_table", "case_4_all_delays_resource_summary_table", "case_4_h2load_graph_creator",
                         "case_1_resource_graph_creator", "case_1_h2load_graph_creator", "case_1_percentile_graph_creator", "case_2_h2load_graph_creator", "case_2_percentile_graph_creator", "case_2_resource_graph_creator", "case_3_resource_graph_creator", "case_3_h2load_graph_creator", "case_3_percentile_graph_creator")
                events = []
                mocks = {name: stack.enter_context(patch.object(run_test, name, side_effect=lambda name=name, **kwargs: events.append(name)))
                         for name in names}
                run_test.main()
                expected = (["pre_build_containers"]
                            + (["execute_case_1"] if case_1 else [])
                            + (["execute_case_2"] if case_2 else [])
                            + (["execute_case_3"] if case_3 else [])
                            + (["execute_case_4"] if case_4 else [])
                            + [stage for enabled in (case_1, case_2, case_3, case_4) if enabled
                               for stage in ("pre_analysis", "execute_trimmed_mean", "execute_percentiles", "execute_average")]
                            + (["case_1_summary_table"] if case_1 else [])
                            + (["case_1_resource_graph_creator", "case_1_h2load_graph_creator", "case_1_percentile_graph_creator"] if case_1 else [])
                            + (["case_2_summary_table", "case_2_resource_graph_creator", "case_2_h2load_graph_creator", "case_2_percentile_graph_creator"] if case_2 else [])
                            + (["case_3_summary_table", "case_3_resource_graph_creator", "case_3_h2load_graph_creator", "case_3_percentile_graph_creator"] if case_3 else [])
                            + (["case_4_summary_table", "case_4_all_delays_summary_table", "case_4_resource_summary_table", "case_4_all_delays_resource_summary_table", "case_4_h2load_graph_creator"] if case_4 else [])
                            + [stage for enabled in (case_1, case_2, case_3, case_4) if enabled
                               for stage in ("h2load_comparison_graph_creator", "percentile_summary_average_graph_creator")]
                            if case_1 or case_2 or case_3 or case_4 else [])
                self.assertEqual(events, expected)
                for enabled, name in ((case_1, "execute_case_1"), (case_2, "execute_case_2"),
                                      (case_3, "execute_case_3"), (case_4, "execute_case_4")):
                    if enabled:
                        mocks[name].assert_called_once_with(config=config)
                for stage in ("pre_analysis", "execute_trimmed_mean", "execute_percentiles", "execute_average", "h2load_comparison_graph_creator", "percentile_summary_average_graph_creator"):
                    self.assertEqual(mocks[stage].call_args_list,
                                     [call(case_name=name) for enabled, name in
                                      ((case_1, "case_1"), (case_2, "case_2"), (case_3, "case_3"), (case_4, "case_4")) if enabled])
                self.assertEqual(mocks["execute_case_1"].call_count, int(case_1))
                self.assertEqual(mocks["execute_case_2"].call_count, int(case_2))
                self.assertEqual(mocks["execute_case_3"].call_count, int(case_3))
                self.assertEqual(mocks["execute_case_4"].call_count, int(case_4))
                if case_1 or case_2 or case_3 or case_4:
                    connection.assert_called_once_with(config)
                else:
                    connection.assert_not_called()

    def test_each_case_executes_only_configured_values_and_all_combinations(self):
        for name, module in (("case_1", execute_case_1), ("case_2", execute_case_2),
                             ("case_3", execute_case_3), ("case_4", execute_case_4)):
            config = Environment(**{name: PARAMETERS})
            with self.subTest(case=name), patch.object(module, "execute") as execute, \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                module.main(config=config)
            expected = [dict(folder_name=name, protocol=protocol, payload=payload,
                             total_request=137, client=client, max_streams=3,
                             delay=delay, packet_loss=loss, iterations=2)
                        for protocol, payload, client, delay, loss in product(
                            ("h3", "h2"), (5, 20), (2, 7), (0, 13), (0, 2))]
            self.assertEqual([call.kwargs for call in execute.call_args_list], expected)
            self.assertIn("[32/32]", log.getvalue())
            self.assertIn("Estimated total time left:", log.getvalue())

    def test_direct_case_entry_loads_yaml_configuration(self):
        config = Environment(case_4=PARAMETERS)
        with patch("test_executor.case_runner.load_environment", return_value=config) as load, \
                patch.object(execute_case_4, "execute") as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            execute_case_4.main()
        load.assert_called_once_with()
        self.assertEqual(execute.call_count, 32)

    def test_missing_enabled_case_settings_fail_before_docker(self):
        with patch.object(run_test, "pre_init_test", return_value=contextlib.nullcontext()), \
                patch.object(run_test, "load_environment", return_value=Environment()), \
                patch.object(run_test, "server_connection") as connect, \
                patch.object(run_test, "pre_build_containers") as build:
            with self.assertRaisesRegex(ValueError, "missing case_1 parameters"):
                run_test.main()
        connect.assert_not_called()
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
