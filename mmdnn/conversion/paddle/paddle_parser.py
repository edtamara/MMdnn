#----------------------------------------------------------------------------------------------
#  Copyright (c) Microsoft Corporation. All rights reserved.
#  Licensed under the MIT License. See License.txt in the project root for license information.
#----------------------------------------------------------------------------------------------

import os
from six import string_types as _string_types
import paddle.v2 as paddle
import paddle.trainer_config_helpers.layers as layers
import numpy as np
from mmdnn.conversion.paddle.paddle_graph import PaddleGraph
import mmdnn.conversion.common.IR.graph_pb2 as graph_pb2
from mmdnn.conversion.common.IR.graph_pb2 import NodeDef, GraphDef, DataType
from mmdnn.conversion.common.DataStructure.parser import Parser
from mmdnn.conversion.common.utils import *


class PaddleParser(Parser):

    dtype_map = {
        "float16" : graph_pb2.DT_FLOAT16,
        "float32" : graph_pb2.DT_FLOAT32,
        "float64" : graph_pb2.DT_FLOAT64,
        "int16"   : graph_pb2.DT_INT16,
        "int32"   : graph_pb2.DT_INT32,
        "int64"   : graph_pb2.DT_INT64,
        "uint8"   : graph_pb2.DT_UINT8,
        "uint16"  : graph_pb2.DT_UINT16
    }

    activation_map = {
        "relu"          : "Relu",
        'softmax'       : "Softmax",
        'sigmoid'       : "Sigmoid",
        "tanh"          : "Tanh",
        "elu"           : "Elu",
        "relu6"         : "Relu6",
        'softplus'      : 'Softplus',
        'softsign'      : 'Softsign',
        'hard_sigmoid'  : 'HardSigmoid'
    }


    def _load_model(self, model_network_path, model_weight_path):
        """Load a paddle model from disk

        Parameters
        ----------
        model_network_path: str
            Path where the model network path is (json file)

        model_weight_path: str
            Path where the model network weights are (hd5 file)

        Returns
        -------
        model: A keras model
        """
        DATA_DIM = 3 * 224 * 224
        CLASS_DIM = 1001
        image = paddle.layer.data(name="image", type=paddle.data_type.dense_vector(DATA_DIM))
        out = resnet.resnet_imagenet(image, class_dim=CLASS_DIM)
        loaded_model = out


        if model_weight_path:
            model_weight_path = '/Users/kit/Downloads/models/image_classification/models/Paddle_ResNet50.tar.gz'
            if os.path.isfile(model_weight_path):
                parameters = paddle.parameters.Parameters.from_tar(gzip.open(model_weight_path, 'r'))
                self.weight_loaded = True
                print("Network file [{}] and [{}] is loaded successfully.".format(model_network_path, model_weight_path))

            else:
                print("Warning: Weights File [%s] is not found." % (model_weight_path))

        return loaded_model, parameters

    @property
    def src_graph(self):
        return self.paddle_graph


    def __init__(self, model, parameters):
        super(PaddleParser, self).__init__()


        # Build network graph
        # self.data_format = _keras.backend.image_data_format()
        self.paddle_graph = PaddleGraph(model)
        self.paddle_graph.build()
        self.get_spec(model)
        self.parameters = parameters
        self.weight_loaded = True



    def get_spec(self, model):
        # credit to https://github.com/lcy-seso/paddle_example/blob/master/seq_slice_demo/test_seq_slice.py#L55
        # Paddle Official: https://github.com/PaddlePaddle/Paddle/blob/develop/python/paddle/v2/layer.py#L263-L322
        # Pb Definition: https://github.com/PaddlePaddle/Paddle/blob/d02a68c4472d3b85559f82c026896bf2cf563b07/proto/ModelConfig.proto
        from paddle.v2.layer import parse_network
        self.spec_dict = dict()
        net_pb = parse_network(model)
        for l in net_pb.layers:
            self.spec_dict[l.name] = l


    def gen_IR(self):
        for layer in self.paddle_graph.topological_sort:
            current_node = self.paddle_graph.get_node(layer)
            print(current_node.name)
        for layer in self.paddle_graph.topological_sort:
            current_node = self.paddle_graph.get_node(layer)
            print(current_node.name)
            node_type = current_node.type
            print(node_type)
            if hasattr(self, "rename_" + node_type):
                func = getattr(self, "rename_" + node_type)
                func(current_node)
            else:
                print("KerasParser has not supported operator [%s]." % (node_type))
                self.rename_UNKNOWN(current_node)



    @staticmethod
    def _set_output_shape(source_node, IR_node, output_shapes):
        shape = graph_pb2.TensorShape()
        for output_shape in output_shapes:
            new_dim = shape.dim.add()
            new_dim.size = output_shape
        IR_node.attr["_output_shapes"].list.shape.extend([shape])


    @staticmethod
    def _copy_and_reop(source_node, IR_node, new_op = None):
        IR_node.name = source_node.name.strip('_')
        IR_node.op = source_node.type if new_op == None else new_op

        if hasattr(source_node.layer, "dtype"):
            IR_node.attr["dtype"].type = PaddleParser.dtype_map[source_node.layer.dtype]

        # PaddleParser._set_output_shape(source_node, IR_node)


    @staticmethod
    def _copy_shape(source_node, target_node, output_shapes):
        for dim in output_shapes:
            new_dim = target_node.attr["shape"].shape.dim.add()
            new_dim.size =  dim


    @staticmethod
    def _convert_dataformat(source_node, target_node):
        if source_node.keras_layer.data_format == 'channels_last':
            target_node.attr["data_format"].s = "NHWC"
        elif source_node.keras_layer.data_format == 'channels_first':
            target_node.attr["data_format"].s = "NCHW"
        else:
            print("Warning: [%s] don't have data format info." % (source_node.keras_layer.name))




    def _defuse_activation(self, source_node):
        src_spec = self.spec_dict[source_node.name]

        IR_node = self.IR_graph.node.add()
        IR_node.name = source_node.real_name.strip('_') + "_activation"
        IR_node.op = PaddleParser.activation_map[src_spec.active_type.encode()]
        IR_node.input.append(source_node.real_name.strip('_'))

        source_node.real_name = IR_node.name


    def _convert_merge(self, source_node, new_name = None):
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node, new_name)

        # input edge
        self.convert_inedge(source_node, IR_node)

        # For concat axis
        if hasattr(source_node.layer, 'axis'):
            IR_node.attr['axis'].i = -1
        return IR_node



    def rename_UNKNOWN(self, source_node):
        print (source_node.layer.get_config())

        # only for training
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node)

        # input edge
        self.convert_inedge(source_node, IR_node)


    def rename_conv(self, source_node):
        IR_node = self.IR_graph.node.add()

        # input edge
        self.convert_inedge(source_node, IR_node)

        # layer and spec
        conv_node = source_node.layer
        conv_spec = self.spec_dict[source_node.name]

        spec = conv_spec.inputs[0].conv_conf



        # width <=> x or height <=> y
        width = spec.filter_size
        height = spec.filter_size_y if spec.HasField('filter_size_y') else spec.filter_size
        inputchannel = spec.channels
        outputchannel = conv_spec.num_filters
        stride_x = spec.stride
        stride_y = spec.stride_y if spec.HasField('stride_y') else stride_x
        padding_x = spec.padding
        padding_y = spec.padding_y if spec.HasField('padding_y') else padding_x
        dilation_x = spec.dilation
        dilation_y = spec.dilation_y if spec.HasField('dilation_y') else dilation_x
        output_x = spec.output_x
        output_y = spec.output_y if spec.HasField('output_y') else output_x
        input_x = spec.img_size
        input_y = spec.img_size_y if spec.HasField('img_size_y') else input_x


        # output shape
        output_shapes = [-1, outputchannel, output_y, output_x]
        PaddleParser._set_output_shape(source_node, IR_node, output_shapes)


        kwargs = dict()

        if conv_spec.type == 'exconv' or 'cudnn_conv':
            # name, op
            PaddleParser._copy_and_reop(source_node, IR_node, "Conv")
        else:
            kwargs['isDeconvolution'] = True
            PaddleParser._copy_and_reop(source_node, IR_node, "ConvTranspose")


        w_name = conv_spec.inputs[0].input_parameter_name
        w = self.parameters.get(w_name)


        self.set_weight(IR_node.name, 'weights', w.reshape([outputchannel, inputchannel, height, width]).transpose([ 2, 3, 1, 0]))

        #  it should be in the shape of height x width x inputchannel x outputchannel



        kwargs['kernel_shape'] = [height, width, inputchannel, outputchannel]

        # use_bias: TODO
        kwargs['use_bias'] = False

        # pad_dim
        pad_dim = [0, 0, padding_x, padding_y, padding_x, padding_y, 0, 0]

        # fail report because of auto_pad
        # if dilation_x == 1 and dilation_y == 1:
        #     if output_x * stride_x == input_x and output_y * stride_y == input_y:
        #         auto_pad = "SAME"
        #         kwargs['auto_pad'] = auto_pad
        #     elif output_x * stride_x == input_x - width + 1 and output_y * stride_y == input_y - height + 1:
        #         auto_pad = "VALID"
        #         kwargs['auto_pad'] = auto_pad

        if input_x == output_x and input_y == output_y:
            auto_pad = "SAME"
        else:
            auto_pad = "SAME"

        pad_dim = convert_tf_pad_to_onnx(pad_dim)
        kwargs['pads'] = pad_dim

        kwargs['group'] = spec.groups

        kwargs['dilation'] = [1, dilation_x, dilation_y, 1]

        kwargs['strides'] = [1, stride_x, stride_y, 1]

        assign_IRnode_values(IR_node, kwargs)

        # defuse the activation layer

        if conv_spec.HasField('active_type') and  conv_spec.active_type != '':
            self._defuse_activation(source_node)


    def rename_batch_norm(self, source_node):
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node, "BatchNorm")

        # input edge
        self.convert_inedge(source_node, IR_node)

        # layer and spec
        bn_node = source_node.layer
        bn_spec = self.spec_dict[source_node.name]


        IR_node.attr['scale'].b = True
        IR_node.attr['bias'].b = bn_spec.HasField('bias_parameter_name')

        w_name = bn_spec.inputs[0].input_parameter_name
        mean_name = bn_spec.inputs[1].input_parameter_name
        var_name = bn_spec.inputs[2].input_parameter_name
        bias_name = bn_spec.bias_parameter_name

        gamma = self.parameters.get(w_name)
        mean = self.parameters.get(mean_name)
        variance = self.parameters.get(var_name)
        beta = self.parameters.get(bias_name)

        # channels_first, then axis = 1
        IR_node.attr['axis'].i = -1

        # epsilon
        IR_node.attr['epsilon'].f = bn_spec.epsilon

        # compute adjusted parameters
        # Reference: parameter transformation https://github.com/apple/coremltools/issues/153
        f = 1.0 / np.sqrt(variance +  bn_spec.epsilon)
        gamma1 = gamma*f
        beta1 = beta - gamma*mean*f
        mean[:] = 0.0 #mean
        variance[:] = 1.0 - .00001 #stddev

        # convert type because of tensorflow
        gamma1 = gamma1.astype(np.float32)
        beta1 = beta1.astype(np.float32)
        mean = mean.astype(np.float32)
        variance = variance.astype(np.float32)

        if IR_node.attr['scale'].b:
            self.set_weight(IR_node.name, "scale", gamma1)

        if IR_node.attr['bias'].b:
            self.set_weight(IR_node.name, "bias", beta1)

        # mean
        self.set_weight(IR_node.name, "mean", mean)

        # var
        self.set_weight(IR_node.name, "var", variance)

        # defuse the activation layer

        if bn_spec.HasField('active_type') and  bn_spec.active_type != '':
            self._defuse_activation(source_node)



    def rename_pool(self, source_node):
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node, "Pool")

        # input edge
        self.convert_inedge(source_node, IR_node)

        # layer and spec
        pool_node = source_node.layer
        pool_spec = self.spec_dict[source_node.name]
        spec = pool_spec.inputs[0].pool_conf

        # assert False
        kwargs = dict()

        if spec.pool_type == 'max-projection':
            kwargs['pooling_type'] = 'MAX'
        elif spec.pool_type == 'avg-projection':
            kwargs['pooling_type'] = 'AVG'
        else:
            kwargs['pooling_type'] = 'MAX'



        width = spec.size_x
        height = spec.size_y if spec.HasField('size_y') else width
        channel = spec.channels
        stride_x = spec.stride
        stride_y = spec.stride_y if spec.HasField('stride_y') else stride_x
        padding_x = spec.padding
        padding_y = spec.padding_y if spec.HasField('padding_y') else padding_x
        output_x = spec.output_x
        output_y = spec.output_y if spec.HasField('output_y') else output_x
        input_x = spec.img_size
        input_y = spec.img_size_y if spec.HasField('img_size_y') else input_x


        # output shape
        output_shapes = [-1, channel, output_y, output_x]
        PaddleParser._set_output_shape(source_node, IR_node, output_shapes)


        kwargs['global_pooling'] = False

        kwargs['strides'] = [1, stride_x, stride_y, 1]
        kwargs['kernel_shape'] = [1, width, height, 1]

        # pad_dim
        pad_dim = [0, 0, padding_x, padding_y, padding_x, padding_y, 0, 0]


        # padding mode
        # If padding == "SAME": output_spatial_shape[i] = ceil(input_spatial_shape[i] / strides[i])
        # If padding == "VALID": output_spatial_shape[i] = ceil((input_spatial_shape[i] - (spatial_filter_shape[i]-1) * dilation_rate[i]) / strides[i]).

        if output_x * stride_x == input_x and output_y * stride_y == input_y:
            auto_pad = "SAME"
            kwargs['auto_pad'] = auto_pad
        elif output_x * stride_x == input_x - width + 1 and output_y * stride_y == input_y - height + 1:
            auto_pad = "VALID"
            kwargs['auto_pad'] = auto_pad

        pad_dim = convert_tf_pad_to_onnx(pad_dim)
        kwargs['pads'] = pad_dim



        assign_IRnode_values(IR_node, kwargs)

        if pool_spec.HasField('active_type') and  pool_spec.active_type != '':
            self._defuse_activation(source_node)

    def rename_fc(self, source_node):
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node, "FullyConnected")

        # input edge
        self.convert_inedge(source_node, IR_node)

        # layer and spec
        fc_node = source_node.layer
        fc_spec = self.spec_dict[source_node.name]

        # units
        IR_node.attr['units'].i = fc_spec.size

        # use_bias
        IR_node.attr['use_bias'].b = fc_spec.HasField('bias_parameter_name')

        w_name = fc_spec.inputs[0].input_parameter_name
        bias_name = fc_spec.bias_parameter_name

        w = self.parameters.get(w_name)
        bias = self.parameters.get(bias_name)

        # weights
        self.set_weight(IR_node.name, 'weights', w)
        if IR_node.attr['use_bias'].b:
            self.set_weight(IR_node.name, 'bias', bias)

        if fc_spec.HasField('active_type') and  fc_spec.active_type != '':
            self._defuse_activation(source_node)





    def rename_addto(self, source_node):
        add_spec = self.spec_dict[source_node.name]
        self._convert_merge(source_node, 'Add')
        if add_spec.HasField('active_type') and  add_spec.active_type != '':
            self._defuse_activation(source_node)


    def rename_data(self, source_node):
        # need the shape TODO

        # only for training
        IR_node = self.IR_graph.node.add()

        # name, op
        PaddleParser._copy_and_reop(source_node, IR_node, "DataInput")

        # input edge
        self.convert_inedge(source_node, IR_node)

        output_shapes = [-1, 224, 224, 3]
        # shape
        PaddleParser._copy_shape(source_node.layer, IR_node, output_shapes)

