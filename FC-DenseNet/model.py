# NIST-developed software is provided by NIST as a public service. You may use, copy and distribute copies of the software in any medium, provided that you keep intact this entire notice. You may improve, modify and create derivative works of the software or any portion of the software, and you may copy and distribute such modifications or works. Modified works should carry a notice stating that you changed the software and should note the date and nature of any such change. Please explicitly acknowledge the National Institute of Standards and Technology as the source of the software.

# NIST-developed software is expressly provided "AS IS." NIST MAKES NO WARRANTY OF ANY KIND, EXPRESS, IMPLIED, IN FACT OR ARISING BY OPERATION OF LAW, INCLUDING, WITHOUT LIMITATION, THE IMPLIED WARRANTY OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT AND DATA ACCURACY. NIST NEITHER REPRESENTS NOR WARRANTS THAT THE OPERATION OF THE SOFTWARE WILL BE UNINTERRUPTED OR ERROR-FREE, OR THAT ANY DEFECTS WILL BE CORRECTED. NIST DOES NOT WARRANT OR MAKE ANY REPRESENTATIONS REGARDING THE USE OF THE SOFTWARE OR THE RESULTS THEREOF, INCLUDING BUT NOT LIMITED TO THE CORRECTNESS, ACCURACY, RELIABILITY, OR USEFULNESS OF THE SOFTWARE.

# You are solely responsible for determining the appropriateness of using and distributing the software and you assume all risks associated with its use, including but not limited to the risks and costs of program errors, compliance with applicable laws, damage to or loss of data, programs or equipment, and the unavailability or interruption of operation. This software is not intended to be used in any situation where a failure could cause risk of injury or damage to property. The software developed by NIST employees is not subject to copyright protection within the United States.

import sys
if sys.version_info[0] < 3:
    raise RuntimeError('Python3 required')

import tensorflow as tf
tf_version = tf.__version__.split('.')
if int(tf_version[0]) != 2:
    raise RuntimeError('Tensorflow 2.x.x required')

import numpy as np


class FCDensenet():

    __BN_MOMENTUM = 0.9
    __WEIGHT_DECAY = 1e-4
    __DROPOUT_RATE = 0.2

    NAME_SCOPE = 'FCDenseNet56'
    RADIUS = 384
    NB_DENSE_BLOCK = 5
    NB_LAYERS_PER_BLOCK = 4
    INIT_CONV_FILTERS = 48
    GROWTH_RATE = 12

    # NAME_SCOPE = 'FCDenseNet67'
    # RADIUS = 480
    # NB_DENSE_BLOCK = 5
    # NB_LAYERS_PER_BLOCK = 5
    # INIT_CONV_FILTERS = 48
    # GROWTH_RATE = 16

    # NAME_SCOPE = 'FCDenseNet103'
    # RADIUS = 1120
    # NB_DENSE_BLOCK = 5
    # NB_LAYERS_PER_BLOCK = [4, 5, 7, 10, 12, 15]
    # INIT_CONV_FILTERS = 48
    # GROWTH_RATE = 16

    SIZE_FACTOR = 2**NB_DENSE_BLOCK

    @staticmethod
    def __conv_block(x, nb_filters):
        x = tf.keras.layers.BatchNormalization(axis=1, momentum=FCDensenet.__BN_MOMENTUM)(x)
        x = tf.keras.layers.Activation('relu')(x)
        x = tf.keras.layers.Conv2D(filters=nb_filters,
                                        kernel_size=3,
                                        padding='same',
                                        activation=None,
                                        use_bias=False,
                                        data_format='channels_first')(x)
        x = tf.keras.layers.Dropout(FCDensenet.__DROPOUT_RATE)(x)
        return x

    @staticmethod
    def __dense_block(x, nb_layers, nb_filters, growth_rate, grow_nb_filters=True):
        x_list = [x]
        for i in range(nb_layers):
            cb = FCDensenet.__conv_block(x, growth_rate)
            x_list.append(cb)
            x = tf.keras.layers.concatenate([x, cb], axis=1)
            if grow_nb_filters:
                nb_filters += growth_rate

        return x, nb_filters, x_list

    @staticmethod
    def __transition_down_block(x, nb_filters):
        x = tf.keras.layers.BatchNormalization(momentum=FCDensenet.__BN_MOMENTUM, axis=1)(x)
        x = tf.keras.layers.Activation('relu')(x)
        x = tf.keras.layers.Conv2D(filters=nb_filters,
                                   kernel_size=1,
                                   padding='same',
                                   activation=None,
                                   use_bias=False,
                                   kernel_regularizer=tf.keras.regularizers.l2(FCDensenet.__WEIGHT_DECAY),
                                   data_format='channels_first')(x)
        x = tf.keras.layers.MaxPooling2D(pool_size=2, strides=2, data_format='channels_first')(x)
        return x

    @staticmethod
    def __transition_up_block(x, nb_filters):
        return tf.keras.layers.Conv2DTranspose(filters=nb_filters,
                                   kernel_size=3,
                                   activation='relu',
                                   padding='same',
                                   strides=2,
                                   kernel_regularizer=tf.keras.regularizers.l2(FCDensenet.__WEIGHT_DECAY),
                                    data_format='channels_first')(x)

    def __init__(self, number_classes, global_batch_size, number_channels, learning_rate=1e-4, label_smoothing=0):

        self.number_channels = number_channels
        self.global_batch_size = global_batch_size
        self.learning_rate = learning_rate
        self.nb_classes = number_classes
        self.initial_kernel_size = (3, 3)

        # image is HWC (normally e.g. RGB image) however data needs to be NCHW for network
        self.inputs = tf.keras.Input(shape=(self.number_channels, None, None))

        self.model = self.build_model()

        self.loss_fn = tf.keras.losses.CategoricalCrossentropy(from_logits=False, label_smoothing=label_smoothing, reduction=tf.keras.losses.Reduction.NONE)

        self.optimizer = tf.keras.optimizers.Adam(learning_rate=self.learning_rate)

    def load_checkpoint(self, checkpoint_filepath: str):
        checkpoint = tf.train.Checkpoint(optimizer=self.optimizer, model=self.model)
        checkpoint.restore(checkpoint_filepath).expect_partial()

    def build_model(self):

        with tf.name_scope(FCDensenet.NAME_SCOPE):

            # row, col, channel = self.img_size

            # `nb_layers` is a list with the number of layers in each dense block
            if type(FCDensenet.NB_LAYERS_PER_BLOCK) is list or type(FCDensenet.NB_LAYERS_PER_BLOCK) is tuple:
                nb_layers = list(FCDensenet.NB_LAYERS_PER_BLOCK)  # Convert tuple to list

                if len(nb_layers) != (FCDensenet.NB_DENSE_BLOCK + 1):
                    raise RuntimeError('If `nb_layers_per_block` is a list, its length must be (`nb_dense_block` + 1)')

                bottleneck_nb_layers = nb_layers[-1]
                rev_layers = nb_layers[::-1]
                nb_layers.extend(rev_layers[1:])
            else:
                bottleneck_nb_layers = FCDensenet.NB_LAYERS_PER_BLOCK
                nb_layers = [FCDensenet.NB_LAYERS_PER_BLOCK] * (2 * FCDensenet.NB_DENSE_BLOCK + 1)

            print('Layers in each dense block: {}'.format(nb_layers))

            # Initial convolution
            x = tf.keras.layers.Conv2D(filters=FCDensenet.INIT_CONV_FILTERS, kernel_size=self.initial_kernel_size, padding='same',
                       use_bias=False, kernel_regularizer=tf.keras.regularizers.l2(FCDensenet.__WEIGHT_DECAY), data_format='channels_first')(self.inputs)
            x = tf.keras.layers.BatchNormalization(momentum=FCDensenet.__BN_MOMENTUM, axis=1)(x)
            x = tf.keras.layers.Activation('relu')(x)

            # keeps track of the current number of feature maps
            nb_filter = FCDensenet.INIT_CONV_FILTERS

            # collect skip connections on the downsampling path so that
            # they can be concatenated with outputs on the upsampling path
            skip_list = []

            # Build the downsampling path by adding dense blocks and transition down blocks
            for block_idx in range(FCDensenet.NB_DENSE_BLOCK):
                x, nb_filter, _ = FCDensenet.__dense_block(x, nb_layers[block_idx], nb_filter, FCDensenet.GROWTH_RATE)

                skip_list.append(x)
                x = FCDensenet.__transition_down_block(x, nb_filter)

            # Add the bottleneck dense block.
            _, nb_filter, concat_list = FCDensenet.__dense_block(x, bottleneck_nb_layers, nb_filter, FCDensenet.GROWTH_RATE)

            print('Number of skip connections: {}'.format(len(skip_list)))

            # reverse the list of skip connections
            skip_list = skip_list[::-1]

            # Build the upsampling path by adding dense blocks and transition up blocks
            for block_idx in range(FCDensenet.NB_DENSE_BLOCK):
                n_filters_keep = FCDensenet.GROWTH_RATE * nb_layers[FCDensenet.NB_DENSE_BLOCK + block_idx]

                # upsampling block must upsample only the feature maps (concat_list[1:]),
                # not the concatenation of the input with the feature maps
                l = tf.keras.layers.concatenate(concat_list[1:], axis=1, name='Concat_DenseBlock_out_{}'.format(block_idx))

                t = FCDensenet.__transition_up_block(l, nb_filters=n_filters_keep)

                # concatenate the skip connection with the transition block output
                x = tf.keras.layers.concatenate([t, skip_list[block_idx]], axis=1, name='Concat_SkipCon_{}'.format(block_idx))

                # Dont allow the feature map size to grow in upsampling dense blocks
                x_up, nb_filter, concat_list = FCDensenet.__dense_block(x, nb_layers[FCDensenet.NB_DENSE_BLOCK + block_idx + 1], nb_filters=FCDensenet.GROWTH_RATE,
                                                            growth_rate=FCDensenet.GROWTH_RATE, grow_nb_filters=False)

            # final convolution
            x = tf.keras.layers.concatenate(concat_list[1:], axis=1)
            x = tf.keras.layers.Conv2D(filters=self.nb_classes, kernel_size=1, activation='linear', padding='same', use_bias=False, data_format='channels_first', name='logit')(x)

            # convert NCHW to NHWC so that softmax axis is the last dimension
            x = tf.keras.layers.Permute((2, 3, 1))(x)
            x = tf.keras.layers.Softmax(axis=-1, name='softmax')(x)

        fc_densenet = tf.keras.Model(self.inputs, x, name='fcd')

        return fc_densenet

    def get_keras_model(self):
        return self.model

    def get_optimizer(self):
        return self.optimizer

    def set_learning_rate(self, learning_rate):
        self.optimizer.learning_rate = learning_rate

    def get_learning_rate(self):
        return self.optimizer.learning_rate

    @staticmethod
    def __round_radius(x):
        f = np.ceil(float(x) / FCDensenet.SIZE_FACTOR)
        return int(FCDensenet.SIZE_FACTOR * f)

    def estimate_radius(self):
        N = 2 * FCDensenet.RADIUS  # get the theoretical radius
        M = 64
        # create random noise input image
        img = tf.Variable(np.random.normal(size=(1, self.number_channels, M, N)), dtype=tf.float32)

        mid_idx_n = int(N / 2)  # determine the midpoint of the image
        mid_idx_m = int(M / 2)  # determine the midpoint of the image
        # create loss function we can force to be 1 at mid_idx and zero everywhere else
        loss_fn = tf.keras.losses.MeanAbsoluteError(reduction=tf.keras.losses.Reduction.NONE)

        # compute the gradient for the noise input image
        for i in range(10):
            with tf.GradientTape() as tape:
                softmax = self.model(img, training=False)
                # modify the softmax output to create the desired loss pattern, a dirac function at the mid_idx
                msk = softmax.numpy()
                msk[0, mid_idx_m, mid_idx_n, :] = 1.0 - msk[0, mid_idx_m, mid_idx_n, :]
                loss_value = loss_fn(msk, softmax)

        # compute the gradient of the input image with respect to the loss value
        grads = tape.gradient(loss_value, img)
        # get image gradient as 2D numpy array
        grad_img = np.abs(grads[0].numpy().squeeze())
        # average over channels
        if self.number_channels > 1:
            grad_img = np.average(grad_img, axis=0)

        print('Theoretical RF: {}'.format(FCDensenet.RADIUS))
        eps = 1e-8
        # this assumes square image, which with FCDenseNet takes alot of memory
        # vec = np.maximum(np.max(grad_img, axis=0).squeeze(), np.max(grad_img, axis=1).squeeze())
        vec = np.max(grad_img, axis=0).squeeze()
        idx = np.nonzero(vec > eps)[0]
        if len(idx) < 2:
            radius = FCDensenet.RADIUS
            print('ERF based radius detection failed, defaulting to theoretical radius: {}'.format(radius))
        else:
            erf = int((np.max(idx) - np.min(idx)) / 2)
            radius = FCDensenet.__round_radius(erf)
            print('computed radius : "{}"'.format(radius))
        return radius

    def train_step(self, inputs):
        (images, labels, loss_metric, accuracy_metric) = inputs
        # Open a GradientTape to record the operations run
        # during the forward pass, which enables autodifferentiation.
        with tf.GradientTape() as tape:
            softmax = self.model(images, training=True)

            loss_value = self.loss_fn(labels, softmax) # [NxHxWx1]
            # average across the batch (N) with the approprite global batch size
            loss_value = tf.reduce_sum(loss_value, axis=0) / self.global_batch_size
            # reduce down to a scalar (reduce H, W)
            loss_value = tf.reduce_mean(loss_value)

        # Use the gradient tape to automatically retrieve
        # the gradients of the trainable variables with respect to the loss.
        grads = tape.gradient(loss_value, self.model.trainable_weights)

        # Run one step of gradient descent by updating
        # the value of the variables to minimize the loss.
        self.optimizer.apply_gradients(zip(grads, self.model.trainable_weights))

        loss_metric.update_state(loss_value)
        accuracy_metric.update_state(labels, softmax)

        return loss_value

    @tf.function
    def dist_train_step(self, dist_strategy, inputs):
        per_gpu_loss = dist_strategy.experimental_run_v2(self.train_step, args=(inputs,))
        loss_value = dist_strategy.reduce(tf.distribute.ReduceOp.SUM, per_gpu_loss, axis=None)

        return loss_value

    def test_step(self, inputs):
        (images, labels, loss_metric, accuracy_metric) = inputs
        softmax = self.model(images, training=False)

        loss_value = self.loss_fn(labels, softmax)
        # average across the batch (N) with the approprite global batch size
        loss_value = tf.reduce_sum(loss_value, axis=0) / self.global_batch_size
        # reduce down to a scalar (reduce H, W)
        loss_value = tf.reduce_mean(loss_value)

        loss_metric.update_state(loss_value)
        accuracy_metric.update_state(labels, softmax)

        return loss_value

    @tf.function
    def dist_test_step(self, dist_strategy, inputs):
        per_gpu_loss = dist_strategy.experimental_run_v2(self.test_step, args=(inputs,))
        loss_value = dist_strategy.reduce(tf.distribute.ReduceOp.SUM, per_gpu_loss, axis=None)
        return loss_value
