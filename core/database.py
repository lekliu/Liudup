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