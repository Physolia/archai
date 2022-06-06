import random
from typing import List, Optional, Dict
from overrides.overrides import overrides
import copy
import uuid
import sys

import torch
import torch_geometric

import tensorwatch as tw

from archai.common.common import logger
from archai.nas.arch_meta import ArchWithMetaData
from archai.nas.discrete_search_space import EncodableDiscreteSearchSpace

from archai.algos.evolution_pareto_image_seg.model import OPS, SegmentationNasModel


def random_neighbor(param_values: List[int], current_value: int):
    param_values = sorted(copy.deepcopy(param_values))
    param2idx = {param: idx for idx, param in enumerate(param_values)}

    # Gets the index of the closest value to the current value
    if current_value in param2idx:
        current_idx = param2idx[current_value]
    else:
        current_idx = param2idx[min(param2idx, key=lambda k: abs(k - current_value))]

    offset = random.randint(
        a=-1 if current_idx > 0 else 0,
        b=1 if current_idx < len(param_values) - 1 else 0
    )
    
    return param_values[current_idx + offset]


def rename_dag_node_list(node_list: List[Dict], prefix: str = '', 
                         rename_input_output: bool = True,
                         add_input_output: bool = False) -> List[Dict]:
    node_list = copy.deepcopy(node_list)
    prefix = prefix + '_' if prefix else ''

    rename_map = {}
    if not rename_input_output:
        rename_map = {'input': 'input', 'output': 'output'}

    for i, node in enumerate(node_list):
        if node['name'] not in rename_map:

            if add_input_output:
                new_name = (
                    'input' if i == 0 else 
                    'output' if i == len(node_list) - 1 else
                    prefix + f'layer_{i}'
                )
            else:
                new_name = prefix + f'layer_{i}'

            rename_map[node['name']] = new_name
            node['name'] = new_name
        
        if node['inputs']:
            node['inputs'] = [
                rename_map[inp_name] 
                for inp_name in node['inputs']
                if inp_name and inp_name in rename_map
            ]

    return node_list

class DiscreteSearchSpaceSegmentation(EncodableDiscreteSearchSpace):
    def __init__(self, datasetname:str, 
                 encoder_features: Optional[List[str]] = None,
                 min_mac:int=0, 
                 max_mac:int=sys.maxsize,
                 min_layers:int=1,
                 max_layers:int=12,
                 max_downsample_factor:int=16,
                 skip_connections:bool=True,
                 max_skip_connection_length:int=3,
                 max_scale_delta:int=1,
                 max_post_upsample_layers:int=3,
                 min_base_channels:int=8,
                 max_base_channels:int=48,
                 base_channels_binwidth:int=8,
                 min_delta_channels:int=8,
                 max_delta_channels:int=48,
                 delta_channels_binwidth:int=8,
                 downsample_prob_ratio:float=1.5,
                 op_subset: Optional[List[str]] = None,
                 mult_delta: bool = False,
                 img_size: int = 256):
        super().__init__()
        self.datasetname = datasetname
        assert self.datasetname != ''

        self.operations = list(OPS.keys())
        op_subset = op_subset.split(',') if op_subset is not None else []

        if op_subset:
            self.operations = [op for op in self.operations if op in op_subset]

        assert len(self.operations) > 0

        self.encoder_features = ['scale', 'channels', 'op'] if not encoder_features else encoder_features

        self.min_mac = min_mac
        self.max_mac = max_mac
        assert self.min_mac <= self.max_mac

        self.min_layers = min_layers
        self.max_layers = max_layers
        assert self.min_layers <= self.max_layers

        self.max_downsample_factor = max_downsample_factor
        assert self.max_downsample_factor in set([2, 4, 8, 16])

        self.max_skip_connection_length = max_skip_connection_length
        assert self.max_skip_connection_length > 0

        self.max_scale_delta = max_scale_delta
        assert self.max_scale_delta in set([1, 2, 3])

        self.post_upsample_layers_list = list(range(1, max_post_upsample_layers + 1))
        assert len(self.post_upsample_layers_list) < 5

        self.base_channels_list = list(range(min_base_channels, max_base_channels + 1, base_channels_binwidth))
        assert min_base_channels <= max_base_channels
        assert len(self.base_channels_list) > 1
        
        self.delta_channels_list = list(range(min_delta_channels, max_delta_channels + 1, delta_channels_binwidth))
        self.mult_delta = mult_delta
        assert min_delta_channels <= max_delta_channels
        assert len(self.delta_channels_list) >= 1

        self.skip_connections = skip_connections
        self.downsample_prob_ratio = downsample_prob_ratio
        self.img_size = img_size
        

    @overrides
    def random_sample(self)->ArchWithMetaData:
        ''' Uniform random sample an architecture within the limits of min and max MAC '''

        found_valid = False
        while not found_valid:
            
            # randomly pick number of layers    
            num_layers = random.randint(self.min_layers, self.max_layers)

            model = SegmentationNasModel.sample_model(
                base_channels_list=self.base_channels_list,
                delta_channels_list=self.delta_channels_list,
                post_upsample_layer_list=self.post_upsample_layers_list,
                nb_layers=num_layers,                                                         
                max_downsample_factor=self.max_downsample_factor,
                skip_connections=self.skip_connections,
                max_skip_connection_length=self.max_skip_connection_length,             
                max_scale_delta=self.max_scale_delta,
                op_subset=self.operations,
                mult_delta=self.mult_delta,
                img_size=self.img_size
            )

            # check if the model is within desired bounds    
            input_tensor_shape = (1, 3, model.img_size, model.img_size)
            model_stats = tw.ModelStats(model, input_tensor_shape, clone_model=True)
            if model_stats.MAdd > self.min_mac and model_stats.MAdd < self.max_mac:
                found_valid = True

            meta_data = {
                'datasetname': self.datasetname,
                'archid': model.to_hash(),
                'parent': None,
                'macs': model_stats.MAdd
            }
            arch_meta = ArchWithMetaData(model, meta_data)
            
        return arch_meta
        

    @overrides
    def get_neighbors(self, base_model: ArchWithMetaData, patience: int = 20) -> List[ArchWithMetaData]:
        parent_id = base_model.metadata['archid']
        neighbors = []
        nb_tries = 0

        while nb_tries < patience and len(neighbors) == 0:
            nb_tries += 1
            graph = copy.deepcopy(list(base_model.arch.graph.values()))
            channels_per_scale = copy.deepcopy(base_model.arch.channels_per_scale)

            # sanity check the graph
            assert len(graph) > 1
            assert graph[-1]['name'] == 'output'
            assert graph[0]['name'] == 'input'

            # `base_channels` and `delta_channels` mutation
            channels_per_scale = {
                'base_channels': random_neighbor(self.base_channels_list, channels_per_scale['base_channels']),
                'delta_channels': random_neighbor(self.delta_channels_list, channels_per_scale['delta_channels']),
                'mult_delta': base_model.arch.channels_per_scale['mult_delta']
            }

            # `post_upsample_layers` mutation
            post_upsample_layers = random_neighbor(
                self.post_upsample_layers_list,
                base_model.arch.post_upsample_layers
            )

            # pick a node at random (but not input node)
            # and change its operator at random
            # and its input sources
            chosen_node_idx = random.randint(1, len(graph) - 1)
            node = graph[chosen_node_idx]
            
            if node['name'] != 'output':
                node['op'] = random.choice(self.operations)
            
            # choose up to k inputs from previous nodes
            max_inputs = 3 # TODO: make config 

            # Gets the out connections for each node
            edges = [tuple(k.split('-')) for k in base_model.arch.edge_dict.keys()]
            out_degree = lambda x: len([(orig, dest) for orig, dest in edges if orig == x])

            if node['name'] != 'input':
                k = min(chosen_node_idx, random.randint(1, max_inputs))
                input_idxs = random.sample(range(chosen_node_idx), k)

                # Removes everything except inputs that have out degree == 1
                node['inputs'] = [input for input in node['inputs'] if out_degree(input) <= 1]

                # Adds `k` new inputs
                node['inputs'] += [
                    graph[idx]['name'] for idx in input_idxs
                    if graph[idx]['name'] not in node['inputs']
                ]

            # compile the model
            try:
                nbr_model = SegmentationNasModel(
                    graph, channels_per_scale, post_upsample_layers,
                    img_size=self.img_size    
                )
                out_shape = nbr_model.validate_forward(
                    torch.randn(1, 3, nbr_model.img_size, nbr_model.img_size)
                ).shape

                assert out_shape == torch.Size([1, 19, nbr_model.img_size, nbr_model.img_size])
            
            except Exception as e:
                logger.info(f'Neighbor generation {base_model.arch.to_hash()} -> {nbr_model.to_hash()} failed')
                logger.info(str(e))
                continue

            # check if the model is within desired bounds    
            input_tensor_shape = (1, 3, nbr_model.img_size, nbr_model.img_size)
            model_stats = tw.ModelStats(nbr_model, input_tensor_shape, clone_model=True)
            if model_stats.MAdd > self.min_mac and model_stats.MAdd < self.max_mac:
                neighbors += [ArchWithMetaData(nbr_model, {
                    'datasetname': self.datasetname,
                    'archid': nbr_model.to_hash(),
                    'parent': parent_id,
                    'macs': model_stats.MAdd
                })]
            else:
                logger.info(f'Model {base_model.arch.to_hash()} neighbor MACs {model_stats.MAdd}'
                            f' falls outside of acceptable range. Retrying (nb_tries = {nb_tries})')

        return neighbors

    @overrides
    def get_arch_repr(self, arch: ArchWithMetaData) -> torch_geometric.data.Data:
        graph = arch.arch.graph
        scale_levels = [1, 2, 4, 8, 16] # TODO: get this dynamically

        # Gets node features
        onehot = lambda x, categories: [category == x for category in categories]
        x = []

        if 'scale' in self.encoder_features:
            x.append(
                [onehot(n['scale'], scale_levels) for n in graph.values()]
            )

        if 'op' in self.encoder_features:
            x.append(
                [onehot(n['op'], self.operations) for n in graph.values()]
            )
        
        if 'channels' in self.encoder_features:
            ch_map = arch.arch.channels_per_scale
            x.append(
                [[ch_map['base_channels'] / 64, ch_map['delta_channels'] / 64] for _ in graph.values()]
            )

        # Builds node feature matrix
        x = torch.cat([torch.tensor(xi, dtype=torch.float) for xi in x], axis=1)
        assert len(x.shape) == 2 and x.shape[0] == len(graph), 'Invalid node features'

        # Builds edge_index COO matrix
        nodename2idx = {n['name']: i for i, n in enumerate(graph.values())}
        edges = [
            [nodename2idx[v_name], nodename2idx[u['name']]]
            for u in graph.values() if u['inputs']
            for v_name in u['inputs']
        ]
        edge_index = torch.tensor(edges, dtype=torch.long).T

        # Returns torch_geometric.data.Data object
        return torch_geometric.data.Data(x=x, edge_index=edge_index, archid=arch.metadata['archid'])

    def load_from_graph(self, graph: List[Dict], channels_per_scale: Dict,
                        post_upsample_layers: int = 1) -> ArchWithMetaData:
        model = SegmentationNasModel(
            graph, channels_per_scale, post_upsample_layers,
            img_size=self.img_size
        )
        
        return ArchWithMetaData(model, {
            'datasetname': self.datasetname,
            'archid': model.to_hash(),
            'parent': None
        })

    def load_from_file(self, config_file: str) -> ArchWithMetaData:
        model = SegmentationNasModel.from_file(config_file, img_size=self.img_size)
        
        return ArchWithMetaData(model, {
            'datasetname': self.datasetname,
            'archid': model.to_hash(),
            'parent': None
        })
   
    def crossover(self, model_1: ArchWithMetaData, model_2: ArchWithMetaData, 
                  patience: int = 10) -> Optional[ArchWithMetaData]:
        # Chooses randomly left and right models
        left_m, right_m = random.sample([model_1, model_2], 2)
        left_g, right_g = [list(m.arch.graph.values()) for m in [left_m, right_m]]

        # Renames nodes to avoid name collision
        left_g, right_g = rename_dag_node_list(left_g, 'left'), rename_dag_node_list(right_g, 'right')

        # Stores node names
        left_n, right_n = [[n['name'] for n in g] for g in [left_g, right_g]]

        if len(left_n) <= 2 or len(right_n) <= 2:
            return

        # Tries to merge left_m and right_m
        result_g = None
        nb_tries = 0

        while result_g is None and nb_tries < patience:
            left_g, right_g = copy.deepcopy(left_g), copy.deepcopy(right_g)
            nb_tries += 1

            # Samples a pivot node from the left model
            left_pivot_idx = random.randint(1, len(left_n) - 2)
            left_pivot = left_n[left_pivot_idx]

            # Samples a pivot node from the right model w/ the same scale as the left_pivot
            # excluding input and output nodes
            right_candidates = [
                i
                for i, fields in enumerate(right_g)
                if fields['scale'] == left_g[left_pivot_idx]['scale'] and\
                0 < i < (len(right_n) - 1)
            ]

            if len(right_candidates) > 0:
                # Picks a right pivot
                right_pivot_idx = random.choice(right_candidates)

                # Splits right_g and left_g using the pivot nodes
                left_half = left_g[:left_pivot_idx + 1]
                right_half = right_g[right_pivot_idx:]

                # Gets node2idx for right model
                right_node2idx = {node: i for i, node in enumerate(right_n)}

                # Corrects connections from right_g
                for fields in right_half[::-1]:
                    for inp_idx, inp in enumerate(fields['inputs']):

                        # Checks if this connection falls outside of right_half
                        if inp not in right_n[right_pivot_idx:]:
                            # Finds a new starting node to connect this edge
                            # with the same original input scale
                            candidates = [
                                n['name'] for n in left_half
                                if n['scale'] == right_g[right_node2idx[inp]]['scale']
                            ]

                            fields['inputs'][inp_idx] = (
                                random.choice(candidates) if len(candidates) > 0
                                else None
                            )
                            
                # Renames end node
                right_half[-1]['name'] = 'output'

                # Connects left_half and right_half
                if left_pivot not in right_half[0]['inputs']:
                    right_half[0]['inputs'].append(left_pivot)

                # Merge and rename nodes
                result_g = rename_dag_node_list(left_half + right_half, add_input_output=True)
        
        if result_g:
            # Pick `channels_per_scale` and `post_upsample_layers` from left_m or right_m
            ch_map = random.choice(
                [left_m.arch.channels_per_scale, right_m.arch.channels_per_scale]
            )

            post_upsample_layers = random.choice(
                [left_m.arch.post_upsample_layers, right_m.arch.post_upsample_layers]
            )

            # Re-builds model and adds crossover metadata
            result_g = self.load_from_graph(
                result_g, 
                {'base_channels': ch_map['base_channels'],
                 'delta_channels': ch_map['delta_channels'],
                 'mult_delta': ch_map['mult_delta']},
                post_upsample_layers
            )

            result_g.metadata['parents'] = left_m.metadata['archid'] + ',' + right_m.metadata['archid']

            return result_g
