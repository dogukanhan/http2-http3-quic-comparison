from importlib import import_module

from test_executor.pre_init_test import pre_init_test
from test_executor.pre_build_containers import pre_build_containers
from test_executor.execute_case_1 import main as execute_case_1
from test_executor.execute_case_2 import main as execute_case_2
from test_executor.execute_case_3 import main as execute_case_3
from test_executor.execute_case_4 import main as execute_case_4
from test_executor.environment import load_environment
from test_executor.server_host import server_connection
from test_executor.pre_analysis import pre_analysis
from test_executor.execute_trimmed_mean import execute_trimmed_mean
from test_executor.average import execute_average
from test_executor.h2load_comparison_graph_creator import h2load_comparison_graph_creator
from test_executor.percentile_summary_average_graph_creator import percentile_summary_average_graph_creator
from test_executor.case_1_summary_table import case_1_summary_table
from test_executor.case_2_summary_table import case_2_summary_table
from test_executor.case_3_summary_table import case_3_summary_table
from test_executor.case_4_summary_table import case_4_summary_table
from test_executor.case_4_all_delays_summary_table import case_4_all_delays_summary_table
from test_executor.case_4_resource_summary_table import case_4_resource_summary_table
from test_executor.case_4_all_delays_resource_summary_table import case_4_all_delays_resource_summary_table
from test_executor.case_1_resource_graph_creator import case_1_resource_graph_creator
from test_executor.case_1_h2load_graph_creator import case_1_h2load_graph_creator
from test_executor.case_1_percentile_graph_creator import case_1_percentile_graph_creator
from test_executor.h2load_graph_creator import h2load_graph_creator as case_2_h2load_graph_creator
from test_executor.percentile_graph_creator import percentile_graph_creator as case_2_percentile_graph_creator
from test_executor.resource_graph_creator import resource_graph_creator as case_2_resource_graph_creator

from test_executor.case_3_resource_graph_creator import case_3_resource_graph_creator
from test_executor.case_3_h2load_graph_creator import case_3_h2load_graph_creator
from test_executor.case_4_h2load_graph_creator import case_4_h2load_graph_creator
from test_executor.case_3_percentile_graph_creator import case_3_percentile_graph_creator

# The requested module name begins with digits, so load it with import_module.
execute_percentiles = import_module("test_executor.95_99_percentile").execute_percentiles


def main() -> None:
    with pre_init_test():
        config = load_environment()
        cases = (
            (config.case_1_enabled, "case_1", execute_case_1),
            (config.case_2_enabled, "case_2", execute_case_2),
            (config.case_3_enabled, "case_3", execute_case_3),
            (config.case_4_enabled, "case_4", execute_case_4),
        )
        if not any(enabled for enabled, _name, _execute in cases):
            print("All test cases are disabled in env.yaml; nothing to run.", flush=True)
            return
        for enabled, name, _execute in cases:
            if enabled:
                config.case_parameters(name)
        with server_connection(config):
            pre_build_containers(config=config)
            for enabled, name, execute_case in cases:
                if enabled:
                    print(f"Running {name}...", flush=True)
                    execute_case(config=config)
                else:
                    print(f"Skipping {name}: disabled in env.yaml.", flush=True)
        for enabled, case_name, _execute in cases:
            if enabled:
                pre_analysis(case_name=case_name)
                execute_trimmed_mean(case_name=case_name)
                execute_percentiles(case_name=case_name)
                execute_average(case_name=case_name)
        if config.case_1_enabled:
            case_1_summary_table()
            case_1_resource_graph_creator()
            case_1_h2load_graph_creator()
            case_1_percentile_graph_creator()
        if config.case_2_enabled:
            case_2_summary_table()
            case_2_resource_graph_creator()
            case_2_h2load_graph_creator()
            case_2_percentile_graph_creator()
        if config.case_3_enabled:
            case_3_summary_table()
            case_3_resource_graph_creator()
            case_3_h2load_graph_creator()
            case_3_percentile_graph_creator()
        if config.case_4_enabled:
            case_4_summary_table()
            case_4_all_delays_summary_table()
            case_4_resource_summary_table()
            case_4_all_delays_resource_summary_table()
            case_4_h2load_graph_creator()
        for enabled, case_name, _execute in cases:
            if enabled:
                h2load_comparison_graph_creator(case_name=case_name)
                percentile_summary_average_graph_creator(case_name=case_name)


if __name__ == "__main__":
    main()
