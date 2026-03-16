import os
import time
import shutil
from collections import deque
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QPushButton, QFileDialog, QLabel, QScrollArea, QFrame,
                             QMessageBox, QSpinBox, QCheckBox, QProgressBar, QPlainTextEdit,
                             QSplitter, QComboBox, QListView, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QTextCursor, QFont

from utils.config_manager import load_config, save_config
from core.remote_storage import MinioManager
from core.scanner import ImageScanner
from core.database import DatabaseManager
from ui.components.image_card import ImageCard
from utils.flow_layout import FlowLayout
from PyQt5.QtWidgets import QListView


class WorkerThread(QThread):
    """【全量恢复】原始异步执行引擎，包含完整信号与 ETA 算法"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, str)
    eta_signal = pyqtSignal(str)
    result_signal = pyqtSignal(dict, dict)
    finished_signal = pyqtSignal()

    def __init__(self, task_type, config, method, threshold, is_full):
        super().__init__()
        self.task_type = task_type
        self.config = config
        self.method = method
        self.threshold = threshold
        self.is_full = is_full
        self.db = DatabaseManager()
        self.time_history = deque(maxlen=11)

    def run(self):
        try:
            if self.task_type == "sync":
                self.do_sync()
            else:
                scanner = ImageScanner(method=self.method)

                def on_scanner_progress(curr, total, fname):
                    now = time.time()
                    self.time_history.append(now)
                    eta_text = "计算中..."
                    if len(self.time_history) > 1:
                        intervals = [self.time_history[i] - self.time_history[i - 1] for i in
                                     range(1, len(self.time_history))]
                        avg_interval = sum(intervals) / len(intervals)
                        remaining = total - curr
                        eta_total_seconds = int(avg_interval * remaining)
                        m, s = divmod(eta_total_seconds, 60)
                        h, m = divmod(m, 60)
                        eta_text = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

                    update_step = max(1, total // 100)
                    if curr % update_step == 0 or curr == total:
                        self.progress_signal.emit(curr, total, f"正在分析素材: {fname}")
                        self.eta_signal.emit(f"预计剩余时间: {eta_text}")
                    self.log_signal.emit(f"[{curr}/{total}] AI 推理中: {fname}")

                res, met = scanner.find_duplicates_with_metrics(
                    self.config['local_path'], self.db, self.threshold, self.is_full,
                    progress_callback=on_scanner_progress,
                    log_callback=self.log_signal.emit
                )
                self.result_signal.emit(res, met)
        except Exception as e:
            self.log_signal.emit(f"❌ 线程异常: {e}")
        finally:
            self.finished_signal.emit()

    def do_sync(self):
        """【全量恢复】Minio 同步与本地数据库映射逻辑"""
        self.log_signal.emit("🌐 开始执行：拉取并清理素材模式...")
        minio = MinioManager()
        bucket, local_dir = self.config['bucket_name'], self.config['local_path']
        if not os.path.exists(local_dir): os.makedirs(local_dir)
        try:
            keys = minio.list_images(bucket)
            total = len(keys)
            if total == 0:
                self.log_signal.emit(f"☁️ 远程桶 '{bucket}' 为空")
                return
            success_count = 0
            for i, k in enumerate(keys):
                safe_name = k.replace("/", "_")
                lp = os.path.normpath(os.path.join(local_dir, safe_name))
                if not os.path.exists(lp):
                    minio.s3.download_file(bucket, k, lp)
                    self.log_signal.emit(f"⏬ 已下载 [{i + 1}/{total}]: {k}")
                if os.path.exists(lp):
                    if minio.delete_image(bucket, k):
                        self.db.save_mapping(lp, k)
                        success_count += 1
            self.log_signal.emit(f"✅ 同步完成: 成功拉取并清理了 {success_count} 张素材")
        except Exception as e:
            self.log_signal.emit(f"❌ 同步失败: {e}")


class CleanerPage(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.config = load_config()
        self.db = db_manager
        self.current_results = {}
        self.initUI()

    def initUI(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)

        # =============================================================================
        # 1. 顶部配置面板 (重构为业界通用的工具栏风格)
        # =============================================================================
        header = QFrame()
        header.setObjectName("ConfigHeader")
        # --- 核心修复 1：设置垂直方向不拉伸 ---
        header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header.setMinimumHeight(135)
        header.setStyleSheet("""
            QFrame#ConfigHeader {
                background-color: #ffffff;
                border-bottom: 1px solid #e4e7ed;
                border-radius: 12px;
            }
            QLabel { 
            font-size: 18px; 
            color: #303133; 
            font-weight: bold; 
        }
            QLineEdit, QComboBox, QSpinBox {
                border: 2px solid #dcdfe6;
                border-radius: 6px;
                padding: 5px 12px;
                background: white;
                height: 40px;
                font-size: 18px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #409eff;
            }
            QPushButton#ActionBtn {
                height: 42px;
                padding: 0 25px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 20px;
            }




/* 3. 下拉列表容器（关键！） */
QComboBox QAbstractItemView {
    border: 1px solid #dcdfe6;
    background-color: #ffffff;
    selection-background-color: #409eff; /* 选中时的蓝色背景 */
    outline: none;
}

/* 4. 下拉列表中的每一项 */
QComboBox QAbstractItemView::item {
    min-height: 35px; /* 增加项高度 */
    color: #606266;   /* 强制默认文字为深灰色 */
    background-color: #ffffff;
}

/* 5. 鼠标悬停/选中状态（解决消失问题的核心） */
QComboBox QAbstractItemView::item:selected {
    background-color: #409eff;
    color: #ffffff;   /* 强制选中时文字为白色 */
}
        """)

        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(25, 20, 25, 20)  # 充足的留白
        h_layout.setSpacing(18)

        # 第一行：数据源与引擎配置
        r1 = QHBoxLayout()
        r1.setSpacing(15)

        # AI引擎组
        r1.addWidget(QLabel("AI 引擎:"))
        self.model_sel = QComboBox()
        self.model_sel.setView(QListView())

        self.model_sel.addItems(["移动端优化 (CNN)", "深度语义增强 (DINOv2)"])
        self.model_sel.setFixedWidth(220)
        current_model = self.config.get("model_type", "cnn")
        self.model_sel.setCurrentIndex(1 if current_model == "vit" else 0)
        self.model_sel.currentIndexChanged.connect(self.save_cfg)
        r1.addWidget(self.model_sel)

        r1.addSpacing(10)

        # 路径组
        r1.addWidget(QLabel("工作目录:"))
        self.path_input = QLineEdit(self.config.get("local_path", ""))
        self.path_input.setPlaceholderText("选择本地素材存放路径...")
        r1.addWidget(self.path_input)

        btn_br = QPushButton(" 浏览 ")
        btn_br.setObjectName("ActionBtn")
        btn_br.setStyleSheet("background: #f4f4f5; color: #606266; border: 1px solid #dcdfe6;")
        btn_br.setCursor(Qt.PointingHandCursor)
        btn_br.clicked.connect(self.browse)
        r1.addWidget(btn_br)

        h_layout.addLayout(r1)

        # 第二行：参数设置与操作按钮
        r2 = QHBoxLayout()
        r2.setSpacing(15)

        # Minio桶组
        r2.addWidget(QLabel("远程桶名:"))
        self.bucket_input = QLineEdit(self.config.get("bucket_name", ""))
        self.bucket_input.setFixedWidth(160)
        r2.addWidget(self.bucket_input)

        r2.addSpacing(10)

        # 相似度组
        r2.addWidget(QLabel("容差阈值:"))
        self.th_spin = QSpinBox()
        self.th_spin.setRange(0, 100)
        self.th_spin.setValue(5)
        self.th_spin.setSuffix(" %")
        self.th_spin.setFixedWidth(90)
        r2.addWidget(self.th_spin)

        self.full_check = QCheckBox("全量重算特征")
        self.full_check.setStyleSheet("margin-left: 10px; color: #606266;")
        r2.addWidget(self.full_check)

        r2.addStretch()

        # 动作按钮组
        self.btn_sync = QPushButton("🔄 同步远程")
        self.btn_sync.setObjectName("ActionBtn")
        self.btn_sync.setCursor(Qt.PointingHandCursor)
        self.btn_sync.setStyleSheet("""
            QPushButton { background: white; color: #409eff; border: 1px solid #b3d8ff; }
            QPushButton:hover { background: #ecf5ff; }
        """)

        self.btn_analyze = QPushButton("⚡ 开始分析")
        self.btn_analyze.setObjectName("ActionBtn")
        self.btn_analyze.setCursor(Qt.PointingHandCursor)
        self.btn_analyze.setStyleSheet("""
            QPushButton { background: #67c23a; color: white; border: none; }
            QPushButton:hover { background: #85ce61; }
            QPushButton:pressed { background: #5daf34; }
        """)

        self.btn_toggle_log = QPushButton("📋 日志")
        self.btn_toggle_log.setObjectName("ActionBtn")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setChecked(True)
        self.btn_toggle_log.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_log.setStyleSheet("""
            QPushButton { background: white; color: #909399; border: 1px solid #dcdfe6; }
            QPushButton:checked { background: #fdf6ec; color: #e6a23c; border-color: #f5dab1; }
        """)
        self.btn_toggle_log.clicked.connect(self.toggle_log_panel)

        r2.addWidget(self.btn_sync)
        r2.addWidget(self.btn_analyze)
        r2.addWidget(self.btn_toggle_log)
        h_layout.addLayout(r2)
        main_layout.addWidget(header)

        # --- 2. 中间主体区域 (Splitter 隔离区) ---
        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setStyleSheet("QSplitter::handle { background: #ebeef5; width: 1px; }")

        # 左侧：流式列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: 1px solid #e4e7ed; background: #f5f7fa; border-radius: 4px; }")
        self.container = QWidget()
        self.g_layout = QVBoxLayout(self.container)
        self.g_layout.setAlignment(Qt.AlignTop)
        self.g_layout.setContentsMargins(15, 15, 15, 15)
        self.scroll.setWidget(self.container)
        self.content_splitter.addWidget(self.scroll)

        # 右侧：日志与批量操作面板
        self.right_panel = QFrame()
        self.right_panel.setFixedWidth(320)
        self.right_panel.setStyleSheet("background: #ffffff; border-left: 1px solid #e4e7ed;")
        p_layout = QVBoxLayout(self.right_panel)
        p_layout.setContentsMargins(16, 16, 16, 16)
        p_layout.setSpacing(12)

        # 进度指示
        progress_group = QVBoxLayout()
        progress_group.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar { 
                background: #f5f7fa; border-radius: 4px; text-align: center; color: #303133; font-weight: bold; height: 18px; border: none;
            }
            QProgressBar::chunk { background-color: #409eff; border-radius: 4px; }
        """)
        self.eta_label = QLabel("预计剩余: --:--")
        self.eta_label.setStyleSheet("color: #909399; font-size: 20px;")
        self.status_label = QLabel("状态: 等待指令")
        self.status_label.setStyleSheet("color: #606266; font-size: 20px;")

        progress_group.addWidget(QLabel("🚀 执行进度"))
        progress_group.addWidget(self.progress_bar)
        progress_group.addWidget(self.eta_label)
        progress_group.addWidget(self.status_label)
        p_layout.addLayout(progress_group)

        p_layout.addSpacing(10)

        # 统计与批量
        self.lbl_total = QLabel("照片总数: 0")
        self.lbl_total.setStyleSheet("font-weight: bold; color: #303133; font-size: 20px;")
        self.lbl_stat = QLabel("相似组数: 0")
        self.lbl_stat.setStyleSheet("font-weight: bold; color: #303133; font-size: 20px;")
        self.btn_batch_del = QPushButton("⚡ 一键保留最高质量图")
        self.btn_batch_del.setFixedHeight(40)
        self.btn_batch_del.setEnabled(False)
        self.btn_batch_del.setStyleSheet("""
            QPushButton { background: #f56c6c; color: white; font-weight: bold; border-radius: 4px; border: none; }
            QPushButton:hover { background: #f78989; }
            QPushButton:disabled { background: #fab6b6; }
        """)
        self.btn_batch_del.clicked.connect(self.batch_keep_best)

        p_layout.addWidget(self.lbl_total)
        p_layout.addWidget(self.lbl_stat)
        p_layout.addWidget(self.btn_batch_del)

        # 日志区
        p_layout.addSpacing(10)
        p_layout.addWidget(QLabel("📝 实时日志"))
        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFont(QFont("Consolas", 10))
        self.log_console.setStyleSheet("""
            QPlainTextEdit { 
                background: #f5f7fa; border: 1px solid #e4e7ed; border-radius: 4px; color: #606266; padding: 8px;
            }
        """)
        p_layout.addWidget(self.log_console)

        self.content_splitter.addWidget(self.right_panel)
        self.content_splitter.setStretchFactor(0, 1)
        main_layout.addWidget(self.content_splitter)

        self.btn_sync.clicked.connect(lambda: self.run_task("sync"))
        self.btn_analyze.clicked.connect(lambda: self.run_task("analyze"))

    # --- 逻辑恢复区 ---

    def toggle_log_panel(self, checked):
        self.right_panel.setVisible(checked)

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "选择目录")
        if p: self.path_input.setText(p); self.save_cfg()

    def save_cfg(self):
        self.config["local_path"] = self.path_input.text()
        self.config["bucket_name"] = self.bucket_input.text()
        self.config["model_type"] = "cnn" if self.model_sel.currentIndex() == 0 else "vit"
        save_config(self.config)

    def append_log(self, text):
        self.log_console.appendPlainText(text)
        if self.log_console.blockCount() > 100:
            cursor = self.log_console.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.select(QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        self.log_console.moveCursor(QTextCursor.End)

    def update_progress(self, curr, total, status):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(curr)
        self.status_label.setText(status)

    def run_task(self, t):
        self.save_cfg()
        self.progress_bar.setValue(0)
        self.btn_sync.setEnabled(False)
        self.btn_analyze.setEnabled(False)

        self.worker = WorkerThread(t, self.config, self.config.get("model_type", "cnn"), self.th_spin.value(),
                                   self.full_check.isChecked())
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.eta_signal.connect(self.eta_label.setText)
        if t == "analyze": self.worker.result_signal.connect(self.update_results_ui)
        self.worker.finished_signal.connect(lambda: [self.btn_sync.setEnabled(True), self.btn_analyze.setEnabled(True),
                                                     self.status_label.setText("✅ 任务已完成")])
        self.worker.start()

    def update_results_ui(self, res, met):
        """【流式布局渲染】保留所有质量对比逻辑"""
        self.current_results = res
        total_files = met.get('total_files', 0)
        self.lbl_total.setText(f"照片总数: {total_files}")
        self.lbl_stat.setText(f"相似组数: {len(res)}")
        self.btn_batch_del.setEnabled(len(res) > 0)
        for i in reversed(range(self.g_layout.count())):
            if (w := self.g_layout.itemAt(i).widget()): w.setParent(None)

        for i, (master_path, info) in enumerate(res.items()):
            group_paths = [master_path] + info['dups']
            max_tol = info['max_tol']

            # 计算最佳图片
            scored = []
            for p in group_paths:
                mi = self.db.get_info(os.path.normpath(p))
                scored.append(
                    {'path': p, 'area': (mi[2] * mi[3] if mi else 0), 'size': (mi[4] if mi else 0), 'meta': mi})
            scored.sort(key=lambda x: (x['area'], x['size']), reverse=True)
            best_p = scored[0]['path']

            box = QFrame()
            box.setStyleSheet(
                "background: #fdfdfd; border: none; border-radius: 12px; margin-bottom: 20px;")
            v_layout = QVBoxLayout(box)

            t_row = QHBoxLayout()
            title = QLabel(f"相似组 #{i + 1} ({len(group_paths)}张) - 容差: {max_tol}%")
            title.setStyleSheet(f"font-weight: bold; color: {'#67c23a' if max_tol < 1.0 else '#e6a23c'};")
            btn_kb = QPushButton("👑保留组内最佳")
            btn_kb.setFixedSize(180, 50)
            btn_kb.setCursor(Qt.PointingHandCursor)
            btn_kb.setStyleSheet("""
                QPushButton {
                    background: #67c23a; color: white; 
                    border-radius: 6px; 
                    font-weight: bold; 
                    font-size: 20px;
                }
                QPushButton:hover { background: #85ce61; }
            """)
            btn_kb.clicked.connect(
                lambda chk, mp=master_path, dp=info['dups'], bx=box: self.keep_best_in_group(mp, dp, bx))

            t_row.addWidget(title)
            t_row.addStretch()
            t_row.addWidget(btn_kb)
            v_layout.addLayout(t_row)

            # 流式布局区域
            container = QWidget()
            flow = FlowLayout(container, spacing=15)
            for item in scored:
                card = ImageCard(item['path'], item['meta'][0] if item['meta'] else None, item['meta'], group_paths,
                                 self.on_del, is_best=(item['path'] == best_p))
                flow.addWidget(card)
            v_layout.addWidget(container)
            self.g_layout.addWidget(box)

    def on_del(self, lp, rk, w):
        """【逻辑恢复】物理备份、Minio同步删除、数据库清理"""
        try:
            backup_dir = os.path.join(self.config['local_path'], "_backup")
            if not os.path.exists(backup_dir): os.makedirs(backup_dir)
            target = os.path.join(backup_dir, os.path.basename(lp))
            if os.path.exists(lp): shutil.move(lp, target)
            if rk: MinioManager().delete_image(self.config['bucket_name'], rk)
            self.db.remove_mapping(lp)

            # UI局部刷新
            p_box = w.parentWidget()
            w.setParent(None)
            if p_box.layout().count() < 2: p_box.parentWidget().setParent(None)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除失败: {e}")

    def batch_keep_best(self):
        """【逻辑恢复】一键保留所有组的最佳图"""
        if not self.current_results: return
        to_move = []
        for master, info in self.current_results.items():
            scored = []
            for p in [master] + info['dups']:
                mi = self.db.get_info(os.path.normpath(p))
                scored.append({'path': p, 'score': (mi[2] * mi[3] if mi else 0)})
            scored.sort(key=lambda x: x['score'], reverse=True)
            for item in scored[1:]: to_move.append(item['path'])

        if QMessageBox.warning(self, "批量确认", f"将移动 {len(to_move)} 个副本至 _backup，是否继续？",
                               QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            backup_dir = os.path.join(self.config['local_path'], "_backup")
            if not os.path.exists(backup_dir): os.makedirs(backup_dir)
            for lp in to_move:
                if os.path.exists(lp):
                    shutil.move(lp, os.path.join(backup_dir, os.path.basename(lp)))
                    self.db.remove_mapping(lp)
            self.update_results_ui({}, {'total_files': 0})

    def keep_best_in_group(self, master, duplicates, container):
        all_members = [master] + duplicates
        scored = []
        for p in all_members:
            mi = self.db.get_info(os.path.normpath(p))
            scored.append({'path': p, 'area': (mi[2] * mi[3] if mi else 0), 'size': (mi[4] if mi else 0)})
        scored.sort(key=lambda x: (x['area'], x['size']), reverse=True)
        losers = scored[1:]

        if QMessageBox.question(self, "组内清理确认", "将保留该组最佳图片并移动副本，是否继续？",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            backup_dir = os.path.join(self.config['local_path'], "_backup")
            if not os.path.exists(backup_dir): os.makedirs(backup_dir)
            for l in losers:
                if os.path.exists(l['path']):
                    shutil.move(l['path'], os.path.join(backup_dir, os.path.basename(l['path'])))
                    self.db.remove_mapping(l['path'])
            container.setParent(None)