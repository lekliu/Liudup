import os
import json
import time
import shutil
import paramiko
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QLineEdit,
                             QTabWidget, QPushButton, QProgressBar, QPlainTextEdit, QMessageBox, QApplication)
from PyQt5.QtCore import Qt, QTimer, QDateTime, QThread, pyqtSignal
from utils.config_manager import load_config, save_config, ProjectPaths
from utils.dataset_utils import prepare_yolo_dataset
from core.remote_storage import MinioManager

class NotebookWorker(QThread):
    """任务 11：专门处理 Notebook 模式的异步打包与上传"""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, config, db, files):
        super().__init__()
        self.config = config
        self.db = db
        self.files = files

    def run(self):
        try:
            self.log_signal.emit("🛠 正在构建镜像数据集结构...")
            yaml_path = prepare_yolo_dataset(self.config['local_path'], self.files, self.config.get("classes", []), dir_name="yolo_dataset_nb")
            
            self.log_signal.emit("📦 正在生成物理压缩包 (ZIP)...")
            zip_path = shutil.make_archive("notebook_dataset", 'zip', os.path.dirname(yaml_path))
            
            self.log_signal.emit(f"⏫ 正在上传至 Minio 摆渡站 ({os.path.getsize(zip_path)/1024/1024:.1f} MB)...")
            minio = MinioManager()
            success = minio.upload_file(self.config['bucket_name'], zip_path, "cloud_notebook/dataset.zip")
            
            if os.path.exists(zip_path): os.remove(zip_path)
            self.finished_signal.emit(success)
        except Exception as e:
            self.log_signal.emit(f"❌ 处理异常: {str(e)}")
            self.finished_signal.emit(False)

class SSHWorker(QThread):
    """任务 4.2：处理局域网 SSH 核心同步与执行"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    metrics_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, cfg, dataset_path, classes):
        super().__init__()
        self.cfg = cfg
        self.dataset_path = dataset_path
        self.classes = classes
        self._stop_flag = False
        self.client = None

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            # 1. 建立连接
            self.log_signal.emit("📡 建立 SSH 安全连接...")
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(self.cfg['host'], username=self.cfg['user'], password=self.cfg['pass'], timeout=10)
            
            # 2. 执行增量同步
            sftp = self.client.open_sftp()
            remote_base = self.cfg['remote_path'].replace('\\', '/')
            self.log_signal.emit(f"🚀 开启增量同步至: {remote_base}...")
            
            self.sync_dir(sftp, self.dataset_path, remote_base + "/yolo_dataset")
            sftp.close()
            
            if self._stop_flag: return

            # 3. 启动远程训练指令
            self.log_signal.emit("🔥 远程环境自检成功，正在点火算力...")
            cmd = f"cd /d {self.cfg['remote_path']} && python -m ultralytics train data=yolo_dataset/data.yaml epochs=50 imgsz=640"
            
            # 使用 PTY 截获实时流日志
            stdin, stdout, stderr = self.client.exec_command(cmd, get_pty=True)
            
            for line in iter(stdout.readline, ""):
                if self._stop_flag:
                    self.client.exec_command(f"taskkill /F /IM python.exe /T") # 中止远程进程
                    break
                
                raw_line = line.strip()
                if raw_line:
                    self.log_signal.emit(f"[REMOTE] {raw_line}")
                    self.parse_yolo_output(raw_line)
            
            self.finished_signal.emit(True, "任务圆满完成")
            
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if self.client: self.client.close()

    def sync_dir(self, sftp, local_dir, remote_dir):
        """递归增量同步"""
        try:
            sftp.mkdir(remote_dir)
        except: pass

        for item in os.listdir(local_dir):
            lp = os.path.join(local_dir, item)
            rp = remote_dir + "/" + item
            
            if os.path.isdir(lp):
                self.sync_dir(sftp, lp, rp)
            else:
                # 增量比对：大小一致则跳过
                local_size = os.path.getsize(lp)
                try:
                    remote_stat = sftp.stat(rp)
                    if remote_stat.st_size == local_size:
                        continue
                except: pass
                
                sftp.put(lp, rp)

    def parse_yolo_output(self, line):
        """从日志流解析进度 (简单正则匹配示例)"""
        if "Epoch" in line and "/" in line:
            try:
                # 尝试抓取 Epoch 进度更新 UI
                parts = line.split()
                e_idx = parts.index("Epoch")
                curr, total = parts[e_idx+1].split('/')
                self.progress_signal.emit(int(curr), int(total))
            except: pass

NOTEBOOK_SCRIPT_TEMPLATE = """# Liudup Notebook 协作脚本 (Colab/Kaggle 专用)
import os, shutil
# 1. 环境准备
print("📦 正在安装依赖...")
!pip install ultralytics boto3
import boto3
from ultralytics import YOLO

# 2. 配置信息
cfg = {minio_cfg}
s3 = boto3.client('s3', endpoint_url=cfg['endpoint'], 
                  aws_access_key_id=cfg['ak'], aws_secret_access_key=cfg['sk'])

# 3. 下载数据集
print("⏬ 正在从摆渡站下载数据集...")
s3.download_file(cfg['bucket'], 'cloud_notebook/dataset.zip', 'dataset.zip')
if os.path.exists('yolo_dataset'): shutil.rmtree('yolo_dataset')
shutil.unpack_archive('dataset.zip', 'yolo_dataset')

# 4. 【增量逻辑】自动寻找历史权重
weight_to_use = 'yolov8n.pt'
try:
    print("🔄 正在检查云端是否存在上一次的训练结果...")
    s3.download_file(cfg['bucket'], 'cloud_notebook/best.pt', 'last_best.pt')
    if os.path.exists('last_best.pt'):
        weight_to_use = 'last_best.pt'
        print("✅ 成功发现历史权重，将基于此进行增量训练。")
except Exception:
    print("ℹ️ 未发现历史权重（这可能是第一次训练），将使用官方基准模型。")

# 5. 启动云端训练
print(f"🚀 正在以 {{weight_to_use}} 为起点启动训练...")
model = YOLO(weight_to_use)
results = model.train(data='yolo_dataset/data.yaml', epochs={epochs}, imgsz=640)

# 6. 训练结束，自动回传结果
best_path = str(results.save_dir / 'weights' / 'best.pt')
print(f"⏫ 训练完成，正在回传模型: {{best_path}}")
s3.upload_file(best_path, cfg['bucket'], 'cloud_notebook/best.pt')
print("✅ 回传成功！请回到 Liudup 点击『同步结果』按钮。")
"""

REMOTE_WORKER_TEMPLATE = """# Liudup Remote Worker v1.0
import os, time, json, shutil, boto3
from ultralytics import YOLO

# --- 自动填充的配置 ---
MINIO_CFG = {minio_cfg}

def update_progress(epoch, epochs, loss, mAP):
    s3 = boto3.client('s3', endpoint_url=MINIO_CFG['endpoint'], 
                      aws_access_key_id=MINIO_CFG['ak'], aws_secret_access_key=MINIO_CFG['sk'])
    data = {{"epoch": epoch, "epochs": epochs, "loss": round(loss, 4), "mAP": round(mAP, 4), "timestamp": time.time()}}
    s3.put_object(Bucket=MINIO_CFG['bucket'], Key='cloud_results/progress.json', Body=json.dumps(data))

def run_worker():
    print("🚀 Liudup 远程工兵启动，正在监听任务...")
    s3 = boto3.client('s3', endpoint_url=MINIO_CFG['endpoint'], 
                      aws_access_key_id=MINIO_CFG['ak'], aws_secret_access_key=MINIO_CFG['sk'])
    
    while True:
        try:
            # 1. 检查触发信号
            s3.head_object(Bucket=MINIO_CFG['bucket'], Key='cloud_pending/trigger.json')
            print("🎯 发现新任务！正在下载数据集...")
            
            # 2. 下载并解压
            s3.download_file(MINIO_CFG['bucket'], 'cloud_pending/dataset.zip', 'dataset.zip')
            if os.path.exists('yolo_dataset'): shutil.rmtree('yolo_dataset')
            shutil.unpack_archive('dataset.zip', 'yolo_dataset')
            
            # 3. 开启训练
            model = YOLO('yolov8n.pt')
            def on_epoch_end(trainer):
                met = trainer.metrics
                update_progress(trainer.epoch + 1, trainer.epochs, trainer.loss_items[0], met.get('metrics/mAP50(B)', 0))
            
            model.add_callback("on_fit_epoch_end", on_epoch_end)
            results = model.train(data='yolo_dataset/data.yaml', epochs=50, imgsz=640)
            
            # 4. 上传结果并清理
            s3.upload_file(str(results.save_dir / 'weights' / 'best.pt'), MINIO_CFG['bucket'], 'cloud_results/best.pt')
            s3.delete_object(Bucket=MINIO_CFG['bucket'], Key='cloud_pending/trigger.json')
            print("✅ 任务完成，已回传模型。")
        except:
            time.sleep(10)

if __name__ == "__main__": run_worker()
"""


class CloudPage(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self.config = load_config()
        self.worker = None
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("☁️ 云端工厂 - 算力协同中心")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #2c3e50; margin-bottom: 15px;")
        layout.addWidget(title)

        # 使用 Tab 页区分不同的云端方案
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab { height: 50px; width: 300px; font-size: 18px; font-weight: bold; }")

        # --- 方案一：Minio 全自动同步 ---
        self.tab_minio = self.setup_auto_sync_ui()
        self.tab_ssh = self.setup_ssh_ui()
        self.tab_notebook = self.setup_notebook_ui()

        self.tabs.addTab(self.tab_minio, "⚡ 算力摆渡 (Minio 全自动)")
        self.tabs.addTab(self.tab_ssh, "⚡ 局域网协同 (SSH 直连)")
        self.tabs.addTab(self.tab_notebook, "☁️ 云端协作 (Notebook 模式)")

        layout.addWidget(self.tabs)


    def setup_auto_sync_ui(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        # 1. 核心原理与指导区
        guide = QFrame()
        guide.setStyleSheet("background: #fdf6ec; border: 1px solid #f5dab1; border-radius: 12px;")
        g_lay = QHBoxLayout(guide)
        g_lay.setContentsMargins(20, 20, 20, 20)

        # 左侧文案：原理与功能
        info_text = QLabel(
            "<b>⚙️ 工作原理：</b><br>"
            "利用 Minio 作为算力中继站。本地端推送数据集与启动信号，远程 GPU 端自动感应并开始训练，"
            "训练指标通过心跳文件实时回传，实现物理隔离环境下的全自动生产。<br><br>"
            "<b>📦 脚本功能：</b><br>"
            "• 自动解压与环境自检 &nbsp; • 支持断点续传检测<br>"
            "• 每轮次指标自动上报 &nbsp; • 训练结束自动回传 best.pt"
        )
        info_text.setStyleSheet("color: #606266; font-size: 16px; line-height: 1.6;")

        # 右侧按钮：部署指导
        deploy_box = QVBoxLayout()
        deploy_label = QLabel("<b>🛠 远程工兵部署：</b>")
        deploy_label.setStyleSheet("color: #e6a23c; font-size: 18px;")
        deploy_tips = QLabel("1. 安装 Python3.8+, CUDA<br>2. pip install ultralytics boto3")
        deploy_tips.setStyleSheet("color: #8a6d3b; font-size: 15px;")

        self.btn_copy_script = QPushButton("📄 复制远程工兵脚本 (.py)")
        self.btn_copy_script.setFixedSize(280, 50)
        self.btn_copy_script.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.btn_copy_script.clicked.connect(self.copy_worker_script)

        deploy_box.addWidget(deploy_label)
        deploy_box.addWidget(deploy_tips)
        deploy_box.addWidget(self.btn_copy_script)

        g_lay.addWidget(info_text, 3)
        g_lay.addLayout(deploy_box, 1)
        layout.addWidget(guide)

        # 2. 控制台
        ctrl = QFrame()
        c_lay = QHBoxLayout(ctrl)
        self.btn_run = QPushButton("🚀 开启全自动同步训练")
        self.btn_run.setFixedHeight(60)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setStyleSheet("background: #67c23a; color: white; font-weight: bold; font-size: 22px; border-radius: 8px;")
        self.btn_run.clicked.connect(self.start_auto_pipeline)

        self.lbl_pipeline_status = QLabel("流水线就绪")
        self.lbl_pipeline_status.setStyleSheet("font-weight: bold; color: #909399; font-size: 20px;")

        c_lay.addWidget(self.btn_run, 2)
        c_lay.addWidget(self.lbl_pipeline_status, 1, Qt.AlignCenter)
        layout.addWidget(ctrl)

        # 3. 监控看板
        self.prog_bar = QProgressBar()
        self.prog_bar.setStyleSheet("QProgressBar { height: 35px; text-align: center; font-size: 16px; font-weight: bold; }")
        self.lbl_metrics = QLabel("远程指标: Epoch --/-- | Loss: -- | mAP: --")
        self.lbl_metrics.setStyleSheet("font-size: 22px; font-weight: bold; color: #409eff;")

        layout.addWidget(self.prog_bar)
        layout.addWidget(self.lbl_metrics, 0, Qt.AlignCenter)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background: #1e1e1e; color: #dcdcdc; font-family: Consolas;")
        layout.addWidget(self.log_view)

        return page

    def setup_ssh_ui(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        ssh_cfg = self.config.get("ssh_node", {})

        # 1. 原理与部署要求
        guide = QFrame()
        guide.setStyleSheet("background: #e1f5fe; border: 1px solid #81d4fa; border-radius: 12px;")
        g_lay = QHBoxLayout(guide)
        g_lay.setContentsMargins(10, 10, 10, 10)
        
        info_text = QLabel(
            "<b>⚙️ 工作原理：</b><br>"
            "通过 SSH 隧道直连局域网算力机。本地执行增量数据同步（SFTP），远程端直接调用原生 Python "
            "或 WSL 环境进行计算。支持管道实时拦截输出，实现零延迟日志回传。<br><br>"
            "<b>📦 核心功能：</b><br>"
            "• 局域网 P2P 秒级同步 &nbsp; • 流式日志实时回传<br>"
            "• 远程显卡一键探测 &nbsp; • 支持原生与 WSL 环境"
        )
        info_text.setStyleSheet("color: #01579b; font-size: 16px; line-height: 1.6;")
        
        deploy_box = QVBoxLayout()
        deploy_label = QLabel("<b>🛠 远程 SSH 部署：</b>")
        deploy_label.setStyleSheet("color: #0288d1; font-size: 18px;")
        deploy_tips = QLabel("1. 开启 OpenSSH Server<br>2. 安装 Ultralytics 库")
        deploy_tips.setStyleSheet("color: #039be5; font-size: 15px;")
        deploy_box.addWidget(deploy_label)
        deploy_box.addWidget(deploy_tips)
        
        g_lay.addWidget(info_text, 3)
        g_lay.addLayout(deploy_box, 1)
        layout.addWidget(guide)

        # 2. 连接配置面板
        config_panel = QFrame()
        config_panel.setStyleSheet("background: #ffffff; border: 1px solid #dcdfe6; border-radius: 8px; padding: 10px;")
        cp_lay = QHBoxLayout(config_panel)
        
        self.ssh_host = QLineEdit(ssh_cfg.get("host", "192.168."))
        self.ssh_user = QLineEdit(ssh_cfg.get("user", "administrator"))
        self.ssh_pass = QLineEdit(ssh_cfg.get("pass", ""))
        self.ssh_pass.setEchoMode(QLineEdit.Password)
        self.ssh_path = QLineEdit(ssh_cfg.get("remote_path", "D:/liudup_remote"))
        
        for label, widget in [("主机:", self.ssh_host), ("用户:", self.ssh_user), 
                             ("密码:", self.ssh_pass), ("路径:", self.ssh_path)]:
            cp_lay.addWidget(QLabel(label))
            cp_lay.addWidget(widget)
            widget.textChanged.connect(self.save_ssh_config)

        self.btn_test_ssh = QPushButton("🔍 探测 GPU")
        self.btn_test_ssh.setFixedSize(150, 40)
        self.btn_test_ssh.setStyleSheet("background: #409eff; color: white; font-weight: bold;")
        self.btn_test_ssh.clicked.connect(self.test_ssh_connection)
        cp_lay.addWidget(self.btn_test_ssh)
        layout.addWidget(config_panel)

        # 3. 控制台与监控 (修复：将之前错位的代码移入此处)
        self.btn_run_ssh = QPushButton("🚀 开启局域网同步训练")
        self.btn_run_ssh.setFixedHeight(60)
        self.btn_run_ssh.setCursor(Qt.PointingHandCursor)
        self.btn_run_ssh.setStyleSheet("background: #67c23a; color: white; font-weight: bold; font-size: 22px; border-radius: 8px;")
        self.btn_run_ssh.clicked.connect(self.start_ssh_pipeline)
        layout.addWidget(self.btn_run_ssh)

        self.prog_bar_ssh = QProgressBar()
        self.prog_bar_ssh.setStyleSheet("QProgressBar { height: 35px; text-align: center; font-size: 16px; font-weight: bold; }")
        self.lbl_metrics_ssh = QLabel("远程指标: Epoch --/-- | Loss: -- | mAP: --")
        self.lbl_metrics_ssh.setStyleSheet("font-size: 22px; font-weight: bold; color: #409eff;")
        
        layout.addWidget(self.prog_bar_ssh)
        layout.addWidget(self.lbl_metrics_ssh, 0, Qt.AlignCenter)

        self.log_view_ssh = QPlainTextEdit()
        self.log_view_ssh.setReadOnly(True)
        self.log_view_ssh.setStyleSheet("background: #1e1e1e; color: #dcdcdc; font-family: Consolas; font-size: 16px; padding: 10px;")
        layout.addWidget(self.log_view_ssh)

        return page

    def setup_notebook_ui(self):
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)

        # 1. 原理与指南
        guide = QFrame()
        guide.setStyleSheet("background: transparent; border: none;")
        g_lay = QHBoxLayout(guide)
        g_lay.setContentsMargins(20, 20, 20, 20)
        
        info_text = QLabel(
            "<p style='margin-bottom:8px;'><b style='color: #111827; font-size: 18px;'>⚙️ 工作原理：</b></p>"
            "<span style='color: #374151;'>专为 Google Colab 等网页算力设计。本地打包推送数据集至 Minio，用户在云端粘贴执行生成的 Python "
            "脚本。训练结束后，脚本自动将结果回传至 Minio，本地一键拉回。</span><br><br>"
            "<p style='margin-bottom:8px;'><b style='color: #111827; font-size: 18px;'>📦 核心功能：</b></p>"
            "<span style='color: #374151;'>• 云端适配器：自动注入 Minio 凭据 &nbsp; • 环境全自动：内置依赖安装与解压<br>"
            "• 结果闭环：云端模型自动回传，本地毫秒级回收</span>"
        )
        info_text.setStyleSheet("font-size: 16px; line-height: 1.8;")
        
        g_lay.addWidget(info_text)
        layout.addWidget(guide)

        # 2. 协作控制台 (1-2-3 步骤)
        steps_panel = QFrame()
        steps_panel.setStyleSheet("background: #ffffff; border: 1px solid #dcdfe6; border-radius: 8px; padding: 15px;")
        s_lay = QHBoxLayout(steps_panel)
        s_lay.setSpacing(20)

        # 定义按钮动态样式
        btn_style = """
            QPushButton { 
                background: #ffffff; border: 1px solid #dcdfe6; border-radius: 6px; 
                font-weight: bold; font-size: 16px; color: #606266;
            }
            QPushButton:hover { background: #ecf5ff; border-color: #b3d8ff; color: #409eff; }
            QPushButton:pressed { background: #409eff; color: #ffffff; }
            QPushButton:disabled { background: #f5f7fa; color: #c0c4cc; }
        """

        self.btn_nb_push = QPushButton("📦 1. 打包并推送数据集")
        self.btn_nb_copy = QPushButton("📋 2. 复制训练脚本")
        self.btn_nb_sync = QPushButton("🔄 3. 同步训练结果")

        for btn in [self.btn_nb_push, self.btn_nb_copy, self.btn_nb_sync]:
            btn.setFixedHeight(50)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.PointingHandCursor)
            s_lay.addWidget(btn)

        self.btn_nb_push.clicked.connect(self.export_for_notebook)
        self.btn_nb_copy.clicked.connect(self.copy_notebook_script)
        self.btn_nb_sync.clicked.connect(self.sync_notebook_results)
        layout.addWidget(steps_panel)

        # 3. 日志与状态
        self.log_view_nb = QPlainTextEdit()
        self.log_view_nb.setReadOnly(True)
        self.log_view_nb.setStyleSheet("background: #1e1e1e; color: #dcdcdc; font-family: Consolas; font-size: 16px; padding: 10px;")
        layout.addWidget(self.log_view_nb)

        return page

    def export_for_notebook(self):
        self.log_view_nb.clear()
        self.db.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        files = [row[0] for row in self.db.cursor.fetchall()]
        if not files: return QMessageBox.warning(self, "错误", "无标注数据")
        
        self.btn_nb_push.setEnabled(False)
        self.btn_nb_push.setText("⌛ 处理中...")
        
        self.nb_worker = NotebookWorker(self.config, self.db, files)
        self.nb_worker.log_signal.connect(lambda msg: self.log_view_nb.appendPlainText(msg))
        self.nb_worker.finished_signal.connect(self.on_notebook_export_finished)
        self.nb_worker.start()

    def on_notebook_export_finished(self, success):
        self.btn_nb_push.setEnabled(True)
        self.btn_nb_push.setText("📦 1. 打包并推送数据集")
        if success:
            self.log_view_nb.appendPlainText("✅ 数据全量推送成功！请点击第 2 步。")
            QMessageBox.information(self, "成功", "数据集已送达云端暂存区！")
        else:
            self.log_view_nb.appendPlainText("❌ 推送失败，请检查网络或 Minio 连接")

    def copy_notebook_script(self):
        cfg = load_config()
        minio_data = {"endpoint": cfg.get("minio_endpoint"), "ak": cfg.get("minio_access_key"), 
                      "sk": cfg.get("minio_secret_key"), "bucket": cfg.get("bucket_name")}
        script = NOTEBOOK_SCRIPT_TEMPLATE.format(minio_cfg=json.dumps(minio_data, indent=4), epochs=50)
        QApplication.clipboard().setText(script)
        QMessageBox.information(self, "已复制", "Notebook 专用脚本已存入剪贴板！\n请在 Colab 单元格中粘贴并运行。")

    def sync_notebook_results(self):
        self.log_view_nb.appendPlainText("📡 正在检查云端产出...")
        minio = MinioManager()
        local_res_dir = os.path.join(ProjectPaths.RUNS_DIR, "notebook_results")
        if not os.path.exists(local_res_dir): os.makedirs(local_res_dir)
        
        target_pt = os.path.join(local_res_dir, "best.pt")
        try:
            minio.s3.download_file(self.config['bucket_name'], "cloud_notebook/best.pt", target_pt)
            self.log_view_nb.appendPlainText(f"🏆 云端模型已回收：{target_pt}")
            QMessageBox.information(self, "同步成功", f"云端模型已成功降落至：\n{target_pt}")
        except:
            self.log_view_nb.appendPlainText("❌ 尚未发现云端产出，请确认云端脚本是否运行完毕。")

    def start_ssh_pipeline(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_run_ssh.setText("🚀 开启局域网同步训练")
            self.btn_run_ssh.setStyleSheet("background: #67c23a; color: white; font-weight: bold; font-size: 22px;")
            return

        # 1. 准备本地数据集 (隔离路径)
        self.log_view_ssh.clear()
        self.db.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        files = [row[0] for row in self.db.cursor.fetchall()]
        if not files: return QMessageBox.warning(self, "错误", "没有已标注的数据")
        
        yolo_path = prepare_yolo_dataset(self.config['local_path'], files, self.config.get("classes", []), dir_name="yolo_dataset_ssh")
        
        # 2. 启动异步工兵
        self.worker = SSHWorker(self.config['ssh_node'], os.path.dirname(yolo_path), self.config.get("classes", []))
        self.worker.log_signal.connect(lambda msg: self.log_view_ssh.appendPlainText(msg))
        self.worker.progress_signal.connect(self.update_ssh_progress)
        self.worker.finished_signal.connect(self.on_ssh_finished)
        
        self.btn_run_ssh.setText("🛑 中止局域网训练")
        self.btn_run_ssh.setStyleSheet("background: #e74c3c; color: white; font-weight: bold; font-size: 22px;")
        self.worker.start()

    def save_ssh_config(self):
        self.config["ssh_node"] = {
            "host": self.ssh_host.text(),
            "user": self.ssh_user.text(),
            "pass": self.ssh_pass.text(),
            "remote_path": self.ssh_path.text()
        }
        save_config(self.config)

    def test_ssh_connection(self):
        self.log_view_ssh.appendPlainText(f"[{QDateTime.currentDateTime().toString('HH:mm:ss')}] 📡 正在尝试连接 {self.ssh_host.text()}...")
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(self.ssh_host.text(), username=self.ssh_user.text(), password=self.ssh_pass.text(), timeout=5)
            stdin, stdout, stderr = client.exec_command('nvidia-smi --query-gpu=gpu_name,memory.total --format=csv,noheader')
            gpu_info = stdout.read().decode().strip()
            client.close()
            self.log_view_ssh.appendPlainText(f"✅ 连接成功！探测到算力: {gpu_info}")
            QMessageBox.information(self, "连接成功", f"远程 GPU 探测成功：\n{gpu_info}")
        except Exception as e:
            self.log_view_ssh.appendPlainText(f"❌ 连接失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"远程 SSH 连接失败:\n{str(e)}")
    def append_log(self, txt):
        self.log_view.appendPlainText(f"[{QDateTime.currentDateTime().toString('HH:mm:ss')}] {txt}")

    def copy_worker_script(self):
        cfg = load_config()
        minio_data = {
            "endpoint": cfg.get("minio_endpoint"),
            "ak": cfg.get("minio_access_key"),
            "sk": cfg.get("minio_secret_key"),
            "bucket": cfg.get("bucket_name")
        }
        script = REMOTE_WORKER_TEMPLATE.format(minio_cfg=json.dumps(minio_data, indent=4))
        QApplication.clipboard().setText(script)
        QMessageBox.information(self, "成功", "远程工兵脚本已复制到剪贴板！")

    def start_auto_pipeline(self):
        self.log_view.clear()
        self.append_log("🛠 启动自动化流水线...")

        # 1. 打包
        self.lbl_pipeline_status.setText("📦 正在打包...")
        self.db.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        files = [row[0] for row in self.db.cursor.fetchall()]
        if not files: return self.append_log("❌ 错误: 无标注数据")

        yaml_path = prepare_yolo_dataset(self.config['local_path'], files, self.config.get("classes", []), dir_name="yolo_dataset_auto")
        dataset_dir = os.path.dirname(yaml_path)
        zip_path = shutil.make_archive("cloud_dataset", 'zip', dataset_dir)

        # 2. 上传
        self.append_log(f"⏫ 正在上传数据集 ({os.path.getsize(zip_path)/1024/1024:.1f} MB)...")
        self.lbl_pipeline_status.setText("⏫ 正在上传...")
        minio = MinioManager()
        bucket = self.config.get("bucket_name")

        if minio.upload_file(bucket, zip_path, "cloud_pending/dataset.zip"):
            # 上传触发信号
            trigger_data = {"epochs": 50, "timestamp": time.time()}
            with open("trigger.json", "w") as f: json.dump(trigger_data, f)
            minio.upload_file(bucket, "trigger.json", "cloud_pending/trigger.json")
            self.append_log("🚀 信号已发送，远程工兵即将开工！")
            self.lbl_pipeline_status.setText("🛰 远程训练中...")
            self.monitor_timer.start(20000) # 每20秒轮询
            self.last_update_ts = time.time()

        if os.path.exists(zip_path): os.remove(zip_path)

    def poll_cloud_progress(self):
        minio = MinioManager()
        bucket = self.config.get("bucket_name")
        temp_json = "progress_temp.json"

        try:
            minio.s3.download_file(bucket, "cloud_results/progress.json", temp_json)
            with open(temp_json, "r") as f: data = json.load(f)

            self.prog_bar.setMaximum(data['epochs'])
            self.prog_bar.setValue(data['epoch'])
            self.lbl_metrics.setText(f"远程指标: Epoch {data['epoch']}/{data['epochs']} | Loss: {data['loss']} | mAP: {data['mAP']}")
            self.last_update_ts = data['timestamp']

            # 超时检测（10分钟）
            if time.time() - self.last_update_ts > 600:
                self.lbl_pipeline_status.setText("⚠️ 远程连接超时")
                self.lbl_pipeline_status.setStyleSheet("color: red; font-weight: bold;")
                self.append_log("🔎 警告: 远程进度已停止更新超过10分钟，请检查GPU机器！")
            else:
                self.lbl_pipeline_status.setStyleSheet("color: #67c23a; font-weight: bold;")

        except:
            self.append_log("📡 正在等待远程工兵响应...")