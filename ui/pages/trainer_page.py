import os
import time
import pandas as pd
from PyQt5.QtGui import QTextCursor, QPixmap
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QFrame, QSpinBox, QProgressBar, QPlainTextEdit, QMessageBox, QCheckBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QAction)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint
import pyqtgraph as pg

from ui.components.image_preview_dialog import ImagePreviewDialog
from core.trainer_core import TrainingWorker
from core.remote_storage import MinioManager
from utils.dataset_utils import prepare_yolo_dataset
from utils.config_manager import load_config, ProjectPaths


# --- 【任务 14 新增】：支持双击事件的标签类 ---
class ClickableLabel(QLabel):
    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class TrainerPage(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self.config = load_config()
        self.results_path = ProjectPaths.RESULTS_CSV
        # --- 【任务 4 新增】：内存数据缓存 ---
        self.x_data = []
        self.y_box_loss = []
        self.y_cls_loss = []
        self.y_dfl_loss = []
        self.y_map = []
        self.y_precision = []
        self.y_recall = []
        self.y_lr = []
        self.smooth_weight = 0.0  # 任务 9：平滑权重缓存
        self.last_epoch_metrics = {} # 任务 11：锁存最后一轮数据用于总结
        self.last_best_model = None
        self.initUI()

        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self.update_plots)

    def get_btn_style(self, active_color):
        """【业务级 UI 契约】强制全局按钮在所有状态下保持高度、字体、边框的绝对一致"""
        return f"""
            QPushButton {{
                background-color: {active_color};
                color: white;
                font-family: 'Microsoft YaHei';
                font-size: 18px;
                font-weight: bold;
                height: 40px;
                border-radius: 4px;
                border: 1px solid transparent;
                padding: 0 15px;
            }}
            QPushButton:disabled {{
                background-color: #f5f7fa;
                border: 1px solid #e4e7ed;
                color: #c0c4cc;
            }}
            QPushButton:hover {{
                background-color: {active_color};
                opacity: 0.8;
            }}
        """

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. 顶部面板：配置、统计与发布
        top_row = QHBoxLayout()

        config_box = QFrame()
        config_box.setStyleSheet("background: white; border: 1px solid #dcdfe6; border-radius: 8px;")
        config_layout = QHBoxLayout(config_box)

        # --- 新增：增量训练勾选框 ---
        self.check_incremental = QCheckBox("增量训练")  # 文字大幅缩短
        self.check_incremental.setChecked(True)
        # 将原有的详细说明移入 Tooltip
        self.check_incremental.setToolTip(
            "<b>增量模式说明：</b><br>"
            "开启：基于上次产出的 <font color='#e67e22'>best.pt</font> 继续训练（适合微调优化）。<br>"
            "关闭：从官方基准权重重新开始（适合更换数据集或大幅修改类别时）。"
        )
        # 保持 16px 风格（确保样式表中没有覆盖它）
        self.check_incremental.setStyleSheet("color: #2980b9; font-weight: bold; margin-left: 10px; border: none;")
        
        self.spin_epochs = QSpinBox()
        self.spin_epochs.setRange(1, 1000);
        self.spin_epochs.setValue(50)
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 128);
        self.spin_batch.setValue(16)
        config_layout.addWidget(QLabel("迭代:"));
        config_layout.addWidget(self.spin_epochs)
        config_layout.addWidget(QLabel("批次:"));
        config_layout.addWidget(self.spin_batch)
        config_layout.addWidget(self.check_incremental)
        top_row.addWidget(config_box)

        self.lbl_stat = QLabel("标注数据: 0 张")
        self.lbl_stat.setStyleSheet("font-weight: bold; color: #2c3e50; font-size: 20px;")
        top_row.addStretch()
        top_row.addWidget(self.lbl_stat)

        # --- 操作按钮组 (统一规范版) ---
        btn_style_base = "border-radius: 4px; font-weight: bold; font-size: 14px;"
        disabled_style = "QPushButton:disabled { background: #f5f7fa; color: #c0c4cc; border: 1px solid #dcdfe6; }"

        # --- 【UI 规范化：核心全局样式定义】 ---
        self.btn_start = QPushButton("🔥 启动训练")
        self.btn_start.setMinimumWidth(130)
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setStyleSheet(self.get_btn_style("#e67e22"))  # 启动：橙色
        self.btn_start.clicked.connect(self.start_training_flow)
        top_row.addWidget(self.btn_start)

        # 增加一个【中止训练】按钮
        self.btn_stop = QPushButton("🛑 中止训练")
        self.btn_stop.setMinimumWidth(130)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setStyleSheet(self.get_btn_style("#f56c6c"))
        self.btn_stop.clicked.connect(self.stop_training)
        top_row.addWidget(self.btn_stop)

        # --- 任务 1：更多操作下拉菜单 ---
        self.btn_more = QPushButton("🛠 更多操作 ▼")
        self.btn_more.setMinimumWidth(160)
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.setStyleSheet(self.get_btn_style("#409eff"))
        self.more_menu = QMenu(self)
        self.action_publish = self.more_menu.addAction("☁️ 一键发布至 Minio")
        self.action_open = self.more_menu.addAction("📂 查看模型目录")
        self.action_export = self.more_menu.addAction("📱 导出手机模型 (ONNX/TFLite)")
        
        self.action_publish.triggered.connect(self.publish_to_cloud)
        self.action_open.triggered.connect(self.open_weights_folder)
        self.action_export.triggered.connect(self.export_mobile_model)
        
        # 初始禁用管理类操作
        self.action_publish.setEnabled(False)
        self.action_open.setEnabled(False)
        self.action_export.setEnabled(False)

        self.btn_more.setMenu(self.more_menu)
        top_row.addWidget(self.btn_more)

        layout.addLayout(top_row)

        # --- 【任务 2 新增】：指标大屏看板 ---
        self.stat_row = QHBoxLayout()
        self.stat_row.setSpacing(15)
        self.stat_cards = {}
        stats_config = [
            ("Epoch", "0/0", "#34495e"),
            ("mAP50", "0.000", "#27ae60"),
            ("Precision", "0.000", "#2980b9"),
            ("Recall", "0.000", "#f39c12"),
            ("Box Loss", "0.000", "#c0392b"),
            ("LR", "0.0000", "#00ced1")  # 任务 8：新增学习率看板
        ]
        for title, val, color in stats_config:
            card = QFrame()

            card.setFixedHeight(60)
            card.setStyleSheet(f"background: white; border: none")
            card_lay = QHBoxLayout(card)

            t_lbl = QLabel(title)
            t_lbl.setStyleSheet("font-size: 20px; color: #7f8c8d; font-weight: bold;")
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color};")

            card_lay.addWidget(t_lbl)
            card_lay.addStretch()
            card_lay.addWidget(v_lbl)

            self.stat_cards[title] = v_lbl

            self.stat_row.addWidget(card)

        layout.addLayout(self.stat_row)

        # --- 【任务 3 修正】：带高度保护的进度面板 ---
        progress_panel = QFrame()
        progress_panel.setMinimumHeight(110)  # 强制预留空间
        progress_panel.setStyleSheet("background: #ffffff; border: 2px solid #ebeef5; border-radius: 8px;")
        prog_layout = QVBoxLayout(progress_panel)
        self.epoch_bar = QProgressBar()
        self.epoch_bar.setFormat("总轮次进度: %v/%m (%p%)")
        self.epoch_bar.setStyleSheet("QProgressBar { height: 25px; text-align: center; font-weight: bold; }")

        self.lbl_eta = QLabel("预计剩余时间: --:--:--")  # 任务 6：新增时间标签
        self.lbl_eta.setStyleSheet("color: #e67e22; font-weight: bold; font-size: 18px; border: none;")

        self.batch_bar = QProgressBar()
        self.batch_bar.setFormat("当前 Batch 进度: %v/%m (%p%)")
        self.batch_bar.setStyleSheet(
            "QProgressBar { height: 15px; text-align: center; } QProgressBar::chunk { background-color: #3498db; }")
        prog_layout.addWidget(self.epoch_bar)
        prog_layout.addWidget(self.lbl_eta, 0, Qt.AlignCenter)  # 插入到两个进度条中间
        prog_layout.addSpacing(10)
        prog_layout.addWidget(self.batch_bar)

        # --- 【任务 5 新增】：多类别明细监控表 ---
        self.class_table = QTableWidget()
        self.class_table.setColumnCount(2)
        self.class_table.setHorizontalHeaderLabels(["监控类别", "mAP50"])
        self.class_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.class_table.setEditTriggers(QTableWidget.NoEditTriggers)  # 禁止编辑
        self.class_table.setStyleSheet("background: #ffffff; border: 1px solid #dcdfe6; border-radius: 8px;")
        self.class_table.setMinimumHeight(150)
        self.refresh_table_structure()  # 根据配置初始化行

        # 2. 可视化监控
        viz_layout = QHBoxLayout()

        # --- 【任务 10 布局】：左侧叠加容器 ---
        self.viz_stack = QFrame()
        self.viz_stack.setObjectName("VizStack")
        self.viz_stack.setStyleSheet(
            "QFrame#VizStack { background: white; border: 1px solid #dcdfe6; border-radius: 8px; }")
        viz_container_layout = QVBoxLayout(self.viz_stack)
        viz_container_layout.setContentsMargins(0, 0, 0, 0)
        viz_container_layout.setSpacing(0)

        # --- 【任务 15 新增】：视图切换工具栏 ---
        view_tab_bar = QFrame()
        view_tab_bar.setFixedHeight(40)
        view_tab_bar.setStyleSheet(
            "background: #f8f9fa; border-bottom: 1px solid #dcdfe6; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        tab_layout = QHBoxLayout(view_tab_bar)
        tab_layout.setContentsMargins(10, 0, 10, 0)

        self.btn_view_curves = QPushButton("📈 实时学习曲线")
        self.btn_view_report = QPushButton("🖼️ 官方诊断报告")
        tab_btn_css = """
            QPushButton { 
                padding: 5px 15px; border: none; border-radius: 4px; color: #606266; font-weight: bold; 
            }
            QPushButton:hover { background: #eef1f6; }
            QPushButton:checked { background: #3498db; color: white; }
        """
        self.btn_view_curves.setCheckable(True)
        self.btn_view_report.setCheckable(True)
        self.btn_view_curves.setStyleSheet(tab_btn_css)
        self.btn_view_report.setStyleSheet(tab_btn_css)
        self.btn_view_curves.setChecked(True)

        self.view_group = [self.btn_view_curves, self.btn_view_report]
        self.btn_view_curves.clicked.connect(lambda: self.switch_viz_view(0))
        self.btn_view_report.clicked.connect(lambda: self.switch_viz_view(1))

        tab_layout.addWidget(self.btn_view_curves)
        tab_layout.addWidget(self.btn_view_report)
        tab_layout.addStretch()
        viz_container_layout.addWidget(view_tab_bar)

        # 内容容器
        self.viz_content = QFrame()
        self.viz_stack_layout = QVBoxLayout(self.viz_content)
        self.viz_stack_layout.setContentsMargins(5, 5, 5, 5)

        # 2.1 实时折线图
        self.plot_widget = pg.PlotWidget(title="AI 学习曲线 (实时更新)")
        self.plot_widget.setBackground('#ffffff')
        self.plot_widget.showGrid(x=True, y=True)
        self.plot_widget.addLegend()

        # --- 【核心修改】：HUD 平滑度悬浮窗 ---
        from PyQt5.QtWidgets import QSlider
        self.smooth_hud = QFrame(self.plot_widget)
        self.smooth_hud.setObjectName("SmoothHUD")
        self.smooth_hud.setFixedSize(220, 36)
        self.smooth_hud.setStyleSheet("""
            QFrame#SmoothHUD {
                background-color: rgba(248, 249, 250, 200);
                border: 1px solid #dcdfe6;
                border-radius: 6px;
            }
            QLabel { border: none; background: transparent; font-size: 13px; color: #606266; }
        """)
        hud_layout = QHBoxLayout(self.smooth_hud)
        hud_layout.setContentsMargins(10, 0, 10, 0)

        self.slider_smooth = QSlider(Qt.Horizontal)
        self.slider_smooth.setRange(0, 99)
        self.slider_smooth.setValue(0)
        self.slider_smooth.setStyleSheet("QSlider::handle:horizontal { background: #3498db; }")
        self.slider_smooth.valueChanged.connect(self.on_smooth_changed)

        self.lbl_smooth_val = QLabel("0.00")
        self.lbl_smooth_val.setFixedWidth(35)
        self.lbl_smooth_val.setStyleSheet("color: #3498db; font-weight: bold;")

        hud_layout.addWidget(QLabel("🌊 平滑:"))
        hud_layout.addWidget(self.slider_smooth)
        hud_layout.addWidget(self.lbl_smooth_val)

        # 2.2 结果图片框 (训练完显示)
        self.res_image_label = ClickableLabel("训练完成后将在此展示诊断报告...")
        self.res_image_label.setAlignment(Qt.AlignCenter)
        self.res_image_label.setStyleSheet("background: #f0f2f5; border: none;")
        self.res_image_label.setCursor(Qt.PointingHandCursor)
        self.res_image_label.setScaledContents(False)  # 保持比例
        self.res_image_label.hide()  # 初始隐藏
        self.res_image_label.double_clicked.connect(self.on_results_double_clicked)
        self.last_results_png = None  # 存储结果图路径

        self.viz_stack_layout.addWidget(self.plot_widget)
        self.viz_stack_layout.addWidget(self.res_image_label)
        viz_container_layout.addWidget(self.viz_content)

        # --- 【任务 4 优化】：增加 symbol 确保单点可见 ---
        self.curve_box_loss = self.plot_widget.plot(name="Box Loss (定位)", pen=pg.mkPen('#e74c3c', width=2),
                                                    symbol='o', symbolSize=6)
        self.curve_cls_loss = self.plot_widget.plot(name="Cls Loss (分类)", pen=pg.mkPen('#9b59b6', width=2),
                                                    symbol='t', symbolSize=6)
        self.curve_dfl_loss = self.plot_widget.plot(name="DFL Loss (边界)", pen=pg.mkPen('#e67e22', width=1),
                                                    symbol='d', symbolSize=4)

        self.curve_map = self.plot_widget.plot(name="mAP50 (精度)", pen=pg.mkPen('#2ecc71', width=3),
                                               symbol='s', symbolSize=6, symbolBrush='#2ecc71')
        # 任务 8 视觉优化：图例注明缩放倍数
        self.curve_lr = self.plot_widget.plot(name="LR (x10000)", pen=pg.mkPen('#00ced1', width=1, style=Qt.DotLine),
                                              symbol='h', symbolSize=4)
        self.curve_precision = self.plot_widget.plot(name="Precision",
                                                     pen=pg.mkPen('#3498db', width=1, style=Qt.DashLine))
        self.curve_recall = self.plot_widget.plot(name="Recall", pen=pg.mkPen('#f1c40f', width=1, style=Qt.DashLine))

        # 右侧日志区 (改为浅色)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QPlainTextEdit { 
                background: #ffffff; color: #2c3e50; 
                font-family: 'Consolas'; font-size: 16px; 
                border: 1px solid #dcdfe6; border-radius: 8px;
            }
                """)
        # --- 【任务 3 UI 修正】：重新组织右侧列布局 ---
        # 重新组织右侧纵向布局
        right_col = QVBoxLayout()
        right_col.addWidget(progress_panel, 0)  # 进度条居上
        right_col.addWidget(self.class_table, 0)
        right_col.addWidget(self.log_view, 1)  # 日志填充剩余空间

        viz_layout.addWidget(self.viz_stack, 3)
        viz_layout.addLayout(right_col, 1)

        layout.addLayout(viz_layout)

    def refresh_status(self):
        self.db.cursor.execute("SELECT COUNT(*) FROM label_records WHERE is_labeled = 1")
        count = self.db.cursor.fetchone()[0]
        self.lbl_stat.setText(f"标注数据: {count} 张")
        self.btn_start.setEnabled(count > 0)
        self.refresh_table_structure()

    def resizeEvent(self, event):
        """动态调整 HUD 位置到右上角"""
        super().resizeEvent(event)
        if hasattr(self, 'smooth_hud') and self.plot_widget.isVisible():
            # 计算在 plot_widget 内部的右上角位置
            px = self.plot_widget.width() - self.smooth_hud.width() - 10
            self.smooth_hud.move(px, 10)

    def open_weights_folder(self):
        """任务 G1：调用系统资源管理器打开权重文件夹"""
        if self.last_best_model and os.path.exists(self.last_best_model):
            folder = os.path.dirname(self.last_best_model)
            os.startfile(folder)

    def switch_viz_view(self, index):
        """任务 15：切换视图显示"""
        self.btn_view_curves.setChecked(index == 0)
        self.btn_view_report.setChecked(index == 1)
        if hasattr(self, 'smooth_hud'):
            self.smooth_hud.setVisible(index == 0)

        self.plot_widget.setVisible(index == 0)
        self.res_image_label.setVisible(index == 1)

    def on_results_double_clicked(self):
        """任务 14：弹出高清预览对话框"""
        if self.last_results_png and os.path.exists(self.last_results_png):
            # 调用系统中已有的高清预览组件
            dialog = ImagePreviewDialog([self.last_results_png], 0, self)
            dialog.exec_()

    def refresh_table_structure(self):
        """任务 5：根据 config 刷新表格行数"""
        classes = self.config.get("classes", ["close", "skip", "return", "wait"])
        self.class_table.setRowCount(len(classes))
        for i, name in enumerate(classes):
            self.class_table.setItem(i, 0, QTableWidgetItem(name))
            # 初始化进度值为 0.0%
            val_item = QTableWidgetItem("0.0%")
            val_item.setTextAlignment(Qt.AlignCenter)
            self.class_table.setItem(i, 1, val_item)

    def append_log(self, text):
        """实时追加日志并自动滚动"""
        self.log_view.appendPlainText(text)
        self.log_view.moveCursor(QTextCursor.End)

    def handle_metrics(self, metrics):
        """任务 1 验证逻辑：确保数据通路畅通"""
        m_type = metrics.get("type")
        if m_type == "epoch":
            # 更新看板数值
            self.stat_cards["Epoch"].setText(f"{metrics.get('epoch')}/{metrics.get('epochs')}")
            self.stat_cards["mAP50"].setText(f"{metrics.get('map50', 0):.3f}")
            self.stat_cards["Precision"].setText(f"{metrics.get('precision', 0):.3f}")
            self.stat_cards["Recall"].setText(f"{metrics.get('recall', 0):.3f}")
            self.stat_cards["Box Loss"].setText(f"{metrics.get('box_loss', 0):.3f}")

            # --- 任务 5 完善：同步更新右侧类别明细表 ---
            class_data = metrics.get("class_data", {})
            for i in range(self.class_table.rowCount()):
                cls_name = self.class_table.item(i, 0).text()
                if cls_name in class_data:
                    score = class_data[cls_name]
                    # 将 0.995 转换为 99.5% 显示
                    self.class_table.item(i, 1).setText(f"{score * 100:.1f}%")
                    # 如果分数太低（低于50%），标红预警
                    self.class_table.item(i, 1).setForeground(Qt.red if score < 0.5 else Qt.black)

            # --- 【任务 4 实时打点】：立即重绘内存数据 ---
            self.x_data.append(metrics.get("epoch"))
            self.y_box_loss.append(metrics.get("box_loss"))
            self.y_cls_loss.append(metrics.get("cls_loss"))
            self.y_dfl_loss.append(metrics.get("dfl_loss"))
            self.y_map.append(metrics.get("map50"))
            self.y_precision.append(metrics.get("precision", 0))
            self.y_recall.append(metrics.get("recall", 0))
            self.y_lr.append(metrics.get("lr", 0))

            # --- 【任务 A2 修复】：改用科学计数法显示 LR ---
            self.stat_cards["LR"].setText(f"{metrics.get('lr', 0):.2e}")

            # --- 【任务 9 实时重绘】：应用 EMA 平滑算法后渲染 ---
            self.update_all_curves()

            # 更新总进度条
            self.epoch_bar.setMaximum(metrics.get("epochs", 100))
            self.epoch_bar.setValue(metrics.get("epoch", 0))

        elif m_type == "batch":
            # Batch 级更新：仅更新 Loss 保持看板活跃
            self.stat_cards["Box Loss"].setText(f"{metrics.get('box_loss', 0):.3f}")
            # 更新当前轮次进度条
            self.batch_bar.setMaximum(metrics.get("total_batches", 100))
            self.batch_bar.setValue(metrics.get("batch_idx", 0))

            # --- 任务 6：更新 ETA 时间显示 ---
            eta_s = metrics.get("eta", 0)
            if eta_s > 0:
                m, s = divmod(int(eta_s), 60)
                h, m = divmod(m, 60)
                self.lbl_eta.setText(f"预计剩余时间: {h:02d}:{m:02d}:{s:02d}")
            else:
                self.lbl_eta.setText("预计剩余时间: 计算中...")

    def on_smooth_changed(self, val):
        """滑动条回调"""
        self.smooth_weight = val / 100.0
        self.lbl_smooth_val.setText(f"{self.smooth_weight:.2f}")
        self.update_all_curves()

    def smooth_data(self, data):
        """EMA 平滑核心算法"""
        if not data: return []
        if self.smooth_weight <= 0: return data
        smoothed = []
        last = data[0]
        for val in data:
            smoothed_val = last * self.smooth_weight + (1 - self.smooth_weight) * val
            smoothed.append(smoothed_val)
            last = smoothed_val
        return smoothed

    def update_all_curves(self):
        """重新渲染所有曲线"""
        if not self.x_data: return
        self.curve_box_loss.setData(self.x_data, self.smooth_data(self.y_box_loss))
        self.curve_cls_loss.setData(self.x_data, self.smooth_data(self.y_cls_loss))
        self.curve_dfl_loss.setData(self.x_data, self.smooth_data(self.y_dfl_loss))
        self.curve_map.setData(self.x_data, self.smooth_data(self.y_map))
        self.curve_precision.setData(self.x_data, self.smooth_data(self.y_precision))
        self.curve_recall.setData(self.x_data, self.smooth_data(self.y_recall))
        self.curve_lr.setData(self.x_data, self.smooth_data([v * 10000 for v in self.y_lr]))

    def update_plots(self):
        """安全读取 CSV"""
        if not os.path.exists(self.results_path):
            return
        try:
            # 解决文件被占用导致读取失败的问题 (Windows 特有)
            with open(self.results_path, 'r', encoding='utf-8') as f:
                df = pd.read_csv(f)

            if df.empty or len(df) < 1: return

            df.columns = [c.strip() for c in df.columns]
            # mAP50 往往在几轮之后才会有值，初期可能为 0
            epochs = range(1, len(df) + 1)

            # 绘制曲线
            self.curve_loss.setData(list(epochs), df['train/box_loss'].tolist())
            self.curve_map.setData(list(epochs), df['metrics/mAP50(B)'].tolist())
            self.curve_precision.setData(epochs, df['metrics/precision(B)'])
            self.curve_recall.setData(epochs, df['metrics/recall(B)'])
        except Exception:
            pass

    def start_training_flow(self):
        self.log_view.clear()
        # 1. 确定训练起点
        base_model = 'yolov8n.pt'
        best_pt = ProjectPaths.BEST_PT

        if self.check_incremental.isChecked():
            if os.path.exists(best_pt):
                base_model = best_pt
                self.log_view.appendPlainText(f"ℹ️ 模式：增量训练，将加载 {best_pt}")
            else:
                self.log_view.appendPlainText("⚠️ 未找到 best.pt，将回退到全新训练模式。")
                self.check_incremental.setChecked(False)
        else:
            self.log_view.appendPlainText("ℹ️ 模式：全新训练 (从 yolov8n 官方模型开始)")

        self.config = load_config()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setStyleSheet("background: #e74c3c; color: white; border-radius: 6px")  # 变红提示

        self.switch_viz_view(0)  # 训练开始，强制切回实时曲线
        self.lbl_eta.setText("预计剩余时间: 准备中...")
        # --- 【任务 4 新增】：启动前重置曲线 ---
        self.x_data, self.y_box_loss, self.y_cls_loss, self.y_dfl_loss, self.y_map, self.y_precision, self.y_recall, self.y_lr = [], [], [], [], [], [], [], []
        self.curve_box_loss.setData([], [])
        self.curve_cls_loss.setData([], [])
        self.curve_dfl_loss.setData([], [])
        self.curve_map.setData([], [])
        self.curve_precision.setData([], [])
        self.curve_recall.setData([], [])
        self.curve_lr.setData([], [])

        # --- 【任务 3 新增】：锁定配置项 ---
        self.spin_epochs.setEnabled(False)
        self.spin_batch.setEnabled(False)
        self.check_incremental.setEnabled(False)

        if os.path.exists(self.results_path):
            try:
                os.remove(self.results_path)
            except:
                pass

        self.log_view.appendPlainText("🛠 准备数据集...")
        self.db.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        files = [row[0] for row in self.db.cursor.fetchall()]

        try:
            yaml_path = prepare_yolo_dataset(
                self.config['local_path'],
                files,
                self.config.get("classes", ["Target"])
            )
            # 2. 传递给 Worker
            self.worker = TrainingWorker(
                yaml_path,
                self.spin_epochs.value(),
                self.spin_batch.value(),
                base_model=base_model
            )
            self.worker.log_signal.connect(self.log_view.appendPlainText)
            self.worker.metrics_signal.connect(self.handle_metrics)
            self.worker.finished_signal.connect(self.on_train_finished)
            self.worker.start()
            self.plot_timer.start(5000)
        except Exception as e:
            self.log_view.appendPlainText(f"❌ 准备失败: {str(e)}")
            self.btn_start.setEnabled(True)

    def stop_training(self):
        """安全的中止逻辑"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.append_log("\n⏳ 正在请求停止训练，请稍候...")
            # 禁用按钮防止重复点击
            self.btn_stop.setEnabled(False)
            # 调用 worker 的自定义 stop 方法
            self.worker.stop()

    def on_train_finished(self, best_model_path):
        # 1. 基础清理（无论成功失败都要做）
        self.plot_timer.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)  # 必须禁用中止按钮
        self.btn_stop.setStyleSheet(self.get_btn_style("#f56c6c"))

        # --- 【任务 3 新增】：解锁配置项 ---
        self.spin_epochs.setEnabled(True)
        self.spin_batch.setEnabled(True)
        self.check_incremental.setEnabled(True)

        # 2. 检查 YOLO 是否真的产出了模型
        if best_model_path and os.path.exists(best_model_path):
            # --- 修复 Bug A：路径对齐（解决图表白板） ---
            # 你的原代码直接 update_plots()，但当时 results_path 是错的。
            # 这里必须根据 best_model_path 反向定位 results.csv 的真实位置。
            train_root = os.path.dirname(os.path.dirname(best_model_path))
            self.results_path = os.path.join(train_root, "results.csv")

            # 路径修正后，执行最后一次刷新，左侧监控图必出曲线
            self.update_plots()

            # --- 【任务 10】：加载并展示官方结果图 ---
            res_png = os.path.join(train_root, "results.png")
            if os.path.exists(res_png):
                # 1. 先切换视图，让 label 进入可见状态，从而触发布局拉伸
                self.switch_viz_view(1)

                # 2. 强制处理一次界面事件，确保布局引擎完成 14/21 寸屏幕的尺寸计算
                from PyQt5.QtWidgets import QApplication
                QApplication.processEvents()

                # --- 【任务 B1 修复】：强制图片自适应全屏 ---
                pix = QPixmap(res_png)

                # --- 修复 AttributeError 并进行真实尺寸捕获 ---
                container_size = self.viz_content.size()
                label_size = self.res_image_label.size()
                self.append_log(
                    f"🔎 [B1 尺寸诊断] 容器: {container_size.width()}x{container_size.height()} | 标签: {label_size.width()}x{label_size.height()} | 图片: {pix.width()}x{pix.height()}")

                # 3. 使用此时已经“撑开”的 label_size 进行缩放
                if label_size.width() > 100:
                    self.res_image_label.setPixmap(pix.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    # 如果还是太小，说明布局未就绪，使用容器尺寸作为保底
                    self.res_image_label.setPixmap(
                        pix.scaled(container_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

                self.last_results_png = res_png  # 存一下路径供预览

            # --- 修复 Bug B：业务状态激活 ---
            self.last_best_model = best_model_path
            self.action_publish.setEnabled(True)
            self.action_open.setEnabled(True)
            self.action_export.setEnabled(True)

            # --- 【任务 11】：生成结构化总结报告 ---
            self.generate_summary_report()
        else:
            # 3. 失败或中止的处理
            self.last_best_model = None
            self.action_publish.setEnabled(False)
            self.action_open.setEnabled(False)
            self.action_export.setEnabled(False)
            self.append_log(f"\n⚠️ 训练已停止，未产出新模型或已中止。")

    def generate_summary_report(self):
        """任务 11：计算并输出训练总结报告"""
        if not self.y_map: return

        # 1. 寻找最佳 mAP 及其轮次
        max_map = max(self.y_map)
        best_epoch = self.x_data[self.y_map.index(max_map)]

        # 2. 计算总耗时
        elapsed = time.time() - self.worker.train_start_time
        mins, secs = divmod(int(elapsed), 60)

        # 3. 分析最差类别
        worst_cls = "N/A"
        min_score = 1.0
        class_data = self.last_epoch_metrics.get("class_data", {})
        for name, score in class_data.items():
            if score < min_score:
                min_score = score
                worst_cls = name

        # 4. 格式化输出
        report = [
            "\n" + "=" * 45,
            "📊 训练任务总结报告",
            "=" * 45,
            f"⏱ 任务总耗时:   {mins}分{secs}秒",
            f"🏆 最佳精度轮次: 第 {best_epoch} 轮 (mAP50: {max_map:.4f})",
            f"📉 弱势类别提醒: [{worst_cls}] (当前精度: {min_score * 100:.1f}%)" if worst_cls != "all" else "✅ 各类别表现均衡",
            "💡 建议: " + (f"针对 [{worst_cls}] 补充更多样本以提升稳定性" if min_score < 0.5 else "模型已达到较高水平"),
            "=" * 45 + "\n",
            f"🏆 模型已就绪：{os.path.basename(self.last_best_model)}"
        ]
        for line in report:
            self.append_log(line)


    def publish_to_cloud(self):
        """一键发布模型到 Minio"""
        if not self.last_best_model or not os.path.exists(self.last_best_model):
            return QMessageBox.warning(self, "发布失败", "找不到训练好的模型文件。")

        self.config = load_config()
        bucket = self.config.get("bucket_name", "ai-training-raw")

        # 构造远程路径：models/best_timestamp.pt
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        remote_key = f"models/best_{timestamp}.pt"

        self.log_view.appendPlainText(f"\n🌐 正在同步至云端: {remote_key} ...")

        try:
            minio = MinioManager()
            if minio.upload_file(bucket, self.last_best_model, remote_key):
                self.log_view.appendPlainText(f"✅ 发布成功！存储桶: {bucket}")
                QMessageBox.information(self, "发布成功", f"模型已成功同步至 Minio：\n{remote_key}")
            else:
                self.log_view.appendPlainText("❌ 上传失败，请检查网络或 Minio 配置。")
        except Exception as e:
            QMessageBox.critical(self, "系统异常", f"发布过程崩溃: {e}")

    def export_mobile_model(self):
        """将最新的 best.pt 导出为手机端格式"""
        if not self.last_best_model or not os.path.exists(self.last_best_model):
            return QMessageBox.warning(self, "导出失败", "找不到训练好的模型文件。")

        self.append_log("\n🚀 正在启动导出任务，请勿关闭程序...")
        self.btn_export.setEnabled(False)

        # 启动一个简单的导出线程，防止 UI 假死
        class ExportThread(QThread):
            finished = pyqtSignal(list)
            error = pyqtSignal(str)

            def __init__(self, model_path):
                super().__init__()
                self.model_path = model_path

            def run(self):
                try:
                    from ultralytics import YOLO
                    model = YOLO(self.model_path)

                    # 1. 导出为 ONNX (通用性最高)
                    onnx_path = model.export(format='onnx', opset=12)

                    # 2. 导出为 TFLite (安卓原生)
                    # 注意：导出 TFLite 可能需要安装 tensorflow 库
                    tflite_path = model.export(format='tflite', int8=False)

                    self.finished.emit([onnx_path, tflite_path])
                except Exception as e:
                    self.error.emit(str(e))

        self.exp_thread = ExportThread(self.last_best_model)
        self.exp_thread.finished.connect(self.on_export_success)
        self.exp_thread.error.connect(lambda e: [self.append_log(f"❌ 导出失败: {e}"), self.btn_export.setEnabled(True)])
        self.exp_thread.start()

    def on_export_success(self, paths):
        self.btn_export.setEnabled(True)
        self.append_log("\n✅ 导出成功！")
        for p in paths:
            self.append_log(f"📍 文件: {p}")
        QMessageBox.information(self, "导出成功", f"模型已转换为手机格式，存放在训练结果目录下。")