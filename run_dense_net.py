import argparse
import os

# from models.OLD_dense_net import DenseNet
# from models.dense_net import DenseNet
# from models.NEW_dense_net import DenseNet
from models.NEWER_dense_net import DenseNet
from data_providers.utils import get_data_provider_by_name

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

# Training parameters for CIFAR datasets (10, 100, 10+, 100+).
train_params_cifar = {
    'batch_size': 64,
    'max_n_ep': 300,  # default was 300
    'initial_learning_rate': 0.1,
    'reduce_lr_1': 0.5,  # mult. by max_n_ep, default was 0.5 (150)
    'reduce_lr_2': 0.75,  # mult. by max_n_ep, default was 0.75 (225)
    'validation_set': True,
    'validation_split': 0.1,  # None or float
    'shuffle': 'every_epoch',  # None, once_prior_train, every_epoch
    'normalization': 'by_chanels',  # None, divide_256, divide_255, by_chanels
}

# Training parameters for the StreetView House Numbers dataset.
train_params_svhn = {
    'batch_size': 64,
    'max_n_ep': 300,
    'initial_learning_rate': 0.1,
    'reduce_lr_1': 0.5,  # mult. by max_n_ep, default was 0.5 (20)
    'reduce_lr_2': 0.75,  # mult. by max_n_ep, default was 0.75 (30)
    'validation_set': True,
    'validation_split': 6000,  # you may set it 6000 as in the paper
    'shuffle': True,  # shuffle dataset every epoch or not
    'normalization': 'divide_255',
}


# Get the right parameters for the current dataset.
def get_train_params_by_name(name):
    if name in ['C10', 'C10+', 'C100', 'C100+']:
        return train_params_cifar
    if name == 'SVHN':
        return train_params_svhn


# Parse arguments for the program.
if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    # What actions (train or test) to do with the model.
    parser.add_argument(
        '--train', action='store_true',
        help='Train the model')
    parser.add_argument(
        '--test', action='store_true',
        help='Test model for required dataset if pretrained model exists.'
             'If provided together with `--train` flag testing will be'
             'performed right after training.')

    # Parameters that define the current DenseNet model.
    parser.add_argument(
        '--model_type', '-m', type=str, choices=['DenseNet', 'DenseNet-BC'],
        default='DenseNet',
        help='Choice of model to use (use bottleneck + compression or not).')
    parser.add_argument(
        '--growth_rate', '-k', type=int,
        default=12,  # choices in paper: 12, 24, 40.
        help='Growth rate (number of convolutions in a new dense layer).')
    parser.add_argument(
        '--dataset', '-ds', type=str,
        choices=['C10', 'C10+', 'C100', 'C100+', 'SVHN'],
        default='C10',
        help='Choice of dataset to use.')
    parser.add_argument(
        '--layer_num_list', '-lnl',
        type=str, default='1', metavar='',
        help='List of the (initial) number of layers in each block, separated'
             ' by comas (e.g. \'12,12,12\', default: 1 block with 1 layer)'
             ' WARNING: in BC models, each layer is preceded by a bottleneck.')
    parser.add_argument(
        '--keep_prob', '-kp', type=float, metavar='',
        help='Keeping probability, for dropout')
    parser.add_argument(
        '--weight_decay', '-wd', type=float, default=1e-4, metavar='',
        help='Weight decay, for optimizer (default: %(default)s).')
    parser.add_argument(
        '--nesterov_momentum', '-nm', type=float, default=0.9, metavar='',
        help='Nesterov momentum (default: %(default)s).')
    parser.add_argument(
        '--reduction', '-red', '-theta', type=float, default=0.5, metavar='',
        help='Reduction (theta) at transition layer, for DenseNets-BC models.')

    # What kind of algorithm (self-constructing, training, etc.) to apply.
    parser.add_argument(
        '--self-construct', dest='should_self_construct', action='store_true',
        help='Apply a self-constructing algorithm for modifying'
             ' the network\'s architecture during training.')
    parser.add_argument(
        '--no-self-construct', dest='should_self_construct',
        action='store_false',
        help='Do not apply a self-constructing algorithm, only train.')
    parser.set_defaults(should_self_construct=True)
    parser.add_argument(
        '--change-learning-rate', '--change-lr',
        dest='should_change_lr', action='store_true',
        help='Allow any changes in the learning rate as defined in the'
             ' training algorithm. When not self-constructing, the learning'
             ' rate is divided by 10 at specific epochs, specified in the'
             ' training parameters for the dataset in use.')
    parser.add_argument(
        '--no-change-learning-rate', '--no-change-lr',
        dest='should_change_lr', action='store_false',
        help='Do not allow any changes in the learning rate, regardless of the'
             ' training algorithm.')
    parser.set_defaults(should_change_lr=True)

    # Parameters that define the self-constructing algorithm.
    parser.add_argument(
        '--self_constructing_variant', '--self_constructing_var', '-var',
        dest='self_constructing_var', type=int, default=-1,
        help='Choice on the algorithm variant to use (from oldest to newest).'
             ' Variants are identified by an int value (0, 1, 2, 3).'
             ' They are each described in their respective functions'
             ' (self_constructing_varX). Passing a negative value, or one that'
             ' does not identify a variant (yet), results in running the'
             ' most recent (default) operational variant.')
    parser.add_argument(
        '--self_constructing_reduce_lr', '--self_constructing_rlr',
        '--self_const_reduce_lr', '--self_constr_rlr', '-rlr',
        dest='self_constr_rlr', type=int, default=-1,
        help='Choice on the learning rate reduction variant to be used with'
             ' self constructing (from oldest to newest).'
             ' Variants are identified by an int value (0, 1).'
             ' They are each described in their respective functions'
             ' (self_constr_rlrX). Passing a negative value, or one that'
             ' does not identify a variant (yet), results in running the'
             ' most recent (default) variant.')
    parser.add_argument(
        '--block_count', '--blocks', '-bc',
        dest='block_count', type=int, default=1,
        help='Maximum number of dense blocks to self-construct. Default is 1.'
             ' If the entered value is < 1, it is changed to 1.')
    parser.add_argument(
        '--layer_connection_strength', '--layer_cs', '-lcs', dest='layer_cs',
        type=str, choices=['relevance', 'spread'], default='relevance',
        help='Choice on \'layer CS\', how to interpret connection strength'
             ' (CS) data when evaluating layers in the algorithm.'
             ' Relevance (default) evaluates a given layer\'s connections from'
             ' the perspective of other layers. Spread evaluates them from the'
             ' perspective of that given layer.')
    parser.add_argument(
        '--ascension_threshold', '--asc_thresh', '-at',
        dest='asc_thresh', type=int, default=10,
        help='Ascension threshold, for the self-constructing algorithm:'
             ' number of epochs before adding a new layer during the'
             ' ascension stage, until a layer settles.')
    parser.add_argument(
        '--patience_parameter', '--patience_param', '-pp',
        dest='patience_param', type=int, default=200,
        help='Patience parameter, for the self-constructing algorithm:'
             ' number of epochs to wait before stopping the improvement'
             ' stage, unless a new layer settles.')
    parser.add_argument(
        '--accuracy_std_tolerance', '--std_tolerance', '-stdt',
        dest='std_tolerance', type=int, default=0.1,
        help='Accuracy std tolerance, for the self-constructing algorithm:'
             ' minimum standard deviation value for a window of previous'
             ' accuracy values in the ascension stage. If the std of the'
             ' previous accuracies (std_window) goes below std_tolerance,'
             ' the ascension stage is forcefully terminated.')
    parser.add_argument(
        '--accuracy_std_window', '--std_window', '-stdw',
        dest='std_window', type=int, default=50,
        help='Accuracy std window, for the self-constructing algorithm:'
             ' number of previous accuracy values that are taken into account'
             ' for deciding if the ascension stage should be forcefully'
             ' terminated (this happens when the std of these accuracy'
             ' values is below std_tolerance)')
    parser.add_argument(
        '--expansion_rate', '-kex', type=int,
        default=1,  # by default, convolutions are added one by one
        help='Expansion rate (rate at which new convolutions are added'
             ' together during the self-construction of a dense layer).')

    # Wether or not to write TensorFlow logs.
    parser.add_argument(
        '--logs', dest='should_save_logs', action='store_true',
        help='Write tensorflow logs.')
    parser.add_argument(
        '--no-logs', dest='should_save_logs', action='store_false',
        help='Do not write tensorflow logs.')
    parser.set_defaults(should_save_logs=True)

    # Wether or not to write CSV feature logs.
    parser.add_argument(
        '--feature-logs', '--ft-logs', dest='should_save_ft_logs',
        action='store_true',
        help='Record the evolution of feature values in a CSV log.')
    parser.add_argument(
        '--no-feature-logs', '--no-ft-logs',  dest='should_save_ft_logs',
        action='store_false',
        help='Do not record feature values in a CSV log.')
    parser.set_defaults(should_save_ft_logs=True)
    parser.add_argument(
        '--feature_period', '--ft_period', '-fp',
        dest='ft_period', type=int, default=1,
        help='Number of epochs between each measurement of feature values.')
    parser.add_argument(
        '--ft_comma_separator', '--ft_comma', '-comma',
        dest='ft_comma', type=str, default=';',
        help='Comma (value) separator for the CSV feature log.')
    parser.add_argument(
        '--ft_decimal_separator', '--ft_decimal', '-dec',
        dest='ft_decimal', type=str, default=',',
        help='Decimal separator for the CSV feature log.')

    # Wether or not to calculate certain feature values (saved in ft-logs).
    parser.add_argument(
        '--feature-filters', '--ft-filters',
        dest='ft_filters', action='store_true',
        help='Calculate feature values from convolution filters'
             ' (e.g. the mean and std of a filter\'s kernel weights).')
    parser.add_argument(
        '--no-feature-filters', '--no-ft-filters',
        dest='ft_filters', action='store_false',
        help='Do not calculate feature values from filters.')
    parser.set_defaults(ft_filters=True)
    parser.add_argument(
        '--feature-cross-entropies', '--ft-cross-entropies', '--ft-cr-entr',
        dest='ft_cross_entropies', action='store_true',
        help='Calculate cross-entropy values'
             ' corresponding to all layers in the last block.')
    parser.add_argument(
        '--no-feature-cross-entropies', '--no-ft-cross-entropies',
        '--no-ft-cr-entr', dest='ft_cross_entropies', action='store_false',
        help='Do not calculate cross-entropy values'
             ' (only the real cross-entropy).')
    parser.set_defaults(ft_cross_entropies=False)

    # Wether or not to save the model's state (to load it back in the future).
    parser.add_argument(
        '--saves', dest='should_save_model', action='store_true',
        help='Save model during training.')
    parser.add_argument(
        '--no-saves', dest='should_save_model', action='store_false',
        help='Do not save model during training.')
    parser.set_defaults(should_save_model=True)

    # Wether or not to save image data, such as representations of filters.
    parser.add_argument(
        '--images', dest='should_save_images', action='store_true',
        help='Produce and save image files (e.g. representing filter states).')
    parser.add_argument(
        '--no-images', dest='should_save_images', action='store_false',
        help='Do not produce and save image files.')
    parser.set_defaults(should_save_images=False)

    # Wether or not to renew logs.
    parser.add_argument(
        '--renew-logs', dest='renew_logs', action='store_true',
        help='Erase previous logs for model if they exist.')
    parser.add_argument(
        '--not-renew-logs', dest='renew_logs', action='store_false',
        help='Do not erase previous logs for model if they exist.')
    parser.set_defaults(renew_logs=True)

    # Parameters related to hardware optimisation.
    parser.add_argument(
        '--num_inter_threads', '-inter', type=int, default=1, metavar='',
        help='Number of inter-operation CPU threads '
             '(for paralellizing the inference/testing phase).')
    parser.add_argument(
        '--num_intra_threads', '-intra', type=int, default=128, metavar='',
        help='Number of intra-operation CPU threads '
             ' (for paralellizing the inference/testing phase).')

    args = parser.parse_args()

    # Perform settings depending on the parsed arguments (model params).
    if not args.keep_prob:
        if args.dataset in ['C10', 'C100', 'SVHN']:
            args.keep_prob = 0.8
        else:
            args.keep_prob = 1.0
    if args.model_type == 'DenseNet':
        args.bc_mode = False
        args.reduction = 1.0
    elif args.model_type == 'DenseNet-BC':
        args.bc_mode = True
    if not args.train and not args.test:
        print("\nFATAL ERROR:")
        print("Operation on network (--train and/or --test) not specified!")
        print("You should train or test your network. Please check arguments.")
        exit()

    # Get model params (the arguments) and train params (depend on dataset).
    model_params = vars(args)
    train_params = get_train_params_by_name(args.dataset)
    print("\nModel parameters (specified as arguments):")
    for k, v in model_params.items():
        print("\t%s: %s" % (k, v))
    print("Train parameters (depend on specified dataset):")
    for k, v in train_params.items():
        print("\t%s: %s" % (k, v))

    # Train and/or test the specified model.
    print("\nPrepare training data...")
    data_provider = get_data_provider_by_name(args.dataset, train_params)
    print("Initialize the model...")
    model = DenseNet(data_provider=data_provider, **model_params)
    if args.train:
        print("Data provider train images: ", data_provider.train.num_examples)
        model.train_all_epochs(train_params)
    if args.test:
        if not args.train:
            model.load_model()
        print("Data provider test images: ", data_provider.test.num_examples)
        print("Testing...")
        loss, accuracy = model.test(data_provider.test, batch_size=200)
        model.print_pertinent_features(loss, accuracy, -1, True)
        print("mean cross_entropy: %f, mean accuracy: %f" % (
            loss[-1], accuracy))
