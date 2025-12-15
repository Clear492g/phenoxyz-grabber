import platform
from typing import Dict, Optional, List, Tuple

# 相机名称与应用标识的映射
# 合并 CamX 和 YeRui-MS602-X 的映射
CAM_MAPPING = {
    "Cam1": "480",
    "Cam2": "550",
    "Cam3": "660",
    "Cam4": "720",
    "Cam5": "840",
    "Cam6": "rgb",
    "YeRui-MS602-1": "480",
    "YeRui-MS602-2": "550",
    "YeRui-MS602-3": "660",
    "YeRui-MS602-4": "720",
    "YeRui-MS602-5": "840",
    "YeRui-MS602-6": "rgb"
}


def check_dependencies_linux() -> bool:
    """检查 Linux 系统是否安装了必要的工具"""
    import shutil
    return shutil.which("v4l2-ctl") is not None


def generate_cams_index_linux() -> Optional[List[Tuple[int, str]]]:
    """生成 Linux 下的相机索引映射"""
    import subprocess
    import re
    try:
        # 运行 v4l2-ctl 并解析输出
        output = subprocess.check_output(['v4l2-ctl', '--list-devices'], universal_newlines=True)
        pattern = re.compile(r'Cam(\d+).*:\s+/dev/video(\d+)')
        matches = pattern.findall(output)
        # 创建设备名称与索引的映射，并按索引排序
        return sorted([(int(video_index), f"Cam{cam_num}") for cam_num, video_index in matches])
    except subprocess.CalledProcessError as e:
        print(f"Error running v4l2-ctl: {e}")
        return None


def generate_cams_index_windows() -> Optional[List[str]]:
    """生成 Windows 下的相机列表"""
    try:
        from PyCameraList.camera_device import list_video_devices
        return list_video_devices()
    except ImportError as e:
        print(f"Error importing PyCameraList: {e}")
        return None


def get_os_type_and_cams_path() -> Tuple[str, Dict[str, Optional[int]]]:
    """生成跨平台的相机索引映射"""
    os_type = platform.system()
    cropeye_s3_cams_index = {v: None for v in CAM_MAPPING.values()}


    if os_type == 'Linux':
        if not check_dependencies_linux():
            print("Error: Missing dependency 'v4l2-ctl'. Please install it via your package manager.")
            return os_type, cropeye_s3_cams_index

        cameras = generate_cams_index_linux()


    elif os_type == 'Windows':
        cameras = generate_cams_index_windows()
    else:
        print(f"Unsupported OS: {os_type}")
        return os_type, cropeye_s3_cams_index

    if not cameras:
        print("No cameras found or an error occurred.")
        return os_type, cropeye_s3_cams_index

    # 映射设备名称到索引
    for device in cameras:
        if isinstance(device, tuple):  # Linux: (index, name)
            video_index, cam_name = device
        else:  # Windows: name (模拟)
            cam_name, video_index = device, None

        if cam_name in CAM_MAPPING:
            cropeye_s3_cams_index[CAM_MAPPING[cam_name]] = video_index

    return os_type, cropeye_s3_cams_index


print(get_os_type_and_cams_path())
