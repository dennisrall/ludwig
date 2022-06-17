from typing import Optional

from marshmallow_dataclass import dataclass

from ludwig.schema import utils as schema_utils
from ludwig.schema.features import base
from ludwig.schema.features.preprocessing import BasePreprocessingConfig, PreprocessingDataclassField


@dataclass
class TimeseriesInputFeatureConfig(base.BaseFeatureConfig):
    """
    TimeseriesInputFeatureConfig is a dataclass that configures the parameters used for a timeseries input feature.
    """

    preprocessing: BasePreprocessingConfig = PreprocessingDataclassField(
        feature_type='timeseries'
    )

    encoder: Optional[str] = schema_utils.StringOptions(
        ["passthrough", "parallel_cnn", "stacked_cnn", "stacked_parallel_cnn", "rnn", "cnnrnn", "transformer"],
        default="parallel_cnn",
        description="Encoder to use for this timeseries feature.",
    )

    # TODO(#1673): Need some more logic here for validating against input features
    tied: Optional[str] = schema_utils.String(
        default=None,
        allow_none=True,
        description="Name of input feature to tie the weights of the encoder with.  It needs to be the name of a "
                    "feature of the same type and with the same encoder parameters.",
    )
