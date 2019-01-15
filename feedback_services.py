# -*- coding: utf-8 -*-
from __future__ import print_function
import feature_generation
import numpy as np
import pandas as pd
import sounddevice as sd
import csv
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.pipeline import Pipeline
import time
import subprocess
import multiprocessing


class PhysicalFeedback:
    # @staticmethod
    def sound_callback(self, indata, outdata, frames, time, status):
        outdata[:] = np.random.rand(512, 2) * self.sound_volume

    def __init__(self):
        self.sound_volume = 0
        self.stream = sd.Stream(channels=2, callback=self.sound_callback)
        self.stream.start()


def alert(alert_type='switch'):
    if alert_type == 'switch':
        audio_file = "eeg_neurofeedback/sounds/switch.wav"
    else:
        audio_file = "eeg_neurofeedback/sounds/big.wav"
    p1 = multiprocessing.Process(target=subprocess.call, args=(["afplay", audio_file],))
    p1.start()


class TuningState:

    def __init__(self, helmet, visuals, physical_feedback,
                 # warm_period=5,
                 # calibration_iters=2,
                 # calibration_halfiter_period=2 * 60,
                 # feedback_period=10 * 60,
                 # relax_period=2 * 60,
                 # recalibration_period=5 * 60,
                 # recalibration_accuracy=0.7
                 protocol_params):
        self.helmet = helmet
        self.visuals = visuals
        self.physical_feedback = physical_feedback
        self.protocol_params = protocol_params
        self.online_filters = [
            feature_generation.OnlineFilter(fs=250, notch_f0=50, notch_q=30, low_cut=1, high_cut=40, order=5) for
            i in range(8)]
        self.tuning_phase = True
        self.filtered_data = np.ndarray(shape=(0, 9))

    def run(self):
        new_data = np.array(self.helmet.get_data())
        # [:,None] - to make arrays the same shape for hstack
        new_filtered_data = np.hstack([np.array([self.online_filters[i].filter(new_data[:, i])
                                                 for i in range(8)]).T, new_data[:, 9][:, None]])
        self.filtered_data = np.append(self.filtered_data, new_filtered_data, axis=0)

        self.visuals.update_tuning(new_data, new_filtered_data, self.filtered_data)

        if not self.tuning_phase:
            return CalibrationRelax(helmet=self.helmet,
                                    visuals=self.visuals,
                                    online_filters=self.online_filters,
                                    physical_feedback=self.physical_feedback,
                                    protocol_params=self.protocol_params,
                                    filtered_data=self.filtered_data,
                                    features_data=pd.DataFrame(),
                                    ml=MachineLearning(),
                                    states_history=[],
                                    calibration_iter=1,
                                    last_time_run=time.time(),
                                    logger=(csv.writer(open('raw_data.csv', 'wb'), delimiter=';'),)
                                    )
        return self


class MachineLearning:

    def __init__(self):
        estimators = [('reduce_dim', PCA(n_components=35)),
                      ('scaling', StandardScaler()),
                      ('clf', SVC(probability=True, kernel='sigmoid', C=0.1, gamma=0.1))]
        self.clf = Pipeline(estimators)

    def fit(self, featurespace, states_history, just_score=False):
        # values - data, index - seconds
        # todo exclude warm period
        extended_states = states_history + [(time.time() * 10, 'end')]
        featurespace = featurespace.unstack()

        featurespace = featurespace[featurespace.index.values >= extended_states[0][0]]
        cs = 0
        y = []

        # todo simplify iteration
        for s in featurespace.index.values:
            if s >= extended_states[cs + 1][0]:
                cs += 1

            y.append(extended_states[cs][1])

        if just_score:
            return self.clf.score(featurespace.values, y)
        else:
            self.clf.fit(featurespace.values, y)
            return self.clf.score(featurespace.values, y)


# todo add logger
class ProtocolCommonState:

    def __init__(self, helmet, visuals, online_filters, physical_feedback, protocol_params,
                 filtered_data, features_data, ml, states_history, calibration_iter,
                 last_time_run, logger):

        self.helmet = helmet
        self.visuals = visuals
        self.online_filters = online_filters
        self.physical_feedback = physical_feedback
        self.protocol_params = protocol_params
        self.filtered_data = filtered_data
        self.features_data = features_data
        self.ml = ml
        self.states_history = states_history
        self.state_start = time.time()
        self.calibration_iter = calibration_iter
        self.last_time_run = last_time_run
        self.new_features_data = pd.DataFrame()
        self.logger = logger
        self.current_prediction = 0

        self.params_to_pass = '''dict(helmet=self.helmet,
                                   visuals=self.visuals,
                                   online_filters=self.online_filters,
                                   physical_feedback=self.physical_feedback,
                                   protocol_params=self.protocol_params,
                                   filtered_data=self.filtered_data,
                                   features_data=self.features_data,
                                   ml=self.ml,
                                   states_history=self.states_history,
                                   calibration_iter=self.calibration_iter,
                                   last_time_run=self.last_time_run,
                                   logger=self.logger
                                   )'''

        self.human_state_mapper = {
            'CalibrationRelax': ['relax', 'calibration'],
            'CalibrationTarget': ['target', 'calibration'],
            'FeedbackTarget': ['target', 'feedback'],
            'FeedbackRelax': ['relax', 'feedback']
        }

        self.states_history.append([self.state_start] + self.human_state_mapper[self.__class__.__name__])

        alert('switch')

    def update_data_charts(self):

        new_q = self.helmet.get_data()

        self.logger[0].writerow(new_q)

        new_data = np.array(new_q)
        # [:,None] - to make arrays the same shape for hstack
        new_filtered_data = np.hstack([np.array([self.online_filters[i].filter(new_data[:, i])
                                                 for i in range(8)]).T, new_data[:, 9][:, None]])
        self.filtered_data = np.append(self.filtered_data, new_filtered_data, axis=0)
        self.new_features_data = pd.DataFrame(
            self.filtered_data[np.where((self.filtered_data[:, 8] > int(self.last_time_run) - 1) &
                               (self.filtered_data[:, 8] <= int(time.time()) - 1))]
            ).groupby(8).apply(
            lambda x: pd.DataFrame([feature_generation.spectral_features(x[i]) for i in range(8)]).T)

        self.features_data = self.features_data.append(self.new_features_data)

        self.visuals.update_tuning(new_data, new_filtered_data, self.filtered_data)
        self.visuals.update_protocol(self.new_features_data,
                                     self.human_state_mapper[self.__class__.__name__],
                                     self.current_prediction)


# вот это вообще не понял
# что у тебя описывает ProtocolCommonState
# state.run() вообще странно звучит

# советую отделить состояние от его представления
# вот у тебя данные все в одном классе
# отдаешь его в какую нибудь шнягу для визуализации и рисуешь

# вот эти методы `run` говорят о том, что у тебя нет языка описания области
# когда у тебя есть нормальный язык и объекты, ему соответствующие
# то код выглядит как английская проза
# if state.relaxed(at_least=0.8):
#     feedback.nice()

class CalibrationRelax(ProtocolCommonState):

    def run(self):
        self.update_data_charts()
        self.last_time_run = time.time()
        if time.time()-self.state_start > self.protocol_params['calibration_halfiter_period']:
            return CalibrationTarget(**eval(self.params_to_pass))
        return self


class CalibrationTarget(ProtocolCommonState):

    def run(self):
        self.update_data_charts()
        self.last_time_run = time.time()

        if time.time()-self.state_start > self.protocol_params['calibration_halfiter_period']:
            if self.calibration_iter < self.protocol_params['calibration_iters']:
                self.calibration_iter += 1
                return CalibrationRelax(**eval(self.params_to_pass))
            else:
                self.calibration_iter = 1
                self.ml.fit(self.features_data, self.states_history, just_score=False)
                return FeedbackTarget(**eval(self.params_to_pass))
        return self


class FeedbackTarget(ProtocolCommonState):

    def run(self):
        self.update_data_charts()
        self.last_time_run = time.time()

        if int(self.last_time_run - self.state_start) == self.protocol_params['recalibration_period']:
            # todo check accuracy on last feedback period
            score = self.ml.fit(self.features_data, self.states_history, just_score=True)
            print(score)
            if score < self.protocol_params['recalibration_accuracy']:
                self.physical_feedback.sound_volume = 0
                return CalibrationRelax(**eval(self.params_to_pass))

        elif self.last_time_run - self.state_start > self.protocol_params['feedback_period']:
            self.physical_feedback.sound_volume = 0
            return FeedbackRelax(**eval(self.params_to_pass))

        self.current_prediction = self.ml.clf.predict_proba(
            self.new_features_data.unstack().values)[0][list(self.ml.clf.classes_).index('target')]
        self.physical_feedback.sound_volume = (1 - self.current_prediction)

        return self


class FeedbackRelax(ProtocolCommonState):

    def run(self):
        self.update_data_charts()
        self.last_time_run = time.time()

        if self.last_time_run - self.state_start > self.protocol_params['relax_period']:
            self.ml.fit(self.features_data, self.states_history, just_score=False)
            return FeedbackTarget(**eval(self.params_to_pass))

        return self


class Application:

    def __init__(self, helmet, visuals, physical_feedback, protocol_params):
        self.state = TuningState(helmet, visuals, physical_feedback, protocol_params)

    def run(self):
        self.state = self.state.run()
