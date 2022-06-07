from tkinter import W
import jetson.inference
import jetson.utils

import argparse
import sys
import cv2

class ObjectDetector:
    
    def __init__(self, network="ssd-mobilenet-v2", threshold=0.5, overlay="") -> None:
        self.net = jetson.inference.detectNet(network, sys.argv, threshold)
        self.overlay = overlay

    def __call__(self, cudaImg, template):
        detections = self.net.Detect(cudaImg, overlay=self.overlay)

        print("detected {:d} objects in image".format(len(detections)))

        for detection in detections:
            x, y = int(detection.Center[0]), int(detection.Center[1])
            w_half, h_half = int(detection.Width / 2), int(detection.Height / 2)

            pts1 = x - w_half, y - h_half
            pts2 = x + w_half, y + h_half

            cv2.rectangle(template, pts1, pts2, (255, 255, 0), 3)

        return detections
        