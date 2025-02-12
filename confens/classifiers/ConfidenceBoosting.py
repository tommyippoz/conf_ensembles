import copy

import numpy

from confens.classifiers.Classifier import Classifier
from confens.classifiers.ConfidenceEnsemble import ConfidenceEnsemble


def define_conf_thr(confs, target: float = None, delta: float = 0.01) -> float:
    """
    Method for finding a confidence threshold based on the expected contamination (iterative)
    :param confs: confidences to find threshold of
    :param target: the quantity to be used as reference for gettng to the threshold
    :param delta: the tolerance to stop recursion
    :return: a float value to be used as threshold for updating weights in boosting
    """
    target_thr = target
    left_bound = min(confs)
    right_bound = max(confs)
    c_thr = (right_bound + left_bound) / 2
    a = numpy.average(confs < 0.6)
    b = numpy.average(confs < 0.9)
    actual_thr = numpy.average(confs < c_thr)
    while abs(actual_thr - target_thr) > delta and abs(right_bound - left_bound) > 0.001:
        if actual_thr < target_thr:
            left_bound = c_thr
            c_thr = (c_thr + right_bound) / 2
        else:
            right_bound = c_thr
            c_thr = (c_thr + left_bound) / 2
        actual_thr = numpy.average(confs < c_thr)
    return c_thr


class ConfidenceBoosting(ConfidenceEnsemble):
    """
    Class for creating Confidence Boosting ensembles
    """

    def __init__(self, clf, n_base: int = 10, learning_rate: float = None,
                 sampling_ratio: float = 0.5, boost_thr: float = 0.8, conf_thr: float = None, perc_decisors: float = None,
                 n_decisors: int = None, weighted: bool = False):
        """
        Constructor
        :param clf: the algorithm to be used for creating base learners
        :param n_base: number of base learners (= size of the ensemble)
        :param learning_rate: learning rate for updating dataset weights
        :param sampling_ratio: percentage of the dataset to be used at each iteration
        :param boost_thr: threshold of acceptance for confidence scores. Lower confidence means untrustable result
        :param conf_thr: float value for confidence threshold
        :param perc_decisors: percentage of base learners to be used for prediction
        :param n_decisors: number of base learners to be used for prediction
        :param weighted: True if prediction has to be computed as a weighted sum of probabilities
        """
        super().__init__(clf, n_base, conf_thr, perc_decisors, n_decisors, weighted)
        self.proba_thr = None
        self.boost_thr = boost_thr if boost_thr is not None else 0.8
        if learning_rate is not None:
            self.learning_rate = learning_rate
        else:
            self.learning_rate = 2
        if sampling_ratio is not None:
            self.sampling_ratio = sampling_ratio
        else:
            self.sampling_ratio = 1 / n_base ** (1 / 2)

    def fit_ensemble(self, X, y=None):
        """
        Training function for the confidence boosting ensemble
        :param y: labels of the train set (optional, not required for unsupervised learning)
        :param X: train set
        """
        train_n = len(X)
        samples_n = int(train_n * self.sampling_ratio)
        weights = numpy.full(train_n, 1 / train_n)
        for learner_index in range(0, self.n_base):
            # Draw samples
            sample_x, sample_y = self.draw_samples(X, y, samples_n, weights)
            # Train learner
            learner = copy.deepcopy(self.clf)
            learner.fit(sample_x, sample_y)
            if hasattr(learner, "X_"):
                learner.X_ = None
            if hasattr(learner, "y_"):
                learner.y_ = None
            # Test Learner
            y_proba = learner.predict_proba(X)
            y_conf = numpy.max(y_proba, axis=1)
            p_thr = define_conf_thr(target=self.boost_thr, confs=y_conf)
            self.estimators_.append(learner)
            # Update Weights
            update_flag = numpy.where(y_conf >= p_thr, 0, 1)
            weights = weights * (1 + self.learning_rate * update_flag)
            weights = weights / sum(weights)

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
            return "ConfidenceBoosterWeighted(" + str(clf_name) + "-" + \
                   str(self.n_base) + "-" + str(self.boost_thr) + "-" + \
                   str(self.learning_rate) + "-" + str(self.sampling_ratio) + "-" + \
                   str(self.conf_thr) + "-" + str(self.perc_decisors) + "-" + str(self.n_decisors) + ")"
        else:
            return "ConfidenceBooster(" + str(clf_name) + "-" + \
                   str(self.n_base) + "-" + str(self.boost_thr) + "-" + \
                   str(self.learning_rate) + "-" + str(self.sampling_ratio) + "-" + \
                   str(self.conf_thr) + "-" + str(self.perc_decisors) + "-" + str(self.n_decisors) + ")"
