#!/usr/bin/env python3

import rospy
import math
from raiv_research.srv import GetBestPrediction, GetBestPredictionResponse
from raiv_research.msg import Prediction, ListOfPredictions
from raiv_libraries.srv import get_coordservice
from raiv_libraries.srv import PickingBoxIsEmpty, PickingBoxIsEmptyResponse
from raiv_libraries.get_coord_node import InBoxCoord
from PIL import Image as PILImage
import numpy as np
import torch
from raiv_libraries.CNN import CNN
from raiv_libraries.image_tools import ImageTools
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge
import time


IMAGE_TOPIC = "/RGBClean"
THRESHOLD_ABOVE_TABLE = 10

class NodeBestPrediction:
    """
    This node is both a service and a publisher.
    * Service best_prediction_service : use a GetBestPrediction message (no input message and a ListOfPredictions output message)
    When this service is called, return the current best prediction and invalidate all the predictions in its neighborhood.
    * Publisher : publish on the 'predictions' topic a ListOfPredictions message

    How to run?
    * rosrun raiv_libraries ImageProcessing_Node.py (to provide a /RGBClean topic)
    * rosrun raiv_research node_visu_prediction.py   (to view the success/fail prediction points on the image)
    * rosrun raiv_research node_best_prediction.py CKPT_FILE --invalidation_radius INT --image_topic STR (to provide a /predictions topic)
    * rosservice call /best_prediction_service  (to get the current best prediction. It loads a new image and invalidates the points in the picking zone)

    """
    def __init__(self, ckpt_model_file, invalidation_radius, image_topic):
        rospy.init_node('node_best_prediction')
        # List of all predictions made for some random points
        self.predictions = []
        # Message used to send the list of all predictions
        msg_list_pred = ListOfPredictions()
        # Provide the 'best_prediction_service' service
        rospy.Service('best_prediction_service', GetBestPrediction, self.process_service)
        self.pub_image = rospy.Publisher('new_image', Image, queue_size=10)
        self._get_new_image()
        # Publish the 'predictions' topic (a list of all Prediction)
        pub = rospy.Publisher('predictions', ListOfPredictions, queue_size=10)
        self.invalidation_radius = invalidation_radius  # When a prediction is selected, we invalidate all the previous predictions in this radius
        self.image_topic = image_topic
        self.model_name = ckpt_model_file
        self._load_model()
        self.picking_point = None # No picking point yet
        ### Appel du service emptybox
        rospy.wait_for_service('/Empty_Picking_Box')
        self.call_service = rospy.ServiceProxy('/Empty_Picking_Box', PickingBoxIsEmpty)
        rospy.wait_for_service('In_box_coordService')
        coord_serv = rospy.ServiceProxy('In_box_coordService', get_coordservice)
        resp = coord_serv('random', InBoxCoord.PICK, InBoxCoord.ON_OBJECT, ImageTools.CROP_WIDTH, ImageTools.CROP_HEIGHT, None, None)
        while not self._continue_to_picking():
            # Ask 'In_box_coordService' service for a random point in the picking box located on one of the objects
            resp = coord_serv('random_no_refresh', InBoxCoord.PICK, InBoxCoord.ON_OBJECT, ImageTools.CROP_WIDTH, ImageTools.CROP_HEIGHT, None, None)
            # Compute prediction only for necessary points (on an object, not in forbidden zone, ...)
            #if self._not_in_picking_zone(resp.x_pixel, resp.y_pixel):
            msg = Prediction()
            msg.x = resp.x_pixel
            msg.y = resp.y_pixel
            msg.proba = self._predict(resp.x_pixel, resp.y_pixel, resp.rgb_crop)
            self.predictions.append(msg)
            msg_list_pred.predictions = self.predictions
            pub.publish(msg_list_pred)  # Publish the current list of predictions [ [x1,y1,prediction_1], ..... ]
            rospy.sleep(0.01)
        print('dévracage fini')


    def _continue_to_picking(self):
        picking_box_empty = self.call_service().empty_box
        return picking_box_empty


    def _not_in_picking_zone(self, x, y):
        """ Return True if this (x,y) point is a good candidate i.e. is not in the invalidated zone (current picking zone).
        Prediction will be computed only for good candidate points.
        """
        # Test if inside the picking zone (so, not a good candidate)
        return not (self.picking_point and math.dist((self.picking_point[0], self.picking_point[1]), (x, y)) < self.invalidation_radius)


    def _load_model(self):
        """
        Load a pretrained 'resnet18' model from a CKPT filename, freezed for inference
        """
        self.model = CNN(backbone='resnet18')
        self.inference_model = self.model.load_from_checkpoint(self.model_name)   #  Load the selected model
        self.inference_model.freeze()


    def _predict(self, x, y, msg_rgb_crop):
        """ Predict probability and class for a cropped image centered at (x,y) """
        self.predict_center_x = x
        self.predict_center_y = y
        #image_cropped = self.crop_xy(msg_rgb_crop, x, y)
        image_pil = self._to_pil(msg_rgb_crop)
        img = ImageTools.transform_image(image_pil)  # Get the cropped transformed image (size = [CROP_WIDTH,CROP_HEIGHT])
        img = img.unsqueeze(0)  # To have a 4-dim tensor ([nb_of_images, channels, w, h])
        _, preds = self._evaluate_image(img, self.inference_model)
        pred = torch.exp(preds)
        return pred[0][1].item()  # Return the success probability


    def crop_xy(self, msg_image, x, y):
        """ Crop image at position (predict_center_x,predict_center_y) and with size (WIDTH,HEIGHT) """
        pil_image = self._to_pil(msg_image)
        return ImageTools.crop_xy(pil_image, x, y)


    def _to_pil(self, msg):
        """ Recover the image in the msg sensor_msgs.Image message and convert it to a PILImage"""
        size = (msg.width, msg.height)  # Image size
        img = PILImage.frombytes('RGB', size, msg.data)  # sensor_msg Image to PILImage
        return img

    def _get_new_image(self):
        # Get a new image and publish it to the new_image topic(for node_visu_prediction.py )
        msg_image = rospy.wait_for_message(IMAGE_TOPIC, Image)
        self.pub_image.publish(msg_image)

    @torch.no_grad()
    def _evaluate_image(self, image, model):
        features, prediction = model(image)
        return features.detach().numpy(), prediction.detach()


    def process_service(self, req):
        self._get_new_image()

        if req.mode == 'classic':
            prediction = self._get_best_prediction()
        elif req.mode == 'without_invalidation':
            prediction = self._get_best_prediction(invalidation=False)
        elif req.mode == 'just_invalidation':
            prediction = self._get_best_prediction(prediction=False, invalidation=False)

        return GetBestPredictionResponse(prediction)


    def _get_best_prediction(self, invalidation=True, just_invalidation=True, prediction=True):
        # Find best prediction
        if prediction==True:
            if not(self.predictions): # This list is empty
                raise rospy.ServiceException("self.predictions : empty list")
            proba = 0.5
            while proba < 0.7:
                self.best_prediction = self.predictions[0]
                for prediction in self.predictions:
                    if prediction.proba > self.best_prediction.proba:
                        self.best_prediction = prediction
                        proba = float(self.best_prediction.proba)
            if invalidation==True:
                self._invalidate_neighborhood(self.best_prediction.x, self.best_prediction.y)

        if just_invalidation==True:
            self._invalidate_neighborhood_fixed()



        self.picking_point = (self.best_prediction.x, self.best_prediction.y)
        return self.best_prediction


    def _invalidate_neighborhood(self, x, y):
        """ Invalidate (remove) all the predictions in a circle of radius INVALIDATION_RADIUS centered in (x,y)"""
        self.predictions = [pred for pred in self.predictions if math.dist((pred.x, pred.y), (x, y)) > self.invalidation_radius]

    def _invalidate_neighborhood_fixed(self):
        self.predictions = [pred for pred in self.predictions if math.dist((pred.x, pred.y), (230, 230)) > self.invalidation_radius]


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Compute a list of predictions for random points and provide the best one as a service.')
    parser.add_argument('ckpt_model_file', type=str, help='CKPT model file')
    parser.add_argument('--image_topic', type=str, default="/RGBClean", help='Topic which provides an image')
    parser.add_argument('--invalidation_radius', type=int, default=300, help='Radius in pixels where predictions will be invalidated')
    args = parser.parse_args()

    try:
        n = NodeBestPrediction(args.ckpt_model_file, args.invalidation_radius, args.image_topic)
    except rospy.ROSInterruptException:
        pass