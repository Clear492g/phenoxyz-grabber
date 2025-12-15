import platform
import time

from PyQt5.QtGui import QPixmap, QImage, QPainter, QFont
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QGridLayout, QPushButton, QFileDialog, QSizePolicy, \
    QMessageBox, QComboBox
from PyQt5.QtCore import Qt
import sys
import cv2
import project_core.ms602_index_fixer as ms602_index_fixer
import project_core.video_capture as video_capture
import project_core.ms602_struct as ms602_struct



 
class MyWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        # 创建标签
        self.label_480 = QLabel('Label 480')
        self.label_550 = QLabel('Label 550')
        self.label_660 = QLabel('Label 660')
        self.label_720 = QLabel('Label 720')
        self.label_840 = QLabel('Label 840')
        self.label_rgb = QLabel('Label RGB')

        # 创建按钮
        self.button_save = QPushButton('Save One Frame')  # 保存按钮
        self.button_open_selected_path = QPushButton('Open Selected Path')  # 打开路径按钮
        self.button_select_path = QPushButton('Select Path')  # 选择路径按钮
        self.button_Triger = QPushButton('Triger Test')  # 测试按钮

        # 创建下拉框
        self.comboBox_trigger_mode = QComboBox()
        self.comboBox_trigger_mode.addItems(['CONTINUOUS', 'SOFT_TRIGGER', 'EXTERNAL_TRIGGER'])
        self.comboBox_trigger_mode.setCurrentIndex(0)
        self.comboBox_trigger_mode.currentTextChanged.connect(self.on_trigger_mode_changed)

        # 创建用于显示选择路径的标签
        self.label_selected_path = QLabel('Selected Path: ')  # 初始化 label_selected_path

        # 设置按钮大小策略
        self.button_save.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.button_select_path.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.button_Triger.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.button_open_selected_path.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # 创建布局
        layout = QGridLayout()

        # 布局设置
        # 第一行布局
        layout.addWidget(self.label_selected_path, 0, 0)  # 第一行左侧
        layout.addWidget(self.button_select_path, 0, 1)  # 第一行中间
        layout.addWidget(self.button_open_selected_path, 0, 2)  # 第一行右侧

        # 第二行布局
        layout.addWidget(self.comboBox_trigger_mode, 1, 0)  # 第二行左侧
        layout.addWidget(self.button_Triger, 1, 1)  # 第二行中间
        layout.addWidget(self.button_save, 1, 2)  # 第二行右侧

        # 波段标签布局
        layout.addWidget(self.label_480, 2, 0)
        layout.addWidget(self.label_550, 2, 1)
        layout.addWidget(self.label_660, 2, 2)
        layout.addWidget(self.label_720, 3, 0)
        layout.addWidget(self.label_840, 3, 1)
        layout.addWidget(self.label_rgb, 3, 2)

        # 设置主窗口布局
        self.setLayout(layout)

        # 设置窗口属性
        self.setGeometry(300, 300, 800, 400)  # 调整窗口大小
        self.setWindowTitle('MS602 RunDemo V1.2.1 Fix AArch64')
        self.show()

        # 信号与槽函数连接
        self.button_save.clicked.connect(self.save_img)
        self.button_select_path.clicked.connect(self.select_path)
        self.button_Triger.clicked.connect(self.triger_test)

        # 打开路径按钮的功能
        self.button_open_selected_path.clicked.connect(self.open_selected_path)

    def clear_labels(self):
        # 获取所有标签的引用
        labels = [
            self.label_480,
            self.label_550,
            self.label_660,
            self.label_720,
            self.label_840,
            self.label_rgb
        ]

        # 遍历每个标签并清空内容
        for label in labels:
            label.clear()  # 清空标签内容

    def on_trigger_mode_changed(self, mode):
        if mode =='CONTINUOUS':
            for item in ms602_cv_thread_dict:
                ms602_cv_thread_dict[item].cap_cv.set(cv2.CAP_PROP_BACKLIGHT, 0)  # 逆光比 2为外触发  0为自动触发
                ms602_cv_thread_dict[item].clear_trigger_mode=ms602_struct.TriggerMode.CONTINUOUS.value
        elif mode =='SOFT_TRIGGER':
            for item in ms602_cv_thread_dict:
                ms602_cv_thread_dict[item].clear_trigger_mode=ms602_struct.TriggerMode.SOFT_TRIGGER.value
            self.clear_labels()
        elif mode =='EXTERNAL_TRIGGER':
            for item in ms602_cv_thread_dict:
                ms602_cv_thread_dict[item].cap_cv.set(cv2.CAP_PROP_BACKLIGHT, 2)  # 逆光比 2为外触发  0为自动触发
                ms602_cv_thread_dict[item].clear_trigger_mode=ms602_struct.TriggerMode.EXTERNAL_TRIGGER.value
            self.clear_labels()
        print(f"Trigger mode changed to: {mode}")


    def updateLabel(self, device_current_frame_struct):
        if device_current_frame_struct.cam_name == '480' or device_current_frame_struct.cam_name == '550' or device_current_frame_struct.cam_name == '660' or device_current_frame_struct.cam_name == '720' or device_current_frame_struct.cam_name == '840':
            height, width = device_current_frame_struct.cv_img.shape
            q_img = QImage(device_current_frame_struct.cv_img.data, width, height,
                           device_current_frame_struct.cv_img.strides[0], QImage.Format_Grayscale8)
        elif device_current_frame_struct.cam_name == 'rgb':
            # 彩色图
            height, width, channel = device_current_frame_struct.cv_img.shape
            bytes_per_line = width * channel  # 每行字节数
            if os_type == 'Windows':
                q_img = QImage(device_current_frame_struct.cv_img.data, width, height, bytes_per_line, QImage.Format_BGR888)
            else:
                q_img = QImage(device_current_frame_struct.cv_img.data, width, height, bytes_per_line,
                               QImage.Format_RGB888)

        painter = QPainter(q_img)
        painter.setFont(QFont("Arial", 30))
        painter.setPen(Qt.white)
        painter.drawText(10, 50, f"FPS: {device_current_frame_struct.fps:.2f}")
        painter.drawText(10, 100, f"EPT: {device_current_frame_struct.exposure_time:.5f}" + 'ms')
        painter.drawText(10, 150, f"GAIN: {device_current_frame_struct.iso:.0f}")
        painter.end()

        if device_current_frame_struct.cam_name == 'rgb':
            pixmap = QPixmap.fromImage(q_img).scaled(self.label_rgb.size(), Qt.KeepAspectRatio)
        else:
            pixmap = QPixmap.fromImage(q_img).scaled(self.label_480.size(), Qt.KeepAspectRatio)

        if device_current_frame_struct.cam_name == '480':
            self.label_480.setPixmap(pixmap)
        elif device_current_frame_struct.cam_name == '550':
            self.label_550.setPixmap(pixmap)
        elif device_current_frame_struct.cam_name == '660':
            self.label_660.setPixmap(pixmap)
        elif device_current_frame_struct.cam_name == '720':
            self.label_720.setPixmap(pixmap)
        elif device_current_frame_struct.cam_name == '840':
            self.label_840.setPixmap(pixmap)
        elif device_current_frame_struct.cam_name == 'rgb':
            self.label_rgb.setPixmap(pixmap)

    def save_img(self):
        selected_path = self.label_selected_path.text().split(': ')[-1]  # 提取路径信息
        if selected_path:
            for item in ms602_cv_thread_dict:
                local_time = time.strftime('%Y%m%d-%H%M%S')
                ms602_cv_thread_dict[item].save_oneframe(time = local_time,path=selected_path,pic_bit=16,quality_rgb=1,quality_mono=0)

    def triger_test(self):
        for item in ms602_cv_thread_dict:
            ms602_cv_thread_dict[item].clear_trigger = '0'


    def select_path(self):
        # 使用QFileDialog获取用户选择的文件夹路径
        options = QFileDialog.Options()
        options |= QFileDialog.ShowDirsOnly
        folder_path = QFileDialog.getExistingDirectory(self, "Select Directory", options=options)

        # 更新显示路径的标签
        if folder_path:
            self.label_selected_path.setText(f'Selected Path: {folder_path}')

    # 新增打开路径功能
    def open_selected_path(self):
        import os
        selected_path = self.label_selected_path.text().replace('Selected Path: ', '').strip()
        if selected_path and os.path.exists(selected_path):
            os.startfile(selected_path)  # Windows 系统
        else:
            QMessageBox.warning(self, 'Warning', 'Invalid or empty path!')




if __name__ == '__main__':
    app = QApplication(sys.argv)

    os_type, ms602_cams_index = ms602_index_fixer.get_os_type_and_cams_path()  # {'480': 3, '550': 0, '660': 2, '720': 1, '840': 5, 'rgb': 6}
    missing_devices = [key for key, value in ms602_cams_index.items() if value is None]
    if missing_devices:
        print('No Device！')
        msgBox = QMessageBox()
        msgBox.setText(f"Missing Devices: {', '.join(missing_devices)}. Please Try Again.")
        msgBox.exec_()
        sys.exit()

    window = MyWindow()
    ms602_cv_thread_dict = {"480": None, "550": None, "660": None, "720": None, "840": None, "rgb": None}
    for item in ms602_cv_thread_dict:
        ms602_cv_thread_dict[item] = video_capture.ClearVideoCapture(ms602_cams_index[item], os_type, item, ms602_struct.TriggerMode.CONTINUOUS.value )
        ms602_cv_thread_dict[item].start()

        ms602_cv_thread_dict[item].new_frame_to_ui.connect(window.updateLabel)

    sys.exit(app.exec_())


