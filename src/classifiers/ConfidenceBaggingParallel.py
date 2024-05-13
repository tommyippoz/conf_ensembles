import copy
import multiprocessing
import random
from multiprocessing import Process

import numpy
from sklearn.utils.multiclass import unique_labels
from sklearn.utils.validation import check_is_fitted, check_array

from src.classifiers.Classifier import Classifier
from src.metrics.EnsembleMetric import get_default


class ConfidenceBagging(Classifier):
    """
    Class for creating bagging ensembles
    """

    def __init__(self, clf, n_base: int = 10, max_features: float = 0.7, sampling_ratio: float = 0.7,
                 perc_decisors: float = None, n_decisors: int = None, weighted: bool = False):
        """
        Constructor
        :param clf: the algorithm to be used for creating base learners
        :param n_base: number of base learners (= size of the ensemble)
        :param max_features: percentage of features to be used at each iteration
        :param sampling_ratio: percentage of the dataset to be used at each iteration
        :param perc_decisors: percentage of base learners to be used for prediction
        :param n_decisors: number of base learners to be used for prediction
        :param weighted: True if prediction has to be computed as a weighted sum of probabilities
        """
        super().__init__(clf)
        self.weighted = weighted
        if n_base > 1:
            self.n_base = n_base
        else:
            print("Ensembles have to be at least 2")
            self.n_base = 10

        self.max_features = max_features if max_features is not None and 0 < max_features <= 1 else 0.7

        self.sampling_ratio = sampling_ratio if sampling_ratio is not None and 0 < sampling_ratio <= 1 else 0.7

        if perc_decisors is not None and 0 < perc_decisors <= 1:
            if n_decisors is not None and 0 < n_decisors <= self.n_base:
                print('Both perc_decisors and n_decisors are specified, prioritizing perc_decisors')
            self.n_decisors = int(self.n_base*perc_decisors) if int(self.n_base*perc_decisors) > 0 else 1
        elif n_decisors is not None and 0 < n_decisors <= self.n_base:
            self.n_decisors = n_decisors
        else:
            self.n_decisors = 1 + int(self.n_base / 2)
        self.estimators_ = []
        self.feature_sets = []

    def fit_base(self, X, y, n_samples, f_bag, f_list, c_list, lock):
        # Draw samples
        features = random.sample(range(X.shape[1]), f_bag)
        features.sort()
        sample_x, sample_y = self.draw_samples(X, y, n_samples)
        sample_x = sample_x[:, features]
        if len(features) == 1:
            sample_x = sample_x.reshape(-1, 1)

        # Train learner
        learner = copy.deepcopy(self.clf)
        learner.fit(sample_x, sample_y)
        if hasattr(learner, "X_"):
            learner.X_ = None
        if hasattr(learner, "y_"):
            learner.y_ = None

        # Test Learner
        with lock:
            f_list.append(features)
            c_list.append(learner)

    def fit(self, X, y=None):
        train_n = len(X)
        self.classes_ = unique_labels(y) if y is not None else [0, 1]
        bag_features_n = int(X.shape[1]*self.max_features)
        samples_n = int(train_n * self.sampling_ratio)

        # Parallel Training
        f_list = multiprocessing.Manager().list()
        c_list = multiprocessing.Manager().list()
        lock = multiprocessing.Lock()
        train_tasks = [Process(target=self.fit_base,
                               kwargs={'X': X, 'y': y, 'n_samples': samples_n, 'f_bag': bag_features_n,
                                       'f_list': f_list, 'c_list': c_list, 'lock': lock})
                       for _ in range(0, self.n_base)]
        for running_task in train_tasks:
            running_task.start()
        for running_task in train_tasks:
            running_task.join()

        self.feature_sets = [x for x in f_list]
        self.estimators_ = [x for x in c_list]

        # Compliance with SKLEARN and PYOD
        self.X_ = X[[0, 1], :]
        self.y_ = y
        self.feature_importances_ = self.compute_feature_importances()

    def draw_samples(self, X, y, samples_n):
        indexes = numpy.random.choice(X.shape[0], samples_n, replace=False, p=None)
        sample_x = numpy.asarray(X[indexes, :])
        # If data is labeled we also have to refine labels
        if y is not None and hasattr(self, 'classes_') and self.classes_ is not None and len(self.classes_) > 1:
            sample_y = y[indexes]
            sample_labels = unique_labels(sample_y)
            missing_labels = [item for item in self.classes_ if item not in sample_labels]
            # And make sure that there is at least a sample for each class of the problem
            if missing_labels is not None and len(missing_labels) > 0:
                # For each missing class
                for missing_class in missing_labels:
                    miss_class_indexes = numpy.asarray(numpy.where(y == missing_class)[0])
                    new_sampled_index = numpy.random.choice(miss_class_indexes, None, replace=False, p=None)
                    X_missing_class = X[new_sampled_index, :]
                    sample_x = numpy.append(sample_x, [X_missing_class], axis=0)
                    sample_y = numpy.append(sample_y, missing_class)
        else:
            sample_y = None
        return sample_x, sample_y

    def predict_proba(self, X):
        # Scoring probabilities, ends with a
        proba_array = []
        conf_array = []
        for i in range(0, self.n_base):
            predictions = self.estimators_[i].predict_proba(X[:, self.feature_sets[i]])
            proba_array.append(predictions)
            conf_array.append(numpy.max(predictions, axis=1))
        # 3d matrix (clf, row, probability for class)
        proba_array = numpy.asarray(proba_array)
        # 2dim matrix (clf, confidence for row)
        conf_array = numpy.asarray(conf_array)

        # Choosing the most confident self.n_decisors to compute final probabilities
        proba = numpy.zeros(proba_array[0].shape)
        if self.weighted:
            for i in range(0, X.shape[0]):
                proba[i] = numpy.sum(proba_array[:, i, :].T * conf_array[:, i], axis=1) / numpy.sum(conf_array[:, i])
        else:
            conf_array = conf_array.transpose()
            all_conf = -numpy.sort(-conf_array, axis=1)
            conf_thrs = all_conf[:, self.n_decisors-1]
            for i in range(0, X.shape[0]):
                proba[i] = numpy.average(proba_array[numpy.where(conf_array[i] >= conf_thrs[i]), i, :], axis=1)

        # Final averaged Result
        return proba

    def get_diversity(self, X, y, metrics=None):
        """
        Returns diversity metrics. Works only with ensembles.
        :param metrics: name of the metrics to output (list of Metric objects)
        :param X: test set
        :param y: labels of the test set
        :return: diversity metrics
        """
        X = check_array(X)
        predictions = []
        check_is_fitted(self)
        if hasattr(self, "estimators_"):
            # If it is an ensemble and if it is trained
            for i in range(0, self.n_base):
                predictions.append(self.estimators_[i].predict(X[:, self.feature_sets[i]]))
            predictions = numpy.column_stack(predictions)

        if predictions is not None and len(predictions) > 0:
            # Compute metrics
            metric_scores = {}
            if metrics is None or not isinstance(metrics, list):
                metrics = get_default()
            for metric in metrics:
                metric_scores[metric.get_name()] = metric.compute_diversity(predictions, y)
            return metric_scores
        else:
            # If it is not an ensemble
            return {}

    def classifier_name(self):
        """
        Gets classifier name as string
        :return: the classifier name
        """
        clf_name = self.clf.classifier_name() if isinstance(self.clf, Classifier) else self.clf.__class__.__name__
        if clf_name == 'Pipeline':
            keys = list(self.clf.named_steps.keys())
            clf_name = str(keys) if len(keys) != 2 else str(keys[1]).upper()
        if self.weighted:
            return "ConfidenceBaggerWeighted(" + str(clf_name) + "-" + \
                   str(self.n_base) + "-" + str(self.n_decisors) + "-" + \
                   str(self.max_features) + "-" + str(self.sampling_ratio) + ")"
        else:
            return "ConfidenceBagger(" + str(clf_name) + "-" + \
               str(self.n_base) + "-" + str(self.n_decisors) + "-" + \
               str(self.max_features) + "-" + str(self.sampling_ratio) + ")"
