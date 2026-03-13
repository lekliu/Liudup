import os
import time
import shutil
import re
import traceback
from collections import deque
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QPushButton, QFileDialog, QLabel, QScrollArea, QFrame,
                             QMessageBox, QSpinBox, QCheckBox, QProgressBar, QPlainTextEdit, QSplitter, QComboBox, QListView)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QTextCursor, QFont

from utils.config_manager import load_config, save_config
from core.remote_storage import MinioManager
from core.scanner import ImageScanner
from core.database import DatabaseManager
from ui.components import ImageCard


class WorkerThread(QThread):
    # 增强型信号系统
    log_signal = pyqtSignal(str)  # 控制台日志
    progress_signal = pyqtSignal(int, int, str)  # 进度条信号 (当前, 总数, 状态)
    eta_signal = pyqtSignal(str)  # 预计剩余时间信号
    result_signal = pyqtSignal(dict, dict)  # 分析结果信号
    finished_signal = pyqtSignal()  # 任务完成信号

    def __init__(self, task_type, config, method, threshold, is_full):
        super().__init__()
        self.task_type = task_type
        self.config = config
        self.method = method
        self.threshold = threshold
        self.is_full = is_full
        self.db = DatabaseManager()

        # ETA 平滑计算：存储最近 11 个时间戳用于计算 10 个时间间隔
        self.time_history = deque(maxlen=11)

    def run(self):
        try:
            if self.task_type == "sync":
                self.do_sync()
            else:
                scanner = ImageScanner(method=self.method)

                # 定义进度回调逻辑
                def on_scanner_progress(curr, total, fname):
                    now = time.time()
                    self.time_history.append(now)

                    # 1. 计算 ETA (方案 B: 基于最近 10 张图的移动平均)
                    eta_text = "计算中..."
                    if len(self.time_history) > 1:
                        # 计算时间间隔的平均值
                        intervals = [self.time_history[i] - self.time_history[i - 1] for i in
                                     range(1, len(self.time_history))]
                        avg_interval = sum(intervals) / len(intervals)
                        remaining_count = total - curr
                        eta_total_seconds = int(avg_interval * remaining_count)

                        m, s = divmod(eta_total_seconds, 60)
                        h, m = divmod(m, 60)
                        if h > 0:
                            eta_text = f"{h:02d}:{m:02d}:{s:02d}"
                        else:
                            eta_text = f"{m:02d}:{s:02d}"

                    # 2. UI 性能优化：按 1% 的步长发送 UI 更新信号
                    update_step = max(1, total // 100)
                    if curr % update_step == 0 or curr == total:
                        self.progress_signal.emit(curr, total, f"正在分析素材: {fname}")
                        self.eta_signal.emit(f"预计剩余时间: {eta_text}")

                    # 日志始终外发，行数由主线程控制
                    self.log_signal.emit(f"[{curr}/{total}] AI 推理中: {fname}")

                # 执行核心分析
                res, met = scanner.find_duplicates_with_metrics(
                    self.config['local_path'], self.db, self.threshold, self.is_full,
                    progress_callback=on_scanner_progress,
                    log_callback=self.log_signal.emit
                )
                # 任务核心逻辑执行完毕，强制更新 UI 状态
                self.progress_signal.emit(100, 100, "分析处理完成")
                self.eta_signal.emit("预计剩余: 00:00")
                
                print(f"[DEBUG] 准备发射结果信号: res类型={type(res)}, 元素数={len(res)}")
                if len(res) > 0:
                    first_key = list(res.keys())[0]
                    print(f"[DEBUG] 数据样本: Key={first_key}, Value={res[first_key]}")

                self.result_signal.emit(res, met)  # <-- 怀疑崩溃发生在此行
                print("[DEBUG] 信号发射成功！")



        except Exception as e:
            self.log_signal.emit(f"❌ 线程执行异常: {e}")
        finally:
            self.finished_signal.emit()

    def do_sync(self):
        self.log_signal.emit("🌐 开始执行：拉取并清理素材模式...")
        minio = MinioManager()
        bucket, local_dir = self.config['bucket_name'], self.config['local_path']
        if not os.path.exists(local_dir): os.makedirs(local_dir)

        try:
            keys = minio.list_images(bucket)
        except Exception as e:
            self.log_signal.emit(f"❌ 无法读取远程桶: {e}")
            return

        total = len(keys)
        if total == 0:
            self.log_signal.emit(f"☁️ 远程桶 '{bucket}' 为空，无需同步。")
            return

        success_count = 0
        for i, k in enumerate(keys):
            safe_name = k.replace("/", "_")
            lp = os.path.normpath(os.path.join(local_dir, safe_name))

            try:
                # 逻辑：下载远程文件（如果本地没有）
                if not os.path.exists(lp):
                    minio.s3.download_file(bucket, k, lp)
                    self.log_signal.emit(f"⏬ 已下载 [{i + 1}/{total}]: {k}")

                # 逻辑：本地确认存在后，删除远程文件实现“清理”
                if os.path.exists(lp):
                    if minio.delete_image(bucket, k):
                        self.db.save_mapping(lp, k)
                        success_count += 1
            except Exception as e:
                self.log_signal.emit(f"❌ 处理素材 {k} 失败: {e}")

        self.log_signal.emit(f"✅ 处理完成: 成功拉取并清理了 {success_count} 张素材")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.db = DatabaseManager()
        self.initUI()

    def initUI(self):

        self.setWindowTitle("Liudup v2.9.0 - 专业级 AI 图片去重")
        # 解决核心问题：全局提升字体大小至 12pt，并加重至 DemiBold 权重
        global_font = QFont("Microsoft YaHei", 12, QFont.DemiBold)
        self.setFont(global_font)

        # 全局提升基础字体大小
        self.setFont(QFont("Microsoft YaHei", 12))
        self.setStyleSheet("""
            QMainWindow { background: #ffffff; }
            QLabel, QLineEdit, QPushButton, QSpinBox, QCheckBox, QPlainTextEdit, QComboBox {
                font-size: 20px;
            }
            QComboBox {
                min-height: 50px;
                padding: 1px 15px; /* 增加左侧间距让文字不贴边 */
                border: 1px solid #dcdfe6;
                border-radius: 4px;
                background: white;
            }
            /* 下拉列表容器 */
            QComboBox QListView {
                font-size: 20px;
                background-color: white;
                outline: none;
                border: 1px solid #dcdfe6;
            }
            /* 针对每一行的物理高度控制 */
            QComboBox QListView::item {
                min-height: 50px;
            }
        """)

        cw = QWidget()
        self.setCentralWidget(cw)
        main_layout = QVBoxLayout(cw)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. 顶部配置面板
        header = QFrame()
        header.setStyleSheet("background: white; border-bottom: 1px solid #dcdfe6; border-radius: 8px;")
        # 最小化修改：强制限制顶部面板高度为 130 像素
        header.setFixedHeight(160)

        h_layout = QVBoxLayout(header)

        r1 = QHBoxLayout()
        self.model_sel = QComboBox()
        # 核心修正：显式设置视图为 QListView，解决 Windows 渲染 Bug
        self.model_sel.setView(QListView())
        self.model_sel.setFixedWidth(400)
        self.model_sel.addItems(["移动端优化 (CNN)", "深度语义增强 (DINOv2)"])

        # 1. 确保初始化时根据配置文件设置正确索引
        # 注意：我们将 DINOv2 对应的值定义为 "vit"
        current_model = self.config.get("model_type", "cnn")
        self.model_sel.setCurrentIndex(1 if current_model == "vit" else 0)

        # 2. 【新增】添加切换信号：只要用户改了选项，就自动保存配置
        self.model_sel.currentIndexChanged.connect(self.save_cfg)

        r1.addWidget(QLabel("AI引擎:"))
        r1.addWidget(self.model_sel)

        self.path_input = QLineEdit(self.config.get("local_path", ""))
        self.path_input.setMinimumHeight(45)
        self.path_input.setMaximumWidth(600)  # 限制最大宽度，防止满屏拉伸
        btn_br = QPushButton("选择文件夹")
        btn_br.clicked.connect(self.browse)
        r1.addWidget(QLabel("图片路径:"))
        r1.addWidget(self.path_input)
        r1.addWidget(btn_br)
        r1.addStretch()  # 强制靠左对齐，解决按钮在天边的问题
        h_layout.addLayout(r1)

        r2 = QHBoxLayout()
        self.bucket_input = QLineEdit(self.config.get("bucket_name", ""))
        self.bucket_input.setMaximumWidth(250) # 限制桶名称输入框宽度
        self.bucket_input.setMinimumHeight(40)
        self.th_spin = QSpinBox()
        self.th_spin.setRange(0, 100)
        self.th_spin.setValue(5)
        self.th_spin.setMinimumHeight(40)
        self.full_check = QCheckBox("全量重算特征")
        self.btn_sync = QPushButton("🔄 同步远程")
        self.btn_analyze = QPushButton("⚡ 开始分析")
        self.btn_toggle_log = QPushButton("📋 显示日志")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setChecked(True)
        self.btn_toggle_log.clicked.connect(self.toggle_log_panel)

        btn_style = "font-weight: 800; padding: 8px 20px; border-radius: 6px; border: 1px solid #dcdfe6;"
        self.btn_sync.setStyleSheet("background: #f0f7ff; color: #0078d4; " + btn_style)
        self.btn_analyze.setStyleSheet("background: #f6ffed; color: #52c41a; " + btn_style)
        self.btn_toggle_log.setStyleSheet("background: #fffbe6; color: #faad14; " + btn_style)

        r2.addWidget(QLabel("Minio桶:"))
        r2.addWidget(self.bucket_input)
        r2.addWidget(QLabel(" 相似容差:"))
        r2.addWidget(self.th_spin)
        r2.addWidget(self.full_check)
        r2.addStretch()
        r2.addWidget(self.btn_sync)
        r2.addWidget(self.btn_analyze)
        r2.addWidget(self.btn_toggle_log)
        h_layout.addLayout(r2)
        main_layout.addWidget(header)

        # 2. 中间主体区域 (使用 QSplitter 分割左右)
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setStyleSheet("QSplitter::handle { background: #f0f0f0; width: 2px; }")

        # 2.1 左侧画廊区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: 1px solid #dcdfe6; background: #fafafa; border-radius: 4px; }")
        self.container = QWidget()
        self.g_layout = QVBoxLayout(self.container)
        self.g_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.container)
        content_splitter.addWidget(self.scroll)

        # 2.2 右侧状态与日志面板 (亮色主题)
        self.right_panel = QFrame()
        self.right_panel.setFixedWidth(350)
        self.right_panel.setStyleSheet("background: #ffffff; border-left: 1px solid #dcdfe6;")
        p_layout = QVBoxLayout(self.right_panel)
        p_layout.setContentsMargins(10, 0, 0, 0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #d9d9d9; border-radius: 6px; text-align: center; color: #333; background: #f5f5f5; height: 22px;  font-weight: bold; }
            QProgressBar::chunk { background-color: #1890ff; }
        """)
        self.progress_bar.setValue(0)
        p_layout.addWidget(QLabel("🚀 执行进度:"))
        p_layout.addWidget(self.progress_bar)

        self.eta_label = QLabel("预计剩余: --:--")
        self.eta_label.setStyleSheet("color: #fa8c16; font-weight: 900; font-family: Consolas; margin-top: 10px;")
        p_layout.addWidget(self.eta_label)

        self.status_label = QLabel("状态: 等待指令")
        self.status_label.setStyleSheet("color: #262626; font-weight: bold; margin-top: 8px; border-bottom: 2px solid #f0f0f0; padding-bottom: 8px;")
        p_layout.addWidget(self.status_label)


        # 新增统计信息展示
        self.lbl_total_files = QLabel("图片总数: 0")
        self.lbl_total_files.setStyleSheet("color: #2c3e50; font-weight: bold; margin-top: 5px;")
        p_layout.addWidget(self.lbl_total_files)

        self.lbl_total_groups = QLabel("相似组数: 0")
        self.lbl_total_groups.setStyleSheet("color: #e67e22; font-weight: bold; margin-top: 5px;")
        p_layout.addWidget(self.lbl_total_groups)

        self.btn_batch_del = QPushButton("⚡ 一键保留最高质量图")
        self.btn_batch_del.setFixedHeight(50)
        self.btn_batch_del.setStyleSheet("""
            QPushButton { background-color: #f5222d; color: white; font-weight: 800; border-radius: 6px; margin-top: 10px; }
            QPushButton:hover { background-color: #cf1322; }
            QPushButton:disabled { background-color: #ffa39e; }
        """)
        self.btn_batch_del.setEnabled(False)
        self.btn_batch_del.clicked.connect(self.batch_keep_best)
        p_layout.addWidget(self.btn_batch_del)

        log_title = QLabel("📝 实时日志:")
        log_title.setStyleSheet("margin-top: 10px; font-size: 20px;")
        p_layout.addWidget(log_title)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFont(QFont("Consolas", 12))
        self.log_console.setStyleSheet("""
            QPlainTextEdit { background-color: #ffffff; color: #434343; border: 1px solid #d9d9d9; border-radius: 6px; padding: 8px; }
            """)
        p_layout.addWidget(self.log_console)

        content_splitter.addWidget(self.right_panel)
        main_layout.addWidget(content_splitter)

        self.btn_sync.clicked.connect(lambda: self.run_task("sync"))
        self.btn_analyze.clicked.connect(lambda: self.run_task("analyze"))

    def toggle_log_panel(self, checked):
         self.right_panel.setVisible(checked)
         self.btn_toggle_log.setText("📋 显示日志" if not checked else "📁 隐藏日志")

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "选择目录")
        if p: self.path_input.setText(p); self.save_cfg()

    def save_cfg(self):
        self.config["local_path"] = self.path_input.text()
        self.config["bucket_name"] = self.bucket_input.text()
        self.config["model_type"] = "cnn" if self.model_sel.currentIndex() == 0 else "vit"

        save_config(self.config)
        print(f"[Debug] 配置已保存，当前模型类型: {self.config['model_type']}")

    def append_log(self, text):
        """核心逻辑：支持 70 行上限的日志追加"""
        self.log_console.appendPlainText(text)
        if self.log_console.blockCount() > 70:
            cursor = self.log_console.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.select(QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 删除多余的换行符
        self.log_console.moveCursor(QTextCursor.End)

    def update_progress_ui(self, curr, total, status):
        """更新进度条和状态文字"""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(curr)
        self.status_label.setText(f"🔥 {status}")

    def run_task(self, t):
        self.save_cfg()
        # 重置 UI 状态
        self.progress_bar.setValue(0)
        self.eta_label.setText("预计剩余时间: 计算中...")
        self.log_console.clear()
        self.btn_sync.setEnabled(False);
        self.btn_analyze.setEnabled(False)

        self.worker = WorkerThread(t, self.config, self.config.get("model_type", "cnn"), self.th_spin.value(),
                                   self.full_check.isChecked())

        # 信号连接
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.update_progress_ui)
        self.worker.eta_signal.connect(self.eta_label.setText)

        if t == "analyze":
            self.worker.result_signal.connect(self.update_ui)

        self.worker.finished_signal.connect(lambda: [
            self.btn_sync.setEnabled(True),
            self.btn_analyze.setEnabled(True),
            self.status_label.setText("✅ 任务执行完毕")
        ])
        self.worker.start()

    def update_ui(self, res, met):
        self.current_results = res  # 保存结果供一键清理使用
        self.btn_batch_del.setEnabled(len(res) > 0)

        # 动态更新统计数据
        self.lbl_total_files.setText(f"图片总数: {met.get('total_files', 0)}")
        self.lbl_total_groups.setText(f"相似组数: {len(res)}")

        # 清除旧结果
        for i in reversed(range(self.g_layout.count())):
            if (w := self.g_layout.itemAt(i).widget()): w.setParent(None)

        if not res:
            msg = QLabel("\n\n未发现重复素材。提示：若觉得相似但未检出，请调大“相似容差”。")
            msg.setAlignment(Qt.AlignCenter);
            self.g_layout.addWidget(msg)
            return

        for i, (master_path, info) in enumerate(res.items()):
            dup_paths = info['dups']
            max_tol = info['max_tol']
            group_paths = [master_path] + dup_paths

            box = QFrame()
            box.setStyleSheet("background: white; border: 1px solid #dcdfe6; border-radius: 12px; margin-bottom: 15px;")
            bl = QVBoxLayout(box)

            # 组标题行布局
            title_row = QHBoxLayout()
            # 显式设置行高度，防止被下方图片区域压缩
            title_row.setContentsMargins(10, 5, 10, 5)

            title = QLabel(f"相似组 #{i + 1} ({len(group_paths)}张) - [最大容差: {max_tol}]")
            title.setStyleSheet("font-weight: bold; color: #e67e22; border: none; background: transparent;")

            btn_group_best = QPushButton("⚡ 保留此组最佳")
            btn_group_best.setMinimumWidth(220)
            btn_group_best.setMinimumHeight(50)
            btn_group_best.setStyleSheet("background: #fff7e6; color: #d46b08; border: 2px solid #ffd591; font-weight: bold;")

            btn_group_best.clicked.connect(
                lambda checked, mp=master_path, dp=dup_paths, bx=box: self.keep_best_in_group(mp, dp, bx)
            )

            title_row.addWidget(title)
            title_row.addSpacing(30)
            title_row.addWidget(btn_group_best)
            title_row.addStretch()
            bl.addLayout(title_row)

            hl = QHBoxLayout()
            hl.setAlignment(Qt.AlignLeft)
            hl.setSpacing(15)
            for p in group_paths:
                info = self.db.get_info(os.path.normpath(p))
                hl.addWidget(ImageCard(p, info[0] if info else None, info, group_paths, self.on_del))
            bl.addLayout(hl)
            self.g_layout.addWidget(box, 0, Qt.AlignLeft)

    def on_del(self, lp, rk, w):
        try:
            # 1. 移动到备份区
            backup_dir = os.path.join(self.config['local_path'], "_backup")
            if not os.path.exists(backup_dir): os.makedirs(backup_dir)
            target_p = os.path.join(backup_dir, os.path.basename(lp))
            if os.path.exists(target_p):
                target_p = os.path.join(backup_dir, f"{int(time.time())}_{os.path.basename(lp)}")
            if os.path.exists(lp): shutil.move(lp, target_p)

            # 2. 清理远程与数据库
            if rk: MinioManager().delete_image(self.config['bucket_name'], rk)
            self.db.remove_mapping(lp)

            # 3. 动态刷新组内状态
            p_box = w.parentWidget()
            w.setParent(None)

            title_label = p_box.layout().itemAt(0).widget()
            hbox_layout = p_box.layout().itemAt(1).layout()
            remaining_count = hbox_layout.count()

            if remaining_count < 2:
                p_box.setParent(None)  # 不再构成重复组，直接移除容器
            else:
                # 动态更新标题上的张数
                new_text = re.sub(r'\((\d+)张\)', f'({remaining_count}张)', title_label.text())
                title_label.setText(new_text)

        except Exception as e:
            QMessageBox.critical(self, "执行失败", str(e))

    def batch_keep_best(self):
        """核心业务：全局一键保留最高质量图"""
        if not self.current_results:
            return

        groups_count = len(self.current_results)
        to_move_list = []

        # 1. 预计算：按照 (分辨率 DESC, 文件大小 DESC) 筛选待清理文件
        for master, info in self.current_results.items():
            duplicates = info['dups']
            all_members = [master] + duplicates
            scored_members = []

            for p in all_members:
                info = self.db.get_info(os.path.normpath(p))
                # info: (remote_key, hash, width, height, file_size)
                w = info[2] if info else 0
                h = info[3] if info else 0
                sz = info[4] if info else 0
                scored_members.append({
                    'path': p,
                    'area': w * h,
                    'size': sz
                })

            # 排序：面积大者优先，其次大小大者优先
            scored_members.sort(key=lambda x: (x['area'], x['size']), reverse=True)

            # 胜出者是 [0]，其余全是 losers
            losers = scored_members[1:]
            for l in losers:
                to_move_list.append(l['path'])

        # 2. 弹窗确认
        keep_count = groups_count
        move_count = len(to_move_list)
        msg = f"检测到相似组：{groups_count} 组\n\n将保留：{keep_count} 张最高质量原图\n将移动：{move_count} 张普通副本至 _backup 目录\n\n是否立即执行批量清理？"

        if QMessageBox.warning(self, "批量清理确认", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                backup_dir = os.path.join(self.config['local_path'], "_backup")
                if not os.path.exists(backup_dir): os.makedirs(backup_dir)

                for lp in to_move_list:
                    if os.path.exists(lp):
                        target_p = os.path.join(backup_dir, os.path.basename(lp))
                        if os.path.exists(target_p):
                            target_p = os.path.join(backup_dir, f"{int(time.time())}_{os.path.basename(lp)}")
                        shutil.move(lp, target_p)
                        self.db.remove_mapping(lp)

                QMessageBox.information(self, "处理完成", f"已成功清理 {move_count} 张素材。")

                # 3. 刷新 UI
                self.current_results = {}
                self.btn_batch_del.setEnabled(False)
                for i in reversed(range(self.g_layout.count())):
                    if (w := self.g_layout.itemAt(i).widget()): w.setParent(None)
                self.status_label.setText(f"✅ 批量清理完成，已归档 {move_count} 个文件")

            except Exception as e:
                QMessageBox.critical(self, "批量处理失败", str(e))

    def keep_best_in_group(self, master, duplicates, container):
        """局部业务：保留当前组内最高质量图"""
        all_members = [master] + duplicates
        scored_members = []

        for p in all_members:
            info = self.db.get_info(os.path.normpath(p))
            w, h, sz = (info[2], info[3], info[4]) if info else (0, 0, 0)
            scored_members.append({'path': p, 'area': w * h, 'size': sz})

        # 排序：分辨率 > 大小
        scored_members.sort(key=lambda x: (x['area'], x['size']), reverse=True)

        winner = scored_members[0]
        losers = scored_members[1:]

        msg = f"该组共 {len(all_members)} 张图。\n\n将保留最佳图：\n{os.path.basename(winner['path'])}\n\n将移动其余 {len(losers)} 张至备份区。确认执行？"

        if QMessageBox.question(self, "组内清理确认", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                backup_dir = os.path.join(self.config['local_path'], "_backup")
                if not os.path.exists(backup_dir): os.makedirs(backup_dir)

                for l in losers:
                    lp = l['path']
                    if os.path.exists(lp):
                        target_p = os.path.join(backup_dir, os.path.basename(lp))
                        if os.path.exists(target_p):
                            target_p = os.path.join(backup_dir, f"{int(time.time())}_{os.path.basename(lp)}")
                        shutil.move(lp, target_p)
                        self.db.remove_mapping(lp)

                # 移除整个组的 UI 容器
                container.setParent(None)

                # 更新统计信息中的组数 (简单刷新显示，不重算)
                current_groups = int(re.search(r'\d+', self.lbl_total_groups.text()).group())
                self.lbl_total_groups.setText(f"相似组数: {max(0, current_groups - 1)}")

                self.status_label.setText(f"✅ 组内清理完成，保留了最佳图")

            except Exception as e:
                QMessageBox.critical(self, "组清理失败", str(e))