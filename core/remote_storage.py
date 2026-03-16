
import os
import boto3
from botocore.client import Config
from dotenv import load_dotenv

load_dotenv()

class MinioManager:
    def __init__(self):
        self.s3 = boto3.client(
            's3',
            endpoint_url=os.getenv('MINIO_ENDPOINT'),
            aws_access_key_id=os.getenv('MINIO_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('MINIO_SECRET_KEY'),
            config=Config(signature_version='s3v4'),
            region_name='us-east-1',
            verify=os.getenv('MINIO_SECURE') == 'True'
        )

    def list_images(self, bucket_name, prefix=""):
        """列出桶内所有图片对象"""
        paginator = self.s3.get_paginator('list_objects_v2')
        valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')

        image_keys = []
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].lower().endswith(valid_extensions):
                    image_keys.append(obj['Key'])
        return image_keys

    def download_to_temp(self, bucket_name, object_key, temp_dir):
        """下载单个文件到临时目录供分析"""
        local_path = os.path.join(temp_dir, os.path.basename(object_key))
        self.s3.download_file(bucket_name, object_key, local_path)
        return local_path

    def delete_image(self, bucket_name, object_key):
        """从 Minio 桶中删除对象"""
        try:
            self.s3.delete_object(Bucket=bucket_name, Key=object_key)
            return True
        except Exception as e:
            print(f"删除失败 {object_key}: {e}")
            return False

    def upload_file(self, bucket_name, local_path, object_key):
        """上传本地文件到指定桶"""
        try:
            self.s3.upload_file(local_path, bucket_name, object_key)
            return True
        except Exception as e:
            print(f"上传失败 {local_path}: {e}")
            return False