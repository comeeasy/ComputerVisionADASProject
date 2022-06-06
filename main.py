import cv2
from detections.LaneDetector import LaneDetector



def main():
    video_name = "/Users/joono/Desktop/joono/ComputerVisionADASProject/videos/highway_D6_Trim.mp4"
    fps = 30

    capfile = f'filesrc location={video_name} ! qtdemux ! queue \
                            ! h264parse ! omxh264dec ! nvvidconv ! video/x-raw,format=BGRx,width=512,height=256 \
                            ! videorate ! video/x-raw,framerate={fps}/1 !queue ! videoconvert ! queue ! video/x-raw, format=BGR \
                            ! appsink'

    cap = cv2.VideoCapture(capfile, cv2.CAP_GSTREAMER)

    cap = cv2.VideoCapture(video_name)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(w, h)

    laneDetector = LaneDetector(video_name)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        template = frame[round(h*(1/3)):, :, :]
        cv2.imshow("half image", template)

        template = cv2.resize(template, (256, 128))
        gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)

        laneDetector(gray, hsv, template)
        laneDetector.show_BEV()

        cv2.imshow("template", template)

        k = cv2.waitKey(30)
        if 27 == k:
            break


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    main()
