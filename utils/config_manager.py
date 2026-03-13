import json
import os

CONFIG_FILE = "config.json"

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
        "model_type": "cnn"
    }