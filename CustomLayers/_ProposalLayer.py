import numpy as np
import tensorflow as tf
from tensorflow.python.eager import context

from mrcnn.resources.utils import batch_slice
from mrcnn.CustomLayers._Common import apply_box_deltas_graph, clip_boxes_graph


class Interface:
    def __init__(self, config):
        self.NMS_THRESHOLD = config.RPN_NMS_THRESHOLD
        self.IMAGES_PER_GPU = config.IMAGES_PER_GPU
        self.PRE_NMS_LIMIT = config.PRE_NMS_LIMIT
        self.RPN_BBOX_STD_DEV = config.RPN_BBOX_STD_DEV

    def to_dict(self):
        return {a: getattr(self, a)
                for a in sorted(dir(self))
                if not a.startswith("__") and not callable(getattr(self, a))}


class ProposalLayer(tf.keras.layers.Layer):
    """Receives anchor scores and selects a subset to pass as proposals
    to the second stage. Filtering is done based on anchor scores and
    non-max suppression to remove overlaps. It also applies bounding
    box refinement deltas to anchors.

    Inputs:
        rpn_probs: [batch, num_anchors, (bg prob, fg prob)]
        rpn_bbox: [batch, num_anchors, (dy, dx, log(dh), log(dw))]
        anchors: [batch, num_anchors, (y1, x1, y2, x2)] anchors in normalized coordinates

    Returns:
        Proposals in normalized coordinates [batch, rois, (y1, x1, y2, x2)]
    """

    def __init__(
            self,
            proposal_count,
            config,
            **kwargs
    ):
        super(ProposalLayer, self).__init__(**kwargs)
        self.proposal_count = proposal_count
        self.interface = Interface(config)

        self.batch_slice = batch_slice
        self.apply_box_deltas_graph = apply_box_deltas_graph
        self.clip_boxes_graph = clip_boxes_graph

    @tf.function
    def call(self, inputs):
        # Box Scores. Use the foreground class confidence. [Batch, num_rois, 1]
        scores = inputs[0][:, :, 1]
        # Box deltas [batch, num_rois, 4]
        deltas = inputs[1] * np.reshape(self.interface.RPN_BBOX_STD_DEV, [1, 1, 4])
        # Anchors
        anchors = inputs[2]

        # Improve performance by trimming to top anchors by score
        # and doing the rest on the smaller subset.
        pre_nms_limit = tf.minimum(self.interface.PRE_NMS_LIMIT, tf.shape(anchors)[1])
        ix = tf.nn.top_k(
            scores, pre_nms_limit, sorted=True,
            name="top_anchors"
        ).indices

        scores = tf.convert_to_tensor(self.batch_slice(
            [scores, ix],
            lambda x, y: tf.gather(
                x, y,
                name="gather_score"
            ),
            self.interface.IMAGES_PER_GPU
        ))
        # Tensor("ROI/packed:0", shape=(8, 4092), dtype=float32)

        deltas = self.batch_slice(
            [deltas, ix],
            lambda x, y: tf.gather(
                x, y,
                name="gather_deltas"
            ),
            self.interface.IMAGES_PER_GPU
        )
        # Tensor("ROI/packed_8:0", shape=(8, 4092, 4), dtype=float32)

        pre_nms_anchors = self.batch_slice(
            [anchors, ix],
            lambda a, x: tf.gather(
                a, x,
                name="gather_pre_nms_anchors"
            ),
            self.interface.IMAGES_PER_GPU,
            names=["pre_nms_anchors"]
        )
        # Tensor("ROI/pre_nms_anchors:0", shape=(8, 4092, 4), dtype=float32)

        # Apply deltas to anchors to get refined anchors.
        # [batch, N, (y1, x1, y2, x2)]
        boxes = self.batch_slice(
            [pre_nms_anchors, deltas],
            lambda x, y: self.apply_box_deltas_graph(x, y),
            self.interface.IMAGES_PER_GPU,
            names=["refined_anchors"]
        )
        # Tensor("ROI/refined_anchors:0", shape=(8, 4092, 4), dtype=float32)

        # Clip to image boundaries. Since we're in normalized coordinates,
        # clip to 0..1 range. [batch, N, (y1, x1, y2, x2)]
        window = np.array([0, 0, 1, 1], dtype=np.float32)
        boxes = self.batch_slice(
            boxes,
            lambda x: self.clip_boxes_graph(x, window),
            self.interface.IMAGES_PER_GPU,
            names=["refined_anchors_clipped"]
        )
        # Filter out small boxes
        # According to Xinlei Chen's paper, this reduces detection accuracy
        # for small objects, so we're skipping it.

        # Non-max suppression
        @tf.function
        def nms(_boxes, _scores):
            _indices = tf.image.non_max_suppression(
                _boxes, _scores, self.proposal_count,
                self.interface.NMS_THRESHOLD, name="rpn_non_max_suppression"
            )

            _proposals = tf.gather(
                _boxes, _indices,
                name="dp_nms"
            )

            # Pad if needed
            padding = tf.maximum(self.proposal_count - tf.shape(_proposals)[0], 0)
            _proposals = tf.pad(_proposals, [(0, padding), (0, 0)])
            return _proposals

        proposals = self.batch_slice(
            [boxes, scores],
            nms,
            self.interface.IMAGES_PER_GPU
        )

        if not context.executing_eagerly():
            # Infer the static output shape:
            out_shape = self.compute_output_shape(None)
            proposals.set_shape(out_shape)

        return proposals

    def compute_output_shape(self, input_shape):
        return None, self.proposal_count, 4

    def get_config(self):
        config = super(ProposalLayer, self).get_config()
        config.update({
            "proposal_count": self.proposal_count,
            "interface": self.interface.to_dict(),
        })
        return config
