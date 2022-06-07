import cv2
import numpy as np
# from sklearn import linear_model
from tqdm import tqdm

from detections.Ultra.utils.config import Config
from detections.Ultra.data.constant import culane_row_anchor

import torch
import torch.nn as nn
import os

from torch2trt import TRTModule

class LaneDetectorAI:
    
    def __init__(self):
        self.cfg = Config.fromfile('/home/r320/ComputerVisionADASProject/detections/Ultra/configs/culane.py')
        self.cfg.test_model = f"/home/r320/ComputerVisionADASProject/detections/Ultra/weights/culane_18_fp16.pth"
        torch.backends.cudnn.benchmark = True

        assert self.cfg.backbone in ['18','34','50','101','152','50next','101next','50wide','101wide']

        if self.cfg.dataset == 'CULane':
            self.cls_num_per_lane = 18
            self.row_anchor = culane_row_anchor
        elif self.cfg.dataset == 'Tusimple':
            self.cls_num_per_lane = 56
        else:
            raise NotImplementedError
            
        #addition 12/26
        self.sm = nn.Softmax(dim=0)
        # we add for fix segementation fault
        self.idx = torch.tensor(list(range(self.cfg.griding_num)), device='cuda', dtype=torch.float16) + 1
        # origin
        # self.idx = torch.arange(self.cfg.griding_num).type(torch.HalfTensor).cuda() + 1
        self.idx = self.idx.reshape(-1, 1, 1)

        # tensorrt model
        self.net_trt = TRTModule()
        self.net_trt.load_state_dict(torch.load(self.cfg.test_model))
        self.net_trt.eval()
        
        self.col_sample = np.linspace(0, 800 - 1, self.cfg.griding_num)
        self.col_sample_w = self.col_sample[1] - self.col_sample[0]
        self.img_h = 288

    def __call__(self, img):
        frame_lane = cv2.resize(img, (800, 288))

        img = torch.tensor(frame_lane, device=torch.device("cuda")).permute(2, 0, 1)
        img = img.view(1, 3, 288, 800)
        img = torch.div(img, 255.)

        with torch.no_grad():
            out = self.net_trt(img)

        ### Lane: calculate out_j
        out_j = out.squeeze()
        prob = self.sm(out_j[:-1, :, :])
        
        loc = torch.sum(prob * self.idx, axis=0)
        
        out_j = torch.argmax(out_j, axis=0)
        out_j = out_j%self.cfg.griding_num
        out_j = out_j.bool().int()
        loc = loc * out_j

        out_j = loc.detach().cpu().numpy()
        out_j = out_j[::-1,:]

        ### Lane: calculate ppp
        line = []
        lane_loc_list = []
        for i in range(out_j.shape[1]):
            if np.sum(out_j[:, i] != 0) > 2:
                for k in range(out_j.shape[0]):
                    if out_j[k, i] > 0:
                        ppp = (int(out_j[k, i] * self.col_sample_w) - 1, int(self.img_h * (self.row_anchor[self.cls_num_per_lane-1-k]/288)) - 1 )
                        line.append(ppp)
                lane_loc_list.append(line)
                line = []
        
        for line in lane_loc_list:
            for locate in line:
                if locate[0] > 0:
                    cv2.circle(frame_lane, tuple(np.int32(locate)), 3, (0, 255, 0), 3)
        
        cv2.imshow("lane", frame_lane)



class LaneDetector:
    WARPAFFINE_WIDTH = 256
    WARPAFFINE_HEIGHT = 128

    BEV_WIDTH = 32
    BEV_HEIGHT = 128

    N_WINDOWS = 32
    window_height = BEV_HEIGHT // N_WINDOWS
    window_width = 10

    BEV_POINTS = [
        [0, 0],
        [0, BEV_HEIGHT],
        [BEV_WIDTH, 0],
        [BEV_WIDTH, BEV_HEIGHT]
    ]

    def __init__(self, video_name: str):
        # (x, y)순임. (y, x)순 아님
        # LU -> LB -> RU -> RB
        # 256 x 128 (W, H) image 기준
        if video_name.endswith("highway_D6_Trim.mp4"):
            self.LANE_ROI_POINTS = [
                [self.WARPAFFINE_WIDTH // 2 - 25, 20],
                [35, self.WARPAFFINE_HEIGHT],
                [self.WARPAFFINE_WIDTH // 2 + 30, 20],
                [self.WARPAFFINE_WIDTH - 30, self.WARPAFFINE_HEIGHT],
            ]
        elif video_name.endswith("highway_D5_Trim.mp4"):
            self.LANE_ROI_POINTS = [
                [self.WARPAFFINE_WIDTH // 2 - 30, 20],
                [30, self.WARPAFFINE_HEIGHT],
                [self.WARPAFFINE_WIDTH // 2 + 30, 20],
                [self.WARPAFFINE_WIDTH - 30, self.WARPAFFINE_HEIGHT],
            ]

        # self.lr = linear_model.RANSACRegressor()
        self.M = cv2.getPerspectiveTransform(
            np.array(self.LANE_ROI_POINTS, dtype=np.float32),
            np.array(self.BEV_POINTS, dtype=np.float32)
        )
        self.inv_M = np.linalg.inv(self.M)
        self.BEV2TEMPLATE_LOOKUPTBL = []
        # HSV yello
        self.low_yellow = np.array([10, 100, 100])
        self.upper_yellow = np.array([52, 255, 255])

        for x in tqdm(range(self.BEV_WIDTH)):
            self.BEV2TEMPLATE_LOOKUPTBL.append([])
            for y in range(self.BEV_HEIGHT):
                bef_coor = np.array([x, y, 1])
                aft_coor = np.matmul(self.inv_M, np.transpose(bef_coor))

                self.BEV2TEMPLATE_LOOKUPTBL[x].append(
                    [
                        np.array([int(aft_coor[0] * (1 / aft_coor[2])),
                                  int(aft_coor[1] * (1 / aft_coor[2]))])
                    ])

        self.BEV_color = np.zeros((self.BEV_HEIGHT, self.BEV_WIDTH, 3))
        self.closing_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def right_line_detect(self, img, template):
        X, Y = [], []
        for i in range(self.N_WINDOWS):
            right_window = img[i*(self.window_height):(i+1)*self.window_height, self.BEV_WIDTH-self.window_width:]

            moments = cv2.moments(right_window)
            try:
                cX, cY = int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])
                X.append(cX + self.BEV_WIDTH - self.window_width)
                Y.append(cY + i * (self.window_height))
                cv2.circle(self.BEV_color, (cX + self.BEV_WIDTH - self.window_width, cY + i * (self.window_height)), 2, (0, 150, 0), -1)
            except:
                pass

        if len(X) > 1:
            # self.lr.fit(np.array(X).reshape(-1, 1), np.array(Y))
            try:
                z = np.polyfit(X, Y, 1)
            except:
                return None
            p = np.poly1d(z)
            # print(p)

            if abs(p.c[0]) < 20:
                return None

            right_points = []
            for i in range(self.BEV_WIDTH//2, self.BEV_WIDTH):
                # x, y = i, int(self.lr.predict(np.array(i).reshape(-1, 1)))
                x, y = i, int(p(i))
                if not (0 < y < self.BEV_HEIGHT):
                    continue

                right_points.append(self.BEV2TEMPLATE_LOOKUPTBL[x][y])
                # cv2.circle(self.BEV_color, (x, y), 5, (0, 255, 255), -1)

            x, y = int((self.BEV_HEIGHT - p.c[1]) / p.c[0]), self.BEV_HEIGHT-1
            print(x, y)
            if self.BEV_WIDTH // 2 < x < self.BEV_WIDTH:
                right_points.append(self.BEV2TEMPLATE_LOOKUPTBL[x][y])

                if not template is None:
                    cv2.polylines(template, [np.array(right_points)], False, (0, 255, 0), 4)

                return x
            else:
                return None

    def left_line_detect(self, img, template):
        X, Y = [], []
        for i in range(self.N_WINDOWS):
            left_window = img[i*(self.window_height):(i+1)*self.window_height, :self.window_width]

            moments = cv2.moments(left_window)
            try:
                cX, cY = int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])
                X.append(cX)
                Y.append(cY + i * (self.window_height))
                cv2.circle(self.BEV_color, (cX, cY + i * (self.window_height)), 2, (0, 150, 0), -1)
            except:
                pass

        if len(X) > 1:
            # self.lr.fit(np.array(X).reshape(-1, 1), np.array(Y))
            try:
                z = np.polyfit(X, Y, 1)
            except:
                return None
            p = np.poly1d(z)

            if abs(p.c[0]) < 20:
                return None

            left_points = []
            for i in range(self.BEV_WIDTH//2):
                # x, y = i, int(self.lr.predict(np.array(i).reshape(-1, 1)))
                x, y = i, int(p(i))
                if not (0 < y < self.BEV_HEIGHT):
                    continue

                left_points.append(self.BEV2TEMPLATE_LOOKUPTBL[x][y])
                # cv2.circle(self.BEV_color, (x, y), 5, (0, 255, 255), -1)

            x, y = int((self.BEV_HEIGHT - p.c[1]) / p.c[0]), self.BEV_HEIGHT - 1
            if 0 <= x < self.BEV_WIDTH // 2:
                left_points.append(self.BEV2TEMPLATE_LOOKUPTBL[x][y])

                if not template is None:
                    cv2.polylines(template, [np.array(left_points)], False, (0, 255, 0), 4)

                return x
            else:
                return None

    def __call__(self, gray, hsv, template=None):
        # self.BEV_color = np.zeros_like(self.BEV_color)

        # edges = cv2.GaussianBlur(gray, (15, 15), sigmaX=3, sigmaY=3)
        # edges = cv2.Canny(edges, 50, 70)
        # edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel=self.closing_kernel, iterations=3)

        # center line (yellow line)
        center_line_mask = cv2.inRange(hsv, self.low_yellow, self.upper_yellow)

        # white line image
        ret, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)

        # combine yellow, white line image
        # out = cv2.addWeighted(thresh, 0.5, center_line_mask, 0.5, 0)
        out = cv2.add(thresh, center_line_mask)
        # out = cv2.add(out, edges)


        # BEV image (256, 1024) (W, H)
        out = cv2.warpPerspective(out, self.M, (self.BEV_WIDTH, self.BEV_HEIGHT))
        out = cv2.normalize(out, None, 255, 0, cv2.NORM_MINMAX, cv2.CV_8UC1)

        # cv2.imshow("lane middle result", out)

        return self.left_line_detect(out, template), self.right_line_detect(out, template)

    def show_BEV(self):
        cv2.imshow("BEV", self.BEV_color)
