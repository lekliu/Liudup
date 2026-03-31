import json
import sqlite3
import os


class DatabaseManager:
    def __init__(self, db_path="liudup_cache.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_mapping (
                local_path TEXT,
                remote_key TEXT,
                algo TEXT,
                hash TEXT,
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (local_path, algo)
            )
        ''')
        # 新增标注专用表，实现数据隔离
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS label_records (
                local_path TEXT PRIMARY KEY,
                is_labeled INTEGER DEFAULT 0,
                label_data TEXT,
                dataset_type TEXT DEFAULT 'train',
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def save_mapping(self, local_path, remote_key):
        # 保存路径映射时，由于 algo 尚不明确，我们先存入一条 algo 为空的基础记录，或仅作为路径占位
        self.cursor.execute('INSERT OR IGNORE INTO file_mapping (local_path, remote_key, algo) VALUES (?, ?, "base")',
                            (local_path, remote_key))
        self.conn.commit()

    def update_metadata(self, local_path, algo, hash_val, w, h, size):
        try:
            # 获取该路径已有的 remote_key（跨算法共用）
            self.cursor.execute("SELECT remote_key FROM file_mapping WHERE local_path = ? LIMIT 1", (local_path,))
            row = self.cursor.fetchone()
            rk = row[0] if row else None

            self.cursor.execute('''
                INSERT OR REPLACE INTO file_mapping (local_path, remote_key, algo, hash, width, height, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?)                    
            ''', (local_path, rk, algo, hash_val, w, h, size))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"SQL写入失败: {e}")
            return False

    def get_info(self, local_path):
        # 核心修正：确保获取的是包含元数据（宽高）的记录，避免查到 base 占位记录
        self.cursor.execute(
            "SELECT remote_key, hash, width, height, file_size FROM file_mapping WHERE local_path = ? AND hash IS NOT NULL LIMIT 1",
            (local_path,))

        return self.cursor.fetchone()

    def remove_mapping(self, local_path):
        self.cursor.execute("DELETE FROM file_mapping WHERE local_path = ?", (local_path,))
        self.conn.commit()

    def get_known_hashes_by_algo(self, algo):
        self.cursor.execute("SELECT local_path, hash FROM file_mapping WHERE algo = ? AND hash IS NOT NULL", (algo,))
        return {row[0]: row[1] for row in self.cursor.fetchall()}

    def clear_all_hashes(self, algo=None):
        # 核心修正：支持按算法清理，不影响另一种模型的缓存
        if algo:
            self.cursor.execute("DELETE FROM file_mapping WHERE algo = ?", (algo,))
        else:
            self.cursor.execute("DELETE FROM file_mapping")
        self.conn.commit()

    # --- 标注模块专用逻辑 ---

    def get_unlabeled_images(self, folder_path):
        """
        获取待标注图片列表（完全解耦版）。
        逻辑：扫描磁盘物理文件，排除数据库 label_records 中已完成标注的记录。
        """
        import os
        valid_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
        unlabeled_files = []

        # 1. 从数据库获取所有已标注的路径集合（为了查询效率，使用 set）
        self.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        labeled_set = {os.path.normpath(row[0]) for row in self.cursor.fetchall()}

        # 2. 遍历磁盘文件夹
        if not os.path.exists(folder_path):
            print(f"警告：工作目录不存在 -> {folder_path}")
            return []

        for root, dirs, files in os.walk(folder_path):
            # 任务 9：全量黑名单隔离，排除所有中间产物目录
            blacklist = ['_backup']
            dirs[:] = [d for d in dirs if d not in blacklist and not d.startswith('yolo_dataset')]

            for f in files:
                if f.lower().endswith(valid_exts):
                    # 归一化路径，确保与数据库存储的路径格式一致
                    full_path = os.path.normpath(os.path.abspath(os.path.join(root, f)))

                    # 3. 如果不在“已标注”集合中，则加入待标注队列
                    if full_path not in labeled_set:
                        unlabeled_files.append(full_path)

        return unlabeled_files

    def save_label(self, local_path, label_data, w, h, size):
        """持久化标注结果"""
        try:
            self.cursor.execute('''
                INSERT OR REPLACE INTO label_records (local_path, is_labeled, label_data, width, height, file_size)
                VALUES (?, 1, ?, ?, ?, ?)
            ''', (local_path, label_data, w, h, size))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"标注存入失败: {e}")
            return False

    def get_labeled_images(self, folder_path):
        """获取已标注的图片列表"""
        import os
        self.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        all_labeled = [row[0] for row in self.cursor.fetchall()]
        # 过滤出属于当前工作目录的
        return [os.path.normpath(p) for p in all_labeled if p.startswith(os.path.normpath(folder_path))]

    def reset_label(self, local_path):
        """取消标注：从数据库抹除记录"""
        self.cursor.execute("DELETE FROM label_records WHERE local_path = ?", (local_path,))
        self.conn.commit()

    def clean_orphaned_labels(self):
        """清理孤儿记录：删除那些磁盘上已经不存在的标注记录"""
        import os
        self.cursor.execute("SELECT local_path FROM label_records")
        all_paths = [row[0] for row in self.cursor.fetchall()]

        orphans = []
        for p in all_paths:
            if not os.path.exists(p):
                orphans.append(p)

        if orphans:
            # 批量删除不存在的路径记录
            self.cursor.executemany("DELETE FROM label_records WHERE local_path = ?", [(p,) for p in orphans])
            self.conn.commit()
            return len(orphans)
        return 0

    def get_all_class_counts(self):
        """核心统计：穿透所有标注记录的 JSON，计算各类别实例总数"""
        counts = {}
        try:
            # 1. 只查询已标注的记录
            self.cursor.execute("SELECT label_data FROM label_records WHERE is_labeled = 1")
            rows = self.cursor.fetchall()

            for row in rows:
                label_str = row[0]
                if not label_str: continue

                # 2. 解析 JSON 数据: [[cls_id, [box]], [cls_id, [box]]...]
                labels = json.loads(label_str)
                for item in labels:
                    cls_id = int(item[0])  # 类别 ID
                    counts[cls_id] = counts.get(cls_id, 0) + 1

            return counts
        except Exception as e:
            print(f"统计类别失败: {e}")
            return {}
