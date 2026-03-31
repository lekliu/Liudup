import os
import shutil
import random
import yaml


def prepare_yolo_dataset(local_path, labeled_files, class_names, train_ratio=0.8, dir_name="yolo_dataset"):
    """
    组织 YOLO 格式数据集
    labeled_files: 包含已标注 local_path 的列表
    """
    dataset_root = os.path.join(local_path, dir_name)

    # 【核心修复】：如果旧的数据集目录存在，直接物理删除，确保数据不堆积
    if os.path.exists(dataset_root):
        shutil.rmtree(dataset_root)

    dirs = [
        "images/train", "images/val",
        "labels/train", "labels/val"
    ]

    # 1. 创建目录结构
    for d in dirs:
        os.makedirs(os.path.join(dataset_root, d), exist_ok=True)

    # 2. 划分数据集
    random.shuffle(labeled_files)
    split_idx = int(len(labeled_files) * train_ratio)
    train_set = labeled_files[:split_idx]
    val_set = labeled_files[split_idx:]

    def copy_pair(file_list, target_sub):
        root_abs = os.path.normpath(local_path)
        for img_p in file_list:
            if not os.path.exists(img_p): continue
            label_p = os.path.splitext(img_p)[0] + ".txt"
            if not os.path.exists(label_p): continue

            # 任务 10：计算相对路径，实现镜像目录拷贝
            rel_p = os.path.relpath(img_p, root_abs)
            
            target_img_p = os.path.join(dataset_root, "images", target_sub, rel_p)
            target_lbl_p = os.path.join(dataset_root, "labels", target_sub, os.path.splitext(rel_p)[0] + ".txt")
            
            # 自动创建目标子目录
            os.makedirs(os.path.dirname(target_img_p), exist_ok=True)
            os.makedirs(os.path.dirname(target_lbl_p), exist_ok=True)

            shutil.copy2(img_p, target_img_p)
            shutil.copy2(label_p, target_lbl_p)

    copy_pair(train_set, "train")
    copy_pair(val_set, "val")

    # 3. 生成 data.yaml
    yaml_data = {
        'path': 'yolo_dataset',
        'train': 'images/train',
        'val': 'images/val',
        'names': {i: name for i, name in enumerate(class_names)}
    }

    yaml_path = os.path.join(dataset_root, "data.yaml")
    with open(yaml_path, 'w') as f:
        # 使用 sort_keys=False 保持顺序
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return yaml_path
