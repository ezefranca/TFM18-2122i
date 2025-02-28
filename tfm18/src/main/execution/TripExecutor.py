from typing import Optional

from tfm18.src.main.algorithm.AlgorithmRepository import AlgorithmRepository
from tfm18.src.main.algorithm.AlgorithmType import AlgorithmType
from tfm18.src.main.algorithm.BaseAlgorithm import BaseAlgorithm
from tfm18.src.main.algorithm.HistoryBasedApproach import HistoryBasedApproach
from tfm18.src.main.algorithm.PredictionInput import PredictionInput
from tfm18.src.main.dataset.DatasetTimestampDto import DatasetTimestampDto
from tfm18.src.main.evaluation.AlgorithmEvaluationType import AlgorithmEvaluationType
from tfm18.src.main.evaluation.BaseAlgorithmEvaluation import BaseAlgorithmEvaluation
from tfm18.src.main.execution.TripExecutionResultDto import TripExecutionResultDto
from tfm18.src.main.execution.TripExecutorConfigDto import TripExecutorConfigDto
from tfm18.src.main.util.Chronometer import Chronometer
from tfm18.src.main.util.StrUtil import format_millis


class TripExecutor:

    def execute_trip(
        self,
        config: TripExecutorConfigDto
    ) -> TripExecutionResultDto:

        # Initialize trip execution start time
        execution_chronometer = Chronometer()

        # Initialize eRange_distance_results dictionary
        eRange_distance_results: dict[AlgorithmType, list[float]] = dict()
        eRange_exec_chronometer_dict: dict[AlgorithmType, Chronometer] = dict()
        history_based_approach: Optional[HistoryBasedApproach] = None
        for algorithm in config.enabled_algorithms:
            algorithm_type = algorithm.get_algorithm_type()
            eRange_distance_results[algorithm_type] = []
            if algorithm_type is AlgorithmType.HISTORY_BASED:
                # noinspection PyTypeChecker
                history_based_approach = algorithm

        eRange_history_aec_timestamps_min_list: list[float] = list()
        eRange_history_aec_ma_KWh_by_100km_list: list[float] = list()
        eRange_history_aec_wma_KWh_by_100km_list: list[float] = list()
        eRange_history_aec_KWh_by_100km_list: list[float] = list()

        if history_based_approach is not None:
            eRange_history_aec_timestamps_min_list = history_based_approach.execution_timestamps_min
            eRange_history_aec_ma_KWh_by_100km_list = history_based_approach.aec_ma_KWh_by_100km_list
            eRange_history_aec_wma_KWh_by_100km_list = history_based_approach.aec_wma_KWh_by_100km_list
            eRange_history_aec_KWh_by_100km_list = history_based_approach.aec_KWh_by_100km_list

        # Initialize expected list and algorithm if not already on enabled_algorithms
        expected_algorithm_type: Optional[AlgorithmType] = None
        expected_algorithm: Optional[BaseAlgorithm] = None
        should_predict_expected_result_explicitly: bool = False
        expected_result_list: Optional[list[float]] = None
        enabled_algorithm_types: Optional[list[AlgorithmType]] = None
        if config.expected_algorithm is not None:
            enabled_algorithm_types = \
                list(map(lambda _algorithm: _algorithm.get_algorithm_type(), config.enabled_algorithms))

            expected_algorithm_type = config.expected_algorithm.get_algorithm_type()
            # If expected algorithm is an enabled algorithm, use its result list
            if expected_algorithm_type in enabled_algorithm_types:
                expected_result_list = eRange_distance_results[expected_algorithm_type]
            # Fetch the expected algorithm otherwise, so it can later be used for explicit prediction
            else:
                expected_algorithm: BaseAlgorithm = AlgorithmRepository().get_algorithm(expected_algorithm_type)
                expected_result_list = []
                should_predict_expected_result_explicitly = True

        # Predict each eRange for each enabled algorithm
        dataset_timestamp_dto: DatasetTimestampDto
        for dataset_timestamp_dto in config.dataset_trip_dto.dataset_timestamp_dto_list:
            prediction_input = PredictionInput(
                dataset_timestamp_dto=dataset_timestamp_dto,
                dataset_vehicle_dto=config.dataset_trip_dto.vehicle_static_data
            )

            for algorithm in config.enabled_algorithms:
                algorithm_type = algorithm.get_algorithm_type()

                # Use the algorithm to predict the eRange for this timestamp
                curr_timestamp_algo_chronometer: Chronometer = Chronometer()
                # Predict trip timestamp eRange with algorithm
                eRange_distance_result = algorithm.predict(prediction_input=prediction_input)
                # Stop counting time for trip algorithm prediction timestamp
                curr_timestamp_algo_chronometer.stop()

                # Append algorithm timestamp prediction time to algorithm trip prediction time
                if algorithm_type in eRange_exec_chronometer_dict:
                    curr_timestamp_algo_chronometer += eRange_exec_chronometer_dict[algorithm_type]
                eRange_exec_chronometer_dict[algorithm_type] = curr_timestamp_algo_chronometer

                # Append trip timestamp eRange algorithm prediction
                eRange_distance_results[algorithm_type].append(eRange_distance_result)

            # If expected algorithm exists and is not part of enabled algorithms, predict the result explicitly
            if should_predict_expected_result_explicitly:
                expected_timestamp_result: float = expected_algorithm.predict(prediction_input=prediction_input)
                expected_result_list.append(expected_timestamp_result)

        # Print trip execution time
        if config.print_execution_time:
            time_delta_secs: str = execution_chronometer.get_elapsed_str()
            print("[Trip] %s's Execution time=%s" % (config.dataset_trip_dto.trip_identifier, time_delta_secs))

        eRange_result_evaluation_dict: dict[AlgorithmType, dict[AlgorithmEvaluationType, float]] = dict()
        algorithm_evaluation_dict: dict[AlgorithmEvaluationType, float]
        if config.expected_algorithm is not None:
            metrics_str: str = ""
            for algorithm_type in enabled_algorithm_types:

                # Do not calculate evaluation metrics for target algorithm
                if algorithm_type == config.expected_algorithm.get_algorithm_type():
                    continue

                # Initialize algorithm evaluation results dict
                algorithm_evaluation_dict = dict()
                eRange_result_evaluation_dict[algorithm_type] = algorithm_evaluation_dict
                metrics_str += "[Trip] %s: " % algorithm_type.value[3]

                # Calculate each evaluation for the specific enabled algorithm
                avaluation_algorithm: BaseAlgorithmEvaluation
                for avaluation_algorithm in config.evaluation_algorithms:
                    algorithm_evaluation_type: AlgorithmEvaluationType = avaluation_algorithm.get_type()
                    evaluation_value: float = avaluation_algorithm.evaluate(
                        expected=expected_result_list,
                        result=eRange_distance_results[algorithm_type],
                        variable_count=11  # TODO make it not constant
                    )
                    algorithm_evaluation_dict[algorithm_evaluation_type] = evaluation_value
                    # Save evaluation value for printing later
                    # noinspection PyUnresolvedReferences
                    metrics_str += "%s=%.3f, " % (algorithm_evaluation_type.value[0], evaluation_value)

                # If algorithm is machine learning, add training time
                if algorithm_type in config.train_times_dict:
                    metrics_str += "Time(All train)=%s, " % config.train_times_dict[algorithm_type].get_elapsed_str()

                # Average time of algorithm execution
                metrics_str += "Time(Avg test)=%s, " % format_millis(
                    millis=eRange_exec_chronometer_dict[algorithm_type] \
                               .get_elapsed_millis() / len(config.dataset_trip_dto.timestamps_min_list)
                )
                # All time of algorithm execution
                metrics_str += "Time(All test)=%s\n" % eRange_exec_chronometer_dict[algorithm_type].get_elapsed_str()
            print(metrics_str)

        return TripExecutionResultDto(
            dataset_trip_dto=config.dataset_trip_dto,
            eRange_distance_results=eRange_distance_results,
            eRange_result_evaluation_dict=eRange_result_evaluation_dict,
            eRange_history_aec_ma_KWh_by_100km_list=eRange_history_aec_ma_KWh_by_100km_list,
            eRange_history_aec_wma_KWh_by_100km_list=eRange_history_aec_wma_KWh_by_100km_list,
            eRange_history_aec_KWh_by_100km_list=eRange_history_aec_KWh_by_100km_list,
            eRange_history_aec_timestamps_min_list=eRange_history_aec_timestamps_min_list,
            expected_agorithm_type=expected_algorithm_type
        )
