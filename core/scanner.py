
import os
import time
import numpy as np
import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image


# =============================================================================
# 原生 AI 特征提取引擎 (MobileNetV2) - 保持高性能与稳定性
# =============================================================================
class FeatureExtractor:
    def __init__(self, method='cnn'):
        self.method = method
        if method == 'vit':
            # 引入 Meta DINOv2 (Vision Transformer) - 深度语义理解，准确率 97%+
            # 首次运行会自动下载约 80MB 权重
            self.model = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14')
        else:
            # 经典的 MobileNetV2 (CNN) - 速度快，适合近重复识别
            self.model = models.mobilenet_v2(weights=models.MobileNet_V2_Weights.IMAGENET1K_V1)
            self.model.classifier = nn.Identity()

        self.model.eval()
        # 自动硬件检测：如果有 GPU 则使用加速，否则使用 CPU
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        # 标准化预处理逻辑
        self.transform = transforms.Compose([
            transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def extract(self, image_path):
        try:
            with Image.open(image_path).convert('RGB') as img:
                img_t = self.transform(img).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    feat = self.model(img_t).flatten().cpu().numpy()
                # 归一化，确保模长为 1，方便点积计算余弦相似度
                return feat / (np.linalg.norm(feat) + 1e-9)
        except Exception as e:
            print(f"提取异常 [{os.path.basename(image_path)}]: {e}")
            return None


class ImageScanner:
    def __init__(self, method='cnn'):
        self.method_name = method # 'cnn' (移动端优化) 或 'vit' (深度语义增强)
        self.extractor = FeatureExtractor(method=method)

    def get_image_params(self, path):
        try:
            with Image.open(path) as img:
                return img.size[0], img.size[1], os.path.getsize(path)
        except:
            return 0, 0, 0

    def find_duplicates_with_metrics(self, folder_path, db_manager, threshold=10, full_recompute=False,
                                     progress_callback=None, log_callback=None):
        metrics = {'total_files': 0, 'new_encodes': 0, 'cleaned_records': 0, 'duration': 0}
        start_time = time.time()

        # 1. 扫描磁盘 (路径归一化，防止 Windows/Unix 斜杠冲突)
        valid_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
        disk_files = set()

        for root, dirs, files in os.walk(folder_path):
            # 【核心修正】：如果发现 _backup 目录，从 dirs 中移除它，os.walk 就不会进入该子目录
            if '_backup' in dirs:
                dirs.remove('_backup')
            if 'yolo_dataset' in dirs:
                dirs.remove('yolo_dataset')
            for f in files:
                if f.lower().endswith(valid_exts):
                    path = os.path.normpath(os.path.abspath(os.path.join(root, f)))
                    disk_files.add(path)

        metrics['total_files'] = len(disk_files)

        # 2. 数据库一致性检查 (需求：记录存在但物理文件不存在则删除)
        # 获取当前算法下已有的缓存
        current_algo_hashes = db_manager.get_known_hashes_by_algo(self.method_name)
        current_algo_hashes = {os.path.normpath(k): v for k, v in current_algo_hashes.items()}

        for path in list(current_algo_hashes.keys()):
            if path not in disk_files:
                db_manager.remove_mapping(path)
                metrics['cleaned_records'] += 1
                del current_algo_hashes[path]

        # 3. 全量重算逻辑处理
        if full_recompute:
            if log_callback: log_callback("--- 全量重算模式已激活：清空特征库 ---")
            db_manager.clear_all_hashes(self.method_name)
            current_algo_hashes = {}


        # 4. 增量特征提取与持久化
        to_encode = [f for f in disk_files if f not in current_algo_hashes]
        metrics['new_encodes'] = len(to_encode)
        total_to_do = len(to_encode)

        if to_encode:
            if log_callback: log_callback(f"🚀 开始提取 {len(to_encode)} 张图片的深度特征...")
            success_count = 0
            for i, f in enumerate(to_encode):
                # 【核心逻辑】：触发进度回调
                if progress_callback:
                    progress_callback(i + 1, total_to_do, os.path.basename(f))

                feat = self.extractor.extract(f)
                if feat is not None:
                    # 存入数据库：高精度浮点字符串
                    feat_str = ",".join([f"{x:.8f}" for x in feat.tolist()])
                    try:
                        w, h, sz = self.get_image_params(f)
                        if db_manager.update_metadata(f, self.method_name, feat_str, w, h, sz):
                            success_count += 1
                    except Exception as e:
                        if log_callback: log_callback(f"❌ 数据库存入失败 [{os.path.basename(f)}]: {e}")
            if log_callback: log_callback(f"✅ 诊断：成功存入 {success_count} 条记录")

        # 5. 执行强一致性相似度分组 (核心需求：j 必须与组内每一个成员都相似)
        results = {}
        # 【核心隔离逻辑】：只从数据库中提取符合当前 self.method_name (cnn/vit) 的特征记录
        # 这样即便 pic1.jpg 同时拥有 cnn 和 vit 记录，比对引擎也只会看到与当前选择匹配的那一条
        active_model_hashes = db_manager.get_known_hashes_by_algo(self.method_name)

        if len(active_model_hashes) > 1:
            paths = list(active_model_hashes.keys())
            # 解析向量矩阵：此时所有向量的长度(维度)必然是统一的（要么全是1280，要么全是384）
            vectors = np.array([list(map(float, s.split(','))) for s in active_model_hashes.values()])
            # 矩阵点积得到余弦相似度矩阵
            sim_matrix = np.dot(vectors, vectors.T)

            limit = 1.0 - (threshold / 100.0)
            if log_callback: log_callback(f"📊 正在对比特征矩阵 ({len(paths)}x{len(paths)}), 判定阈值: {limit:.4f}")

            used_indices = set()
            for i in range(len(paths)):
                if i in used_indices: continue

                # 初始化一个可能的分组
                current_group = [i]

                # 寻找候选成员 j
                for j in range(i + 1, len(paths)):
                    if j in used_indices: continue

                    # 【核心逻辑】：j 必须与当前组内所有的已有成员都满足相似度 > 阈值
                    match_all_members = True
                    for member_idx in current_group:
                        score = sim_matrix[j][member_idx]
                        if score <= limit:
                            match_all_members = False
                            break

                    if match_all_members:
                        current_group.append(j)

                # 如果组内成员大于1个，记录结果并将所有成员标记为已分配
                if len(current_group) > 1:
                    master_path = paths[current_group[0]]
                    dup_paths = [paths[idx] for idx in current_group[1:]]

                    # --- 诊断逻辑 1：计算过程验证 ---
                    try:
                        group_sims = sim_matrix[np.ix_(current_group, current_group)]
                        raw_min_sim = np.min(group_sims)
                        safe_max_tol = round(float((1.0 - raw_min_sim) * 100), 1)

                        # 封装为纯净的 Python 对象
                        results[master_path] = {
                            "dups": list(dup_paths),
                            "max_tol": safe_max_tol
                        }
                    except Exception as diag_e:
                        if log_callback: log_callback(f"⚠️ 内部计算异常: {diag_e}")

                    for idx in current_group:
                        used_indices.add(idx)

            if log_callback: log_callback(f"✨ 分组完成，找到 {len(results)} 个强一致性相似组")

        metrics['duration'] = round(time.time() - start_time, 2)
        return results, metrics
