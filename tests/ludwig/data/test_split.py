from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from ludwig.data.split import get_splitter

try:
    from ludwig.data.dataframe.dask import DaskEngine
    from ludwig.data.dataframe.pandas import PandasEngine
except ImportError:
    DaskEngine = Mock


@pytest.mark.parametrize(
    ("df_engine",),
    [
        pytest.param(PandasEngine(), id="pandas"),
        pytest.param(DaskEngine(_use_ray=False), id="dask", marks=pytest.mark.distributed),
    ],
)
def test_random_split(df_engine):
    nrows = 100
    npartitions = 10

    df = pd.DataFrame(np.random.randint(0, 100, size=(nrows, 3)), columns=["A", "B", "C"])
    if isinstance(df_engine, DaskEngine):
        df = df_engine.df_lib.from_pandas(df, npartitions=npartitions)

    probs = (0.7, 0.1, 0.2)
    split_params = {
        "type": "random",
        "probabilities": probs,
    }
    splitter = get_splitter(**split_params)

    backend = Mock()
    backend.df_engine = df_engine
    splits = splitter.split(df, backend)

    assert len(splits) == 3
    for split, p in zip(splits, probs):
        if isinstance(df_engine, DaskEngine):
            # Dask splitting is not exact, so apply soft constraint here
            assert np.isclose(len(split), int(nrows * p), atol=5)
        else:
            assert len(split) == int(nrows * p)


@pytest.mark.parametrize(
    ("df_engine",),
    [
        pytest.param(PandasEngine(), id="pandas"),
        pytest.param(DaskEngine(_use_ray=False), id="dask", marks=pytest.mark.distributed),
    ],
)
def test_fixed_split(df_engine):
    nrows = 100
    npartitions = 10
    thresholds = [60, 80, 100]

    df = pd.DataFrame(np.random.randint(0, 100, size=(nrows, 3)), columns=["A", "B", "C"])

    def get_split(v):
        if v < thresholds[0]:
            return 0
        if thresholds[0] <= v < thresholds[1]:
            return 1
        return 2

    df["split_col"] = df["C"].map(get_split).astype(np.int8)

    if isinstance(df_engine, DaskEngine):
        df = df_engine.df_lib.from_pandas(df, npartitions=npartitions)

    split_params = {
        "type": "fixed",
        "column": "split_col",
    }
    splitter = get_splitter(**split_params)

    backend = Mock()
    backend.df_engine = df_engine
    splits = splitter.split(df, backend)

    assert len(splits) == 3

    last_t = 0
    for split, t in zip(splits, thresholds):
        if isinstance(df_engine, DaskEngine):
            split = split.compute()

        assert np.all(split["C"] < t)
        assert np.all(split["C"] >= last_t)
        last_t = t
