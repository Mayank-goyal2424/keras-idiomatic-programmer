# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import tensorflow as tf
from tensorflow.keras import Sequential, Model, Input
from tensorflow.keras import layers
from tensorflow.keras.layers import ReLU, Dense, Conv2D, Conv2DTranspose
from tensorflow.keras.layers import DepthwiseConv2D, SeparableConv2D, Dropout
from tensorflow.keras.layers import GlobalAveragePooling2D, Activation, BatchNormalization
from tensorflow.keras.regularizers import l2
from tensorflow.keras.optimizers import Adam, SGD
from tensorflow.compat.v1.keras.initializers import glorot_uniform, he_normal
from tensorflow.keras.callbacks import LearningRateScheduler
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras.utils import to_categorical
import tensorflow_datasets as tfds
import tensorflow.keras.backend as K
import numpy as np
from sklearn.model_selection import train_test_split

import random
import math
import sys

class Composable(object):
    ''' Composable base (super) class for Models '''
    init_weights = 'he_normal'	# weight initialization
    reg          = None         # kernel regularizer
    relu         = None         # ReLU max value
    bias         = True         # whether to use bias in dense/conv layers

    def __init__(self, init_weights=None, reg=None, relu=None, bias=True):
        """ Constructor
            init_weights : kernel initializer
            reg          : kernel regularizer
            relu         : clip value for ReLU
            bias         : whether to use bias
        """
        if init_weights is not None:
            self.init_weights = init_weights
        if reg is not None:
            self.reg = reg
        if relu is not None:
            self.relu = relu
        if bias is not None:
            self.bias = bias

        # Feature maps encoding at the bottleneck layer in classifier (high dimensionality)
        self._encoding = None
        # Pooled and flattened encodings at the bottleneck layer (low dimensionality)
        self._embedding = None
        # Pre-activation conditional probabilities for classifier
        self._probabilities = None
        # Post-activation conditional probabilities for classifier
        self._softmax = None

        self._model = None

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, _model):
        self._model = _model

    @property
    def encoding(self):
        return self._encoding

    @encoding.setter
    def encoding(self, layer):
        self._encoding = layer

    @property
    def embedding(self):
        return self._embedding

    @embedding.setter
    def embedding(self, layer):
        self._embedding = layer

    @property
    def probabilities(self):
        return self._probabilities

    @probabilities.setter
    def probabilities(self, layer):
        self._probabilities = layer

    def prestem(self, inputs, **metaparameters):
      """ Construct a Pre-stem for Stem Group
          inputs : input to the pre-stem
          norm   : include normalization layer
      """
      x = inputs
      if 'norm' in metaparameters:
          norm = metaparameters['norm']
          if norm:
              x = self.Normalize(inputs)
      return x

    def stem(self, inputs, kernel_size=(7, 7), **metaparameters):
      """ Construct the Stem Group
          inputs     : input to the stem
          kernel_size: kernel (filter) size
          pooling    : pooling option
      """
      if 'pooling' in metaparameters:
          pooling = metaparameters['pooling']
      else:
          pooling = None

      x = self.Conv2D(inputs, kernel_size, strides=(1, 1), padding='same')
      x = self.BatchNormalization(x)
      x = self.ReLU(x)

      if pooling == 'max':
          x = MaxPooling2D((2, 2), strides=2)(x)
      elif pooling == 'feature':
          # feature pooling
          x = self.Conv2D(x, kernel_size, strides=(2, 2), padding='same')
          x = self.BatchNormalization(x)
          x = self.ReLU(x)
      return x

    def classifier(self, x, n_classes, **metaparameters):
      """ Construct the Classifier Group 
          x         : input to the classifier
          n_classes : number of output classes
          pooling   : type of feature map pooling
          dropout   : hidden dropout unit
      """
      if 'pooling' in metaparameters:
          pooling = metaparameters['pooling']
      else:
          pooling = GlobalAveragePooling2D
      if 'dropout' in metaparameters:
          dropout = metaparameters['dropout']
      else:
          dropout = None

      if pooling is not None:
          # Save the encoding layer (high dimensionality)
          self.encoding = x

          # Pooling at the end of all the convolutional groups
          x = pooling()(x)

          # Save the embedding layer (low dimensionality)
          self.embedding = x

      if dropout is not None:
          x = Dropout(dropout)(x)

      # Final Dense Outputting Layer for the outputs
      x = self.Dense(x, n_classes, use_bias=True, **metaparameters)
      
      # Save the pre-activation probabilities layer
      self.probabilities = x
      outputs = Activation('softmax')(x)
      # Save the post-activation probabilities layer
      self.softmax = outputs
      return outputs

    def top(self, layer):
        """ Add layer to the top of the neural network
            layer : layer to add
        """
        outputs = layer(self._model.outputs)
        self._model = Model(self._model.inputs, outputs)

    def summary(self):
        """ Call underlying summary method
        """
        self._model.summary()

    def Dense(self, x, units, activation=None, use_bias=True, **hyperparameters):
        """ Construct Dense Layer
            x           : input to layer
            activation  : activation function
            use_bias    : whether to use bias
            init_weights: kernel initializer
            reg         : kernel regularizer
        """
        if 'reg' in hyperparameters:
            reg = hyperparameters['reg']
        else:
            reg = self.reg
        if 'init_weights' in hyperparameters:
            init_weights = hyperparameters['init_weights']
        else:
            init_weights = self.init_weights
            
        x = Dense(units, activation, use_bias=use_bias,
                  kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        return x

    def Conv2D(self, x, n_filters, kernel_size, strides=(1, 1), padding='valid', activation=None, **hyperparameters):
        """ Construct a Conv2D layer
            x           : input to layer
            n_filters   : number of filters
            kernel_size : kernel (filter) size
            strides     : strides
            padding     : how to pad when filter overlaps the edge
            activation  : activation function
            use_bias    : whether to include the bias
            init_weights: kernel initializer
            reg         : kernel regularizer
        """
        if 'reg' in hyperparameters:
            reg = hyperparameters['reg']
        else:
            reg = self.reg
        if 'init_weights' in hyperparameters:
            init_weights = hyperparameters['init_weights']
        else:
            init_weights = self.init_weights
        if 'bias' in hyperparameters:
            bias = hyperparameters['bias']
        else:
            bias = self.bias

        x = Conv2D(n_filters, kernel_size, strides=strides, padding=padding, activation=activation,
                   use_bias=bias, kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        return x

    def Conv2DTranspose(self, x, n_filters, kernel_size, strides=(1, 1), padding='valid', activation=None, **hyperparameters):
        """ Construct a Conv2DTranspose layer
            x           : input to layer
            n_filters   : number of filters
            kernel_size : kernel (filter) size
            strides     : strides
            padding     : how to pad when filter overlaps the edge
            activation  : activation function
            use_bias    : whether to include the bias
            init_weights: kernel initializer
            reg         : kernel regularizer
        """
        if 'reg' in hyperparameters:
            reg = hyperparameters['reg']
        else:
            reg = self.reg
        if 'init_weights' in hyperparameters:
            init_weights = hyperparameters['init_weights']
        else:
            init_weights = self.init_weights 
        if 'bias' in hyperparameters:
            bias = hyperparameters['bias']
        else:
            bias = self.bias

        x = Conv2DTranspose(n_filters, kernel_size, strides=strides, padding=padding, activation=activation, 
			    use_bias=bias, kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        return x

    def DepthwiseConv2D(self, x, kernel_size, strides=(1, 1), padding='valid', activation=None, **hyperparameters):
        """ Construct a DepthwiseConv2D layer
            x           : input to layer
            kernel_size : kernel (filter) size
            strides     : strides
            padding     : how to pad when filter overlaps the edge
            activation  : activation function
            use_bias    : whether to include the bias
            init_weights: kernel initializer
            reg         : kernel regularizer
        """
        if 'reg' in hyperparameters:
            reg = hyperparameters['reg']
        else:
            reg = self.reg
        if 'init_weights' in hyperparameters:
            init_weights = hyperparameters['init_weights']
        else:
            init_weights = self.init_weights
        if 'bias' in hyperparameters:
            bias = hyperparameters['bias']
        else:
            bias = self.bias

        x = DepthwiseConv2D(kernel_size, strides=strides, padding=padding, activation=activation, 
			    use_bias=bias, kernel_initializer=init_weights, kernel_regularizer=reg)(x)
        return x

    def SeparableConv2D(self, x, n_filters, kernel_size, strides=(1, 1), padding='valid', activation=None, **hyperparameters):
        """ Construct a SeparableConv2D layer
            x           : input to layer
            n_filters   : number of filters
            kernel_size : kernel (filter) size
            strides     : strides
            padding     : how to pad when filter overlaps the edge
            activation  : activation function
            use_bias    : whether to include the bias
            init_weights: kernel initializer
            reg         : kernel regularizer
        """
        if 'reg' in hyperparameters:
            reg = hyperparameters['reg']
        else:
            reg = self.reg
        if 'init_weights' in hyperparameters:
            init_weights = hyperparameters['init_weights']
        else:
            init_weights = self.init_weights
        if 'bias' in hyperparameters:
            bias = hyperparameters['bias']
        else:
            bias = self.bias

        x = SeparableConv2D(n_filters, kernel_size, strides=strides, padding=padding, activation=activation,
                            use_bias=bias, kernel_initializer=init_weights, kernel_regularizer=reg)(x)

        return x

    def ReLU(self, x):
        """ Construct ReLU activation function
            x  : input to activation function
        """
        x = ReLU(self.relu)(x)
        return x
	
    def HS(self, x):
        """ Construct Hard Swish activation function
            x  : input to activation function
        """
        return (x * K.relu(x + 3, max_value=6.0)) / 6.0

    def BatchNormalization(self, x, **params):
        """ Construct a Batch Normalization function
            x : input to function
        """
        x = BatchNormalization(epsilon=1.001e-5, **params)(x)
        return x

    ###
    # Pre-stem Layers
    ###

    class Normalize(layers.Layer):
        """ Custom Layer for Preprocessing Input - Normalization """
        def __init__(self, max=255.0, **parameters):
            """ Constructor """
            super(Composable.Normalize, self).__init__(**parameters)
            self.max = max
    
        def build(self, input_shape):
            """ Handler for Build (Functional) or Compile (Sequential) operation """
            self.kernel = None # no learnable parameters
    
        @tf.function
        def call(self, inputs):
            """ Handler for run-time invocation of layer """
            inputs = inputs / self.max
            return inputs

    class Standarize(layers.Layer):
        """ Custom Layer for Preprocessing Input - Standardization """
        def __init__(self, mean, std, **parameters):
            """ Constructor """
            super(Composable.Standardize, self).__init__(**parameters)
            self.mean = mean
            self.std  = std

        def build(self, input_shape):
            """ Handler for Build (Functional) or Compile (Sequential) operation """
            self.kernel = None # no learnable parameters

        @tf.function
        def call(self, inputs):
            """ Handler for run-time invocation of layer """
            inputs = (inputs - self.mean) / self.std
            return inputs

    ###
    # Preprocessing
    ###

    def normalization(self, x_train, x_test=None, centered=False):
        """ Normalize the input
            x_train : training images
            y_train : test images
        """
        if x_train.dtype == np.uint8:
            if centered:
                x_train = ((x_train - 1) / 127.5).astype(np.float32)
                if x_test is not None:
                    x_test  = ((x_test  - 1) / 127.5).astype(np.float32)
            else:
                x_train = (x_train / 255.0).astype(np.float32)
                if x_test is not None:
                    x_test  = (x_test  / 255.0).astype(np.float32)
        return x_train, x_test

    def standardization(self, x_train, x_test=None):
        """ Standardize the input
            x_train : training images
            x_test  : test images
        """
        self.mean = np.mean(x_train)
        self.std  = np.std(x_train)
        x_train = ((x_train - self.mean) / self.std).astype(np.float32)
        if x_test is not None:
            x_test  = ((x_test  - self.mean) / self.std).astype(np.float32)
        return x_train, x_test

    def label_smoothing(self, y_train, n_classes, factor=0.1):
        """ Convert a matrix of one-hot row-vector labels into smoothed versions. 
            y_train  : training labels
            n_classes: number of classes
            factor   : smoothing factor (between 0 and 1)
        """
        if 0 <= factor <= 1:
            # label smoothing ref: https://www.robots.ox.ac.uk/~vgg/rg/papers/reinception.pdf
            y_train *= 1 - factor
            y_train += factor / n_classes
        else:
            raise Exception('Invalid label smoothing factor: ' + str(factor))
        return y_train

    ###
    # Training
    ###

    def compile(self, loss='categorical_crossentropy', optimizer=Adam(lr=0.001, decay=1e-5), metrics=['acc']):
        """ Compile the model for training
            loss     : the loss function
            optimizer: the optimizer
            metrics  : metrics to report
        """
        self.model.compile(loss=loss, optimizer=optimizer, metrics=metrics)

    # training variables
    hidden_dropout = None # hidden dropout in classifier
    w_lr           = 0    # target warmup rate
    w_epochs       = 0    # number of epochs in warmup
    i_lr           = 0    # initial warmup rate during full training
    e_decay        = 0    # weight decay rate during full training
    e_steps        = 0    # number of steps (batches) in an epoch
    t_steps        = 0    # total number of steps in training job

    def init_draw(self, x_train, y_train, ndraws=5, epochs=3, steps=350, lr=1e-06, batch_size=32):
        """ Use the lottery ticket principle to find the best weight initialization
            x_train : training images
            y_train : training labels
            ndraws  : number of draws to find the winning lottery ticket
            epochs  : number of trial epochs
            steps   :
            lr      :
            batch_size:
        """
        print("*** Initialize Draw")
        loss = sys.float_info.max
        weights = None
        for _ in range(ndraws):
            self.model = tf.keras.models.clone_model(self.model)
            self.compile(optimizer=Adam(lr))
            w = self.model.get_weights()

            # Create generator for training in steps
            datagen = ImageDataGenerator()

            print("*** Lottery", _)
            self.model.fit(datagen.flow(x_train, y_train, batch_size=batch_size),
                                                  epochs=epochs, steps_per_epoch=steps, verbose=1)

            d_loss = self.model.history.history['loss'][epochs-1]
            if d_loss < loss:
                loss = d_loss
                w = self.model.get_weights()

        # Set the best
        self.model.set_weights(w)

    def warmup_scheduler(self, epoch, lr):
        """ learning rate schedular for warmup training
            epoch : current epoch iteration
            lr    : current learning rate
        """
        if epoch == 0:
           return lr
        if epoch == 2:
            # loss is diverging
            if self.model.history.history['loss'][1] > self.model.history.history['loss'][0]:
                print("*** Loss is divergining, Reducing Warmnup Rate")
                self.w_lr /= 10
        return epoch * self.w_lr / self.w_epochs

    def warmup(self, x_train, y_train, epochs=5, s_lr=1e-6, e_lr=0.001):
        """ Warmup for numerical stability
            x_train : training images
            y_train : training labels
            epochs  : number of epochs for warmup
            s_lr    : start warmup learning rate
            e_lr    : end warmup learning rate
        """
        print("*** Warmup (for numerical stability)")
        # Setup learning rate scheduler
        self.compile(optimizer=Adam(s_lr))
        lrate = LearningRateScheduler(self.warmup_scheduler, verbose=1)
        self.w_epochs = epochs
        self.w_lr     = e_lr - s_lr

        # Train the model
        self.model.fit(x_train, y_train, epochs=epochs, batch_size=32, verbose=1,
                       callbacks=[lrate])

    def _tune(self, x_train, y_train, x_test, y_test, epochs, steps, lr, batch_size, weights):
        """ Helper function for hyperparameter tuning
            x_train   : training images
            y_train   : training labels
            x_test    : test images
            y_test    : test labels
            lr        : trial learning rate
            batch_size: the batch size (constant)
            epochs    : the number of epochs
            steps     : steps per epoch
            weights   : warmup weights
        """
        # Compile the model for the new learning rate
        self.compile(optimizer=Adam(lr))

        # Create generator for training in steps
        datagen = ImageDataGenerator()
         
        # Train the model
        print("*** Learning Rate", lr)
        self.model.fit(datagen.flow(x_train, y_train, batch_size=batch_size),
                                 epochs=epochs, steps_per_epoch=steps, verbose=1)

        # Evaluate the model
        result = self.evaluate(x_test, y_test)
         
        # Reset the weights
        self.model.set_weights(weights)

        return result

    def grid_search(self, x_train, y_train, x_test, y_test, epochs=3, steps=250,
                          lr_range=[0.0001, 0.001, 0.01, 0.1], batch_range=[32, 128]):
        """ Do a grid search for hyperparameters
            x_train : training images
            y_train : training labels
            epochs  : number of epochs
            steps   : number of steps per epoch
            lr_range: range for searching learning rate
            batch_range: range for searching batch size
        """
        print("*** Hyperparameter Search")

        # Save the original weights
        weights = self.model.get_weights()

        # Search learning rate
        v_loss = []
        for lr in lr_range:
            result = self._tune(x_train, y_train, x_test, y_test, epochs, steps, lr, batch_range[0], weights)
            v_loss.append(result[0])
            
        # Find the best starting learning rate based on validation loss
        best = sys.float_info.max
        for _ in range(len(lr_range)):
            if v_loss[_] < best:
                best = v_loss[_]
                lr = lr_range[_]

        # Best was smallest learning rate
        if lr == lr_range[0]:
            # try 1/2 the lowest learning rate
            result = self._tune(x_train, y_train, x_test, y_test, epochs, steps, (lr / 2.0), batch_range[0], weights)

            # 1/2 of lr is even better
            if result[0] < best:
                lr = lr / 2.0
            # try halfway between the first and second value
            else:
                n_lr = (lr_range[0] + lr_range[1]) / 2.0
                result = self._tune(x_train, y_train, x_test, y_test, epochs, steps, n_lr, batch_range[0], weights)

                # 1/2 of lr is even better
                if result[0] < best:
                    lr = lr / 2.0
                
        elif lr == lr_range[len(lr_range)-1]:
            # try 2X the largest learning rate
            result = self._tune(x_train, y_train, x_test, y_test, epochs, steps, (lr * 2.0), batch_range[0], weights)

            # 2X of lr is even better
            if result[0] < best:
                lr = lr * 2.0
		
        print("*** Selected best learning rate:", lr)

        # Compile the model for the new learning rate
        self.compile(optimizer=Adam(lr))
        
        v_loss = []
        # skip the first batch size - since we used it in searching learning rate
        datagen = ImageDataGenerator()
        for bs in batch_range[1:]:
            print("*** Batch Size", bs)

            # equalize the number of examples per epoch
            steps = int(batch_range[0] * steps / bs)

            self.model.fit(datagen.flow(x_train, y_train, batch_size=bs),
                                     epochs=epochs, steps_per_epoch=steps, verbose=1)

            # Evaluate the model
            result = self.evaluate(x_test, y_test)
            v_loss.append(result[0])
            
            # Reset the weights
            self.model.set_weights(weights)

        # Find the best batch size based on validation loss
        best = sys.float_info.max
        bs = batch_range[0]
        for _ in range(len(batch_range)-1):
            if v_loss[_] < best:
                best = v_loss[_]
                bs = batch_range[_]

        print("*** Selected best batch size:", bs)

        # return the best learning rate and batch size
        return lr, bs

    def time_decay(self, epoch, lr):
        """ Time-based Decay
        """
        return lr * (1. / (1. + self.e_decay[1] * epoch))

    def step_decay(self, epoch, lr):
        """ Step-based decay
        """
        return self.i_lr * self.e_decay[1]**(epoch)

    def exp_decay(self, epoch, lr):
        """ Exponential Decay
        """
        return self.i_lr * math.exp(-self.e_decay[1] * epoch)

    def cosine_decay(self, epoch, lr, alpha=0.0):
        """ Cosine Decay
        """
        cosine_decay = 0.5 * (1 + np.cos(np.pi * (self.e_steps * epoch) / self.t_steps))
        decayed = (1 - alpha) * cosine_decay + alpha
        return lr * decayed

    def training_scheduler(self, epoch, lr):
        """ Learning Rate scheduler for full-training
            epoch : epoch number
            lr    : current learning rate
        """
        # First epoch (not started) - do nothing
        if epoch == 0:
            return lr

        # Hidden dropout unit in classifier
        if self.hidden_dropout is not None:
            # If training accuracy and validation accuracy more than 3% apart
            if self.model.history.history['acc'][epoch-1] > self.model.history.history['val_acc'][epoch-1] + 0.03:
                if self.hidden_dropout.rate == 0.0:
                    self.hidden_dropout.rate = 0.5
                elif self.hidden_dropout.rate < 0.75:
                    self.hidden_dropout.rate *= 1.1
                print("*** Overfitting, set dropout to", self.hidden_dropout.rate)
            else:
                if self.hidden_dropout.rate != 0.0:
                    print("*** Turning off dropout")
                    self.hidden_dropout.rate = 0.0

        if self.e_decay[0] is None:
            return lr

        # Decay the learning rate
        if self.e_decay[0] == 'time':
            lr = self.time_decay(epoch, lr)
        elif self.e_decay[0] == 'step':
            lr = self.step_decay(epoch, lr)
        elif self.e_decay[0] == 'exp':
            lr = self.exp_decay(epoch, lr)
        else:
            lr = self.cosine_decay(epoch, lr)
        return lr

    def training(self, x_train, y_train, epochs=10, batch_size=32, lr=0.001, decay=(None, 0)):
        """ Full Training of the Model
            x_train    : training images
            y_train    : training labels
            epochs     : number of epochs
            batch_size : size of batch
            lr         : learning rate
            decay      : step-wise learning rate decay
        """

        print("*** Full Training")

        # Check for hidden dropout layer in classifier
        for layer in self.model.layers:
            if isinstance(layer, Dropout):
                self.hidden_dropout = layer
                break    

        if decay is None or 0:
            decay = (None, 0)
        elif isinstance(decay, float):
            decay = ('time', decay)
        elif not isinstance(decay, tuple):
            raise Exception("Training: decay must be (time, value)")
        elif decay[0] not in [None, 'time', 'step', 'exp', 'cosine']:
            raise Exception("Training: invalid method for decay")

        self.i_lr    = lr
        self.e_decay = decay
        self.e_steps = x_train.shape[0] // batch_size
        self.t_steps = self.e_steps * epochs
        self.compile(optimizer=Adam(lr=lr, decay=decay[1]))

        lrate = LearningRateScheduler(self.training_scheduler, verbose=1)
        self.model.fit(x_train, y_train, epochs=epochs, batch_size=batch_size, validation_split=0.1, verbose=1,
                       callbacks=[lrate])

    def evaluate(self, x_test, y_test):
        """ Call underlying evaluate() method
        """
        return self._model.evaluate(x_test, y_test)

    def cifar10(self, epochs=10, decay=('cosine', 0)):
        """ Train on CIFAR-10
            epochs : number of epochs for full training
        """
        from tensorflow.keras.datasets import cifar10
        (x_train, y_train), (x_test, y_test) = cifar10.load_data()
        x_train, x_test = self.standardization(x_train, x_test)
        y_train = to_categorical(y_train, 10)
        y_test  = to_categorical(y_test, 10)
        y_train = self.label_smoothing(y_train, 10, 0.1)

        # compile the model
        self.compile(loss='categorical_crossentropy', metrics=['acc'])

        self.warmup(x_train, y_train)

        lr, batch_size = self.grid_search(x_train, y_train, x_test, y_test)

        self.training(x_train, y_train, epochs=epochs, batch_size=batch_size,
                      lr=lr, decay=decay)
        self.evaluate(x_test, y_test)

    def cifar100(self, epochs=20, decay=('cosine', 0)):
        """ Train on CIFAR-100
            epochs : number of epochs for full training
        """
        from tensorflow.keras.datasets import cifar100
        (x_train, y_train), (x_test, y_test) = cifar100.load_data()
        x_train, x_test = self.normalization(x_train, x_test)
        y_train = to_categorical(y_train, 100)
        y_test  = to_categorical(y_test, 100)
        y_train = self.label_smoothing(y_train, 10, 0.1)
        self.compile(loss='categorical_crossentropy', metrics=['acc'])

        self.warmup(x_train, y_train)

        lr, batch_size = self.grid_search(x_train, y_train, x_test, y_test)

        self.training(x_train, y_train, epochs=epochs, batch_size=batch_size,
                      lr=lr, decay=decay)
        self.evaluate(x_test, y_test)

    def coil100(self, epochs=20, decay=('cosine', 0)):
        """
        """
        # Get TF.dataset generator for COIL100
        train, info = tfds.load('coil100', split='train', shuffle_files=True, with_info=True, as_supervised=True)
        n_classes = info.features['label'].num_classes
        n_images = info.splits['train'].num_examples
        input_shape = info.features['image'].shape

        # Get the dataset into memory
        train = train.shuffle(n_images).batch(n_images)
        for images, labels in train.take(1):
            pass
    
        images = np.asarray(images)
        images, _ = self.standardization(images, None)
        labels = to_categorical(np.asarray(labels), n_classes)

        # split the dataset into train/test
        x_train, x_test, y_train, y_test = train_test_split(images, labels, test_size=0.2)

        self.compile(loss='categorical_crossentropy', metrics=['acc'])

        self.warmup(x_train, y_train)

        lr, batch_size = self.grid_search(x_train, y_train, x_test, y_test)

        self.training(x_train, y_train, epochs=epochs, batch_size=batch_size,
                      lr=lr, decay=decay)
        self.evaluate(x_test, y_test)

