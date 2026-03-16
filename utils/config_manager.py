
import json
import os

CONFIG_FILE = "config.json"

# --- 全局统一路径管理 ---
class ProjectPaths:
    RUNS_DIR = "runs"
    TRAIN_NAME = "liudup_train"

    # 原代码：TRAIN_ROOT = os.path.join(RUNS_DIR, TRAIN_NAME)
    TRAIN_ROOT = os.path.join(RUNS_DIR, "detect", "runs", TRAIN_NAME)

    # 权重目录
    WEIGHTS_DIR = os.path.join(TRAIN_ROOT, "weights")
    # 核心模型路径
    BEST_PT = os.path.join(WEIGHTS_DIR, "best.pt")
    LAST_PT = os.path.join(WEIGHTS_DIR, "last.pt")
    # 训练指标路径
    RESULTS_CSV = os.path.join(TRAIN_ROOT, "results.csv")
    # 数据集目录名
    YOLO_DATASET = "yolo_dataset"
    # 备份目录名
    BACKUP_DIR = "_backup"
    # 数据库名
    DB_NAME = "liudup_cache.db"

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "local_path": "",
        "minio_endpoint": "127.0.0.1:9000",
        "minio_access_key": "",
        "minio_secret_key": "",
        "bucket_name": "images",
        "model_type": "cnn",
        "classes": [],
        "last_model_path": ""
    }
