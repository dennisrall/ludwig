import logging
import math

import numpy as np
import torch
from pytorch_metric_learning.losses import NTXentLoss

from ludwig.data.dataset.base import Dataset
from ludwig.models.ecd import ECD
from ludwig.models.trainer import Trainer
from ludwig.modules.fully_connected_modules import FCStack
from ludwig.utils.torch_utils import LudwigModule

logger = logging.getLogger(__name__)


def shuffle_along_axis(a, axis):
    idx = np.random.rand(*a.shape).argsort(axis=axis)
    return np.take_along_axis(a, idx, axis=axis)


class ScarfModel(LudwigModule):
    def __init__(
            self,
            model: ECD,
            corruption_rate: float = 0.6,
    ):
        self.input_features = model.input_features
        self.output_features = torch.nn.ModuleDict()
        self.combiner = model.combiner
        self.g = FCStack(
            first_layer_input_size=self.combiner.output_shape,
            num_layers=2,
            default_fc_size=256,
        )
        self.q = math.floor(corruption_rate * len(self.input_features))

    def forward(self, inputs):
        if isinstance(inputs, tuple):
            inputs, _ = inputs

        assert inputs.keys() == self.input_features.keys()
        for input_feature_name, input_values in inputs.items():
            inputs[input_feature_name] = torch.from_numpy(input_values)

        anchor_embeddings = self._embed(inputs)
        corrupted_embeddings = self._embed(self._corrupt(inputs))
        return anchor_embeddings, corrupted_embeddings

    def _corrupt(self, inputs):
        # per SCARF paper: select a subset of the features and replace them with a
        # sample from the marginal training distribution

        # compute augmentations for all features
        batch_size = None
        augmentations = []
        for input_feature_name, input_values in inputs.items():
            batch_size = len(input_values)
            encoder = self.input_features[input_feature_name]
            augmentations.append(encoder.sample_augmentations(
                batch_size,
                self.training_set_metadata[input_feature_name]
            ))

        # construct N x M matrix, where every row (batch) samples q features
        # to corrupt
        m = len(inputs)
        mask = np.zeros((batch_size, m), dtype=int)
        mask[:, :self.q] = 1
        mask = shuffle_along_axis(mask, axis=1)

        corrupted_inputs = {}
        for j, (input_feature_name, input_values) in enumerate(inputs.items()):
            encoder = self.input_features[input_feature_name]
            augmentations = encoder.sample_augmentations(
                len(input_values),
                self.training_set_metadata[input_feature_name]
            )

            corrupted_values = np.where(
                mask[:, j],
                augmentations,
                input_values
            )
            corrupted_values[input_feature_name] = corrupted_inputs
        return corrupted_inputs

    def _embed(self, inputs):
        encoder_outputs = {}
        for input_feature_name, input_values in inputs.items():
            encoder = self.input_features[input_feature_name]
            encoder_output = encoder(input_values)
            encoder_outputs[input_feature_name] = encoder_output

        combiner_outputs = self.combiner(encoder_outputs)
        return self.g(combiner_outputs['combiner_output'])

    def train_loss(self, targets, predictions, regularization_lambda=0.0):
        anchor_embeddings, corrupted_embeddings = predictions
        embeddings = torch.cat((anchor_embeddings, corrupted_embeddings))
        indices = torch.arange(0, anchor_embeddings.size(0), device=anchor_embeddings.device)
        labels = torch.cat((indices, indices))

        loss = NTXentLoss(temperature=0.10)
        return loss(embeddings, labels)


class Pretrainer(Trainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def pretrain(
            self,
            model: ECD,
            dataset: Dataset,
            **kwargs
    ):
        ssl_model = ScarfModel(model)
        self.train(
            ssl_model,
            training_set=dataset,
            **kwargs
        )