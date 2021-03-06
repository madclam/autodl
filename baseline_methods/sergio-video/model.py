from __future__ import print_function
# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modified by: Zhengying Liu, Isabelle Guyon

"""An example of code submission for the AutoDL challenge.

It implements 3 compulsory methods: __init__, train, and test.
model.py follows the template of the abstract class algorithm.py found
in folder AutoDL_ingestion_program/.

To create a valid submission, zip model.py together with an empty
file called metadata (this just indicates your submission is a code submission
and has nothing to do with the dataset metadata.
"""

import tensorflow as tf
import os

# Import the challenge algorithm (model) API from algorithm.py
import algorithm

# Utility packages
import time
import datetime
import numpy as np
np.random.seed(42)

def tf_repeat(tensor, repeats):
    """
    Args:

    input: A Tensor. 1-D or higher.
    repeats: A list. Number of repeat for each dimension, length must be the same as the number of dimensions in input
    https://github.com/tensorflow/tensorflow/issues/8246

    Returns:

    A Tensor. Has the same type as input. Has the shape of tensor.shape * repeats
    """
    with tf.variable_scope("repeat"):
        expanded_tensor = tf.expand_dims(tensor, -1)
        multiples = [1] + repeats
        tiled_tensor = tf.tile(expanded_tensor, multiples=multiples)
        repeated_tensor = tf.reshape(tiled_tensor, tf.shape(tensor) * repeats)
    return repeated_tensor

class Model(algorithm.Algorithm):
  """Construct CNN for classification."""

  def __init__(self, metadata):
    super(Model, self).__init__(metadata)

    # Get dataset name.
    self.dataset_name = self.metadata_.get_dataset_name()\
                          .split('/')[-2].split('.')[0]

    # Infer dataset domain and use corresponding model function
    self.domain = self.infer_domain()
    if self.domain == 'image':
      model_fn = self.image_model_fn
    elif self.domain == 'video' or self.domain == 'text':
      model_fn = self.video_model_fn
    else:
      model_fn = self.model_fn

    tf.logging.set_verbosity(tf.logging.INFO)

    # Classifier using model_fn (see image_model_fn and other model_fn below)
    self.classifier = tf.estimator.Estimator(
      model_fn=model_fn)#,
      # model_dir='checkpoints_' + self.dataset_name)

    # Attributes for managing time budget
    # Cumulated number of training steps
    self.birthday = time.time()
    self.total_train_time = 0
    self.cumulated_num_steps = 0
    self.estimated_time_per_step = None
    self.total_test_time = 0
    self.cumulated_num_tests = 0
    self.estimated_time_test = None
    self.done_training = False
    self.early_stop_proba = 0.05
    ################################################
    # Important critical number for early stopping #
    ################################################
    self.num_epochs_we_want_to_train = 300 # see the function self.choose_to_stop_early() below for more details

  def train(self, dataset, remaining_time_budget=None):
    """Train this algorithm on the tensorflow |dataset|.

    This method will be called REPEATEDLY during the whole training/predicting
    process. So your `train` method should be able to handle repeated calls and
    hopefully improve your model performance after each call.

    Args:
      dataset: a `tf.data.Dataset` object. Each example is of the form
            (matrix_bundle_0, matrix_bundle_1, ..., matrix_bundle_(N-1), labels)
          where each matrix bundle is a tf.Tensor of shape
            (batch_size, sequence_size, row_count, col_count)
          with default `batch_size`=30 (if you wish you can unbatch and have any
          batch size you want). `labels` is a tf.Tensor of shape
            (batch_size, output_dim)
          The variable `output_dim` represents number of classes of this
          multilabel classification task. For the first version of AutoDL
          challenge, the number of bundles `N` will be set to 1.

      remaining_time_budget: time remaining to execute train(). The method
          should keep track of its execution time to avoid exceeding its time
          budget. If remaining_time_budget is None, no time budget is imposed.
    """
    if self.done_training:
      return

    # Turn `features` in the tensor tuples (matrix_bundle_0,...,matrix_bundle_(N-1), labels)
    # to a dict. This example model only uses the first matrix bundle
    # (i.e. matrix_bundle_0) (see the documentation of this train() function above for the description of each example)
    dataset = dataset.map(lambda *x: ({'x': x[0]}, x[-1]))

    def train_input_fn():
      iterator = dataset.make_one_shot_iterator()
      features, labels = iterator.get_next()
      return features, labels

    if not remaining_time_budget: # This is never true in the competition anyway
      remaining_time_budget = 1200 # if no time limit is given, set to 20min

    # The following snippet of code intends to do
    # 1. If no training is done before, train for 1 step (one batch);
    # 2. Otherwise, estimate training time per step and time needed for test,
    #    then compare to remaining time budget to compute a potential maximum
    #    number of steps (max_steps) that can be trained within time budget;
    # 3. Choose a number (steps_to_train) between 0 and max_steps and train for
    #    this many steps. Double it each time.
    if not self.estimated_time_per_step:
      steps_to_train = 1
    else:
      if self.estimated_time_test:
        tentative_estimated_time_test = self.estimated_time_test
      else:
        tentative_estimated_time_test = 50 # conservative estimation for test
      max_steps = int((remaining_time_budget - tentative_estimated_time_test) / self.estimated_time_per_step)
      max_steps = max(max_steps, 1)
      if self.cumulated_num_tests < np.log(max_steps) / np.log(2):
        steps_to_train = int(2 ** self.cumulated_num_tests) # Double steps_to_train after each test
      else:
        steps_to_train = 0
    if steps_to_train <= 0:
      print_log("Not enough time remaining for training. " +\
            "Estimated time for training per step: {:.2f}, ".format(self.estimated_time_per_step) +\
            "and for test: {}, ".format(tentative_estimated_time_test) +\
            "but remaining time budget is: {:.2f}. ".format(remaining_time_budget) +\
            "Skipping...")
      self.done_training = True
    else:
      msg_est = ""
      if self.estimated_time_per_step:
        msg_est = "estimated time for this: " +\
                  "{:.2f} sec.".format(steps_to_train * self.estimated_time_per_step)
      print_log("Begin training for another {} steps...{}".format(steps_to_train, msg_est))
      train_start = time.time()
      # Start training
      with tf.Session() as sess:
        self.classifier.train(
          input_fn=train_input_fn,
          steps=steps_to_train)
      train_end = time.time()
      # Update for time budget managing
      train_duration = train_end - train_start
      self.total_train_time += train_duration
      self.cumulated_num_steps += steps_to_train
      self.estimated_time_per_step = self.total_train_time / self.cumulated_num_steps
      print_log("{} steps trained. {:.2f} sec used. ".format(steps_to_train, train_duration) +\
            "Now total steps trained: {}. ".format(self.cumulated_num_steps) +\
            "Total time used for training: {:.2f} sec. ".format(self.total_train_time) +\
            "Current estimated time per step: {:.2e} sec.".format(self.estimated_time_per_step))

  def test(self, dataset, remaining_time_budget=None):
    """Test this algorithm on the tensorflow |dataset|.

    Args:
      Same as that of `train` method, except that the `labels` will be empty.
    Returns:
      predictions: A `numpy.ndarray` matrix of shape (sample_count, output_dim).
          here `sample_count` is the number of examples in this dataset as test
          set and `output_dim` is the number of labels to be predicted. The
          values should be binary or in the interval [0,1].
    """
    if self.done_training:
      return None

    # Turn `features` in the tensor pair (features, labels) to a dict
    dataset = dataset.map(lambda *x: ({'x': x[0]}, x[-1]))

    def test_input_fn():
      iterator = dataset.make_one_shot_iterator()
      features, labels = iterator.get_next()
      return features, labels

    # The following snippet of code intends to do:
    # 0. Use the function self.choose_to_stop_early() to decide if stop the whole
    #    train/predict process for next call
    # 1. If there is time budget limit, and some testing has already been done,
    #    but not enough remaining time for testing, then return None to stop
    # 2. Otherwise: make predictions normally, and update some
    #    variables for time managing
    if self.choose_to_stop_early():
      print_log("Oops! Choose to stop early for next call!")
      self.done_training = True
    test_begin = time.time()
    if remaining_time_budget and self.estimated_time_test and\
        self.estimated_time_test > remaining_time_budget:
      print_log("Not enough time for test. " +\
            "Estimated time for test: {:.2e}, ".format(self.estimated_time_test) +\
            "But remaining time budget is: {:.2f}. ".format(remaining_time_budget) +\
            "Stop train/predict process by returning None.")
      return None

    msg_est = ""
    if self.estimated_time_test:
      msg_est = "estimated time: {:.2e} sec.".format(self.estimated_time_test)
    print_log("Begin testing...", msg_est)
    test_results = self.classifier.predict(input_fn=test_input_fn)
    predictions = [x['probabilities'] for x in test_results]
    has_same_length = (len({len(x) for x in predictions}) == 1)
    print_log("Asserting predictions have the same number of columns...")
    assert(has_same_length)
    predictions = np.array(predictions)
    test_end = time.time()
    test_duration = test_end - test_begin
    self.total_test_time += test_duration
    self.cumulated_num_tests += 1
    self.estimated_time_test = self.total_test_time / self.cumulated_num_tests
    print_log("[+] Successfully made one prediction. {:.2f} sec used. ".format(test_duration) +\
          "Total time used for testing: {:.2f} sec. ".format(self.total_test_time) +\
          "Current estimated time for test: {:.2e} sec.".format(self.estimated_time_test))
    return predictions

  ##############################################################################
  #### Above 3 methods (__init__, train, test) should always be implemented ####
  ##############################################################################

  # Model functions that contain info on neural network architectures
  # Several model functions are to be implemented, for different domains
  def image_model_fn(self, features, labels, mode):
    """Simple CNN model for image datasets.

    Two CNN layers are used then dropout.
    """
    col_count, row_count = self.metadata_.get_matrix_size(0)
    sequence_size = self.metadata_.get_sequence_size()
    output_dim = self.metadata_.get_output_size()

    # Input Layer
    # Transpose X to 4-D tensor: [batch_size, row_count, col_count, sequence_size]
    # Normally the last axis should be channels instead of time axis, but they
    # are both equal to 1 for images
    input_layer = tf.transpose(features["x"], [0, 2, 3, 1])
    # input_layer = tf.reshape(features["x"], [-1, sequence_size, row_count, col_count])

    # Convolutional Layer #1
    # Computes 32 features using a 5x5 filter with ReLU activation.
    # Padding is added to preserve width and height. For MNIST, we have
    # Input Tensor Shape: [batch_size, 28, 28, 1]
    # Output Tensor Shape: [batch_size, 28, 28, 32]
    conv1 = tf.layers.conv2d(
        inputs=input_layer,
        filters=32,
        kernel_size=[5, 5],
        padding="same",
        activation=tf.nn.relu)

    # Pooling Layer #1
    # First max pooling layer with a 2x2 filter and stride of 2
    # Input Tensor Shape: [batch_size, 28, 28, 32]
    # Output Tensor Shape: [batch_size, 14, 14, 32]
    pool1 = tf.layers.max_pooling2d(inputs=conv1, pool_size=[2, 2], strides=2)

    # Convolutional Layer #2
    # Computes 64 features using a 5x5 filter.
    # Padding is added to preserve width and height.
    # Input Tensor Shape: [batch_size, 14, 14, 32]
    # Output Tensor Shape: [batch_size, 14, 14, 64]
    conv2 = tf.layers.conv2d(
        inputs=pool1,
        filters=64,
        kernel_size=[5, 5],
        padding="same",
        activation=tf.nn.relu)

    # Pooling Layer #2
    # Second max pooling layer with a 2x2 filter and stride of 2
    # Input Tensor Shape: [batch_size, 14, 14, 64]
    # Output Tensor Shape: [batch_size, 7, 7, 64]
    pool2 = tf.layers.max_pooling2d(inputs=conv2, pool_size=[2, 2], strides=2)

    # Flatten tensor into a batch of vectors
    # Input Tensor Shape: [batch_size, 7, 7, 64]
    # Output Tensor Shape: [batch_size, 7 * 7 * 64]
    pool2_flat = tf.reshape(pool2,
                            [-1, (row_count//4) * (col_count//4) * 64])

    # Dense Layer
    # Densely connected layer with 1024 neurons
    # Input Tensor Shape: [batch_size, 7 * 7 * 64]
    # Output Tensor Shape: [batch_size, 1024]
    dense = tf.layers.dense(inputs=pool2_flat, units=1024, activation=tf.nn.relu)

    # Add dropout operation; 0.6 probability that element will be kept
    dropout = tf.layers.dropout(
        inputs=dense, rate=0.4, training=mode == tf.estimator.ModeKeys.TRAIN)

    # Logits layer
    # Input Tensor Shape: [batch_size, 1024]
    # Output Tensor Shape: [batch_size, 10]
    logits = tf.layers.dense(inputs=dropout, units=output_dim)

    predictions = {
        # Generate predictions (for PREDICT and EVAL mode)
        "classes": tf.argmax(input=logits, axis=1),
        # Add `softmax_tensor` to the graph. It is used for PREDICT and by the
        # `logging_hook`.
        "probabilities": tf.nn.softmax(logits, name="softmax_tensor")
    }
    if mode == tf.estimator.ModeKeys.PREDICT:
      return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)

    # Calculate Loss (for both TRAIN and EVAL modes)
    loss = tf.losses.softmax_cross_entropy(onehot_labels=labels, logits=logits)
    # loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)

    # Configure the Training Op (for TRAIN mode)
    if mode == tf.estimator.ModeKeys.TRAIN:
      optimizer = tf.train.AdamOptimizer()
      train_op = optimizer.minimize(
          loss=loss,
          global_step=tf.train.get_global_step())
      return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op, eval_metric_ops={'loss' : loss})

    # Add evaluation metrics (for EVAL mode)
    eval_metric_ops = {
        "accuracy": tf.metrics.accuracy(
            labels=labels, predictions=predictions["classes"])}
    return tf.estimator.EstimatorSpec(
        mode=mode, loss=loss, eval_metric_ops=eval_metric_ops)

  # def video_model_fn(self, features, labels, mode):
  #   """Model function for video dataset.
  #
  #   Sum over time axis and then use dense neural network. Here this model is
  #   applied to video and text, for efficiency.
  #   """
  #
  #   col_count, row_count = self.metadata_.get_matrix_size(0)
  #   sequence_size = self.metadata_.get_sequence_size()
  #   output_dim = self.metadata_.get_output_size()
  #
  #   # Sum over time axis
  #   input_layer = tf.reduce_sum(features['x'], axis=1)
  #
  #   # Construct a neural network with 0 hidden layer
  #   input_layer = tf.reshape(input_layer,
  #                            [-1, row_count*col_count])
  #
  #   # Replace missing values by 0
  #   input_layer = tf.where(tf.is_nan(input_layer),
  #                          tf.zeros_like(input_layer), input_layer)
  #
  #   logits = tf.layers.dense(inputs=input_layer, units=output_dim)
  #
  #   # For multi-label classification, the correct loss is actually sigmoid with
  #   # sigmoid_cross_entropy_with_logits, not softmax with
  #   # softmax_cross_entropy.
  #   softmax_tensor = tf.nn.softmax(logits, name="softmax_tensor")
  #
  #   # sigmoid_tensor = tf.nn.sigmoid(logits, name="sigmoid_tensor")
  #   # threshold = 0.5
  #   # binary_predictions = tf.cast(tf.greater(sigmoid_tensor, threshold), tf.int32)
  #
  #   predictions = {
  #     # Generate predictions (for PREDICT and EVAL mode)
  #     "classes": tf.argmax(input=logits, axis=1),
  #     # "classes": binary_predictions,
  #     # Add `softmax_tensor` to the graph. It is used for PREDICT and by the
  #     # `logging_hook`.
  #     "probabilities": softmax_tensor
  #     # "probabilities": sigmoid_tensor
  #   }
  #
  #   # accuracy = tf.metrics.accuracy(labels=tf.argmax(input=labels, axis=1), predictions=predictions["classes"])
  #
  #   if mode == tf.estimator.ModeKeys.PREDICT:
  #     return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)
  #
  #   # Calculate Loss (for both TRAIN and EVAL modes)
  #   loss = tf.losses.softmax_cross_entropy(onehot_labels=labels, logits=logits)
  #   # loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)
  #
  #   # Configure the Training Op (for TRAIN mode)
  #   if mode == tf.estimator.ModeKeys.TRAIN:
  #     optimizer = tf.train.AdamOptimizer()
  #
  #     train_op = optimizer.minimize(
  #         loss=loss,
  #         global_step=tf.train.get_global_step())
  #     return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op)
  #
  #   # Add evaluation metrics (for EVAL mode)
  #   assert mode == tf.estimator.ModeKeys.EVAL
  #   return tf.estimator.EstimatorSpec(
  #       mode=mode, loss=loss)

  def video_model_fn(self, features, labels, mode):
    """Model function for video dataset.

    Sum over time axis and then use dense neural network. Here this model is
    applied to video and text, for efficiency.
    """

    col_count, row_count = self.metadata_.get_matrix_size(0)
    sequence_size = self.metadata_.get_sequence_size()  # length of padded sequences
    output_dim = self.metadata_.get_output_size()

    input_raw = features['x']
    batch_size = tf.shape(input_raw)[0]
    num_frames = 5  # number of frames to sample from each sequence

    # Obtain the actual length of sequences without padded (zero-sum) frames
    frame_sums = tf.reduce_sum(features['x'], axis=[2,3]) # frame that sum of pixels is zero are padded frames
    lengths = tf.cast(tf.count_nonzero(tf.not_equal(frame_sums, 0), axis=1)[:,tf.newaxis], tf.float32)

    # Sample randomly "num_frames" within each sequence, avoiding padded frames
    sampled_frames = tf.cast(lengths * tf.random_uniform([batch_size, num_frames]), tf.int32)

    # Index the sampled frames from "input_raw"
    image_mesh_ij = tf.meshgrid(tf.range(col_count), tf.range(row_count), indexing='ij')
    image_indices = tf.reshape(tf.stack(image_mesh_ij,axis=-1), [row_count * col_count, 2])

    batch_indices = tf.reshape(tf.tile(tf.range(batch_size)[:, tf.newaxis], [1, num_frames]), [batch_size * num_frames, 1])
    batchframe_indices = tf.stack(
      [
        batch_indices[..., -1],
        tf.reshape(sampled_frames, [tf.shape(sampled_frames)[0] * tf.shape(sampled_frames)[1]])],
      axis=-1
    )

    nd_indices = tf.concat(
      [
        tf_repeat(batchframe_indices, [tf.shape(image_indices)[0], 1]),
        tf.tile(image_indices, [tf.shape(batchframe_indices)[0], 1])
      ],
      axis=-1
    )

    input_raw_indexed = tf.gather_nd(input_raw, nd_indices) # gather_nd propagates the gradient in later TF versions

    # Process each sampled frame using a CNN
    # *** CAUTION *** the number of spatial convolutions and poolings has to account for the input frame sizes. For
    # 120x160, 6 convolutions and 2x2 poolings with stride 2 reduce the dimensions is the maximum. Leading to 1x2
    # spatial resolution.

    input_layer = tf.reshape(input_raw_indexed, [-1, col_count, row_count, 1])  # temporary reshape

    conv1 = tf.layers.conv2d(
      inputs=input_layer,
      filters=16,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)
    pool1 = tf.layers.max_pooling2d(inputs=conv1, pool_size=[2, 2], strides=2)

    conv2 = tf.layers.conv2d(
      inputs=pool1,
      filters=32,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)
    pool2 = tf.layers.max_pooling2d(inputs=conv2, pool_size=[2, 2], strides=2)

    conv3 = tf.layers.conv2d(
      inputs=pool2,
      filters=32,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)
    pool3 = tf.layers.max_pooling2d(inputs=conv3, pool_size=[2, 2], strides=2)

    conv4 = tf.layers.conv2d(
      inputs=pool3,
      filters=64,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)
    pool4 = tf.layers.max_pooling2d(inputs=conv4, pool_size=[2, 2], strides=2)

    conv5 = tf.layers.conv2d(
      inputs=pool4,
      filters=64,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)
    pool5 = tf.layers.max_pooling2d(inputs=conv5, pool_size=[2, 2], strides=2)

    conv6 = tf.layers.conv2d(
      inputs=pool5,
      filters=128,
      kernel_size=[3, 3],
      padding="same",
      activation=tf.nn.relu)

    # Global spatial max pooling
    # Input: [batch_size*num_frames, 1, 2, 128]  Note: spatial resolution could vary as a function of CNN architecture.
    # Output: [batch_size*num_frames, 1, 1, 128]
    pool6 = tf.reduce_max(conv6, axis=[1,2])
    flat = tf.reshape(pool6, [-1, num_frames, 128]) # restore the last reshape operation

    # Feature pooling of convolutional features along sequence frames
    # Input: [batch_size, num_frames, 256]
    # Output: [batch_size, 256]
    features_layer = tf.reduce_max(flat, axis=1)

    # Add dropout operation; 0.6 probability that element will be kept
    dropout = tf.layers.dropout(
        inputs=features_layer, rate=0.4, training=mode == tf.estimator.ModeKeys.TRAIN)

    # Logits layer
    # Input Tensor Shape: [batch_size, 256]
    # Output Tensor Shape: [batch_size, output_dim]
    logits = tf.layers.dense(inputs=dropout, units=output_dim)

    # For multi-label classification, the correct loss is actually sigmoid with
    # sigmoid_cross_entropy_with_logits, not softmax with
    # softmax_cross_entropy.
    softmax_tensor = tf.nn.softmax(logits, name="softmax_tensor")

    # sigmoid_tensor = tf.nn.sigmoid(logits, name="sigmoid_tensor")
    # threshold = 0.5
    # binary_predictions = tf.cast(tf.greater(sigmoid_tensor, threshold), tf.int32)

    predictions = {
      # Generate predictions (for PREDICT and EVAL mode)
      "classes": tf.argmax(input=logits, axis=1),
      # "classes": binary_predictions,
      # Add `softmax_tensor` to the graph. It is used for PREDICT and by the
      # `logging_hook`.
      "probabilities": softmax_tensor
      # "probabilities": sigmoid_tensor
    }

    # accuracy = tf.metrics.accuracy(labels=tf.argmax(input=labels, axis=1), predictions=predictions["classes"])

    if mode == tf.estimator.ModeKeys.PREDICT:
      return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)

    # Calculate Loss (for both TRAIN and EVAL modes)
    loss = tf.losses.softmax_cross_entropy(onehot_labels=labels, logits=logits)
    # loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)

    # Configure the Training Op (for TRAIN mode)
    if mode == tf.estimator.ModeKeys.TRAIN:
      optimizer = tf.train.AdamOptimizer()

      train_op = optimizer.minimize(
          loss=loss,
          global_step=tf.train.get_global_step())
      return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op)

    # Add evaluation metrics (for EVAL mode)
    assert mode == tf.estimator.ModeKeys.EVAL
    return tf.estimator.EstimatorSpec(
        mode=mode, loss=loss)

  def model_fn(self, features, labels, mode):
    """Dense neural network with 0 hidden layer.

    Flatten then dense. Can be applied to any task. Here we apply it to speech
    and tabular data.
    """
    col_count, row_count = self.metadata_.get_matrix_size(0)
    sequence_size = self.metadata_.get_sequence_size()
    output_dim = self.metadata_.get_output_size()

    # Construct a neural network with 0 hidden layer
    input_layer = tf.reshape(features["x"],
                             [-1, sequence_size*row_count*col_count])

    # Replace missing values by 0
    input_layer = tf.where(tf.is_nan(input_layer),
                           tf.zeros_like(input_layer), input_layer)

    logits = tf.layers.dense(inputs=input_layer, units=output_dim)

    # For multi-label classification, the correct loss is actually sigmoid with
    # sigmoid_cross_entropy_with_logits, not softmax with softmax_cross_entropy.
    softmax_tensor = tf.nn.softmax(logits, name="softmax_tensor")

    predictions = {
      # Generate predictions (for PREDICT and EVAL mode)
      "classes": tf.argmax(input=logits, axis=1),
      # "classes": binary_predictions,
      # Add `softmax_tensor` to the graph. It is used for PREDICT and by the
      # `logging_hook`.
      "probabilities": softmax_tensor
      # "probabilities": sigmoid_tensor
    }
    if mode == tf.estimator.ModeKeys.PREDICT:
      return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)

    # Calculate Loss (for both TRAIN and EVAL modes)
    loss = tf.losses.softmax_cross_entropy(onehot_labels=labels, logits=logits)

    # Configure the Training Op (for TRAIN mode)
    if mode == tf.estimator.ModeKeys.TRAIN:
      optimizer = tf.train.AdamOptimizer()
      train_op = optimizer.minimize(
          loss=loss,
          global_step=tf.train.get_global_step())
      return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op)

    # Add evaluation metrics (for EVAL mode)
    assert mode == tf.estimator.ModeKeys.EVAL
    eval_metric_ops = {
        "accuracy": tf.metrics.accuracy(
            labels=labels, predictions=predictions["classes"])}
    return tf.estimator.EstimatorSpec(
        mode=mode, loss=loss, eval_metric_ops=eval_metric_ops)

  # Some helper functions
  def infer_domain(self):
    col_count, row_count = self.metadata_.get_matrix_size(0)
    sequence_size = self.metadata_.get_sequence_size()
    output_dim = self.metadata_.get_output_size()
    if sequence_size > 1:
      if col_count == 1 and row_count == 1:
        return "speech"
      elif col_count > 1 and row_count > 1:
        return "video"
      else:
        return 'text'
    else:
      if col_count > 1 and row_count > 1:
        return 'image'
      else:
        return 'tabular'

  def age(self):
    return time.time() - self.birthday

  def choose_to_stop_early(self):
    """The criterion to stop further training (thus finish train/predict
    process).
    """
    # return self.cumulated_num_tests > 10 # Limit to make 10 predictions
    # return np.random.rand() < self.early_stop_proba
    batch_size = 30 # See ingestion program: D_train.init(batch_size=30, repeat=True)
    num_examples = self.metadata_.size()
    num_epochs = self.cumulated_num_steps * batch_size / num_examples
    return num_epochs > self.num_epochs_we_want_to_train # Train for 40 epochs then stop

def print_log(*content):
  """Logging function. (could've also used `import logging`.)"""
  now = datetime.datetime.now().strftime("%y-%m-%d %H:%M:%S")
  print("MODEL INFO: " + str(now)+ " ", end='')
  print(*content)
