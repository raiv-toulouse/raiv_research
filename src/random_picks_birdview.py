#!/usr/bin/env python
# coding: utf-8

"""
Used to get picking images from random position. The images go to 'success/<rgb and depth>' or 'fail/<rgb and depth>' folders, depending if the gripper succeed or not

- We need to establish a connection to the robot with the following command:
roslaunch raiv_libraries ur3_bringup_cartesian.launch robot_ip:=10.31.56.102 kinematics_config:=${HOME}/Calibration/ur3_calibration.yaml

- Information from Arduino
rosrun rosserial_arduino serial_node.py _port:=/dev/ttyACM0

- launch program:
python random_pick_birdview.py <images_folder> <calibration_files_folder>

"""
import numpy as np
import rospy
from datetime import datetime
from pathlib import Path
import sys
from cv_bridge import CvBridge
import cv2
from raiv_camera_calibration.perspective_calibration import PerspectiveCalibration
from raiv_libraries.robot_with_vaccum_gripper import Robot_with_vaccum_gripper
from raiv_libraries.srv import get_coordservice
from raiv_libraries.get_coord_node import InBoxCoord
from raiv_libraries.robotUR import RobotUR
import geometry_msgs.msg as geometry_msgs
from sensor_msgs.msg import Image
from raiv_libraries.image_tools import ImageTools
import os

#
# Constants
#

Z_PICK_PLACE = 0.12  # Z coord to start pick or place movement (in meter)
X_INT = 0.3  # XYZ coord where the robot is on intermediaire position (in meter)
Y_INT = 0.0
Z_INT = 0.12
X_OUT = 0.21  # XYZ coord where the robot is out of camera scope (in meter)
Y_OUT = -0.27
Z_OUT = 0.12
bridge = CvBridge()

def normalize(image, bins=255):
    image_histogram, bins = np.histogram(image.flatten(), bins, density=True)
    cdf = image_histogram.cumsum()  # cumulative distribution function
    cdf = cdf / cdf[-1]  # normalize

    # use linear interpolation of cdf to find new pixel values
    image_equalized = np.interp(image.flatten(), bins[:-1], cdf)

    return image_equalized.reshape(image.shape), cdf


def xyz_to_pose(x, y, z):
    return geometry_msgs.Pose(geometry_msgs.Vector3(x, y, z), RobotUR.tool_down_pose)


def save_images(folder, rgb, depth):
    image_name = str(datetime.now()) + '.png'
    for image_type, image in zip(['rgb', 'depth'], [rgb, depth]):
        image_path = (parent_image_folder / image_type / folder / image_name ).resolve()
        cv2.imwrite(str(image_path), image)
        image_path.chmod(0o777) # Write permission for everybody


#
# Main program
#

# Check if number of arguments is OK
if len(sys.argv) != 3:
    print("Syntax : python random_picks_birdview.py <images_folder> <calibration_files_folder>")
    exit(1)

# Check if the <calibration_files_folder>" exists
calibration_folder = Path(sys.argv[2])
if not calibration_folder.exists():
    print("This folder doesn't exist : {}".format(sys.argv[2]))
    exit(2)

# Create, if they don't exist, <images_folder>/success/rgb, <images_folder>/success/depth,
# <images_folder>/fail/rgb and <images_folder>/fail/depth folders
parent_image_folder = Path(sys.argv[1])

for sf_folder in ['success', 'fail']:
    for rd_folder in ['rgb', 'depth']:
        folder = parent_image_folder / rd_folder / sf_folder
        Path.mkdir(folder, parents=True, exist_ok=True)
        folder.chmod(0o777)  # Write permission for everybody

rospy.init_node('random_picks_birdview')
# A UR robot with a vaccum gripper
robot = Robot_with_vaccum_gripper()
robot.go_to_xyz_position(X_OUT, Y_OUT, Z_OUT)  # Go out of camera scope
# We can now ask a service to get and process 3D images
coord_service_name = 'In_box_coordService'
rospy.wait_for_service(coord_service_name)
coord_service = rospy.ServiceProxy(coord_service_name, get_coordservice)
#### NO NEED _ = coord_service('refresh', None, None, None, None) # ask to refresh (i.e get a new rgb and depth images)

# A PerspectiveCalibration object to perform 2D => 3D conversion
dPoint = PerspectiveCalibration(calibration_folder)

# Main loop to get image
while True:

    # Get all information from the camera
    resp_pick = coord_service('color', InBoxCoord.PICK, InBoxCoord.ON_OBJECT, ImageTools.CROP_WIDTH, ImageTools.CROP_HEIGHT, None, None)
    resp_place = coord_service('random', InBoxCoord.PLACE, InBoxCoord.IN_THE_BOX, ImageTools.CROP_WIDTH, ImageTools.CROP_HEIGHT, None, None)

    # For debug
    distance = rospy.wait_for_message('/Distance_Here', Image)
    distance = bridge.imgmsg_to_cv2(distance, desired_encoding='passthrough')

    rgb_crop = bridge.imgmsg_to_cv2(resp_pick.rgb_crop, desired_encoding='passthrough')
    depth_crop = bridge.imgmsg_to_cv2(resp_pick.depth_crop, desired_encoding='passthrough')
    depth_crop = depth_crop.astype(np.uint16)
    depth_crop = normalize(depth_crop)[0]
    depth_crop = depth_crop * 255

    # For debug
    #cv2.imshow("rgb256", cv2.resize(rgb_crop, (256, 256)))
    #cv2.imshow("depth256", cv2.resize(depth_crop, (256, 256)))
    #cv2.waitKey(1000)

    # Move robot to pick position
    pick_pose = xyz_to_pose(resp_pick.x_robot, resp_pick.y_robot, Z_PICK_PLACE)
    object_gripped = robot.pick(pick_pose)
    # Place the object
    print(resp_place.x_pixel, 'Xplace')
    print(resp_place.y_pixel, 'Yplace')
    place_pose = xyz_to_pose(resp_place.x_robot, resp_place.y_robot, Z_PICK_PLACE)
    robot.place(place_pose)
    object_gripped = robot.object_gripped()
    robot.release_gripper()        # Switch off the gripper

    if object_gripped == True:
        save_images('success', rgb_crop, depth_crop)               # Save images in success folders
        #robot.go_to_xyz_position(X_INT, Y_INT, Z_INT, duration=2)  # Intermediate position to avoid collision with the shoulder
    else:
        save_images('fail', rgb_crop, depth_crop)       # Save images in fail folders
        #robot.go_to_xyz_position(X_INT, Y_INT, Z_INT, duration=2)  # Intermediate position to avoid collision with the shoulder

    robot.go_to_xyz_position(X_OUT, Y_OUT, Z_OUT, duration=2)  # The robot must go out of the camera field
