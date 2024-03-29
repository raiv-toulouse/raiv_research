#!/usr/bin/env python
# coding: utf-8

"""
Used to get picking images from selected positions. The images go to '<rgb AND depth>/<success OR fail>' folders,
depending if the gripper succeed or not.

- We need to establish a connection to the robot with the following command:
roslaunch raiv_libraries ur3_bringup_cartesian.launch robot_ip:=10.31.56.102 kinematics_config:=${HOME}/Calibration/ur3_calibration.yaml

- Information from Arduino
rosrun rosserial_arduino serial_node.py _port:=/dev/ttyACM0

- To get an image :
roslaunch realsense2_camera rs_camera.launch align_depth:=true

- To get information about boxes :
rosrun raiv_libraries get_coord_node.py

- launch program:
python create_image_dataset.py

"""
import time

import rospy
from pathlib import Path
import sys
from raiv_camera_calibration.perspective_calibration import PerspectiveCalibration
from raiv_libraries import tools
from raiv_libraries.robot_with_vaccum_gripper import Robot_with_vaccum_gripper
from raiv_libraries.image_tools import ImageTools
import raiv_libraries.tools
from raiv_libraries.get_coord_node import InBoxCoord
from raiv_libraries.srv import get_coordservice
from raiv_libraries.robotUR import RobotUR
from sensor_msgs.msg import Image
import geometry_msgs.msg as geometry_msgs
from PyQt5.QtWidgets import *
from PyQt5 import uic


#
# Constants
#
X_OUT = 0.0  # XYZ coord where the robot is out of camera scope
Y_OUT = -0.3
Z_OUT = 0.16


class CreateImageDataset(QWidget):

    def __init__(self):
        super().__init__()
        uic.loadUi("create_image_dataset.ui",self)  # needs the canvas_create_image_dataset.py file in the current directory
        # Event handlers
        self.btn_calibration_folder.clicked.connect(self._select_calibration_folder)
        self.btn_image_folder.clicked.connect(self._select_image_folder)
        self.btn_launch_robot.clicked.connect(self._launch_robot)
        # Attributs
        self.robot = None
        self.calibration_folder = None
        self.image_folder = None
        # Define service
        coord_service_name = 'In_box_coordService'
        rospy.wait_for_service(coord_service_name)
        self.coord_service = rospy.ServiceProxy(coord_service_name, get_coordservice)
        # Load a first image
        self.image_controller = rospy.wait_for_message('/camera/color/image_raw', Image)
        self.canvas.set_image(self.image_controller)

    #
    # Event handlers
    #
    def _enable_robot_button(self):
        if self.calibration_folder and self.image_folder:
            self.btn_launch_robot.setEnabled(True)

    def _select_calibration_folder(self):
        dir = QFileDialog.getExistingDirectory(self, "Select camera calibration directory", ".", QFileDialog.ShowDirsOnly)
        if dir:
            self.calibration_folder = Path(dir)
            self.lbl_calibration_folder.setText(str(self.calibration_folder))
            self._enable_robot_button()

    def _select_image_folder(self):
        dir = QFileDialog.getExistingDirectory(self, "Select image directory", ".", QFileDialog.ShowDirsOnly)
        if dir:
            self.image_folder = Path(dir)
            self.lbl_image_folder.setText(str(self.image_folder))
            self._enable_robot_button()

    def _launch_robot(self):
        # Create, if they don't exist, <images_folder>/success/rgb, <images_folder>/success/depth,  <images_folder>/fail/rgb and <images_folder>/fail/depth folders
        self.parent_image_folder = Path(self.image_folder)
        tools.create_rgb_depth_folders(self.parent_image_folder)
        # Define robot and send it out of camera scope
        self.robot = Robot_with_vaccum_gripper()
        self.robot.go_to_xyz_position(X_OUT, Y_OUT, Z_OUT)
        # A PerspectiveCalibration object to perform 2D => 3D conversion
        self.dPoint = PerspectiveCalibration(self.calibration_folder)
        self._get_new_image()

    #
    # Public method
    #
    def process_click(self, px, py):
        if self.robot:
            """ send the robot to this (px, py) position and store the image file in the right folder (success or fail) """
            response_from_coord_service = self.coord_service('fixed', InBoxCoord.PICK, InBoxCoord.IN_THE_BOX, tools.BIG_CROP_WIDTH, tools.BIG_CROP_HEIGHT, px, py)
            self.canvas_preview.update_image(response_from_coord_service.rgb_crop)
            # Move robot to pick position
            pick_pose = self._pixel_to_pose(px, py)
            self.robot.pick(pick_pose)
            # The robot must go out of the camera field
            self.robot.go_to_xyz_position(X_OUT, Y_OUT, Z_OUT)
            object_gripped = self.robot.check_if_object_gripped()
            print('Gripped' if object_gripped else 'NOT gripped')
            self.robot.release_gripper()  # Switch off the gripper
            tools.generate_and_save_rgb_depth_images(response_from_coord_service, self.image_folder, object_gripped)
            self._get_new_image()

    #
    # Private methods
    #
    def _get_new_image(self):
        """ Get an new image from image_raw topic and display it on the canvas """
        self.canvas.set_image(rospy.wait_for_message('/camera/color/image_raw', Image))


    def _pixel_to_pose(self, px, py):
        """ Transpose pixel coord to XYZ coord (in the base robot frame) and return the corresponding frame """
        x, y, z = self.dPoint.from_2d_to_3d([px, py])
        print('xyz :', x, y, z)
        return geometry_msgs.Pose(
            geometry_msgs.Vector3(x, y, Z_OUT), RobotUR.tool_down_pose
        )

#
# Main program
#
if __name__ == '__main__':
    rospy.init_node('create_image_dataset')
    rate = rospy.Rate(0.5)
    app = QApplication(sys.argv)
    gui = CreateImageDataset()
    gui.show()
    sys.exit(app.exec_())