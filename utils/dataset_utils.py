import os
import shutil
import random
import yaml


def prepare_yolo_dataset(local_path, labeled_files, class_names, train_ratio=0.8):
    """
    组织 YOLO 格式数据集
    labeled_files: 包含已标注 local_path 的列表
    """
    dataset_root = os.path.join(local_path, "yolo_dataset")

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
        for img_p in file_list:
            if not os.path.exists(img_p): continue
            label_p = os.path.splitext(img_p)[0] + ".txt"
            if not os.path.exists(label_p): continue

            # 拷贝图片
            shutil.copy2(img_p, os.path.join(dataset_root, "images", target_sub, os.path.basename(img_p)))
            # 拷贝标注
            shutil.copy2(label_p, os.path.join(dataset_root, "labels", target_sub, os.path.basename(label_p)))

    copy_pair(train_set, "train")
    copy_pair(val_set, "val")

    # 3. 生成 data.yaml
    yaml_data = {
        'path': dataset_root,
        'train': 'images/train',
        'val': 'images/val',
        'names': {i: name for i, name in enumerate(class_names)}
    }

    yaml_path = os.path.join(dataset_root, "data.yaml")
    with open(yaml_path, 'w') as f:
        yaml.dump(yaml_data, f, default_flow_style=False)

    return yaml_path
