import os
import time
import shutil
from collections import deque
from datetime import timedelta, datetime

import numpy as np
import scipy.misc
import tensorflow as tf


TF_VERSION = list(map(int, tf.__version__.split('.')[:2]))


class DenseNet:

    # -------------------------------------------------------------------------
    # --------------------------- CLASS INITIALIZER ---------------------------
    # -------------------------------------------------------------------------

    def __init__(self, data_provider, growth_rate, layer_num_list,
                 keep_prob, num_inter_threads, num_intra_threads,
                 weight_decay, nesterov_momentum, model_type, dataset,
                 should_self_construct, should_change_lr,
                 self_constructing_var, self_constr_rlr, block_count,
                 layer_cs, asc_thresh, patience_param,
                 std_tolerance, std_window, expansion_rate,
                 should_save_logs, should_save_ft_logs, ft_period,
                 ft_comma, ft_decimal, ft_filters, ft_cross_entropies,
                 should_save_model, should_save_images,
                 renew_logs=False,
                 reduction=1.0,
                 bc_mode=False,
                 **kwargs):
        """
        Class to implement DenseNet networks as defined in this paper:
        https://arxiv.org/pdf/1611.05552.pdf

        Args:
            data_provider: data provider object for the required data set;
            growth_rate: `int`, number of convolutions in a new dense layer;
            layer_num_list: `str`, list of number of layers in each block,
                separated by commas (e.g. '12,12,12');
            keep_prob: `float`, keep probability for dropout. If keep_prob = 1
                dropout will be disabled;
            weight_decay: `float`, weight decay for L2 loss, paper = 1e-4;
            nesterov_momentum: `float`, momentum for Nesterov optimizer;
            model_type: `str`, model type name ('DenseNet' or 'DenseNet-BC'),
                should we use bottleneck layers and compression or not;
            dataset: `str`, dataset name;
            should_self_construct: `bool`, should use self-constructing or not;
            should_change_lr: `bool`, should change the learning rate or not;
            self_constructing_var: `int`, variant of the self-constructing
                algorithm to be used, if the int does not identify any variant
                the most recent (default) variant is used;
            self_constr_rlr: `int`, learning rate reduction variant to be used
                with the self-constructing algorithm, if the int does not
                identify any variant the most recent (default) variant is used;
            block_count: `int`, maximum number of blocks to self-construct;
            layer_cs: `str`, 'layer CS', preferred interpretation of CS values
                when evaluating layers (using 'relevance' or 'spread');
            asc_thresh: `int`, ascension threshold for self-constructing;
            patience_param: `int`, patience parameter for self-constructing;
            std_tolerance: `int`, std tolerance for self-constructing;
            std_window: `int`, std window for self-constructing;
            expansion_rate: `int`, rate at which new convolutions are added
                together during the self-construction of a dense layer;
            should_save_logs: `bool`, should tensorflow logs be saved or not;
            should_save_ft_logs: `bool`, should feature logs be saved or not;
            ft_period: `int`, number of epochs between two measurements of
                feature values (e.g. accuracy, loss, weight mean and std);
            ft_comma: `str`, 'comma' separator in the CSV feature logs;
            ft_decimal: `str`, 'decimal' separator in the CSV feature logs;
            ft_filters: `bool`, should check filter features or not;
            ft_cross_entropies: `bool`, should measure cross-entropies for
                each individual layer in the last block or not;
            should_save_model: `bool`, should the model be saved or not;
            should_save_images: `bool`, should images be saved or not;
            renew_logs: `bool`, remove previous logs for current model;
            reduction: `float`, reduction (theta) at transition layers for
                DenseNets with compression (DenseNet-BC);
            bc_mode: `bool`, boolean equivalent of model_type, should we use
                bottleneck layers and compression (DenseNet-BC) or not.
        """
        # Main DenseNet and DenseNet-BC parameters.
        self.creation_time = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        self.data_provider = data_provider
        self.data_shape = data_provider.data_shape
        self.n_classes = data_provider.n_classes
        self.growth_rate = growth_rate
        self.num_inter_threads = num_inter_threads
        self.num_intra_threads = num_intra_threads
        # Number of outputs (feature maps) produced by the initial convolution
        # (2*k, same value as in the original Torch code).
        self.first_output_features = growth_rate * 2
        self.layer_num_list = list(map(int, layer_num_list.split(',')))
        self.total_blocks = len(self.layer_num_list)
        self.bc_mode = bc_mode
        self.reduction = reduction

        print("Build %s model with %d blocks, "
              "The number of layers in each block is:" % (
                  model_type, self.total_blocks))
        if not bc_mode:
            print('\n'.join('Block %d: %d composite layers.' % (
                k, self.layer_num_list[k]) for k in range(len(
                    self.layer_num_list))))
        if bc_mode:
            print('\n'.join('Block %d: %d bottleneck layers and %d composite'
                            'layers.' % (k, self.layer_num_list[k],
                                         self.layer_num_list[k])
                            for k in range(len(self.layer_num_list))))

        print("Reduction at transition layers: %.1f" % self.reduction)

        self.keep_prob = keep_prob
        self.weight_decay = weight_decay
        self.nesterov_momentum = nesterov_momentum
        self.model_type = model_type
        self.dataset_name = dataset

        self.should_self_construct = should_self_construct
        self.should_change_lr = should_change_lr

        self.block_count = max(1, block_count)
        self.layer_cs = layer_cs

        # Manage self construction only when self-constructing.
        if should_self_construct:
            # Choice of the self-constructing algorithm variant.
            self.sc_var = self_constructing_var
            if self_constructing_var == 0:
                self.self_constructing_step = self.self_constructing_var0
            elif self_constructing_var == 1:
                self.self_constructing_step = self.self_constructing_var1
            elif self_constructing_var == 2:
                self.self_constructing_step = self.self_constructing_var2
            else:
                self.self_constructing_step = self.self_constructing_var3
            # else:
            #     self.self_constructing_step = self.self_constructing_var_test

            # Choice of the self-constructing learning rate reduction variant.
            if self_constr_rlr == 0:
                self.self_constr_rlr = self.self_constr_rlr0
            else:
                self.self_constr_rlr = self.self_constr_rlr1

            # Self-construction parameters.
            self.asc_thresh = asc_thresh
            self.patience_param = patience_param
            self.patience_cntdwn = patience_param
            self.std_tolerance = std_tolerance
            self.std_window = std_window
            self.acc_FIFO = deque(maxlen=self.std_window)
            self.expansion_rate = expansion_rate

        # Data saving parameters.
        self.should_save_logs = should_save_logs
        self.should_save_ft_logs = should_save_ft_logs
        self.ft_period = ft_period
        self.ftc = ft_comma
        self.ftd = ft_decimal
        self.ft_filters = ft_filters
        self.ft_cross_entropies = ft_cross_entropies

        self.should_save_model = should_save_model
        self.should_save_images = should_save_images
        self.renew_logs = renew_logs
        self.batches_step = 0

        self._define_inputs()
        self._build_graph()
        self._initialize_session()
        self._count_useful_trainable_params()

    # -------------------------------------------------------------------------
    # ------------------------ SAVING AND LOADING DATA ------------------------
    # -------------------------------------------------------------------------

    def update_paths(self):
        """
        Update all paths for saving data to their proper values.
        This is used after the graph is modified (new block or layer).
        This is also used after an AttributeError when calling these paths.
        """
        save_path = 'saves/%s' % self.model_identifier
        if self.should_save_model:
            os.makedirs(save_path, exist_ok=True)
        save_path = '%s/%s' % (save_path, 'model.chkpt')
        self._save_path = save_path

        logs_path = 'logs/%s' % self.model_identifier
        if self.should_save_logs:
            if self.renew_logs:
                shutil.rmtree(logs_path, ignore_errors=True)
            os.makedirs(logs_path, exist_ok=True)
        self._logs_path = logs_path

        ft_logs_path = 'ft_logs/%s' % self.run_identifier
        if self.should_save_ft_logs:
            os.makedirs('ft_logs/', exist_ok=True)
        self._ft_logs_path = ft_logs_path

        images_path = 'images/%s' % self.run_identifier
        if self.should_save_images:
            os.makedirs(images_path, exist_ok=True)
        self._images_path = images_path

        return save_path, logs_path, ft_logs_path, images_path

    @property
    def model_identifier(self):
        """
        Returns an identifier `str` for the current DenseNet model.
        It gives the model's type ('DenseNet' or 'DenseNet-BC'),
        its growth rate k, the number of layers in each block,
        and the dataset that was used.
        """
        return "{}_growth_rate={}_layer_num_list={}_dataset_{}".format(
            self.model_type, self.growth_rate, ",".join(map(
                str, self.layer_num_list)), self.dataset_name)

    @property
    def run_identifier(self):
        """
        Returns an identifier `str` for the current execution of the algorithm.
        It gives the model's type ('DenseNet' or 'DenseNet-BC'),
        its growth rate k, the dataset that was used,
        and the date and hour at which the execution started.
        """
        return "{}_{}_growth_rate={}_dataset_{}".format(
            self.model_type, self.creation_time, self.growth_rate,
            self.dataset_name)

    @property
    def save_path(self):
        """
        Returns a path where the saver should save the current model.
        """
        try:
            save_path = self._save_path
        except AttributeError:
            save_path = self.update_paths()[0]
        return save_path

    @property
    def logs_path(self):
        """
        Returns a path where the logs for the current model should be written.
        """
        try:
            logs_path = self._logs_path
        except AttributeError:
            logs_path = self.update_paths()[1]
        return logs_path

    @property
    def ft_logs_path(self):
        """
        Returns a path where the evolution of features in the current execution
        should be recorded.
        """
        try:
            ft_logs_path = self._ft_logs_path
        except AttributeError:
            ft_logs_path = self.update_paths()[2]
        return ft_logs_path

    @property
    def images_path(self):
        """
        Returns a path where images from the current execution should be saved.
        """
        try:
            images_path = self._images_path
        except AttributeError:
            images_path = self.update_paths()[3]
        return images_path

    def save_model(self, global_step=None):
        """
        Saves the current trained model at the proper path, using the saver.

        Args:
            global_step: `int` or None, used for numbering saved model files
        """
        self.saver.save(self.sess, self.save_path, global_step=global_step)

    def load_model(self):
        """
        Loads a saved model to use (instead of a new one) using the saver.
        This is a previously trained and saved model using the model_type
        ('DenseNet' or 'DenseNet-BC'), growth rate, layers in each block,
        and dataset that was specified in the program arguments.
        """
        try:
            self.saver.restore(self.sess, self.save_path)
        except Exception as e:
            raise IOError("Failed to to load model "
                          "from save path: %s" % self.save_path)
        self.saver.restore(self.sess, self.save_path)
        print("Successfully load model from save path: %s" % self.save_path)

    def log_loss_accuracy(self, loss, accuracy, epoch, prefix,
                          should_print=True):
        """
        Writes a log of the current mean loss (cross_entropy) and accuracy.

        Args:
            loss: `float`, loss (cross_entropy) for the current log;
            accuracy: `float`, accuracy for the current log;
            epoch: `int`, current training epoch (or batch);
            prefix: `str`, is this log for a batch ('per_batch'), a
                training epoch ('train') or a validation epoch ('valid');
            should_print: `bool`, should we print this log on console or not.
        """
        if should_print:
            print("mean cross_entropy: %f, mean accuracy: %f" % (
                loss, accuracy))
        summary = tf.Summary(value=[
            tf.Summary.Value(
                tag='loss_%s' % prefix, simple_value=float(loss)),
            tf.Summary.Value(
                tag='accuracy_%s' % prefix, simple_value=float(accuracy))
        ])
        self.summary_writer.add_summary(summary, epoch)

    def ft_log_filters(self, b, cs_table_ls, lcs_dst, lcs_src):
        """
        Write a feature log with data concerning filters: the CS of every
        connection in a given block, the 'layer CS' (relevance or spread) for
        destinations and sources for all layers in the same block.

        Args:
            b: `int`, identifier number for the block;
            cs_table_ls: `list` of `list` of `float`, the table of CS for each
                connection to a layer l from a previous layer s;
            lcs_dst: `list` of `float`, 'layer CS' for destinations
                for all layers in the block;
            lcs_src: `list` of `float`, 'layer CS' for sources
                for all layers in the block.
        """
        # printing and saving the data to feature logs
        for l in range(self.layer_num_list[b]):
            # 'layer CS' for destinations of l-1
            print('  - %s for destinations = %f' % (
                self.layer_cs.capitalize(), lcs_dst[l]))
            # destination layer normalised CS (sent from l-1 towards d)
            for d in range(l, self.layer_num_list[b]):
                print('  - Towards layer %d: normalised CS = %f' % (
                    d, cs_table_ls[d][l]/max(
                        fwd[l] for fwd in cs_table_ls if len(fwd) > l)))

            print('\n* Block %d filter %d:' % (b, l))
            # source layer normalised CS (received at l from s)
            for s in range(len(cs_table_ls[l])):
                print('  - From layer %d: normalised CS = %f' % (
                    s, cs_table_ls[l][s]/max(cs_table_ls[l])))
            # 'layer CS' for sources of l
            print('  - %s for sources = %f' % (
                self.layer_cs.capitalize(), lcs_src[l]))

            if self.should_save_ft_logs:
                # write all of the above in the feature log
                self.feature_writer.write(('%s\"%f\"' % (self.ftc, lcs_dst[l])
                                           ).replace(".", self.ftd))
                self.feature_writer.write('%s\"\"' % self.ftc)
                for d in range(l, self.layer_num_list[b]):
                    self.feature_writer.write((
                        '%s\"%f\"' % (self.ftc, cs_table_ls[d][l]/max(
                            fwd[l] for fwd in cs_table_ls if len(fwd) > l))
                        ).replace(".", self.ftd))
                self.feature_writer.write('%s\"\"' % self.ftc)
                for s in range(len(cs_table_ls[l])):
                    self.feature_writer.write(('%s\"%f\"' % (
                        self.ftc, cs_table_ls[l][s]/max(cs_table_ls[l]))
                        ).replace(".", self.ftd))
                self.feature_writer.write('%s\"\"' % self.ftc)
                self.feature_writer.write(('%s\"%f\"' % (self.ftc, lcs_src[l])
                                           ).replace(".", self.ftd))

    # -------------------------------------------------------------------------
    # ----------------------- PROCESSING FEATURE VALUES -----------------------
    # -------------------------------------------------------------------------

    def get_cs_list(self, f_image, f_num):
        """
        Get the list of connection strengths (CS) for all connections to a
        given filter layer.

        The CS of a connection is equal to the mean of its associated absolute
        kernel weights (sum divided by num of weights).

        Args:
            f_image: `np.ndarray`, an array representation of the filter;
            f_num: `int`, identifier for the filter within the block.
        """
        # split kernels by groups, depending on which connection they belong to
        # for this, use filter numbering (different in BC mode!)
        splitting_guide = []
        for i in range(int(f_num/(1+int(self.bc_mode))), 0, -1):
            splitting_guide.append(f_image.shape[0] - i*self.growth_rate)

        if len(splitting_guide) > 0:
            f_split_image = np.split(f_image, splitting_guide)
        else:
            f_split_image = [f_image]

        # calculate CS (means of abs weights) by groups of kernels
        cs_list = []
        for split in range(len(f_split_image)):
            cs_list.append(np.mean(np.abs(f_split_image[split])))

        return cs_list

    def get_relev_dst(self, b, cs_table_ls, tresh_fract=0.67):
        """
        Get the relevance for destinations for all layers (filters) in a block.
        The relevance for destinations of a layer l expresses the portion of
        the connections sent from l-1 that are 'relevant enough' for their
        destination layers to receive information through them.

        For each connection from l-1 to a future layer d, add +1/n_connections
        if the connection's CS is >= tresh_fract * the max CS out of all
        connections received by d.
        N.B.: For l=0, the preceding l-1 is the output from the previous block.

        Args:
            b: `int`, identifier number for the block;
            cs_table_ls: `list` of `list` of `float`, the table of CS for each
                connection to a layer l from a previous layer s;
            tresh_fract: `float`, the fraction of a layer's max CS that a CS
                is compared to to be considered 'relevant enough'.
        """
        relev_dst = []
        max_cs = 0  # the max CS for each future layer

        for l in range(self.layer_num_list[b]):
            relev_dst.append(0)
            for d in range(l, self.layer_num_list[b]):
                max_cs = max(cs_table_ls[d])
                relev_dst[l] += int(cs_table_ls[d][l]/max_cs >= tresh_fract)
            # normalised: 0 = no relevant connections, 1 = all relevant
            relev_dst[l] /= self.layer_num_list[b] - l

        return relev_dst

    def get_relev_src(self, b, cs_table_ls, tresh_fract=0.67):
        """
        Get the relevance for sources for all layers (filters) in a block.
        The relevance for sources of a layer l expresses the portion of the
        connections received by l that are 'relevant enough' for their source
        layers to send information through them.

        For each connection from a past layer s-1 to l, add +1/n_connections
        if the connection's CS is >= tresh_fract * the max CS out of all
        connections sent from s-1.
        N.B.: For s=0, the preceding s-1 is the output from the previous block.

        Args:
            b: `int`, identifier number for the block;
            cs_table_ls: `list` of `list` of `float`, the table of CS for each
                connection to a layer l from a previous layer s;
            tresh_fract: `float`, the fraction of a layer's max CS that a CS
                is compared to to be considered 'relevant enough'.
        """
        relev_src = []
        max_cs = 0  # the max CS for each past layer

        for l in range(self.layer_num_list[b]):
            relev_src.append(0)
            for s in range(len(cs_table_ls[l])):
                max_cs = max(fwd[s] for fwd in cs_table_ls[s:])
                relev_src[l] += int(cs_table_ls[l][s]/max_cs >= tresh_fract)
            # normalised: 0 = no relevant connections, 1 = all relevant
            relev_src[l] /= l+1

        return relev_src

    def get_spread_emi(self, b, cs_table_ls, tresh_fract=0.67):
        """
        Get the spread of emission for all layers (filters) in a block.
        The spread of emission of a layer l expresses the portion of the
        connections sent from l-1 that are 'relevant enough' for l-1 to send
        (emit) information through them.

        For each connection from l-1 to a future layer d, add +1/n_connections
        if the connection's CS is >= tresh_fract * the max CS out of all
        connections sent from l-1.
        N.B.: For l=0, the preceding l-1 is the output from the previous block.

        Args:
            b: `int`, identifier number for the block;
            cs_table_ls: `list` of `list` of `float`, the table of CS for each
                connection to a layer l from a previous layer s;
            tresh_fract: `float`, the fraction of a layer's max CS that a CS
                is compared to to be considered 'relevant enough'.
        """
        spread_emi = []
        max_cs = 0  # the max CS for each future layer

        for l in range(self.layer_num_list[b]):
            spread_emi.append(0)
            max_cs = max(fwd[l] for fwd in cs_table_ls[l:])
            for d in range(l, self.layer_num_list[b]):
                spread_emi[l] += int(cs_table_ls[d][l]/max_cs >= tresh_fract)
            # normalised: 0 = no relevant connections, 1 = all relevant
            spread_emi[l] /= self.layer_num_list[b] - l

        return spread_emi

    def get_spread_rec(self, b, cs_table_ls, tresh_fract=0.67):
        """
        Get the spread of reception for all layers (filters) in a block.
        The spread of reception of a layer l expresses the portion of the
        connections received by l that are 'relevant enough' for l to receive
        information through them.

        For each connection from a past layer s-1 to l, add +1/n_connections
        if the connection's CS is >= tresh_fract * the max CS out of all
        connections received by l.
        N.B.: For s=0, the preceding s-1 is the output from the previous block.

        Args:
            b: `int`, identifier number for the block;
            cs_table_ls: `list` of `list` of `float`, the table of CS for each
                connection to a layer l from a previous layer s;
            tresh_fract: `float`, the fraction of a layer's max CS that a CS
                is compared to to be considered 'relevant enough'.
        """
        spread_rec = []
        max_cs = 0  # the max CS for each past layer

        for l in range(self.layer_num_list[b]):
            spread_rec.append(0)
            max_cs = max(cs_table_ls[l])
            for s in range(len(cs_table_ls[l])):
                spread_rec[l] += int(cs_table_ls[l][s]/max_cs >= tresh_fract)
            # normalised: 0 = no relevant connections, 1 = all relevant
            spread_rec[l] /= l+1

        return spread_rec

    def process_filter(self, filter, block_num, filter_num, epoch):
        """
        Process a given convolution filter's kernel weights, in some cases
        save a representation of the filter and its weights as a PNG image.
        Returns a list with the connection strengths (CS) for connections
        between any given layer l and each past layer s.

        Args:
            filter: tensor, the filter whose kernel weights are processed;
            block_num: `int`, identifier number for the filter's block;
            filter_num: `int`, identifier for the filter within the block;
            epoch: `int`, current training epoch (or batch).
        """
        # get an array representation of the filter, then get its dimensions
        f_image = self.sess.run(filter)
        f_d = filter.get_shape().as_list()
        f_image = f_image.transpose()
        f_image = np.moveaxis(f_image, [0, 1], [1, 0])

        # calculate connection strength for all connections
        cs_list = self.get_cs_list(f_image, filter_num)

        if self.should_save_images:
            # properly place the kernels to save the filter as an image
            f_image = np.moveaxis(f_image, [1, 2], [0, 1])
            f_image = np.resize(f_image, (f_d[1]*f_d[3], f_d[0]*f_d[2]))

            # save the image in the proper file
            im_filepath = './%s/block_%d_filter_%d' % (
                self.images_path, block_num, filter_num)
            os.makedirs(im_filepath, exist_ok=True)
            im_filepath += '/epoch_%d.png' % epoch
            scipy.misc.imsave(im_filepath, f_image)

        return cs_list

    def process_block_filters(self, b, epoch):
        """
        Process a given block's filters. Return values for features related to
        the filters' kernel weights: connection strengths, 'layer CS' for
        destinations, and 'layer CS' for sources. The 'layer CS' can be either
        relevance or spread, depending on what is required by the algorithm.

        Args:
            b: `int`, identifier number for the block;
            epoch: `int`, current training epoch (or batch).
        """
        cs_table_ls = []
        # process each filter separately (except BC bottlenecks),
        # get the conection strength between each layer l and any past layer s
        for f in range(len(self.filter_ref_list[b+1])):
            if not self.bc_mode or not f % 2:
                cs_table_ls.append(self.process_filter(
                    self.filter_ref_list[b+1][f], b, f, epoch))

        # if the required 'layer CS' is relevance
        if self.layer_cs == 'relevance':
            # relevance for destinations: what portion of all the connections
            # sent from a layer l-1 are relevant for their destination layers?
            lcs_dst = self.get_relev_dst(b, cs_table_ls)

            # relevance for sources: what portion of all the connections
            # received by a layer l are relevant for their source layers?
            lcs_src = self.get_relev_src(b, cs_table_ls)

        # else (if the required 'layer CS' is spread)
        else:
            # spread of emission (for destinations): what portion of all the
            # connections sent from a layer l-1 are relevant for l-1?
            lcs_dst = self.get_spread_emi(b, cs_table_ls)

            # spread of reception (for sources): what portion of all the
            # connections received by a layer l are relevant for l?
            lcs_src = self.get_spread_rec(b, cs_table_ls)

        return(cs_table_ls, lcs_dst, lcs_src)

    # -------------------------------------------------------------------------
    # ---------------------- DEFINING INPUT PLACEHOLDERS ----------------------
    # -------------------------------------------------------------------------

    def _define_inputs(self):
        """
        Defines some imput placeholder tensors:
        images, labels, learning_rate, is_training.
        """
        shape = [None]
        shape.extend(self.data_shape)
        self.images = tf.placeholder(
            tf.float32,
            shape=shape,
            name='input_images')
        self.labels = tf.placeholder(
            tf.float32,
            shape=[None, self.n_classes],
            name='labels')
        self.learning_rate = tf.placeholder(
            tf.float32,
            shape=[],
            name='learning_rate')
        self.is_training = tf.placeholder(tf.bool, shape=[])

    # -------------------------------------------------------------------------
    # ---------------------- BUILDING THE DENSENET GRAPH ----------------------
    # -------------------------------------------------------------------------

    # SIMPLEST OPERATIONS -----------------------------------------------------
    # -------------------------------------------------------------------------

    def weight_variable_msra(self, shape, name):
        """
        Creates weights for a fully-connected layer, using an initialization
        method which does not scale the variance.

        Args:
            shape: `list` of `int`, shape of the weight matrix;
            name: `str`, a name for identifying the weight matrix.
        """
        # print("CREATING WEIGHT VARIABLE: " + name)
        # print(shape)
        return tf.get_variable(
            name=name,
            shape=shape,
            initializer=tf.contrib.layers.variance_scaling_initializer())

    def avg_pool(self, _input, k):
        """
        Performs average pooling on a given input (_input),
        within square kernels of side k and stride k.

        Args:
            _input: tensor, the operation's input;
            k: `int`, the size and stride for the kernels.
        """
        ksize = [1, k, k, 1]
        strides = [1, k, k, 1]
        padding = 'VALID'
        output = tf.nn.avg_pool(_input, ksize, strides, padding)
        return output

    def batch_norm(self, _input, scope='BatchNorm'):
        """
        Performs batch normalisation on a given input (_input).

        Args:
            _input: tensor, the operation's input.
            scope: `str`, a variable scope for the operation.
        """
        output = tf.contrib.layers.batch_norm(
            _input, scale=True, is_training=self.is_training,
            updates_collections=None, scope=scope)
        return output

    def conv2d(self, _input, out_features, kernel_size,
               strides=[1, 1, 1, 1], padding='SAME'):
        """
        Creates a 2d convolutional filter layer (applies a certain number of
        kernels on some input features to obtain output features).
        Returns the output of the layer and a reference to its filter.

        Args:
            _input: tensor, the operation's input;
            out_features: `int`, number of feature maps at the output;
            kernel_size: `int`, size of the square kernels (their side);
            strides: `list` of `int`, strides in each direction for kernels;
            padding: `str`, should we use padding ('SAME') or not ('VALID').
        """
        in_features = int(_input.get_shape()[-1])
        filter_ref = self.weight_variable_msra(
            [kernel_size, kernel_size, in_features, out_features],
            name='filter')
        output = tf.nn.conv2d(_input, filter_ref, strides, padding)
        return output, filter_ref

    def conv2d_with_kernels(self, _input, out_features, kernel_size,
                            strides=[1, 1, 1, 1], padding='SAME'):
        """
        Creates a 2d convolutional filter layer, by producing a list of 3d
        kernels and then stacking them together to create the filter.
        Returns the output of the layer and a reference to its convolutional
        filter, as well as the newly generated list of kernels.

        Args:
            _input: tensor, the operation's input;
            out_features: `int`, number of feature maps at the output;
            kernel_size: `int`, size of the square kernels (their side);
            strides: `list` of `int`, strides in each direction for kernels;
            padding: `str`, should we use padding ('SAME') or not ('VALID').
        """
        in_features = int(_input.get_shape()[-1])
        # First create a list with the 3d kernels (easily modifiable):
        kernels = []
        for o in range(out_features):
            kernels.append(self.weight_variable_msra(
                [kernel_size, kernel_size, in_features], name='kernel'+str(o)))
        # The kernels are stacked together so as to create a 4d filter
        # (dimension 3 = output features).
        filter_ref = tf.stack(kernels, axis=3, name='filter')
        # Using the filter, the convolution is defined.
        output = tf.nn.conv2d(_input, filter_ref, strides, padding)
        return output, filter_ref, kernels

    def conv2d_with_given_kernels(self, _input, kernels,
                                  strides=[1, 1, 1, 1], padding='SAME'):
        """
        Creates a 2d convolutional filter layer, by using a given list of 3d
        kernels to create a filter (stacking them together).
        Returns the output of the layer and a reference to its filter.

        Args:
            _input: tensor, the operation's input;
            kernels: `list` of tensors, contains each of the kernels from which
                the convolution will be built;
            strides: `list` of `int`, strides in each direction for kernels;
            padding: `str`, should we use padding ('SAME') or not ('VALID').
        """
        # The kernels are stacked together so as to create a 4d filter.
        # Using the same name = good idea?
        filter_ref = tf.stack(kernels, axis=3, name='filter')
        output = tf.nn.conv2d(_input, filter_ref, strides, padding)
        return output, filter_ref

    def dropout(self, _input):
        """
        If the given keep_prob is not 1 AND if the graph is being trained,
        performs a random dropout operation on a given input (_input).
        The dropout probability is the keep_prob parameter.

        Args:
            _input: tensor, the operation's input.
        """
        if self.keep_prob < 1:
            output = tf.cond(
                self.is_training,
                lambda: tf.nn.dropout(_input, self.keep_prob),
                lambda: _input
            )
        else:
            output = _input
        return output

    # SIMPLEST OPERATIONS (FULLY CONNECTED) -----------------------------------
    # -------------------------------------------------------------------------

    def weight_variable_xavier(self, shape, name):
        """
        Creates weights for a fully-connected layer, using the Xavier
        initializer (keeps gradient scale roughly the same in all layers).

        Args:
            shape: `list` of `int`, shape of the weight matrix;
            name: `str`, a name for identifying the weight matrix.
        """
        return tf.get_variable(
            name,
            shape=shape,
            initializer=tf.contrib.layers.xavier_initializer())

    def bias_variable(self, shape, name='bias'):
        """
        Creates bias terms for a fully-connected layer, initialized to 0.0.

        Args:
            shape: `list` of `int`, shape of the bias matrix;
            name: `str`, a name for identifying the bias matrix.
        """
        initial = tf.constant(0.0, shape=shape)
        return tf.get_variable(name, initializer=initial)

    # COMPOSITE FUNCTION + BOTTLENECK -----------------------------------------
    # -------------------------------------------------------------------------

    def composite_function(self, _input, out_features, kernel_size=3):
        """
        Composite function H_l([x_0, ..., x_l-1]) for a dense layer.

        Takes a concatenation of previous outputs and performs:
        - batch normalisation;
        - ReLU activation function;
        - 2d convolution, with required kernel size (side);
        - dropout, if required (training the graph and keep_prob not set to 1).
        Returns the output tensor and a reference to the 2d convolution filter,
        as well as a list of the kernels in that filter, and the input tensor
        for the 2d convolution.

        Args:
            _input: tensor, the operation's input;
            out_features: `int`, number of feature maps at the output;
            kernel_size: `int`, size of the square kernels (their side).
        """
        with tf.variable_scope("composite_function"):
            # batch normalisation
            in_cv = self.batch_norm(_input)
            # ReLU activation function
            in_cv = tf.nn.relu(in_cv)
            # 2d convolution
            output, filter_ref, kernels = self.conv2d_with_kernels(
                in_cv, out_features=out_features, kernel_size=kernel_size)
            # dropout (if the graph is being trained and keep_prob is not 1)
            output = self.dropout(output)
        return output, filter_ref, kernels, in_cv

    def reconstruct_composite_function(self, in_cv, kernels):
        """
        Reconstruct the output of the composite function H_l([x_0, ..., x_l-1])
        for a dense layer, given the convolution's input and its kernels.

        Args:
            in_cv: tensor, the input of the convolution;
            kernels: `list` of tensors, the kernels for the convolution.
        """
        # 2d convolution
        output, filter_ref = self.conv2d_with_given_kernels(
            in_cv, kernels)
        # dropout
        output = self.dropout(output)
        return output, filter_ref

    def bottleneck(self, _input, out_features):
        """
        Bottleneck function, used before the composite function H_l in the
        dense layers of DenseNet-BC.

        Takes a concatenation of previous outputs and performs:
        - batch normalisation,
        - ReLU activation function,
        - 2d convolution, with kernel size 1 (produces 4x the features of H_l),
        - dropout, if required (training the graph and keep_prob not set to 1).
        Returns the output tensor and a reference to the 2d convolution kernel.

        Args:
            _input: tensor, the operation's input;
            out_features: `int`, number of feature maps at the output of H_l;
            kernel_size: `int`, size of the square kernels (their side).
        """
        with tf.variable_scope("bottleneck"):
            # batch normalisation
            output = self.batch_norm(_input)
            # ReLU activation function
            output = tf.nn.relu(output)
            inter_features = out_features * 4
            # 2d convolution (produces intermediate features)
            output, filter_ref = self.conv2d(
                output, out_features=inter_features, kernel_size=1,
                padding='VALID')
            # dropout (if the graph is being trained and keep_prob is not 1)
            output = self.dropout(output)
        return output, filter_ref

    # BLOCKS AND THEIR INTERNAL LAYERS ----------------------------------------
    # -------------------------------------------------------------------------

    def add_new_kernels_to_layer(self, _input, in_cv, layer, kernel_num,
                                 kernel_size=3):
        """
        Adds new convolution kernels to a layer within a block:
        creates the kernels, reconstructs the composite function, and
        concatenates outputs to ensure the DenseNet paradigm.
        Returns the layer's new output tensor.
        N.B.: This function is meant to be used ONLY in self-constructing mode
            (i.e. when should_self_construct is true).

        Args:
            _input: tensor, the layer's input;
            in_cv: tensor, the input for the layer's convolution;
            layer: `int`, identifier number for this layer (within a block);
            kernel_num: `int`, number of new (square) kernels to be added.
            kernel_size: `int`, size of the kernels (their side);
        """
        with tf.variable_scope("layer_%d" % layer):
            with tf.variable_scope("composite_function"):
                # create kernel_num new kernels
                in_features = int(in_cv.get_shape()[-1])
                for new_k in range(kernel_num):
                    self.kernels_ref_list[-1][-1].append(
                        self.weight_variable_msra(
                            [kernel_size, kernel_size, in_features],
                            name='kernel'+str(
                                len(self.kernels_ref_list[-1][-1])+new_k)))
                # reconstruct the composite function from the current kernels
                comp_out, filter_ref = self.reconstruct_composite_function(
                    in_cv, self.kernels_ref_list[-1][-1])
                # save a reference to the composite function's filter
                self.filter_ref_list[-1][-1] = filter_ref
            # concatenate output with layer input to ensure DenseNet paradigm
            if TF_VERSION[0] >= 1 and TF_VERSION[1] >= 0:
                output = tf.concat(axis=3, values=(_input, comp_out))
            else:
                output = tf.concat(3, (_input, comp_out))
        return output

    def add_internal_layer(self, _input, layer, growth_rate):
        """
        Adds a new convolutional (dense) layer within a block.

        This layer will perform the composite function H_l([x_0, ..., x_l-1])
        to obtain its output x_l.
        It will then concatenate x_l with the layer's input: all the outputs of
        the previous layers, resulting in [x_0, ..., x_l-1, x_l].

        Returns the layer's output, as well as the input of its conv2d.

        Args:
            _input: tensor, the operation's input;
            layer: `int`, identifier number for this layer (within a block);
            growth_rate: `int`, number of new convolutions per dense layer.
        """
        with tf.variable_scope("layer_%d" % layer):
            # use the composite function H_l (3x3 kernel conv)
            if not self.bc_mode:
                comp_out, filter_ref, kernels, in_cv = self.composite_function(
                    _input, out_features=growth_rate, kernel_size=3)
            # in DenseNet-BC mode, add a bottleneck layer before H_l (1x1 conv)
            elif self.bc_mode:
                bottleneck_out, filter_ref = self.bottleneck(
                    _input, out_features=growth_rate)
                if self.ft_filters or self.should_self_construct:
                    self.filter_ref_list[-1].append(filter_ref)
                comp_out, filter_ref, kernels, in_cv = self.composite_function(
                    bottleneck_out, out_features=growth_rate, kernel_size=3)
            # save a reference to the composite function's filter
            if self.ft_filters or self.should_self_construct:
                self.filter_ref_list[-1].append(filter_ref)
                self.kernels_ref_list[-1].append(kernels)
            # concatenate output of H_l with layer input (all previous outputs)
            if TF_VERSION[0] >= 1 and TF_VERSION[1] >= 0:
                output = tf.concat(axis=3, values=(_input, comp_out))
            else:
                output = tf.concat(3, (_input, comp_out))
        return output, in_cv

    def add_block(self, _input, block, growth_rate, layers_in_block, is_last):
        """
        Adds a new block containing several convolutional (dense) layers.
        These are connected together following a DenseNet architecture,
        as defined in the paper.
        Returns the block's output, as well as the inputs to the last layer
        and to its conv2d.

        Args:
            _input: tensor, the operation's input;
            block: `int`, identifier number for this block;
            growth_rate: `int`, number of new convolutions per dense layer;
            layers_in_block: `int`, number of dense layers in this block;
            is_last: `bool`, is this the last block in the network or not.
        """
        if self.ft_filters or self.should_self_construct:
            self.filter_ref_list.append([])
            self.kernels_ref_list.append([])
        if is_last:
            self.cross_entropy = []

        with tf.variable_scope("Block_%d" % block) as self.current_block:
            output = _input
            for layer in range(layers_in_block):
                # The inputs of the last layer and its conv2d must be saved
                # (useful for self-construction kernel by kernel)
                input_lt_lay = output
                output, input_lt_cnv = self.add_internal_layer(
                    input_lt_lay, layer, growth_rate)

                if self.ft_cross_entropies and is_last:
                    # Save the cross-entropy for all layers except the last one
                    # (it is always saved as part of the end-graph operations)
                    if layer != layers_in_block-1:
                        _, cross_entropy = self.cross_entropy_loss(
                            output, self.labels, block,
                            preserve_transition=True)
                        self.cross_entropy.append(cross_entropy)

        return output, input_lt_lay, input_lt_cnv

    # TRANSITION LAYERS -------------------------------------------------------
    # -------------------------------------------------------------------------

    def transition_layer(self, _input, block):
        """
        Adds a new transition layer after a block. This layer's inputs are the
        concatenated feature maps of each layer in the block.

        The layer first runs the composite function with kernel size 1:
        - In DenseNet mode, it produces as many feature maps as the input had.
        - In DenseNet-BC mode, it produces reduction (theta) times as many,
          compressing the output.
        Afterwards, an average pooling operation (of size 2) is carried to
        change the output's size.

        Args:
            _input: tensor, the operation's input;
            block: `int`, identifier number for the previous block.
        """
        with tf.variable_scope("Transition_after_block_%d" % block):
            # add feature map compression in DenseNet-BC mode
            out_features = int(int(_input.get_shape()[-1]) * self.reduction)
            # use the composite function H_l (1x1 kernel conv)
            output, filter_ref, kernels, in_cv = self.composite_function(
                _input, out_features=out_features, kernel_size=1)
            # save a reference to the composite function's filter
            if self.ft_filters or self.should_self_construct:
                self.filter_ref_list[-1].append(filter_ref)
                self.kernels_ref_list[-1].append(kernels)
            # use average pooling to reduce feature map size
            output = self.avg_pool(output, k=2)
        return output

    def transition_layer_to_classes(self, _input, block):
        """
        Adds the transition layer after the last block. This layer outputs the
        estimated probabilities by classes.

        It performs:
        - batch normalisation,
        - ReLU activation function,
        - wider-than-normal average pooling,
        - reshaping the output into a 1d tensor,
        - fully-connected layer (matrix multiplication, weights and biases).

        Args:
            _input: tensor, the operation's input;
            block: `int`, identifier number for the last block.
        """
        self.features_total = int(_input.get_shape()[-1])

        with tf.variable_scope("Transition_to_FC_block_%d" % block,
                               reuse=tf.AUTO_REUSE):
            # Batch normalisation.
            output = self.batch_norm(
                _input, scope='BatchNorm'+str(self.features_total))
            # ReLU activation function.
            output = tf.nn.relu(output)
            # Wide average pooling.
            last_pool_kernel = int(output.get_shape()[-2])
            output = self.avg_pool(output, k=last_pool_kernel)
            # Reshaping the output into 1d.
            output = tf.reshape(output, [-1, self.features_total])

        # FC (fully-connected) layer.
        self.FC_W = []
        for i in range(self.features_total):
            self.FC_W.append(self.weight_variable_xavier(
                [self.n_classes], name="FC_block_%d_W%d" % (block, i)))
        self.FC_bias = self.bias_variable(
            [self.n_classes], name="FC_block_%d_bias" % block)
        stacked_FC_W = tf.stack(self.FC_W, axis=0)
        logits = tf.matmul(output, stacked_FC_W) + self.FC_bias
        return logits

    def reconstruct_transition_to_classes(self, _input, block):
        """
        Reconstruct the transition layer to classes after adding a new kernel
        or layer in the last block (in such a case, the transition layer must
        remain mostly unchanged except for the new weights).

        Args:
            _input: tensor, the operation's input;
            block: `int`, identifier number for the last block.
        """
        new_features_total = int(_input.get_shape()[-1])
        with tf.variable_scope("Transition_to_FC_block_%d" % block,
                               reuse=tf.AUTO_REUSE):
            # The batch norm contains beta and gamma params for each kernel,
            # we first copy the param values from old kernels.
            beta_values = self.sess.run(
                tf.get_variable("BatchNorm"+str(self.features_total)+"/beta",
                                [self.features_total]))
            gamma_values = self.sess.run(
                tf.get_variable("BatchNorm"+str(self.features_total)+"/gamma",
                                [self.features_total]))
            # Then we create a new batch norm and initialize its params.
            output = self.batch_norm(
                _input, scope='BatchNorm'+str(new_features_total))
            new_beta = tf.get_variable(
                "BatchNorm"+str(new_features_total)+"/beta",
                [new_features_total])
            new_gamma = tf.get_variable(
                "BatchNorm"+str(new_features_total)+"/gamma",
                [new_features_total])
            self.sess.run(tf.variables_initializer([new_beta, new_gamma]))
            # For these params, we copy the old param values, and leave
            # the remaining new values for the new kernels.
            new_beta_values = self.sess.run(new_beta)
            new_gamma_values = self.sess.run(new_gamma)
            difference = new_features_total-self.features_total
            new_beta_values[:-difference] = beta_values
            new_gamma_values[:-difference] = gamma_values
            # Then we assign the modified values to reconstruct the batch norm.
            self.sess.run(new_beta.assign(new_beta_values))
            self.sess.run(new_gamma.assign(new_gamma_values))
            self.features_total = new_features_total

            # ReLU, average pooling, and reshaping into 1d
            # these do not contain any trainable params, so they are rewritten.
            output = tf.nn.relu(output)
            last_pool_kernel = int(output.get_shape()[-2])
            output = self.avg_pool(output, k=last_pool_kernel)
            features_total = int(output.get_shape()[-1])
            output = tf.reshape(output, [-1, features_total])

        # For the FC layer: add new weights, keep biases and old weights.
        for i in range(len(self.FC_W), features_total):
            self.FC_W.append(self.weight_variable_xavier(
                [self.n_classes], name="FC_block_%d_W%d" % (block, i)))
        stacked_FC_W = tf.stack(self.FC_W, axis=0)
        logits = tf.matmul(output, stacked_FC_W) + self.FC_bias
        return logits

    # END GRAPH OPERATIONS ----------------------------------------------------
    # -------------------------------------------------------------------------

    def cross_entropy_loss(self, _input, labels, block,
                           preserve_transition=False):
        """
        Takes an input and adds a transition layer to obtain predictions for
        classes. Then calculates the cross-entropy loss for that input with
        respect to expected labels. Returns the prediction tensor and the
        calculated cross-entropy.

        Args:
            _input: tensor, the operation's input;
            labels: tensor, the expected labels (classes) for the data;
            block: `int`, identifier number for the last block;
            preserve_transition: `bool`, whether or not to preserve the
                transition to classes (if yes, adapts the previous transition,
                otherwise creates a new one).
        """
        # add the FC transition layer to the classes (+ softmax).
        if preserve_transition:
            logits = self.reconstruct_transition_to_classes(_input, block)
        else:
            logits = self.transition_layer_to_classes(_input, block)
        prediction = tf.nn.softmax(logits)

        # set the calculation for the losses (cross_entropy and l2_loss)
        if TF_VERSION[0] >= 1 and TF_VERSION[1] >= 5:
            cross_entropy = tf.reduce_mean(
                tf.nn.softmax_cross_entropy_with_logits_v2(logits=logits,
                                                           labels=labels))
        else:
            cross_entropy = tf.reduce_mean(
                tf.nn.softmax_cross_entropy_with_logits(logits=logits,
                                                        labels=labels))

        return prediction, cross_entropy

    def _define_end_graph_operations(self, preserve_transition=False):
        """
        Adds the last layer on top of the (editable portion of the) graph.
        Then defines the operations for cross-entropy, the training step,
        and the accuracy.

        Args:
            preserve_transition: `bool`, whether or not to preserve the
                transition to classes (if yes, adapts the previous transition,
                otherwise creates a new one).
        """
        # obtain the predicted logits, set the calculation for the losses
        # (cross_entropy and l2_loss)
        prediction, cross_entropy = self.cross_entropy_loss(
            self.output, self.labels, self.total_blocks-1, preserve_transition)
        self.cross_entropy.append(cross_entropy)
        var_list = self.get_useful_variables()
        l2_loss = tf.add_n(
            [tf.nn.l2_loss(var) for var in var_list])

        # set the optimizer and define the training step
        optimizer = tf.train.MomentumOptimizer(
            self.learning_rate, self.nesterov_momentum, use_nesterov=True)
        self.train_step = optimizer.minimize(
            cross_entropy + l2_loss * self.weight_decay, var_list=var_list)

        # set the calculation for the accuracy
        correct_prediction = tf.equal(
            tf.argmax(prediction, 1),
            tf.argmax(self.labels, 1))
        self.accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))

    # MAIN GRAPH BUILDING FUNCTIONS -------------------------------------------
    # -------------------------------------------------------------------------

    def _new_kernels_to_last_layer(self):
        """
        Add new convolution kernels to the current last layer.
        The number of kernels to be added is given by the expansion_rate param.
        """
        # safely access the current block's variable scope
        with tf.variable_scope(self.current_block,
                               auxiliary_name_scope=False) as cblock_scope:
            with tf.name_scope(cblock_scope.original_name_scope):
                # Add the layer and save the new relevant inputs and outputs
                self.output = self.add_new_kernels_to_layer(
                    self.input_lt_lay, self.input_lt_cnv,
                    self.layer_num_list[-1]-1, self.expansion_rate)

        # Delete the last cross-entropy from the list, we will recreate it.
        del self.cross_entropy[-1]

        print("ADDED A NEW KERNEL TO LAYER #%d (BLOCK #%d)! "
              "It now has got %d kernels." %
              (self.layer_num_list[-1]-1, self.total_blocks-1,
               len(self.kernels_ref_list[-1][-1])))

        self._define_end_graph_operations(preserve_transition=True)
        self._initialize_uninitialized_variables()
        self._count_useful_trainable_params()

    def _new_layer(self):
        """
        Add a new layer at the end of the current last block.
        In DenseNet-BC mode, two layers (bottleneck and compression) will be
        added instead of just one.
        """
        # safely access the current block's variable scope
        with tf.variable_scope(self.current_block,
                               auxiliary_name_scope=False) as cblock_scope:
            with tf.name_scope(cblock_scope.original_name_scope):
                # Add the layer and save the new relevant inputs and outputs
                self.input_lt_lay = self.output
                self.output, self.input_lt_cnv = self.add_internal_layer(
                    self.input_lt_lay, self.layer_num_list[-1],
                    self.growth_rate)
        self.layer_num_list[-1] += 1

        # Refresh the cross-entropy list if not measuring layer cross-entropies
        if not self.ft_cross_entropies:
            self.cross_entropy = []

        if not self.bc_mode:
            print("ADDED A NEW LAYER to the last block (#%d)! "
                  "It now has got %d layers." %
                  (self.total_blocks-1, self.layer_num_list[-1]))
        if self.bc_mode:
            print("ADDED A NEW PAIR OF LAYERS to the last block (#%d)! "
                  "It now has got %d bottleneck and composite layers." %
                  (self.total_blocks-1, self.layer_num_list[-1]))

        self.update_paths()
        self._define_end_graph_operations(preserve_transition=True)
        self._initialize_uninitialized_variables()
        self._count_useful_trainable_params()

    def _new_block(self):
        """
        Add a transition layer, and a new block (with one layer) at the end
        of the current last block.
        In DenseNet-BC mode, the new module will begin with two layers
        (bottleneck and compression) instead of just one.
        """
        # The input of the last block is useful if the block must be ditched
        self.input_lt_blc = self.transition_layer(
            self.output, self.total_blocks-1)
        # The inputs of the last layer and conv are for kernel-wise self-constr
        self.output, self.input_lt_lay, self.input_lt_cnv = self.add_block(
            self.input_lt_blc, self.total_blocks, self.growth_rate, 1, True)
        self.layer_num_list.append(1)
        self.total_blocks += 1

        print("ADDED A NEW BLOCK (#%d), "
              "The number of layers in each block is now:" %
              (self.total_blocks-1))
        if not self.bc_mode:
            print('\n'.join('Block %d: %d composite layers.' % (
                k, self.layer_num_list[k]) for k in range(len(
                    self.layer_num_list))))
        if self.bc_mode:
            print('\n'.join('Block %d: %d bottleneck layers and %d composite'
                            'layers.' % (k, self.layer_num_list[k],
                                         self.layer_num_list[k])
                            for k in range(len(self.layer_num_list))))

        self.update_paths()
        self._define_end_graph_operations()
        self._initialize_uninitialized_variables()
        self._count_useful_trainable_params()

    def _build_graph(self):
        """
        Builds the graph and defines the operations for:
        cross-entropy (also l2_loss and a momentum optimizer),
        training step (minimize momentum optimizer using l2_loss + cross-entr),
        accuracy (reduce mean).
        """
        growth_rate = self.growth_rate
        layers_in_each_block = self.layer_num_list
        self.output = self.images

        # first add a 3x3 convolution layer with first_output_features outputs
        with tf.variable_scope("Initial_convolution"):
            self.input_lt_blc, filter_ref = self.conv2d(
                self.output, out_features=self.first_output_features,
                kernel_size=3)
            if self.ft_filters or self.should_self_construct:
                self.filter_ref_list = [[filter_ref]]
                self.kernels_ref_list = []

        # then add the required blocks (and save the relevant inputs)
        for block in range(self.total_blocks):
            self.output, self.input_lt_lay, self.input_lt_cnv = self.add_block(
                self.input_lt_blc, block, growth_rate,
                layers_in_each_block[block], block == self.total_blocks - 1)
            #  all blocks except the last have transition layers
            if block != self.total_blocks - 1:
                self.input_lt_blc = self.transition_layer(self.output, block)

        self._define_end_graph_operations()

    # -------------------------------------------------------------------------
    # ------------------ INITIALIZING THE TENSORFLOW SESSION ------------------
    # -------------------------------------------------------------------------

    def _initialize_uninitialized_variables(self):
        """
        Finds the references to all uninitialized variables, then tells
        TensorFlow to initialize these variables.
        """
        # get a set with all the names of uninitialized variables
        uninit_varnames = list(map(str, self.sess.run(
            tf.report_uninitialized_variables())))
        uninit_vars = []
        # for every variable, check if its name is in the uninitialized set
        for var in tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
            varname = 'b\'' + var.name.split(':')[0] + '\''
            if varname in uninit_varnames:
                uninit_vars.append(var)
        # initialize all the new variables
        self.sess.run(tf.variables_initializer(uninit_vars))

    def _initialize_all_variables(self):
        """
        Tells TensorFlow to initialize all variables, using the proper method
        for the TensorFlow version.
        """
        if TF_VERSION[0] >= 0 and TF_VERSION[1] >= 10:
            self.sess.run(tf.global_variables_initializer())
        else:
            self.sess.run(tf.initialize_all_variables())

    def _initialize_session(self):
        """
        Starts a TensorFlow session with the correct configuration.
        Then tells TensorFlow to initialize all variables, create a saver
        and a log file writer.
        """
        config = tf.ConfigProto()

        # specify the CPU inter and intra threads used by MKL
        config.intra_op_parallelism_threads = self.num_intra_threads
        config.inter_op_parallelism_threads = self.num_inter_threads

        # restrict model GPU memory utilization to the minimum required
        config.gpu_options.allow_growth = True
        self.sess = tf.Session(config=config)

        # initialize variables, create saver, create log file writers
        self._initialize_all_variables()
        self.saver = tf.train.Saver()
        if self.should_save_logs:
            if TF_VERSION[0] >= 0 and TF_VERSION[1] >= 10:
                logswriter = tf.summary.FileWriter
            else:
                logswriter = tf.train.SummaryWriter
            self.summary_writer = logswriter(self.logs_path)
        if self.should_save_ft_logs:
            self.feature_writer = open('./%s.csv' % self.ft_logs_path, "w")

    # -------------------------------------------------------------------------
    # ------------------- COUNTING ALL TRAINABLE PARAMETERS -------------------
    # -------------------------------------------------------------------------

    def _count_trainable_params(self):
        """
        Uses TensorFlow commands to count the number of trainable parameters
        in the graph (sum of the multiplied dimensions of each TF variable).
        Then prints the number of parameters.
        """
        total_parameters = 0
        # print("Variable names:")
        for variable in tf.trainable_variables():
            # print(variable.name)
            shape = variable.get_shape()
            variable_parameters = 1
            for dim in shape:
                variable_parameters *= dim.value
            total_parameters += variable_parameters
        print("Total trainable params: %.1fk" % (total_parameters / 1e3))

    def _count_useful_trainable_params(self):
        """
        Uses TensorFlow commands to count the total number of trainable
        parameters in the graph, as well as the number of parameters that are
        currently 'useful'. By 'useful' parameters are meant the multiplied
        dimensions of each TF variable that is not a discarded transition to
        classes or batch normalization.
        The method prints not only the number of parameters, but also the
        number of parameters in the convolutional and fully connected parts
        of the TensorFlow graph.
        """
        # TODO: Reflect the fact that we now will keep the batchnorm and FC
        # within the same block (as they still apply to the same outputs).
        total_parameters = 0
        useful_conv_params = 0
        useful_fc_params = 0
        fc_name = 'FC_'
        t2fc_name = 'Transition_to_FC_'
        true_fc_name = 'FC_block_%d_' % (self.total_blocks-1)
        true_t2fc_name = 'Transition_to_FC_block_%d/BatchNorm%d' % (
            self.total_blocks-1, self.features_total)

        # print("Variable names:")
        for variable in tf.trainable_variables():
            # print(variable.name)
            shape = variable.get_shape()
            variable_parameters = 1
            for dim in shape:
                variable_parameters *= dim.value
            # Add all identified parameters to total_parameters.
            total_parameters += variable_parameters
            # Add params from the current FC layer to useful_fc_params.
            if variable.name.startswith(true_fc_name):
                useful_fc_params += variable_parameters
            # Add params from the current batchnorm to useful_conv_params.
            elif variable.name.startswith(true_t2fc_name):
                useful_conv_params += variable_parameters
            # Add params not in a rejected batchnorm or FC layer (to conv).
            elif (not variable.name.startswith(fc_name) and
                  not variable.name.startswith(t2fc_name)):
                useful_conv_params += variable_parameters
        # Add the two useful parameters counts together.
        total_useful_parameters = useful_conv_params + useful_fc_params

        print("Total trainable params: %.1fk" % (total_parameters / 1e3))
        print("Total useful params: %.1fk" % (total_useful_parameters / 1e3))
        print("\tConvolutional: %.1fk" % (useful_conv_params / 1e3))
        print("\tFully Connected: %.1fk" % (useful_fc_params / 1e3))

    def get_useful_variables(self):
        """
        Get a list of the trainable variables in the graph that are currently
        'useful' (all variables except those in discarded transitions to
        classes or batch normalizations).
        """
        useful_vars = []
        fc_name = 'FC_'
        t2fc_name = 'Transition_to_FC_'
        true_fc_name = 'FC_block_%d_' % (self.total_blocks-1)
        true_t2fc_name = 'Transition_to_FC_block_%d/BatchNorm%d' % (
            self.total_blocks-1, self.features_total)

        for variable in tf.trainable_variables():
            # Add variables from the current FC layer.
            if variable.name.startswith(true_fc_name):
                useful_vars.append(variable)
            # Add variables from the current batchnorm.
            elif variable.name.startswith(true_t2fc_name):
                useful_vars.append(variable)
            # Add variables not in a rejected batchnorm or FC layer.
            elif (not variable.name.startswith(fc_name) and
                  not variable.name.startswith(t2fc_name)):
                useful_vars.append(variable)

        # print("Useful variables:")
        # for var in useful_vars:
        #     print(var.name)

        return useful_vars

    # -------------------------------------------------------------------------
    # -------------------- TRAINING AND TESTING THE MODEL ---------------------
    # -------------------------------------------------------------------------

    def print_pertinent_features(self, loss, accuracy, epoch, validation_set):
        """
        Prints on console the current values of pertinent features.
        The loss and accuracy are those on the validation set if such a set is
        being used, otherwise they are those on the training set.
        If feature logs are being saved, this function saves feature values.
        If images are being saved, it also saves filter features as images.

        Args:
            loss: `list` of `float` (if validation_set == True, else `float`),
                loss (cross_entropy) for this epoch, in some cases (as `list`
                of `float`) contains several loss values, each corresponding to
                each internal layer of the last block;
            accuracy: `float`, accuracy for this epoch;
            epoch: `int`, current training epoch;
            validation_set: `bool`, whether a validation set is used or not.
        """
        # print the current accuracy
        print("Current accuracy = %f" % accuracy)
        if validation_set:
            # print a cross-entropy value for each layer, if calculating them
            if self.ft_cross_entropies:
                print("Cross-entropy per layer in block #%d:" % (
                    self.total_blocks-1))
                for l in range(len(loss)):
                    print("* Layer #%d: cross-entropy = %f" % (l, loss[l]))
            # else print only the current validation cross-entropy
            else:
                print("Current cross-entropy = %f" % loss[-1])
        else:
            print("Current cross-entropy = %f" % loss)

        if self.should_save_ft_logs:
            # save the previously printed feature values
            self.feature_writer.write(("\"Epoch %d\"%s\"%f\"%s" % (
                epoch, self.ftc, accuracy, self.ftc)).replace(".", self.ftd))
            if validation_set:
                for l in range(len(loss)):
                    self.feature_writer.write(("\"%f\"%s" % (loss[l], self.ftc)
                                               ).replace(".", self.ftd))
            else:
                self.feature_writer.write(("\"%f\"%s" % (loss, self.ftc)
                                           ).replace(".", self.ftd))
            self.feature_writer.write('\"\"')

        if self.ft_filters:
            # process filters, sometimes save their state as images
            print('-' * 40 + "\nProcessing filters:")
            print('\n* Global input data (post-processed):')
            for b in range(0, self.total_blocks):
                cs, lcs_dst, lcs_src = self.process_block_filters(b, epoch)
                self.ft_log_filters(b, cs, lcs_dst, lcs_src)

        print('-' * 40)
        if self.should_save_ft_logs:
            self.feature_writer.write('\n')

    # SELF-CONSTRUCTING ALGORITHM VARIANTS ------------------------------------
    # -------------------------------------------------------------------------

    def self_constructing_var0(self, epoch):
        """
        A step of the self-constructing algorithm (variant #0) for one
        training epoch.
        Adds new layers to the last block depending on parameters.
        Returns True if training should continue, False otherwise.

        This algorithm consists in a succession of two stages:
        - Ascension: add one layer every asc_thresh training epochs, break the
          loop when a layer settles (its layer cs for sources is == 1).
        - Improvement: end the stage when a total of max_n_ep epochs have
          elapsed (since the addition of the last block).

        Args:
            epoch: `int`, current training epoch (since adding the last block).
        """
        continue_training = True
        cs, lcs_dst, lcs_src = self.process_block_filters(
            self.total_blocks-1, epoch)

        # calculate number of settled layers (layers with lcs_src == 1)
        settled_layers = 0
        for src in range(1, len(lcs_src)):
            if lcs_src[src] >= 1:
                settled_layers += 1

        # stage #0 = ascension stage
        if self.algorithm_stage == 0:
            if settled_layers > 0:
                self.settled_layers_ceil = settled_layers
                self.algorithm_stage += 1
            elif (epoch-1) % self.asc_thresh == 0:
                self._new_layer()

        # stage #1 = improvement stage
        if self.algorithm_stage == 1:
            if epoch >= self.max_n_ep:
                # stop algorithm and reset everything
                continue_training = False
                self.algorithm_stage = 0

        return continue_training

    def self_constructing_var1(self, epoch):
        """
        A step of the self-constructing algorithm (variant #1) for one
        training epoch.
        Adds new layers to the last block depending on parameters.
        Returns True if training should continue, False otherwise.

        This algorithm consists in a succession of two stages:
        - Ascension: add one layer every asc_thresh training epochs, break the
          loop when a layer settles (its layer cs for sources is == 1).
        - Improvement: end the stage when a total of max_n_ep epochs have
          elapsed (since the addition of the last block),
          if another layer settles, add a layer and restart the countdown.

        Args:
            epoch: `int`, current training epoch (since adding the last block).
        """
        continue_training = True
        cs, lcs_dst, lcs_src = self.process_block_filters(
            self.total_blocks-1, epoch)

        # calculate number of settled layers (layers with lcs_src == 1)
        settled_layers = 0
        for src in range(1, len(lcs_src)):
            if lcs_src[src] >= 1:
                settled_layers += 1

        # stage #0 = ascension stage
        if self.algorithm_stage == 0:
            if settled_layers > 0:
                self.settled_layers_ceil = settled_layers
                self.algorithm_stage += 1
            elif (epoch-1) % self.asc_thresh == 0:
                self._new_layer()

        # stage #1 = improvement stage
        if self.algorithm_stage == 1:
            if epoch >= self.max_n_ep:
                # stop algorithm and reset everything
                continue_training = False
                self.algorithm_stage = 0
            elif settled_layers > self.settled_layers_ceil:
                self.settled_layers_ceil = settled_layers
                self._new_layer()

        return continue_training

    def self_constructing_var2(self, epoch):
        """
        A step of the self-constructing algorithm (variant #2) for one
        training epoch.
        Adds new layers to the last block depending on parameters.
        Returns True if training should continue, False otherwise.

        This algorithm consists in a succession of two stages:
        - Ascension: add one layer every asc_thresh training epochs, break the
          loop when a layer settles (its layer cs for sources is == 1).
        - Improvement: countdown of patience_param epochs until the stage ends,
          if another layer settles, add a layer and restart the countdown.

        Args:
            epoch: `int`, current training epoch (since adding the last block).
        """
        continue_training = True
        cs, lcs_dst, lcs_src = self.process_block_filters(
            self.total_blocks-1, epoch)

        # calculate number of settled layers (layers with lcs_src == 1)
        settled_layers = 0
        for src in range(1, len(lcs_src)):
            if lcs_src[src] >= 1:
                settled_layers += 1

        # stage #0 = ascension stage
        if self.algorithm_stage == 0:
            if settled_layers > 0 and self.layer_num_list[-1] > 2:
                self.settled_layers_ceil = settled_layers
                self.algorithm_stage += 1
                # max_n_ep is used to estimate completion time.
                self.max_n_ep = epoch + self.patience_param
            elif (epoch-1) % self.asc_thresh == 0:
                self._new_layer()

        # stage #1 = improvement stage
        if self.algorithm_stage == 1:
            if self.patience_cntdwn <= 0:
                # stop algorithm and reset everything
                continue_training = False
                self.algorithm_stage = 0
                self.patience_cntdwn = self.patience_param
            elif settled_layers > self.settled_layers_ceil:
                # if a layer settles, add a layer and restart the countdown
                self.settled_layers_ceil = settled_layers
                self._new_layer()
                self.patience_cntdwn = self.patience_param
                # max_n_ep is used to estimate completion time.
                self.max_n_ep = epoch + self.patience_param
            else:
                self.patience_cntdwn -= 1

        return continue_training

    def self_constructing_var3(self, epoch):
        """
        A step of the self-constructing algorithm (variant #3) for one
        training epoch.
        Adds new layers to the last block depending on parameters.
        Returns True if training should continue, False otherwise.

        This algorithm consists in a succession of two stages:
        - Ascension: add one layer every asc_thresh training epochs, the loop
          can only be broken externally (i.e. through accuracy std tolerance).
        - Improvement: countdown of patience_param epochs until the stage ends,
          if another layer settles, add a layer and restart the countdown.

        Args:
            epoch: `int`, current training epoch (since adding the last block).
        """
        continue_training = True
        cs, lcs_dst, lcs_src = self.process_block_filters(
            self.total_blocks-1, epoch)

        # calculate number of settled layers (layers with lcs_src == 1)
        settled_layers = 0
        for src in range(1, len(lcs_src)):
            if lcs_src[src] >= 1:
                settled_layers += 1

        # stage #0 = ascension stage
        if self.algorithm_stage == 0:
            if (epoch-1) % self.asc_thresh == 0:
                self._new_layer()

        # stage #1 = improvement stage
        if self.algorithm_stage == 1:
            if self.patience_cntdwn <= 0:
                # stop algorithm and reset everything
                continue_training = False
                self.algorithm_stage = 0
                self.patience_cntdwn = self.patience_param
            elif settled_layers > self.settled_layers_ceil:
                # if a layer settles, add a layer and restart the countdown
                self.settled_layers_ceil = settled_layers
                self._new_layer()
                self.patience_cntdwn = self.patience_param
                # max_n_ep is used to estimate completion time.
                self.max_n_ep = epoch + self.patience_param
            else:
                self.patience_cntdwn -= 1

        return continue_training

    def self_constructing_var_test(self, epoch):
        """
        A step of the self-constructing algorithm (var_test) for one
        training epoch.
        THIS IS AN EXPERIMENTAL VARIANT, ONLY TO TEST KERNEL-WISE
        IMPLEMENTATION FUNCTIONS.

        Args:
            epoch: `int`, current training epoch (since adding the last block).
        """
        continue_training = True
        cs, lcs_dst, lcs_src = self.process_block_filters(
            self.total_blocks-1, epoch)

        # calculate number of settled layers (layers with lcs_src == 1)
        settled_layers = 0
        for src in range(1, len(lcs_src)):
            if lcs_src[src] >= 1:
                settled_layers += 1

        # stage #0 = ascension stage
        if self.algorithm_stage == 0:
            if (epoch-1) % self.asc_thresh == 0:
                self._new_layer()
            elif (epoch-1) % int(2) == 0:
                self._new_kernels_to_last_layer()

        # stage #1 = improvement stage
        if self.algorithm_stage == 1:
            if epoch >= self.max_n_ep:
                # stop algorithm and reset everything
                continue_training = False
                self.algorithm_stage = 0

        return continue_training

    # LEARNING RATE REDUCTION VARIANTS (FOR SELF CONSTRUCTING) ----------------
    # -------------------------------------------------------------------------

    def self_constr_rlr0(self, learning_rate, initial_lr, rlr_1, rlr_2):
        """
        An optional learning rate reduction (Reduce LR #0) to be performed
        after a step of the self-constructing algorithm (based on the patience
        countdown, so it only works with variant #2 onwards).
        Returns the new learning rate value.

        Whenever the countdown reaches an epoch that corresponds to a given
        fraction of the patience parameter (the patience_param multiplied by
        1-rlr_1 or 1-rlr_2), the current learning rate is divided by 10.
        If at any point the countdown is reset, the current learning rate
        returns to its initial value.

        Args:
            learning_rate: `int`, the current learning rate value.
            initial_lr: the initial value for the learning rate.
            rlr_1: the fraction of epochs through the countdown at which
                the learning rate must be reduced (/10) for the first time.
            rlr_2: the fraction of epochs through the countdown at which
                the learning rate must be reduced (/10) for the second time.
        """
        if (self.patience_cntdwn == int(self.patience_param * (1-rlr_1))):
            learning_rate = learning_rate / 10
        elif (self.patience_cntdwn == int(self.patience_param * (1-rlr_2))):
            learning_rate = learning_rate / 10
        elif (self.patience_cntdwn == self.patience_param):
            learning_rate = initial_lr
        return learning_rate

    def self_constr_rlr1(self, learning_rate, initial_lr, rlr_1, rlr_2):
        """
        An optional learning rate reduction (Reduce LR #1) to be performed
        after a step of the self-constructing algorithm (based on the patience
        countdown, so it only works with variant #2 onwards).
        Returns the new learning rate value.

        The initial learning rate value is initial_lr.
        The first time that the countdown reaches an epoch that corresponds to
        patience_param * (1 - rlr_1), the learning rate becomes initial_lr/10.
        The first time that the countdown reaches an epoch that corresponds to
        patience_param * (1 - rlr_2), the learning rate becomes initial_lr/100.

        Args:
            learning_rate: `int`, the current learning rate value.
            initial_lr: the initial value for the learning rate.
            rlr_1: the fraction of epochs through the countdown at which
                the learning rate must be reduced (/10) for the first time.
            rlr_2: the fraction of epochs through the countdown at which
                the learning rate must be reduced (/10) for the second time.
        """
        if (self.patience_cntdwn == int(self.patience_param * (1-rlr_1))):
            learning_rate = min(learning_rate, initial_lr / 10)
        elif (self.patience_cntdwn == int(self.patience_param * (1-rlr_2))):
            # learning_rate = min(learning_rate, initial_lr / 100)
            learning_rate = initial_lr / 100  # min is unnecessary here
        return learning_rate

    # MAIN TRAINING AND TESTING FUNCTIONS -------------------------------------
    # -------------------------------------------------------------------------

    def train_one_epoch(self, data, batch_size, learning_rate):
        """
        Trains the model for one epoch using data from the proper training set.

        Args:
            data: training data yielded by the dataset's data provider;
            batch_size: `int`, number of examples in a training batch;
            learning_rate: `int`, learning rate for the optimizer.
        """
        num_examples = data.num_examples
        total_loss = []
        total_accuracy = []

        # save each training batch's loss and accuracy
        for i in range(num_examples // batch_size):
            batch = data.next_batch(batch_size)
            images, labels = batch
            feed_dict = {
                self.images: images,
                self.labels: labels,
                self.learning_rate: learning_rate,
                self.is_training: True,
            }
            fetches = [self.train_step, self.cross_entropy[-1], self.accuracy]
            result = self.sess.run(fetches, feed_dict=feed_dict)
            _, loss, accuracy = result
            total_loss.append(loss)
            total_accuracy.append(accuracy)
            if self.should_save_logs:
                self.batches_step += 1
                self.log_loss_accuracy(
                    loss, accuracy, self.batches_step, prefix='per_batch',
                    should_print=False)

        # use the saved data to calculate the mean loss and accuracy
        mean_loss = np.mean(total_loss)
        mean_accuracy = np.mean(total_accuracy)
        return mean_loss, mean_accuracy

    def test(self, data, batch_size):
        """
        Tests the model using the proper testing set.

        Args:
            data: testing data yielded by the dataset's data provider;
            batch_size: `int`, number of examples in a testing batch.
        """
        num_examples = data.num_examples
        total_loss = []
        for l in range(len(self.cross_entropy)):
            total_loss.append([])
        total_accuracy = []

        # save each testing batch's loss and accuracy
        for i in range(num_examples // batch_size):
            batch = data.next_batch(batch_size)
            feed_dict = {
                self.images: batch[0],
                self.labels: batch[1],
                self.is_training: False,
            }
            loss = self.sess.run(self.cross_entropy, feed_dict=feed_dict)
            accuracy = self.sess.run(self.accuracy, feed_dict=feed_dict)
            for j in range(len(loss)):
                total_loss[j].append(loss[j])
            total_accuracy.append(accuracy)

        # use the saved data to calculate the mean loss and accuracy
        mean_loss = []
        for loss_list in total_loss:
            mean_loss.append(np.mean(loss_list))
        mean_accuracy = np.mean(total_accuracy)
        return mean_loss, mean_accuracy

    def train_all_epochs(self, train_params):
        """
        Trains the model for a certain number of epochs, using parameters
        specified in the train_params argument.

        Args (in train_params):
            batch_size: `int`, number of examples in a training batch;
            max_n_ep: `int`, maximum number of training epochs to run;
            initial_learning_rate: `int`, initial learning rate for optimizer;
            reduce_lr_1: `float`, if not self-constructing the network,
                first fraction of max_n_ep after which the current
                learning rate is divided by 10 (initial_learning_rate/10);
            reduce_lr_2: `float`, if not self-constructing the network,
                second fraction of max_n_ep after which the current
                learning rate is divided by 10 (initial_learning_rate/100);
            validation_set: `bool`, should a validation set be used or not;
            validation_split: `float` or None;
                `float`: chunk of the training set used as the validation set;
                None: use the testing set as the validation set;
            shuffle: `str` or None, or `bool`;
                `str` or None: used with CIFAR datasets, should we shuffle the
                    data only before training ('once_prior_train'), on every
                    epoch ('every_epoch') or not at all (None);
                `bool`: used with SVHN, should we shuffle the data or not;
            normalisation: `str` or None;
                None: don't use any normalisation for pixels;
                'divide_255': divide all pixels by 255;
                'divide_256': divide all pixels by 256;
                'by_chanels': substract the mean of the pixel's chanel and
                    divide the result by the channel's standard deviation.
        """
        self.max_n_ep = train_params['max_n_ep']
        initial_lr = train_params['initial_learning_rate']
        learning_rate = train_params['initial_learning_rate']
        batch_size = train_params['batch_size']
        rlr_1 = train_params['reduce_lr_1']
        rlr_2 = train_params['reduce_lr_2']
        validation_set = train_params.get('validation_set', False)
        total_start_time = time.time()

        epoch = 1         # current training epoch
        epoch_last_b = 0  # epoch at which the last block was added
        while True:
            # only print epoch name on certain epochs
            if (epoch-1) % self.ft_period == 0:
                print('\n', '-'*30, "Train epoch: %d" % epoch, '-'*30, '\n')
            start_time = time.time()

            # if not self-constructing, may reduce learning rate at some epochs
            if not self.should_self_construct and self.should_change_lr:
                if (epoch == int(self.max_n_ep * rlr_1)
                    ) or (epoch ==
                          int(self.max_n_ep * rlr_2)):
                    learning_rate = learning_rate / 10
                    print("Learning rate has been divided by 10, new lr = %f" %
                          learning_rate)

            # training step for one epoch
            print("Training...", end=' ')
            loss, acc = self.train_one_epoch(
                self.data_provider.train, batch_size, learning_rate)
            # save logs
            if self.should_save_logs:
                self.log_loss_accuracy(loss, acc, epoch, prefix='train')

            # validation step after the epoch
            if validation_set:
                print("Validation...")
                loss, acc = self.test(
                    self.data_provider.validation, batch_size)
                # save logs
                if self.should_save_logs:
                    self.log_loss_accuracy(loss[-1], acc, epoch,
                                           prefix='valid')

            # save feature logs (on certain epochs)
            if (epoch-1) % self.ft_period == 0:
                self.print_pertinent_features(loss, acc, epoch, validation_set)

            # save model if required
            if self.should_save_model:
                self.save_model()

            # step of the self-constructing algorithm
            if self.should_self_construct:
                if epoch - epoch_last_b != 1:
                    # if the accuracy doesn't change much, ends the ascension.
                    if self.algorithm_stage == 0:
                        # add the current accuracy to the FIFO list.
                        self.acc_FIFO.append(acc)
                        if len(self.acc_FIFO) == self.std_window:
                            if np.std(self.acc_FIFO) < self.std_tolerance:
                                self.algorithm_stage += 1
                                if self.sc_var != 0 and self.sc_var != 1:
                                    # max_n_ep estimates completion time.
                                    self.max_n_ep = epoch + self.patience_param

                    # can break here if self-constructing algorithm is over
                    if not self.self_constructing_step(epoch - epoch_last_b):
                        # add another block if block_count not yet exceeded
                        if self.total_blocks < self.block_count:
                            self._new_block()
                        else:
                            break

                    # optional learning rate reduction for self-constructing
                    if self.should_change_lr:
                        learning_rate = self.self_constr_rlr(learning_rate,
                                                             initial_lr,
                                                             rlr_1, rlr_2)
                # if this is a new block, reset the algorithm's variables
                else:
                    self.settled_layers_ceil = 0  # highest num of settled lay
                    self.algorithm_stage = 0  # start with ascension stage
                    self.patience_cntdwn = self.patience_param

            # measure training time for this epoch
            time_per_epoch = time.time() - start_time
            seconds_left = int((self.max_n_ep - epoch) * time_per_epoch)
            print("Time per epoch: %s, Est. complete (%d epochs) in: %s" % (
                str(timedelta(seconds=time_per_epoch)),
                self.max_n_ep,
                str(timedelta(seconds=seconds_left))))

            # increase epoch, break at max_n_ep if not self-constructing
            epoch += 1
            if not self.should_self_construct and epoch >= self.max_n_ep+1:
                break

        # measure total training time
        total_training_time = time.time() - total_start_time
        print("\nTOTAL TRAINING TIME: %s\n" % str(timedelta(
            seconds=total_training_time)))
        if self.should_save_ft_logs:
            self.feature_writer.write("\nTOTAL TRAINING TIME: %s\n" % str(
                timedelta(seconds=total_training_time)))
        self._count_useful_trainable_params()
