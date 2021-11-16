from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import torch
import sys
from PyQt5 import uic
from ImageProcessing.ImageModel import ImageModel
from torchvision.transforms.functional import crop
from torchvision import transforms
import os
import time
import torchvision
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
import queue
import datetime
from BlobDetector.camera_calibration.PerspectiveCalibration import PerspectiveCalibration
#########################################################################"
import cv2
import threading
from geometry_msgs.msg import Pose, PoseStamped, Point, Quaternion
from BlobDetector.camera_calibration.PerspectiveCalibration import PerspectiveCalibration
from tf.transformations import euler_from_quaternion, quaternion_from_euler
import moveit_commander
import rospy
import rospkg
from geometry_msgs.msg import Pose, PoseStamped, Point, Quaternion
from moveit_commander.conversions import pose_to_list
#from robot2 import Robot
from ai_manager.Environment import Environment
from ai_manager.Environment import Env_cam_bas
from ai_manager.ImageController import ImageController
import random
#from ur_icam_description.robotUR import RobotUR

# WIDTH = HEIGHT = 56
# dPoint = PerspectiveCalibration()
# dPoint.setup_camera()
# robot2 = Robot(Env_cam_bas)
# rospy.init_node('explore2')
# myRobot = RobotUR()
# image_controller = ImageController(image_topic='/usb_cam2/image_raw')

WIDTH = HEIGHT = 56

class CanvasExplore(QWidget):

    def __init__(self,parent):
        super().__init__(parent)
        self.parent = parent
        self.pressed = self.moving = False
        self.previous_image = None
        self.all_preds = None
        self.select_start = self.select_end = None

    def set_image(self,img):
        self.image = QImage(img.tobytes("raw", "RGB"), img.width, img.height, QImage.Format_RGB888)  # Convert PILImage to QImage
        self.setMinimumSize(self.image.width(), self.image.height())
        self.previous_image = None
        self.all_preds = None
        self.update()

    def mousePressEvent(self, event):
        modifiers = QApplication.keyboardModifiers()
        pos = event.pos()
        if event.button() == Qt.LeftButton:
            if modifiers == Qt.ControlModifier:  # Ctrl + left click
                self.parent.ask_robot_to_pick(pos.x(), pos.y())
            else:
                if self.previous_image:  # A new click, we restore the previous image without the rectangle
                    self.image = self.previous_image
                    self.setMinimumSize(self.image.width(), self.image.height())
                self.pressed = True
                self.center = event.pos()
                self.update()
        elif event.button() == Qt.RightButton:
            self.select_start = pos

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.moving = True
            self.center = event.pos()
            self.update()
        elif event.buttons() & Qt.RightButton:
            self.select_end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.previous_image = self.image.copy()
            qp = QPainter(self.image)
            self._draw_rectangle(qp)
            self.pressed = self.moving = False
            self.update()
        elif event.button() == Qt.RightButton:
            if self.select_end:
                self.parent.compute_map(self.select_start, self.select_end)
                self.update()
                self.select_start = self.select_end = None

    def paintEvent(self, event):
        qp = QPainter(self)
        rect = event.rect()
        qp.drawImage(rect, self.image, rect)
        if self.moving or self.pressed:
            self._draw_rectangle(qp)
        if self.all_preds:
            self._draw_pred(qp)
        if self.select_end:
            self._draw_selected_region(qp)

    def _draw_selected_region(self, qp):
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setPen(QPen(Qt.blue, 5))
        w = self.select_end.x() - self.select_start.x()
        h = self.select_end.y() - self.select_start.y()
        qp.drawRect(self.select_start.x(), self.select_start.y(), w, h)

    def _draw_rectangle(self, qp):
        if self.parent.inference_model:  # A model exists, we can do inference
            x = self.center.x() - WIDTH / 2
            y = self.center.y() - HEIGHT / 2
            qp.setRenderHint(QPainter.Antialiasing)
            qp.setPen(QPen(Qt.blue, 5))
            qp.drawPoint(self.center)
            pred = self.parent.predict(self.center.x(), self.center.y())  # calculate the prediction wih CNN
            prob, cl = self._compute_prob_and_class(pred)
            fail_or_success = 'Fail' if cl.item()==0 else 'Success'
            text = f'{fail_or_success} : {prob:.1f}%'
            qp.setPen(Qt.black)
            qp.setFont(QFont('Decorative', 14))
            qp.drawText(x, y, text)
            if cl.item() == 1:  # Success
                qp.setPen(QPen(Qt.green, 1, Qt.DashLine))
            else:  # Fail
                qp.setPen(QPen(Qt.red, 1, Qt.DashLine))
            qp.drawRect(x, y, WIDTH, HEIGHT)

    def _draw_pred(self, qp):
        """ Display all predictions (green/red point + percentage of success) from self.all_preds list """
        threshold = self.parent.sb_threshold.value()
        self.all_preds.sort(key=lambda pred: pred[2][0][1].item(), reverse=True)
        for idx, (x, y , pred) in enumerate(self.all_preds):
            tensor_prob,tensor_cl = torch.max(pred, 1)
            if tensor_cl.item()==0 or (tensor_cl.item()==1 and tensor_prob.item()*100 < threshold):  # Fail
                qp.setPen(QPen(Qt.red, 5)) # Prediction under the threshold
            else:
                qp.setPen(QPen(Qt.green, 5))# Prediction above the threshold
            if idx == 0:
                qp.setPen(QPen(Qt.blue, 5))  # The best prediction
            qp.drawPoint(x, y)
            qp.setPen(Qt.black)
            qp.setFont(QFont('Decorative', 8))
            prob, cl = self._compute_prob_and_class(pred)
            text = f'{prob:.1f}%'
            qp.drawText(x, y, text)

    def _compute_prob_and_class(self, pred):
        """ Retrive class (success or fail) and its associeted percentage from pred """
        prob, cl = torch.max(pred, 1)
        if cl.item() == 0:  # Fail
            prob = 100 * (1 - prob.item())
        else:  # Success
            prob = 100 * prob.item()
        return prob, cl



