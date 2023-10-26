# Copyright (c) OpenMMLab. All rights reserved.
import numpy as np
import scipy.linalg

from ..builder import MOTION


@MOTION.register_module()
class KalmanFilter3D(object):
    """A simple Kalman filter for tracking bounding boxes in image space.

    The implementation is referred to https://github.com/nwojke/deep_sort.
    """
    chi2inv95 = {
        1: 3.8415,
        2: 5.9915,
        3: 7.8147,
        4: 9.4877,
        5: 11.070,
        6: 12.592,
        7: 14.067,
        8: 15.507,
        9: 16.919
    }

    def __init__(self, center_only=False, weight_2d=1.0, weight_3d=1.0, fps=20):
        self.center_only = center_only
        if self.center_only:
            self.gating_threshold = self.chi2inv95[2]
        else:
            self.gating_threshold = self.chi2inv95[4]

        self.weight_2d = weight_2d
        self.weight_3d = weight_3d
        self.fps = fps

        ndim, dt = 4, 1.

        # Create Kalman filter model matrices.
        self._motion_mat = np.eye(2 * ndim, 2 * ndim) ## 8 x 8
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt
        self._update_mat = np.eye(ndim, 2 * ndim)

        # Motion and observation uncertainty are chosen relative to the current
        # state estimate. These weights control the amount of uncertainty in
        # the model. This is a bit hacky.
        self._std_weight_position = 1. / 20
        self._std_weight_velocity = 1. / 160

        ##---------3d root position-----------
        ## x, y, z, vx, vy, vz at 20 fps
        ndim_3d, dt_3d = 3, 1.0/self.fps
        self._motion_mat_3d = np.eye(2 * ndim_3d, 2 * ndim_3d) ## 6 x 6
        for i in range(ndim_3d):
            self._motion_mat_3d[i, ndim_3d + i] = dt_3d
        self._update_mat_3d = np.eye(ndim_3d, 2 * ndim_3d)
        self._std_weight_position_3d = 1. / 20
        self._std_weight_velocity_3d = 1. / 160

        return

    def initiate(self, measurement, measurement_3d):
        """Create track from unassociated measurement.

        Args:
            measurement (ndarray):  Bounding box coordinates (x, y, a, h) with
            center position (x, y), aspect ratio a, and height h.

            measurement_3d (ndarray): 3d location of the root, x, y, z

        Returns:
             (ndarray, ndarray): Returns the mean vector (8 dimensional) and
                covariance matrix (8x8 dimensional) of the new track.
                Unobserved velocities are initialized to 0 mean.
        """
        mean_pos = measurement
        mean_vel = np.zeros_like(mean_pos)
        mean = np.r_[mean_pos, mean_vel] ## row wise merge

        ## measurement[3] is the height of the bbox
        ## bigger the bounding box, more the error in prediction, aspect ratio std dev is constant though
        std = [
            2 * self._std_weight_position * measurement[3],
            2 * self._std_weight_position * measurement[3], 1e-2,
            2 * self._std_weight_position * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            10 * self._std_weight_velocity * measurement[3], 1e-5,
            10 * self._std_weight_velocity * measurement[3]
        ]
        covariance = np.diag(np.square(std))

        ###------------3d-----------------
        assert(len(measurement_3d) == 3)
        mean_pos_3d = measurement_3d
        mean_vel_3d = np.zeros_like(mean_pos_3d)   
        mean_3d = np.r_[mean_pos_3d, mean_vel_3d]

        std_3d = [
            2 * self._std_weight_position_3d * 1,
            2 * self._std_weight_position_3d * 1, 
            2 * self._std_weight_position_3d * 1,
            10 * self._std_weight_velocity_3d * 1 ,
            10 * self._std_weight_velocity_3d * 1, 
            10 * self._std_weight_velocity_3d * 1
        ]
        covariance_3d = np.diag(np.square(std_3d))

        return mean, covariance, mean_3d, covariance_3d

    def predict(self, mean, covariance, mean_3d, covariance_3d):
        """Run Kalman filter prediction step.

        Args:
            mean (ndarray): The 8 dimensional mean vector of the object
                state at the previous time step.

            covariance (ndarray): The 8x8 dimensional covariance matrix
                of the object state at the previous time step.

        Returns:
            (ndarray, ndarray): Returns the mean vector and covariance
                matrix of the predicted state. Unobserved velocities are
                initialized to 0 mean.
        """
        std_pos = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3], 1e-2,
            self._std_weight_position * mean[3]
        ]
        std_vel = [
            self._std_weight_velocity * mean[3],
            self._std_weight_velocity * mean[3], 1e-5,
            self._std_weight_velocity * mean[3]
        ]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel])) ## concatenates

        mean = np.dot(self._motion_mat, mean)
        covariance = np.linalg.multi_dot(
            (self._motion_mat, covariance, self._motion_mat.T)) + motion_cov

        ##--------------------3d-----------------
        std_pos_3d = [
            self._std_weight_position_3d * 1.0,
            self._std_weight_position_3d * 1.0,
            self._std_weight_position_3d * 1.0,
        ]
        std_vel_3d = [
            self._std_weight_velocity_3d * 1.0,
            self._std_weight_velocity_3d * 1.0,
            self._std_weight_velocity_3d * 1.0,
        ]
        motion_cov_3d = np.diag(np.square(np.r_[std_pos_3d, std_vel_3d])) ## concatenates

        mean_3d = np.dot(self._motion_mat_3d, mean_3d)
        covariance_3d = np.linalg.multi_dot(
            (self._motion_mat_3d, covariance_3d, self._motion_mat_3d.T)) + motion_cov_3d

        return mean, covariance, mean_3d, covariance_3d

    def project(self, mean, covariance, mean_3d, covariance_3d):
        """Project state distribution to measurement space.

        Args:
            mean (ndarray): The state's mean vector (8 dimensional array).
            covariance (ndarray): The state's covariance matrix (8x8
                dimensional).

        Returns:
            (ndarray, ndarray):  Returns the projected mean and covariance
            matrix of the given state estimate.
        """
        std = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3], 1e-1,
            self._std_weight_position * mean[3]
        ]
        innovation_cov = np.diag(np.square(std)) ## 4 x 4
        ## update_mat is 4 x 8
        mean = np.dot(self._update_mat, mean)
        covariance = np.linalg.multi_dot(
            (self._update_mat, covariance, self._update_mat.T)) ## 4 x 4

        ##--------------------------------------------------------------
        std_3d = [
            self._std_weight_position_3d * 1.0,
            self._std_weight_position_3d * 1.0,
            self._std_weight_position_3d * 1.0,
        ]
        innovation_cov_3d = np.diag(np.square(std_3d)) ## 3 x 3
        ## update_mat is 4 x 8
        mean_3d = np.dot(self._update_mat_3d, mean_3d)
        covariance_3d = np.linalg.multi_dot(
            (self._update_mat_3d, covariance_3d, self._update_mat_3d.T)) ## 3 x 3

        return mean, covariance + innovation_cov, mean_3d, covariance_3d + innovation_cov_3d

    def update(self, mean, covariance, measurement, mean_3d, covariance_3d, measurement_3d):
        """Run Kalman filter correction step.

        Args:
            mean (ndarray): The predicted state's mean vector (8 dimensional).
            covariance (ndarray): The state's covariance matrix (8x8
                dimensional).
            measurement (ndarray): The 4 dimensional measurement vector
                (x, y, a, h), where (x, y) is the center position, a the
                aspect ratio, and h the height of the bounding box.


        Returns:
             (ndarray, ndarray): Returns the measurement-corrected state
             distribution.
        """
        assert(len(mean) == 8)
        assert(len(mean_3d) == 6)

        projected_mean, projected_cov, projected_mean_3d, projected_cov_3d = self.project(mean, covariance, mean_3d, covariance_3d)

        chol_factor, lower = scipy.linalg.cho_factor(projected_cov, lower=True, check_finite=False)

        kalman_gain = scipy.linalg.cho_solve((chol_factor, lower),
                                             np.dot(covariance,
                                                    self._update_mat.T).T,
                                             check_finite=False).T
        innovation = measurement - projected_mean

        new_mean = mean + np.dot(innovation, kalman_gain.T)
        new_covariance = covariance - np.linalg.multi_dot(
            (kalman_gain, projected_cov, kalman_gain.T))

        ###------------------3d--------------------
        chol_factor_3d, lower_3d = scipy.linalg.cho_factor(
            projected_cov_3d, lower=True, check_finite=False)
        kalman_gain_3d = scipy.linalg.cho_solve((chol_factor_3d, lower_3d),
                                             np.dot(covariance_3d,
                                                    self._update_mat_3d.T).T,
                                             check_finite=False).T
        innovation_3d = measurement_3d - projected_mean_3d

        new_mean_3d = mean_3d + np.dot(innovation_3d, kalman_gain_3d.T)
        new_covariance_3d = covariance_3d - np.linalg.multi_dot(
            (kalman_gain_3d, projected_cov_3d, kalman_gain_3d.T))

        return new_mean, new_covariance, new_mean_3d, new_covariance_3d

    def gating_distance(self,
                        mean,
                        covariance,
                        measurements,
                        only_position=False):
        """Compute gating distance between state distribution and measurements.

        A suitable distance threshold can be obtained from `chi2inv95`. If
        `only_position` is False, the chi-square distribution has 4 degrees of
        freedom, otherwise 2.

        Args:
            mean (ndarray): Mean vector over the state distribution (8
                dimensional).
            covariance (ndarray): Covariance of the state distribution (8x8
                dimensional).
            measurements (ndarray): An Nx4 dimensional matrix of N
                measurements, each in format (x, y, a, h) where (x, y) is the
                bounding box center position, a the aspect ratio, and h the
                height.
            only_position (bool, optional): If True, distance computation is
                done with respect to the bounding box center position only.
                Defaults to False.

        Returns:
            ndarray: Returns an array of length N, where the i-th element
            contains the squared Mahalanobis distance between
            (mean, covariance) and `measurements[i]`.
        """
        mean, covariance = self.project(mean, covariance)
        if only_position:
            mean, covariance = mean[:2], covariance[:2, :2]
            measurements = measurements[:, :2]

        cholesky_factor = np.linalg.cholesky(covariance)
        d = measurements - mean
        z = scipy.linalg.solve_triangular(
            cholesky_factor,
            d.T,
            lower=True,
            check_finite=False,
            overwrite_b=True)
        squared_maha = np.sum(z * z, axis=0)
        return squared_maha

    def track(self, tracks, bboxes):
        """Track forward.

        Args:
            tracks (dict[int:dict]): Track buffer.
            bboxes (Tensor): Detected bounding boxes.

        Returns:
            (dict[int:dict], Tensor): Updated tracks and bboxes.
        """
        costs = []
        for id, track in tracks.items():
            track.mean, track.covariance = self.predict(
                track.mean, track.covariance)
            gating_distance = self.gating_distance(track.mean,
                                                   track.covariance,
                                                   bboxes.cpu().numpy(),
                                                   self.center_only)
            costs.append(gating_distance)

        costs = np.stack(costs, 0)
        costs[costs > self.gating_threshold] = np.nan
        return tracks, costs
