# Copyright (c) 2019 Uber Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
import contextlib
import json
import logging
import os.path
import tempfile
from distutils.version import LooseVersion
from typing import Dict, Optional, Tuple

import pytest
import torch

from ludwig.constants import (
    ACCURACY,
    CATEGORY,
    COMBINER,
    DEFAULTS,
    HYPEROPT,
    INPUT_FEATURES,
    NAME,
    OUTPUT_FEATURES,
    RAY,
    TEXT,
    TRAINER,
    TYPE,
)
from ludwig.hyperopt.execution import get_build_hyperopt_executor
from ludwig.hyperopt.results import HyperoptResults, RayTuneResults
from ludwig.hyperopt.run import hyperopt, update_hyperopt_params_with_defaults
from ludwig.hyperopt.sampling import get_build_hyperopt_sampler
from ludwig.utils.defaults import merge_with_defaults
from tests.integration_tests.utils import category_feature, generate_data, text_feature

try:
    import ray

    _ray112 = LooseVersion("1.12") <= LooseVersion(ray.__version__) < LooseVersion("1.13")
except ImportError:
    ray = None
    _ray112 = None


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("ludwig").setLevel(logging.INFO)

RANDOM_SEARCH_SIZE = 4

HYPEROPT_CONFIG = {
    "parameters": {
        # using only float parameter as common in all search algorithms
        "trainer.learning_rate": {
            "space": "loguniform",
            "lower": 0.001,
            "upper": 0.1,
        },
    },
    "goal": "minimize",
    "executor": {"type": "ray", "num_samples": 2, "scheduler": {"type": "fifo"}},
    "search_alg": {"type": "variant_generator"},
}

SEARCH_ALGS = [
    None,
    "variant_generator",
    "random",
    "hyperopt",
    "bohb",
    "ax",
    "bayesopt",
    "blendsearch",
    "cfo",
    "dragonfly",
    "hebo",
    "skopt",
    "optuna",
]

SCHEDULERS = [
    "fifo",
    "asynchyperband",
    "async_hyperband",
    "median_stopping_rule",
    "medianstopping",
    "hyperband",
    "hb_bohb",
    "pbt",
    # "pb2",  commented out for now: https://github.com/ray-project/ray/issues/24815
    "resource_changing",
]


def _setup_ludwig_config(dataset_fp: str) -> Tuple[Dict, str]:
    input_features = [
        text_feature(name="utterance", cell_type="lstm", reduce_output="sum"),
        category_feature(vocab_size=2, reduce_input="sum"),
    ]

    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    rel_path = generate_data(input_features, output_features, dataset_fp)

    config = {
        INPUT_FEATURES: input_features,
        OUTPUT_FEATURES: output_features,
        COMBINER: {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 2, "learning_rate": 0.001},
    }

    config = merge_with_defaults(config)

    return config, rel_path


def _setup_ludwig_config_with_shared_params(dataset_fp: str) -> Tuple[Dict, str]:
    input_features = [
        text_feature(name="title", cell_type="rnn", reduce_output="sum", encoder="parallel_cnn"),
        text_feature(name="summary", cell_type="rnn"),
        category_feature(vocab_size=2, reduce_input="sum"),
        category_feature(vocab_size=3),
    ]

    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    rel_path = generate_data(input_features, output_features, dataset_fp)

    config = {
        INPUT_FEATURES: input_features,
        OUTPUT_FEATURES: output_features,
        COMBINER: {TYPE: "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 2, "learning_rate": 0.001},
    }

    return config, rel_path


@contextlib.contextmanager
def ray_start(num_cpus: Optional[int] = None, num_gpus: Optional[int] = None):
    res = ray.init(
        num_cpus=num_cpus,
        num_gpus=num_gpus,
        include_dashboard=False,
        object_store_memory=150 * 1024 * 1024,
    )
    try:
        yield res
    finally:
        ray.shutdown()


@pytest.fixture(scope="module")
def ray_cluster():
    gpus = [i for i in range(torch.cuda.device_count())]
    with ray_start(num_gpus=len(gpus)):
        yield


@pytest.mark.distributed
@pytest.mark.parametrize("search_alg", SEARCH_ALGS)
def test_hyperopt_search_alg(
    search_alg, csv_filename, tmpdir, ray_cluster, validate_output_feature=False, validation_metric=None
):
    config, rel_path = _setup_ludwig_config(csv_filename)

    hyperopt_config = HYPEROPT_CONFIG.copy()
    # finalize hyperopt config settings
    if search_alg == "dragonfly":
        hyperopt_config["search_alg"] = {
            "type": search_alg,
            "domain": "euclidean",
            "optimizer": "random",
        }
    elif search_alg is None:
        hyperopt_config["search_alg"] = {}
    else:
        hyperopt_config["search_alg"] = {
            "type": search_alg,
        }

    if validate_output_feature:
        hyperopt_config["output_feature"] = config["output_features"][0]["name"]
    if validation_metric:
        hyperopt_config["validation_metric"] = validation_metric

    update_hyperopt_params_with_defaults(hyperopt_config)

    parameters = hyperopt_config["parameters"]
    split = hyperopt_config["split"]
    output_feature = hyperopt_config["output_feature"]
    metric = hyperopt_config["metric"]
    goal = hyperopt_config["goal"]
    executor = hyperopt_config["executor"]
    search_alg = hyperopt_config["search_alg"]

    hyperopt_sampler = get_build_hyperopt_sampler(RAY)(parameters)
    hyperopt_executor = get_build_hyperopt_executor(RAY)(
        hyperopt_sampler, output_feature, metric, goal, split, search_alg=search_alg, **executor
    )
    raytune_results = hyperopt_executor.execute(config, dataset=rel_path, output_directory=tmpdir)
    assert isinstance(raytune_results, RayTuneResults)


@pytest.mark.distributed
def test_hyperopt_executor_with_metric(csv_filename, tmpdir, ray_cluster):
    test_hyperopt_search_alg(
        "variant_generator",
        csv_filename,
        tmpdir,
        ray_cluster,
        validate_output_feature=True,
        validation_metric=ACCURACY,
    )


@pytest.mark.distributed
@pytest.mark.parametrize("scheduler", SCHEDULERS)
def test_hyperopt_scheduler(
    scheduler, csv_filename, tmpdir, ray_cluster, validate_output_feature=False, validation_metric=None
):
    config, rel_path = _setup_ludwig_config(csv_filename)

    hyperopt_config = HYPEROPT_CONFIG.copy()
    # finalize hyperopt config settings
    if scheduler == "pb2":
        # setup scheduler hyperparam_bounds parameter
        min = hyperopt_config["parameters"]["trainer.learning_rate"]["lower"]
        max = hyperopt_config["parameters"]["trainer.learning_rate"]["upper"]
        hyperparam_bounds = {
            "trainer.learning_rate": [min, max],
        }
        hyperopt_config["executor"]["scheduler"] = {
            "type": scheduler,
            "hyperparam_bounds": hyperparam_bounds,
        }
    else:
        hyperopt_config["executor"]["scheduler"] = {
            "type": scheduler,
        }

    if validate_output_feature:
        hyperopt_config["output_feature"] = config["output_features"][0]["name"]
    if validation_metric:
        hyperopt_config["validation_metric"] = validation_metric

    update_hyperopt_params_with_defaults(hyperopt_config)

    parameters = hyperopt_config["parameters"]
    split = hyperopt_config["split"]
    output_feature = hyperopt_config["output_feature"]
    metric = hyperopt_config["metric"]
    goal = hyperopt_config["goal"]
    executor = hyperopt_config["executor"]
    search_alg = hyperopt_config["search_alg"]

    hyperopt_sampler = get_build_hyperopt_sampler(RAY)(parameters)

    # TODO: Determine if we still need this if-then-else construct
    if search_alg["type"] in {""}:
        with pytest.raises(ImportError):
            get_build_hyperopt_executor(RAY)(
                hyperopt_sampler, output_feature, metric, goal, split, search_alg=search_alg, **executor
            )
    else:
        hyperopt_executor = get_build_hyperopt_executor(RAY)(
            hyperopt_sampler, output_feature, metric, goal, split, search_alg=search_alg, **executor
        )
        raytune_results = hyperopt_executor.execute(config, dataset=rel_path, output_directory=tmpdir)
        assert isinstance(raytune_results, RayTuneResults)


@pytest.mark.distributed
@pytest.mark.parametrize("search_space", ["random", "grid"])
def test_hyperopt_run_hyperopt(csv_filename, search_space, tmpdir, ray_cluster):
    input_features = [
        text_feature(name="utterance", cell_type="lstm", reduce_output="sum"),
        category_feature(vocab_size=2, reduce_input="sum"),
    ]

    output_features = [category_feature(vocab_size=2, reduce_input="sum")]

    rel_path = generate_data(input_features, output_features, csv_filename)

    config = {
        INPUT_FEATURES: input_features,
        OUTPUT_FEATURES: output_features,
        COMBINER: {"type": "concat", "num_fc_layers": 2},
        TRAINER: {"epochs": 2, "learning_rate": 0.001},
    }

    output_feature_name = output_features[0]["name"]

    if search_space == "random":
        # random search will be size of num_samples
        search_parameters = {
            "trainer.learning_rate": {
                "lower": 0.0001,
                "upper": 0.01,
                "space": "loguniform",
            },
            output_feature_name
            + ".fc_layers": {
                "space": "choice",
                "categories": [
                    [{"output_size": 64}, {"output_size": 32}],
                    [{"output_size": 64}],
                    [{"output_size": 32}],
                ],
            },
            output_feature_name + ".output_size": {"space": "choice", "categories": [16, 21, 26, 31, 36]},
            output_feature_name + ".num_fc_layers": {"space": "randint", "lower": 1, "upper": 6},
        }
    else:
        # grid search space will be product each parameter size
        search_parameters = {
            "trainer.learning_rate": {"space": "grid_search", "values": [0.001, 0.005, 0.01]},
            output_feature_name + ".output_size": {"space": "grid_search", "values": [16, 21, 36]},
            output_feature_name + ".num_fc_layers": {"space": "grid_search", "values": [1, 3, 6]},
        }

    hyperopt_configs = {
        "parameters": search_parameters,
        "goal": "minimize",
        "output_feature": output_feature_name,
        "validation_metrics": "loss",
        "executor": {"type": "ray", "num_samples": 1 if search_space == "grid" else RANDOM_SEARCH_SIZE},
        "search_alg": {"type": "variant_generator"},
    }

    # add hyperopt parameter space to the config
    config["hyperopt"] = hyperopt_configs

    with tempfile.TemporaryDirectory() as tmpdir:
        hyperopt_results = hyperopt(config, dataset=rel_path, output_directory=tmpdir, experiment_name="test_hyperopt")
        if search_space == "random":
            assert hyperopt_results.experiment_analysis.results_df.shape[0] == RANDOM_SEARCH_SIZE
        else:
            # compute size of search space for grid search
            grid_search_size = 1
            for k, v in search_parameters.items():
                grid_search_size *= len(v["values"])
            assert hyperopt_results.experiment_analysis.results_df.shape[0] == grid_search_size

        # check for return results
        assert isinstance(hyperopt_results, HyperoptResults)

        # check for existence of the hyperopt statistics file
        assert os.path.isfile(os.path.join(tmpdir, "test_hyperopt", "hyperopt_statistics.json"))


@pytest.mark.distributed
@pytest.mark.parametrize("search_space", ["random"])
def test_hyperopt_run_hyperopt_with_shared_params(csv_filename, search_space):
    config, rel_path = _setup_ludwig_config_with_shared_params(csv_filename)

    categorical_feature_name = config[INPUT_FEATURES][2][NAME]
    output_feature_name = config[OUTPUT_FEATURES][0][NAME]

    cell_types_search_space = ["lstm", "gru"]
    vocab_size_search_space = list(range(4, 9))

    # Create default search space for text features with various cell types
    search_parameters = {
        "trainer.learning_rate": {
            "lower": 0.0001,
            "upper": 0.01,
            "space": "loguniform",
        },
        categorical_feature_name + ".vocab_size": {"space": "randint", "lower": 1, "upper": 3},
        DEFAULTS
        + "."
        + INPUT_FEATURES
        + "."
        + TEXT
        + ".cell_type": {"space": "choice", "categories": cell_types_search_space},
        DEFAULTS + "." + OUTPUT_FEATURES + "." + CATEGORY + ".vocab_size": {"space": "randint", "lower": 4, "upper": 8},
    }

    # add hyperopt parameter space to the config
    config[HYPEROPT] = {
        "parameters": search_parameters,
        "goal": "minimize",
        "output_feature": output_feature_name,
        "validation_metrics": "loss",
        "executor": {"type": "ray", "num_samples": 1 if search_space == "grid" else RANDOM_SEARCH_SIZE},
        "search_alg": {"type": "variant_generator"},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        hyperopt_results = hyperopt(config, dataset=rel_path, output_directory=tmpdir, experiment_name="test_hyperopt")

        # check for return results
        assert isinstance(hyperopt_results, HyperoptResults)

        hyperopt_results_df = hyperopt_results.experiment_analysis.results_df

        # check if trials match random_search_size
        assert hyperopt_results_df.shape[0] == RANDOM_SEARCH_SIZE

        # Check that the trials did sample from defaults in the search space
        for _, trial_row in hyperopt_results_df.iterrows():
            key_delimiter = "."
            # If ray 1.13, then the key name delimiter is different from ray 1.12
            if _ray112 is not None and not _ray112:
                key_delimiter = "/"
            cell_type = trial_row["config" + key_delimiter + "defaults.input_features.text.cell_type"].replace('"', "")
            vocab_size = trial_row["config" + key_delimiter + "defaults.output_features.category.vocab_size"]
            assert cell_type in cell_types_search_space
            assert vocab_size in vocab_size_search_space

        # check for existence of the hyperopt statistics file
        assert os.path.isfile(os.path.join(tmpdir, "test_hyperopt", "hyperopt_statistics.json"))

        # Check that each trial's text input/output configs got updated correctly
        for _, trial_row in hyperopt_results_df.iterrows():
            trial_dir = trial_row["trial_dir"]
            parameters_file_path = os.path.join(trial_dir, "test_hyperopt_run", "model", "model_hyperparameters.json")
            try:
                params_fd = open(parameters_file_path)
                model_parameters = json.load(params_fd)
                input_features = model_parameters[INPUT_FEATURES]
                output_features = model_parameters[OUTPUT_FEATURES]
                text_input_cell_types = set()  # Used to track that all text features have the same cell_type
                for input_feature in input_features:
                    if input_feature[TYPE] == TEXT:
                        cell_type = input_feature["cell_type"]
                        # Check that cell_type got updated from the sampler
                        assert cell_type in cell_types_search_space
                        text_input_cell_types.add(cell_type)
                    elif input_feature[TYPE] == CATEGORY:
                        vocab_size = input_feature["vocab_size"]
                        # Check that vocab_size is not in the output category search space
                        # Category input features have vocab_size in range [1,3] inclusive
                        assert vocab_size not in vocab_size_search_space
                # All text features with defaults should have the same cell_type for this trial
                assert len(text_input_cell_types) == 1
                for output_feature in output_features:
                    if output_feature[TYPE] == CATEGORY:
                        vocab_size = output_feature["vocab_size"]
                        # Check that vocab_size got updated from the sampler
                        assert vocab_size in vocab_size_search_space
                params_fd.close()
            # Likely unable to open trial dir so fail this test
            except Exception as e:
                raise RuntimeError(f"Failed to open hyperopt trial dir with error: \n\t {e}")
