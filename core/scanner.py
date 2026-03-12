import os
from imagededup.methods import PHash

class ImageScanner:
    def __init__(self):
        self.phasher = PHash()

    def scan_folder(self, folder_path):
        """扫描文件夹并返回图片路径列表"""
        valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
        image_paths = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(valid_extensions):
                    image_paths.append(os.path.join(root, file))
        return image_paths

    def find_duplicates(self, folder_path, threshold=10):
        """查找重复图片，返回字典 {主图片: [重复图片列表]}"""
        # encoding 会生成整个文件夹的指纹
        encodings = self.phasher.encode_images(image_dir=folder_path)
        # find_duplicates 基于指纹查找
        duplicates = self.phasher.find_duplicates(encoding_dict=encodings, max_distance_threshold=threshold)
        return duplicates