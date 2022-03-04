# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""NVIDIA's Memory Transformer (Transformer-XL) configuration.
"""

from typing import Any, Dict

from archai.nlp.models.config_base import Config


class MemTransformerLMConfig(Config):
    @property
    def default(self) -> Dict[str, Any]:
        return {
            'd_head': -1,
            'n_token': 267736,
            'dropout': 0.1,
            'dropatt': 0.0,
            'd_embed': -1,
            'div_val': 4,
            'pre_lnorm': False,
            'tgt_len': 192,
            'ext_len': 0,
            'mem_len': 192,
            'same_length': False,
            'attn_type': 0,
            'clamp_len': -1,
            'sample_softmax': -1,
            'cutoffs': [19997, 39997, 199997],
            'tie_projs': [False, True, True, True],
            'tie_weight': True,
            'dtype': None,
            'primer_conv': False,
            'primer_square': False,
            'use_cache': False
        }

    @property
    def search(self) -> Dict[str, Any]:
        return {
            'n_layer': {
                'per_layer': False,
                'value': [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
            },
            'd_model': {
                'per_layer': False,
                'value': list(range(128, 1024, 64))
            },
            'd_inner': {
                'per_layer': True,
                'value': list(range(128, 4096, 64))
            },
            'n_head': {
                'per_layer': True,
                'value': [2, 4, 8]
            }
        }
