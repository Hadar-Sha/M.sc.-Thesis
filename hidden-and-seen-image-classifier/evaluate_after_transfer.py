"""Evaluates the model"""

import argparse
import logging
import os

import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
import utils
import model.net as net
import model.two_labels_data_loader as data_loader

parser = argparse.ArgumentParser()
parser.add_argument('--parent_dir', default=os.path.expanduser(os.environ['USERPROFILE']),
                    help='path to experiments and data folder')
parser.add_argument('--data_dir', default='data', help="Directory containing the dataset")
parser.add_argument('--model_dir', default='experiments', help="Directory containing params.json")
parser.add_argument('--restore_file', default='best', help="name of the file in --model_dir \
                     containing weights to load")


def load_model(model_dir, restore_file, model):
    # reload weights from restore_file if specified
    if restore_file is not None and model_dir is not None:
        restore_path = os.path.join(model_dir, restore_file + '.pth.tar')
        logging.info("Restoring parameters from {}".format(restore_path))
        utils.load_checkpoint(restore_path, model, None)  # optimizer)
    return


def evaluate_after_transfer(model, loss_fn, dataloader, metrics, incorrect, params):  #, epoch):
    """Evaluate the model on `num_steps` batches.

    Args:
        model: (torch.nn.Module) the neural network
        loss_fn: a function that takes batch_output and batch_labels and computes the loss for the batch
        dataloader: (DataLoader) a torch.utils.data.DataLoader object that fetches data
        metrics: (dict) a dictionary of functions that compute a metric using the output and labels of each batch
        incorrect: a function that save all samples with incorrect classification
        params: (Params) hyperparameters
        num_steps: (int) number of batches to train on, each of size params.batch_size
        epoch:
    """

    # set model to evaluation mode
    model.eval()

    # summary for current eval loop
    summ = []
    prop = []

    # incorrect samples of current loop
    incorrect_samples = []

    # compute metrics over the dataset
    for data_batch, labels_batch in dataloader:

        # move to GPU if available
        if params.cuda:
            data_batch, labels_batch = data_batch.cuda(), labels_batch.cuda()
        # fetch the next evaluation batch
        data_batch, labels_batch = Variable(data_batch), Variable(labels_batch)
        if labels_batch.size(1) == 1:
            labels_batch = labels_batch.view(labels_batch.size(0))
        
        # compute model output
        output_batch = model(data_batch)
        loss = loss_fn(output_batch, labels_batch, params.num_classes)

        # extract data from torch Variable, move to cpu, convert to numpy arrays
        output_batch = output_batch.data.cpu().numpy()
        labels_batch = labels_batch.data.cpu().numpy()

        proportions_batch = labels_batch.shape[0] / params.batch_size
        prop.append(proportions_batch)

        # compute all metrics on this batch
        summary_batch = {metric: metrics[metric](output_batch, labels_batch)*proportions_batch
                         for metric in metrics}
        summary_batch['loss'] = loss.item()
        summ.append(summary_batch)

        # find incorrect samples
        incorrect_batch = incorrect(data_batch, output_batch, labels_batch)
        incorrect_samples.extend(incorrect_batch)

    # compute mean of all metrics in summary
    prop_sum = np.sum(prop)
    metrics_mean = {metric: np.sum([x[metric] for x in summ]/prop_sum) for metric in summ[0]}

    metrics_string = " ; ".join("{}: {:05.3f}".format(k, v) for k, v in metrics_mean.items())
    logging.info("- Eval metrics : " + metrics_string)

    return metrics_mean, incorrect_samples


if __name__ == '__main__':
    """
        Evaluate the model on the test set.
    """
    # Load the parameters
    args = parser.parse_args()
    if args.parent_dir:
        os.chdir(args.parent_dir)
    json_path = os.path.join(args.model_dir, 'params.json')
    assert os.path.isfile(json_path), "No json configuration file found at {}".format(json_path)
    params = utils.Params(json_path)

    # use GPU if available
    params.cuda = torch.cuda.is_available()     # use GPU is available

    # Set the random seed for reproducible experiments
    torch.manual_seed(230)
    if params.cuda: torch.cuda.manual_seed(230)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    # Get the logger
    utils.set_logger(os.path.join(args.model_dir, 'evaluate.log'))

    # Create the input data pipeline
    logging.info("Creating the dataset...")

    # fetch dataloaders
    dataloaders = data_loader.fetch_dataloader(['test'], args.data_dir, params)
    test_dl = dataloaders['test']

    logging.info("- done.")

    # Define the model
    model = net.NeuralNet(params).cuda() if params.cuda else net.NeuralNet(params)

    # changing last fully connected layer
    num_ftrs = model.fc4.in_features
    model.fc4 = nn.Linear(num_ftrs, 20)  # 10)

    model = model.to(device)

    optimizer = torch.optim.SGD(model.parameters(), lr=params.learning_rate)

    loss_fn = net.loss_fn_two_labels
    metrics = net.metrics
    incorrect = net.incorrect_two_labels
    
    logging.info("Starting evaluation")

    # Reload weights from the saved file
    load_model(args.model_dir, args.restore_file, model)

    # Evaluate
    test_metrics, incorrect_samples = evaluate_after_transfer(model, loss_fn, test_dl, metrics, incorrect, params)
    save_path = os.path.join(args.model_dir, "metrics_test_{}.json".format(args.restore_file))
    utils.save_dict_to_json(test_metrics, save_path)

