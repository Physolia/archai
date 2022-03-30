# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Constraints pipelines used throughout the search procedure.
"""

from typing import Any, Dict, Optional, Tuple, Union

import torch

from archai.nlp.nas.nas_utils.constraints.onnx_constraints import (measure_onnx_disk_memory,
                                                                   measure_onnx_inference_latency,
                                                                   measure_onnx_parameters)
from archai.nlp.nas.nas_utils.constraints.torch_constraints import (measure_torch_inference_latency,
                                                                    measure_torch_parameters,
                                                                    measure_torch_peak_memory,
                                                                    measure_torch_perplexity)

# Latency upper bound on different device targets
# Any model with more latency than this will be removed from consideration during search
DEVICE_LATENCY_CONSTRAINT = {
    'XeonE5-2690': 10.0,
    'corei7': 10.0,
    'corei5': 10.0,
    'D3_V2': 10.0,
}


class ConstraintPipeline:
    """Defines a constraint pipeline that enables multi-typing (PyTorch and ONNX).

    """

    def __init__(self,
                 use_quantization: bool,
                 use_median: bool,
                 batch_size: int,
                 seq_len: int,
                 n_threads: int,
                 n_trials: int,
                 device: str) -> None:
        """Overrides initialization method.

        Args:
            use_quantization: Whether measurement should be calculated with quantizated model or not.
            use_median: Whether should use median instead of mean for measurement.
            batch_size: Batch size.
            seq_len: Sequence length.
            n_threads: Number of inference threads.
            n_trials: Number of times to repeat the measurement.
            device: Device where should be measured.

        """

        self.use_quantization = use_quantization
        self.use_median = use_median

        self.batch_size = batch_size
        self.seq_len = seq_len
        
        self.n_threads = n_threads
        self.n_trials = n_trials

        self.device = device

    def __call__() -> None:
        """Invokes the built-in call method.

        This method has to be implemented for child classes according to
        their pipeline usage.

        In Archai, we adopt the following:
            - PyTorch: call(model)
            - ONNX: call(model_config)

        Raises:
            Not implemented error.

        """

        raise NotImplementedError


class TorchConstraintPipeline(ConstraintPipeline):
    """Defines a PyTorch-based constraint pipeline.

    """

    def __init__(self,
                 use_training_proxy: Optional[bool] = False,
                 use_quantization: Optional[bool] = False,
                 use_median: Optional[bool] = False,
                 training_dataset: Optional[str] = 'wt103',
                 training_vocab_type: Optional[str] = 'word',
                 training_vocab_size: Optional[int] = 10000,
                 training_max_step: Optional[int] = 100,
                 batch_size: Optional[int] = 1,
                 seq_len: Optional[int] = 192,
                 n_threads: Optional[int] = 1,
                 n_trials: Optional[int] = 10,
                 device: Optional[str] = 'cpu') -> None:
        """Overrides initialization method.

        Args:
            use_training_proxy: Whether measurement should be calculated with decoder parameters instead of perplexity.
            use_quantization: Whether measurement should be calculated with quantizated model or not.
            use_median: Whether should use median instead of mean for measurement.
            training_dataset: Training dataset (if not using proxy).
            training_vocab_type: Type of training vocabulary (if not using proxy).
            training_vocab_size: Training vocabulary size (if not using proxy).
            training_max_step: Maximum training steps (if not using proxy).
            batch_size: Batch size.
            seq_len: Sequence length.
            n_threads: Number of inference threads.
            n_trials: Number of times to repeat the measurement.
            device: Device where should be measured.

        """

        self.use_training_proxy = use_training_proxy
        self.training_dataset = training_dataset
        self.training_vocab_type = training_vocab_type
        self.training_vocab_size = training_vocab_size
        self.training_max_step = training_max_step

        super().__init__(use_quantization, use_median, batch_size,
                         seq_len, n_threads, n_trials, device)

    def __call__(self, model: torch.nn.Module) -> Tuple[Union[int, float], int, float, float]:
        """Invokes the built-in call method.

        Args:
            model: Model to be used within constraint pipeline.

        Returns:
            (Tuple[Union[int, float], int, float, float]): Decoder parameters or
                validation perplexity, total parameters, latency and peak memory.

        """

        if self.use_training_proxy:
            # Number of decoder (non-embedding) parameters
            measure_torch_proxy = measure_torch_parameters(model, ['non_embedding'])
        else:
            # Validation perplexity
            measure_torch_proxy = measure_torch_perplexity(model,
                                                           dataset=self.training_dataset,
                                                           vocab_type=self.training_vocab_type,
                                                           vocab_size=self.training_vocab_size,
                                                           max_step=self.training_max_step)

        return (
            # Proxy (either decoder parameters or validation perplexity)
            measure_torch_proxy,

            # Number of total parameters
            measure_torch_parameters(model, ['total']),

            # Inference latency
            measure_torch_inference_latency(model,
                                            self.use_quantization,
                                            self.use_median,
                                            self.batch_size,
                                            self.seq_len,
                                            self.n_threads,
                                            self.n_trials,
                                            self.device),

            # Peak memory usage
            measure_torch_peak_memory(model,
                                      self.use_quantization,
                                      self.batch_size,
                                      self.seq_len,
                                      self.n_threads,
                                      self.device)
        )


class ONNXConstraintPipeline(ConstraintPipeline):
    """Defines a ONNX-based constraint pipeline.

    """

    def __init__(self,
                 use_quantization: Optional[bool] = False,
                 use_median: Optional[bool] = False,
                 batch_size: Optional[int] = 1,
                 seq_len: Optional[int] = 192,
                 n_trials: Optional[int] = 10) -> None:
        """Overrides initialization method.

        Args:
            use_quantization: Whether measurement should be calculated with quantizated model or not.
            use_median: Whether should use median instead of mean for measurement.
            batch_size: Batch size.
            seq_len: Sequence length.
            n_trials: Number of times to repeat the measurement.

        """

        super().__init__(use_quantization, use_median, batch_size,
                         seq_len, 1, n_trials, 'cpu')

    def __call__(self, model_type: str, model_config: Dict[str, Any]) -> Tuple[int, int, float, float]:
        """Invokes the built-in call method.

        Args:
            model_type: Type of model.
            model_config: Model's configuration.

        Returns:
            (Tuple[int, int, float, float]): Decoder parameters, total parameters,
                latency and disk memory.
            
        """

        return (
            # Number of decoder (non-embedding) parameters
            measure_onnx_parameters(model_type, model_config, 'non_embedding'),

            # Number of total parameters
            measure_onnx_parameters(model_type, model_config, 'total'),

            # Inference latency
            measure_onnx_inference_latency(model_type,
                                           model_config,
                                           self.use_quantization,
                                           self.use_median,
                                           self.batch_size,
                                           self.seq_len,
                                           self.n_trials),

            # Disk memory
            measure_onnx_disk_memory(model_type,
                                     model_config,
                                     self.use_quantization)
        )
