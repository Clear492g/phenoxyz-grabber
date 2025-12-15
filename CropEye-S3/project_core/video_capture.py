import os
import time
import cv2
from PyQt5.QtCore import *

import project_core.ms602_struct as ms602_struct
from collections import deque
import threading
import numpy as np


class ClearVideoCapture(QThread):
    new_frame_to_ui = pyqtSignal(ms602_struct.CamCurrentFrame)
    def __init__(self, cam_index_or_path, os_type, cam_name, trigger_mode=ms602_struct.TriggerMode.CONTINUOUS.value):
        super().__init__()

        self.cam_index_or_path = cam_index_or_path  # '/dev/video1' 3
        self.os_type = os_type  # 'linux'
        self.cam_name = cam_name  # 'cam_480'
        self.cap_cv = None
        self.current_frame = ms602_struct.CamCurrentFrame()
        self.clear_trigger_mode = trigger_mode  # 0：持续 1：软触发 2.外部触发
        self.clear_trigger = ''  # '':pass '0'：单机触发但不保存
        self.lock = threading.Lock()  # 锁，确保线程安全

    def run(self):
        if self.os_type == 'Windows':
            self.cap_cv = cv2.VideoCapture(self.cam_index_or_path, cv2.CAP_DSHOW)
        elif self.os_type == 'Linux':
            self.cap_cv = cv2.VideoCapture(self.cam_index_or_path, cv2.CAP_V4L2)
        print("cv open finished:" + self.cam_name)
        print("*****************************")

        if self.clear_trigger_mode == ms602_struct.TriggerMode.EXTERNAL_TRIGGER.value:
            self.cap_cv.set(cv2.CAP_PROP_BACKLIGHT, 2)  # 逆光比 2为外触发  0为自动触发
        else:
            self.cap_cv.set(cv2.CAP_PROP_BACKLIGHT, 0)  # 逆光比 2为外触发  0为自动触发
        self.cap_cv.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 缓存

        if self.cam_name == 'rgb':
            self.cap_cv.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
            self.cap_cv.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        else:
            self.cap_cv.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap_cv.set(cv2.CAP_PROP_FRAME_HEIGHT, 800)

        # 自动曝光
        if self.os_type == 'Windows':
            self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
        elif self.os_type == 'Linux':
            self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3.0)
        self.cap_cv.set(cv2.CAP_PROP_GAIN, 16)

        # 手动曝光
        # if self.cam_name == 'rgb' or self.cam_name == '480' or self.cam_name == '720' or self.cam_name == '840':
        #     if self.os_type == 'Windows':
        #         self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
        #     elif self.os_type == 'Linux':
        #         self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3.0)
        #         self.cap_cv.set(cv2.CAP_PROP_GAIN, 0)
        #
        # if self.cam_name == '550' or self.cam_name == '660':
        #     if self.os_type == 'Windows':
        #         self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.0)
        #         self.cap_cv.set(cv2.CAP_PROP_EXPOSURE, -6)
        #         self.cap_cv.set(cv2.CAP_PROP_GAIN, 16)
        #     elif self.os_type == 'Linux':
        #         self.cap_cv.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
        #         self.cap_cv.set(cv2.CAP_PROP_EXPOSURE, -6*2**10000)
        #         self.cap_cv.set(cv2.CAP_PROP_GAIN, 16)


        print("Setting finished:" + self.cam_name)
        print("*****************************")

        prev_time = time.time()
        fps_queue = deque(maxlen=3)  # 使用一个队列保存最近3帧的帧速

        while True:
            # 单机持续模式
            if self.clear_trigger_mode == ms602_struct.TriggerMode.CONTINUOUS.value:
                ret, frame = self.cap_cv.read()
                if not ret:
                    print("无法读取视频流: " + self.cam_name)
                    break
                else:
                    height, width, _ = frame.shape

                    if self.cam_name == 'rgb':
                        # top = int(height * 0.1)
                        # bottom = int(height * 0.9)
                        # left = int(width * 0.1)
                        # right = int(width * 0.9)
                        # frame = frame.copy()[top:bottom, left:right]
                        # frame = np.ascontiguousarray(frame)
                        self.current_frame.cv_img = frame
                        self.current_frame.update(cv_img=frame)
                    else:
                        # top = int(height * 0.1)
                        # bottom = int(height * 0.9)
                        # left = int(width * 0.1)
                        # right = int(width * 0.9)
                        # frame = frame[top:bottom, left:right]
                        self.current_frame.update(cv_img=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

                    self.current_frame.update(cam_name=self.cam_name)
                    self.current_frame.iso = self.cap_cv.get(cv2.CAP_PROP_GAIN)
                    exposure_time = self.cap_cv.get(cv2.CAP_PROP_EXPOSURE)
                    if self.os_type == 'Windows':
                        hutter_speed_ms = ms602_struct.OpenCV_exposure.get(exposure_time, 0.01)
                        self.current_frame.exposure_time = hutter_speed_ms
                    elif self.os_type == 'Linux':
                        self.current_frame.exposure_time = exposure_time

                    # 获取当前帧的时间
                    current_time = time.time()
                    elapsed_time = current_time - prev_time
                    prev_time = current_time
                    # 计算帧速并存入队列
                    fps = 1 / elapsed_time if elapsed_time > 0 else 0
                    fps_queue.append(fps)
                    # 计算滑动平均帧速
                    self.current_frame.fps = sum(fps_queue) / len(fps_queue)

                    self.new_frame_to_ui.emit(self.current_frame)


            elif self.clear_trigger_mode == ms602_struct.TriggerMode.SOFT_TRIGGER.value:

                if self.clear_trigger == '':
                    pass
                elif self.clear_trigger == '0':
                    ret_tmp, frame_tmp = self.cap_cv.read()
                    ret, frame = self.cap_cv.read()
                    if not ret:
                        print("无法读取视频流: " + self.cam_name)
                        break
                    else:
                        if self.cam_name == 'rgb':
                            self.current_frame.update(cv_img=frame)
                        else:
                            self.current_frame.update(cv_img=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

                        self.current_frame.update(cam_name=self.cam_name)
                        exposure_time = self.cap_cv.get(cv2.CAP_PROP_EXPOSURE)
                        if self.os_type == 'Windows':
                            hutter_speed_ms = ms602_struct.OpenCV_exposure.get(exposure_time, 0.01)
                            self.current_frame.exposure_time = hutter_speed_ms
                        elif self.os_type == 'Linux':
                            self.current_frame.exposure_time = exposure_time
                        self.current_frame.iso = self.cap_cv.get(cv2.CAP_PROP_GAIN)

                        if self.clear_trigger == '0':
                            self.new_frame_to_ui.emit(self.current_frame)
                            self.clear_trigger = ''

            elif self.clear_trigger_mode == ms602_struct.TriggerMode.EXTERNAL_TRIGGER.value:

                if not self.cap_cv.grab():
                    print("waiting trigger...")
                    time.sleep(0.01)
                    continue

                ret, frame = self.cap_cv.retrieve()
                if not ret:
                    continue
                else:
                    if self.cam_name == 'rgb':
                        self.current_frame.update(cv_img=frame)
                    else:
                        self.current_frame.update(cv_img=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
                    self.current_frame.update(cam_name=self.cam_name)
                    self.current_frame.iso = self.cap_cv.get(cv2.CAP_PROP_GAIN)
                    exposure_time = self.cap_cv.get(cv2.CAP_PROP_EXPOSURE)
                    if self.os_type == 'Windows':
                        hutter_speed_ms = ms602_struct.OpenCV_exposure.get(exposure_time, 0.01)
                        self.current_frame.exposure_time = hutter_speed_ms
                    elif self.os_type == 'Linux':
                        self.current_frame.exposure_time = exposure_time
                    self.current_frame.fps = 0.0

                    self.new_frame_to_ui.emit(self.current_frame)

    def save_oneframe(self,time=None, path=None, pic_bit=8,quality_rgb=1,quality_mono=0):
        # pic_bit为光谱图像深度 取值8/12/16
        # quality_rgb为压缩，取值范围是 0 到 9，0 = 无压缩（文件最大，但保存速度最快）
        # quality_mono为压缩，取值范围是 0 到 9，0 = 无压缩（文件最大，但保存速度最快）
        def _save_task():
            if self.current_frame.cv_img is None:
                print("Error: No frame captured")
                return
            if path and not os.path.exists(path):
                os.makedirs(path)

            if time is None:
                local_time = time.strftime('%Y%m%d-%H%M%S')
            else:
                local_time = time
            file_path = os.path.join(path, local_time + '_' + self.cam_name + '_' + str(
                self.current_frame.exposure_time) + '_' + str(self.current_frame.iso) + ".png")

            try:
                if self.cam_name == 'rgb':
                    resized_img = cv2.resize(self.current_frame.cv_img, (1600, 1200), interpolation=cv2.INTER_NEAREST)
                    cv2.imwrite(file_path, resized_img, [cv2.IMWRITE_PNG_COMPRESSION, quality_rgb])  # 将图片保存到本目录中
                else:
                    resized_img = cv2.resize(self.current_frame.cv_img, (1280, 800), interpolation=cv2.INTER_NEAREST)
                    if pic_bit == 8:
                        cv2.imwrite(file_path, resized_img, [cv2.IMWRITE_PNG_COMPRESSION, quality_mono])  # 将图片保存到本目录中
                    if pic_bit == 12:
                        img_12bit = (resized_img.astype(np.uint16) * 16)  # 255 -> 4095
                        cv2.imwrite(file_path, img_12bit, [cv2.IMWRITE_PNG_COMPRESSION, quality_mono])  # 将图片保存到本目录中、
                    elif pic_bit == 16:
                        #image_16bit = cv2.cvtColor(self.current_frame.cv_img, cv2.CV_16U)
                        img_16bit = (resized_img.astype(np.uint16) * 257)  # 255 -> 65535
                        cv2.imwrite(file_path, img_16bit, [cv2.IMWRITE_PNG_COMPRESSION, quality_mono])  # 将图片保存到本目录中
            except Exception as e:
                print(f"Failed to save image: {e}")

            # 创建新线程执行保存任务

        save_thread = threading.Thread(target=_save_task)
        save_thread.daemon = True  # 设置为守护线程
        save_thread.start()




    def __del__(self):
        if self.cap_cv is not None:
            self.cap_cv.release()
