import os
import json

from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QListWidget, QFrame, QMessageBox, QInputDialog, QFileDialog, QMenu,
                            QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt5.QtCore import Qt, QRectF, QPoint
from ultralytics import YOLO  # 引入推理能力

from ui.components.label_canvas import LabelCanvas
from ui.components.label_rect import LabelRect
from utils.yolo_utils import convert_to_yolo, save_yolo_file
from utils.config_manager import load_config, save_config, ProjectPaths


def get_color(idx):
    """业界常用颜色表"""
    colors = [
        "#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6",
        "#1abc9c", "#e67e22", "#34495e", "#d35400", "#c0392b"
    ]
    return colors[idx % len(colors)]


class LabellerPage(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self.config = load_config()
        self.image_list = []
        self.current_img_index = -1
        self.current_model = None  # 缓存加载的模型
        self.last_labels_cache = []  # 缓存上一张标注
        self._is_syncing = False  # 任务 3：防止双向联动死循环的标志位

        # 1. 先初始化 UI 控件 (创建 class_list)
        self.initUI()

        self.canvas.selection_changed.connect(self.sync_list_selection)
        self.canvas.item_selected.connect(self.sync_table_selection)
        # 2. 再同步数据到画布
        self.canvas.item_added.connect(self.add_table_row)
        self.canvas.item_removed.connect(self.remove_table_row)
        self.canvas.item_updated.connect(self.update_table_row) # 任务 4：监听变动
        self.sync_classes_to_canvas()

    def initUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. 左侧任务列表
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(250)
        self.sidebar.setStyleSheet("background: #f8f9fa; border-right: 1px solid #dcdfe6;")
        side_layout = QVBoxLayout(self.sidebar)

        # --- 新增统计面板 ---
        stats_box = QFrame()
        stats_box.setStyleSheet("""
                    QFrame { 
                        background: #ffffff; 
                        border: 1px solid #e4e7ed; 
                        border-radius: 8px; 
                        margin-bottom: 5px;
                    }
                    QLabel { border: none; font-size: 18px; }
                """)
        stats_layout = QVBoxLayout(stats_box)

        self.lbl_stat_done = QLabel("✅ 已标注: 0")
        self.lbl_stat_done.setStyleSheet("color: #67c23a; font-weight: bold;")

        self.lbl_stat_todo = QLabel("⏳ 待标注: 0")
        self.lbl_stat_todo.setStyleSheet("color: #e6a23c; font-weight: bold;")

        self.lbl_stat_total = QLabel("📊 总数量: 0")
        self.lbl_stat_total.setStyleSheet("color: #909399;")

        stats_layout.addWidget(self.lbl_stat_total)
        stats_layout.addWidget(self.lbl_stat_done)
        stats_layout.addWidget(self.lbl_stat_todo)
        side_layout.addWidget(stats_box)
        # ------------------

        # ======================================================
        # >>> 新增合入：模式切换按钮组 <<<
        # ======================================================
        mode_container = QHBoxLayout()
        mode_container.setSpacing(0)  # 让两个按钮紧贴，像 Tab 一样

        self.btn_mode_todo = QPushButton("待标注")
        self.btn_mode_todo.setCheckable(True)
        self.btn_mode_todo.setChecked(True)  # 默认处于待标注模式
        self.btn_mode_todo.setFixedHeight(40)

        self.btn_mode_done = QPushButton("已标注")
        self.btn_mode_done.setCheckable(True)
        self.btn_mode_done.setFixedHeight(40)

        # 业界通用 Tab 样式
        mode_style = """
                    QPushButton { 
                        background: #f5f7fa; border: 1px solid #dcdfe6; color: #606266; font-weight: bold;
                    }
                    QPushButton:checked { 
                        background: #409eff; color: white; border: 1px solid #409eff;
                    }
                """
        self.btn_mode_todo.setStyleSheet(mode_style)
        self.btn_mode_done.setStyleSheet(mode_style)

        # 加入布局并绑定事件
        self.mode_group = [self.btn_mode_todo, self.btn_mode_done]
        for btn in self.mode_group:
            btn.clicked.connect(self.switch_mode)
            mode_container.addWidget(btn)

        side_layout.addLayout(mode_container)
        # ======================================================

        side_layout.addWidget(QLabel("📂 任务队列"))
        self.task_list = QListWidget()
        self.task_list.setStyleSheet("""
                    QListWidget {
                        background-color: #ffffff;
                        border: 1px solid #dcdfe6;
                        outline: none; /* 去除虚线框 */
                        font-size: 16px;
                    }
                    QListWidget::item {
                        padding: 10px;
                        border-bottom: 1px solid #f2f2f2;
                        color: #606266;
                    }
                    QListWidget::item:hover {
                        background-color: #ecf5ff;
                    }
                    /* 状态 1：当前选中并处于激活状态（蓝色底） */
                    QListWidget::item:selected {
                        background-color: #3498db;
                        color: white;
                        font-weight: bold;
                        border-left: 5px solid #2980b9; /* 增加左侧粗条增强指引感 */
                    }
                    /* 状态 2：当前选中但鼠标点击了其他地方（失去焦点），依然保持醒目颜色 */
                    QListWidget::item:selected:!active {
                        background-color: #a0cfff; /* 稍浅一点的蓝色 */
                        color: #ffffff;
                    }
                """)
        self.task_list.currentRowChanged.connect(self.on_task_row_changed)  # 任务 5：监听行变化，支持键盘切换
        side_layout.addWidget(self.task_list)
        btn_refresh = QPushButton("🔄 刷新队列")
        btn_refresh.clicked.connect(self.refresh_queue)
        side_layout.addWidget(btn_refresh)
        layout.addWidget(self.sidebar)

        # 2. 中间标注区
        self.canvas = LabelCanvas()
        layout.addWidget(self.canvas, 1)

        # 3. 右侧控制面板
        self.ctrl_panel = QFrame()
        self.ctrl_panel.setFixedWidth(300)
        self.ctrl_panel.setStyleSheet("background: #ffffff; border-left: 1px solid #dcdfe6;")
        ctrl_layout = QVBoxLayout(self.ctrl_panel)

        # --- 智能辅助区 ---
        lbl_ai = QLabel("🤖 AI 智能辅助 (反哺标注)")
        lbl_ai.setStyleSheet("font-weight: bold; color: #2c3e50;")
        ctrl_layout.addWidget(lbl_ai)

        # --- 新增：模型源切换按钮 ---
        self.btn_select_model = QPushButton("🎯 切换模型源中心")
        self.btn_select_model.setFixedHeight(40)
        self.btn_select_model.setStyleSheet("""
            QPushButton { background: #34495e; color: white; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #2c3e50; }
        """)
        self.btn_select_model.clicked.connect(self.show_model_menu)
        ctrl_layout.addWidget(self.btn_select_model)

        # 按钮 1: 单张预标注
        self.btn_ai_assist = QPushButton("✨ 单张自动识别")
        self.btn_ai_assist.setFixedHeight(40)
        self.btn_ai_assist.setStyleSheet("background: #9b59b6; color: white; font-weight: bold;")
        self.btn_ai_assist.clicked.connect(self.run_ai_inference)
        ctrl_layout.addWidget(self.btn_ai_assist)

        # 按钮 2: 批量预标注 (新增)
        self.btn_batch_ai = QPushButton("🚀 队列批量 AI 识别")
        self.btn_batch_ai.setFixedHeight(40)
        self.btn_batch_ai.setStyleSheet("""
                    QPushButton { background: #8e44ad; color: white; font-weight: bold; border: 1px solid #7d3c98; }
                    QPushButton:hover { background: #9b59b6; }
                """)
        self.btn_batch_ai.clicked.connect(self.batch_ai_inference)
        ctrl_layout.addWidget(self.btn_batch_ai)

        # --- 优化：加大当前模型状态的显示字号 ---
        self.lbl_model_status = QLabel("当前模型: ⚡ 待选择")
        self.lbl_model_status.setStyleSheet("""
            font-size: 18px; 
            color: #34495e; 
            font-weight: bold; 
            margin-top: 8px;
            padding: 2px;
        """)
        ctrl_layout.addWidget(self.lbl_model_status)

        ctrl_layout.addSpacing(15)

        # --- 类别管理区 ---
        lbl_cls = QLabel("🏷 类别管理 (双击修改)")
        lbl_cls.setStyleSheet("font-weight: bold; color: #2c3e50;")
        ctrl_layout.addWidget(lbl_cls)

        self.class_list = QListWidget()
        self.class_list.addItems(self.config.get("classes", ["Target"]))
        self.class_list.setCurrentRow(0)
        self.class_list.itemDoubleClicked.connect(self.edit_class)
        ctrl_layout.addWidget(self.class_list)

        h_btn_class = QHBoxLayout()
        btn_add_cls = QPushButton("+ 添加")
        btn_add_cls.clicked.connect(self.add_class)
        btn_del_cls = QPushButton("- 删除")
        btn_del_cls.clicked.connect(self.del_class)
        h_btn_class.addWidget(btn_add_cls)
        h_btn_class.addWidget(btn_del_cls)
        ctrl_layout.addLayout(h_btn_class)

        self.check_inherit = QCheckBox("🔄 继承上一张标注")
        self.check_inherit.setStyleSheet("color: #e67e22; font-weight: bold; margin: 10px 0;")
        ctrl_layout.addWidget(self.check_inherit)

        # --- 任务 1：注入标注审计清单 ---
        self.label_table = QTableWidget()
        self.label_table.setColumnCount(2)
        self.label_table.setHorizontalHeaderLabels(["类别", "坐标 (X,Y,W,H)"])
        # 任务 3：调整比例为 1:2 (300px 下 100:200)
        header = self.label_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.label_table.setColumnWidth(0, 80)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        
        self.label_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.label_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.label_table.setSelectionMode(QTableWidget.SingleSelection)
        self.label_table.setStyleSheet("""
            QTableWidget {
                background: #ffffff;
                border: 1px solid #dcdfe6;
                border-radius: 4px;
                gridline-color: #f5f5f5;
            }
        """)
        self.label_table.setMinimumHeight(200)
        self.label_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        ctrl_layout.addWidget(self.label_table)

        ctrl_layout.addStretch()

        btn_save = QPushButton("💾 保存标注 (S)")
        btn_save.setFixedHeight(50)
        btn_save.setStyleSheet("background: #2ecc71; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.save_current_labels)
        ctrl_layout.addWidget(btn_save)

        btn_next = QPushButton("⏭ 下一张 (Space)")
        btn_next.setFixedHeight(50)
        btn_next.clicked.connect(self.load_next)
        ctrl_layout.addWidget(btn_next)

        # 任务 6：按钮组（取消与物理删除）
        self.btn_reset = QPushButton("🗑 取消标注")
        self.btn_reset.setStyleSheet("background: #f56c6c; color: white;")
        self.btn_reset.clicked.connect(self.cancel_annotation)
        ctrl_layout.addWidget(self.btn_reset)

        self.btn_phys_del = QPushButton("🔥 物理删除素材")
        self.btn_phys_del.setFixedHeight(40)
        self.btn_phys_del.setStyleSheet("background: #e67e22; color: white; font-weight: bold;")
        self.btn_phys_del.setToolTip("从磁盘彻底删除原图及标注文件，不可恢复。")
        self.btn_phys_del.clicked.connect(self.delete_physical_image)
        ctrl_layout.addWidget(self.btn_phys_del)

        layout.addWidget(self.ctrl_panel)
        self.class_list.currentRowChanged.connect(self.on_class_changed)

    def add_table_row(self, item):
        """任务 2：向审计清单添加一行"""
        row = self.label_table.rowCount()
        print(f"[LOG] 准备添加表格行: Row={row}, UID={item.uid}")
        self.label_table.insertRow(row)
        
        # 任务 4：使用 sceneBoundingRect 获取绝对物理坐标
        r = item.sceneBoundingRect()
        coord_text = f"({int(r.x())}, {int(r.y())}, {int(r.width())}, {int(r.height())})"
        
        cls_item = QTableWidgetItem(item.class_name)
        cls_item.setForeground(QColor(item.color)) # 核心修复：转换为 QColor
        cls_item.setData(Qt.UserRole, item.uid) # 存储 UID 用于后续绑定
        
        self.label_table.setItem(row, 0, cls_item)
        self.label_table.setItem(row, 1, QTableWidgetItem(coord_text))

    def update_table_row(self, item):
        """任务 4：实时更新表格行数据（坐标、类别、颜色）"""
        uid = item.uid
        # print(f"[LOG-Page] 收到更新信号, 目标UID: {uid}")
        for r in range(self.label_table.rowCount()):
            it_cls = self.label_table.item(r, 0)
            stored_uid = it_cls.data(Qt.UserRole)
            if it_cls and int(stored_uid) == int(uid):
                # print(f"  -> 匹配成功，正在更新第 {r} 行坐标")
                # 1. 更新坐标 (X, Y, W, H)
                rect = item.sceneBoundingRect()
                coord_text = f"({int(rect.x())}, {int(rect.y())}, {int(rect.width())}, {int(rect.height())})"
                self.label_table.item(r, 1).setText(coord_text)
                
                # 2. 更新类别名称与颜色
                it_cls.setText(item.class_name)
                it_cls.setForeground(QColor(item.color))
                break

    def remove_table_row(self, uid):
        """任务 2：根据 UID 从清单移除行"""
        print(f"[LOG] 收到删除请求信号, 目标UID={uid}, 表格总行数={self.label_table.rowCount()}")
        for r in range(self.label_table.rowCount()):
            it = self.label_table.item(r, 0)
            stored_uid = it.data(Qt.UserRole)
            print(f"  -> 正在检查第 {r} 行: 存储UID={stored_uid} vs 目标UID={uid} | 结果={stored_uid == uid}")
            if it and stored_uid == uid:
                print(f"  [!!!] 匹配成功，执行 removeRow({r})")
                self.label_table.removeRow(r)
                break

    def on_table_selection_changed(self):
        """任务 3：列表选中 -> 画布选中"""
        if self._is_syncing: return
        self._is_syncing = True
        
        selected_items = self.label_table.selectedItems()
        if selected_items:
            uid = selected_items[0].data(Qt.UserRole)
            for item in self.canvas.scene.items():
                if isinstance(item, LabelRect) and item.uid == uid:
                    self.canvas.scene.clearSelection()
                    item.setSelected(True)
                    break
        self._is_syncing = False

    def sync_table_selection(self, canvas_item):
        """任务 3：画布选中 -> 列表选中"""
        if self._is_syncing: return
        self._is_syncing = True
        
        uid = canvas_item.uid
        for r in range(self.label_table.rowCount()):
            it = self.label_table.item(r, 0)
            if it and it.data(Qt.UserRole) == uid:
                self.label_table.selectRow(r)
                self.label_table.scrollToItem(it)
                break
        self._is_syncing = False

    def sync_list_selection(self, class_id):
        self.class_list.blockSignals(True)
        self.class_list.setCurrentRow(class_id)
        self.class_list.blockSignals(False)
        self.canvas.current_class_id = class_id
        self.canvas.current_color = get_color(class_id)

    def sync_classes_to_canvas(self):
        """核心修复：只传递纯净的列表给画布"""
        clean_classes = self.config.get("classes", ["Target"])
        self.canvas.set_class_names(clean_classes)


    def add_class(self):
        text, ok = QInputDialog.getText(self, "新增类别", "类别名称:")
        if ok and text:
            if "classes" not in self.config: self.config["classes"] = []
            self.config["classes"].append(text) # 更新配置
            self.class_list.addItem(text) # 临时占位，会被后续 update_class_counters 刷新
            self.sync_classes()

    def del_class(self):
        if self.class_list.count() <= 1:
            return QMessageBox.warning(self, "提醒", "至少需要保留一个类别")
        row = self.class_list.currentRow()
        self.config["classes"].pop(row) # 从配置中删除
        self.class_list.takeItem(row) # 从 UI 中删除
        self.sync_classes()

    def edit_class(self, item):
        row = self.class_list.row(item)
        old_name = self.config["classes"][row]  # 拿纯净名

        text, ok = QInputDialog.getText(self, "修改类别", "新名称:", text=old_name)
        if ok and text:
            self.config["classes"][row] = text  # 更新纯净名
            self.sync_classes()  # 保存并刷新

    def sync_classes(self):
        """核心修复：不再从 UI 抓取文本，而是由 UI 驱动配置保存"""
        # 此时 self.config["classes"] 应该已经在 add/del/edit 逻辑中被修改过了
        save_config(self.config)
        # 同步给画布纯净名称
        self.sync_classes_to_canvas()
        # 刷新 UI 展示（带括号的数字）
        self.update_class_counters()

    def ensure_model_loaded(self):
        """核心逻辑：智能寻找半成品模型"""
        if self.current_model is not None:
            return True

        # --- 策略 1: 从配置文件读取上次记录的路径 ---
        saved_model_path = self.config.get("last_model_path", "")
        if saved_model_path and os.path.exists(saved_model_path):
            try:
                self.current_model = YOLO(saved_model_path)
                self.lbl_model_status.setText(f"模型: {os.path.basename(saved_model_path)}")
                return True
            except:
                pass

        # --- 策略 2: 深度扫描所有可能的 best.pt 存放位置 ---
        # 考虑到你日志中出现的嵌套路径 runs/detect/runs/detect/liudup_train
        search_paths = [
            ProjectPaths.BEST_PT,  # 优先查最新定义的统一路径
            os.path.join("runs", "detect", "liudup_train", "weights", "best.pt"),  # 兼容旧路径
        ]

        for p in search_paths:
            if os.path.exists(p):
                try:
                    self.current_model = YOLO(p)
                    self.lbl_model_status.setText(f"模型: 自动加载")
                    # 存入配置，下次重启直接用
                    self.save_model_path_to_config(p)
                    return True
                except:
                    continue

        # --- 策略 3: 如果都找不到，才弹窗让用户选 ---
        model_path, _ = QFileDialog.getOpenFileName(
            self, "选择训练好的权重 (best.pt)", "runs/detect", "YOLO Model (*.pt)"
        )
        if model_path:
            try:
                self.current_model = YOLO(model_path)
                self.lbl_model_status.setText(f"模型: {os.path.basename(model_path)}")
                self.save_model_path_to_config(model_path)
                return True
            except Exception as e:
                QMessageBox.critical(self, "错误", f"模型加载失败: {e}")
        return False

    def save_model_path_to_config(self, path):
        """将模型路径持久化到 config.json"""
        self.config["last_model_path"] = os.path.abspath(path)
        save_config(self.config)

    def show_model_menu(self):
        """弹出多源模型选择菜单"""
        menu = QMenu(self)
        menu.setStyleSheet("""
                    QMenu {
                        background-color: white;
                        border: 1px solid #dcdfe6;
                        font-size: 18px;             /* 字体从默认加大到 18px */
                        font-weight: 500;
                        padding: 5px 0px;
                    }
                    QMenu::item {
                        padding: 12px 40px;          /* 增加上下内边距让菜单项更高，横向增加留白 */
                        border-bottom: 1px solid #f2f2f2; /* 增加细分割线 */
                        color: #2c3e50;
                    }
                    QMenu::item:selected {
                        background-color: #3498db;   /* 选中时背景变蓝 */
                        color: white;                /* 选中时文字变白 */
                    }
                    QMenu::item:disabled {
                        color: #909399;             /* 工业中灰色 */
                        background-color: #f5f7fa;  /* 给个淡淡的灰色背景，增强不可用感 */
                    }
                    QMenu::separator {
                        height: 2px;
                        background: #ebeef5;
                        margin: 5px 0px;
                    }
                """)

        # 定义四大标准算力产出路径
        model_paths = {
            "🏠 本地训练 (liudup_train)": os.path.join(ProjectPaths.RUNS_DIR, "detect", "runs", "liudup_train", "weights", "best.pt"),
            "☁️ 云端协作 (Notebook)": os.path.join(ProjectPaths.RUNS_DIR, "notebook_results", "best.pt"),
            "⚡ 算力摆渡 (Minio AutoSync)": os.path.join(ProjectPaths.RUNS_DIR, "auto_results", "best.pt"),
            "📡 局域网协同 (SSH Direct)": os.path.join(ProjectPaths.RUNS_DIR, "ssh_results", "best.pt")
        }

        for name, path in model_paths.items():
            action = menu.addAction(name)
            if not os.path.exists(path):
                action.setEnabled(False)
                action.setText(f"{name} (未发现产出)")
            else:
                # 使用 lambda 捕获当前路径和名称
                action.triggered.connect(lambda chk, p=path, n=name: self.switch_model_to(p, n))

        menu.addSeparator()
        action_custom = menu.addAction("📂 手动加载其他权重...")
        action_custom.triggered.connect(self.manually_select_model)

        # 在按钮正下方弹出
        menu.exec_(self.btn_select_model.mapToGlobal(QPoint(0, self.btn_select_model.height())))

    def switch_model_to(self, path, name):
        """切换当前推理引擎"""
        try:
            self.current_model = YOLO(path)
            self.lbl_model_status.setText(f"当前模型: {name}")
            self.save_model_path_to_config(path)
            QMessageBox.information(self, "切换成功", f"引擎已切换至：\n{name}")
        except Exception as e:
            QMessageBox.critical(self, "切换失败", f"模型文件可能损坏或版本不符: {e}")

    def manually_select_model(self):
        """原有手动选择方法的调用"""
        path, _ = QFileDialog.getOpenFileName(self, "选择模型权重", "runs", "YOLO Model (*.pt)")
        if path: self.switch_model_to(path, os.path.basename(path))

    def run_ai_inference(self):
        """单张推理逻辑"""
        if self.current_img_index == -1: return
        if not self.ensure_model_loaded(): return

        img_path = self.image_list[self.current_img_index]
        try:
            # conf=0.25 是平衡漏检和误检的常用经验值
            results = self.current_model.predict(source=img_path, conf=0.25, save=False, verbose=False)

            if results and len(results[0].boxes) > 0:
                count = 0
                for box in results[0].boxes:
                    coords = box.xyxy[0].cpu().numpy()
                    cls_id = int(box.cls[0].cpu().numpy())
                    rect = QRectF(coords[0], coords[1], coords[2] - coords[0], coords[3] - coords[1])
                    self.canvas.add_label_box(rect, cls_id)
                    count += 1
                # self.append_log(f"AI 生成了 {count} 个框")
            else:
                QMessageBox.warning(self, "AI 辅助", "未发现目标。")
        except Exception as e:
            QMessageBox.warning(self, "推理失败", str(e))

    def batch_ai_inference(self):
        """【需求 14】批量 AI 辅助：一键处理整个待标注队列"""
        if not self.image_list:
            return QMessageBox.warning(self, "提醒", "当前任务队列为空，请先刷新队列。")

        if not self.ensure_model_loaded(): return

        msg = f"确定要对当前队列中的 {len(self.image_list)} 张图片进行批量识别吗？\n\n注意：这会直接覆盖已存在的同名标注文件。"
        if QMessageBox.question(self, "批量预标注确认", msg) != QMessageBox.Yes:
            return

        # 进度提示
        success_count = 0
        total = len(self.image_list)

        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog("  🚀 AI 正在拼命跑图...   ", "取消", 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        for i, path in enumerate(self.image_list):
            if progress.wasCanceled(): break

            try:
                # 1. 运行推理
                results = self.current_model.predict(source=path, conf=0.25, save=False, verbose=False, stream=True)

                for res in results:
                    if len(res.boxes) > 0:
                        from PyQt5.QtGui import QImageReader
                        reader = QImageReader(path)
                        sz = reader.size()
                        w, h = sz.width(), sz.height()

                        if w <= 0 or h <= 0: continue

                        yolo_data_list = []
                        for box in res.boxes:
                            # --- 核心修复：强制转换为 Python 标准类型 (int 和 float) ---
                            coords = box.xyxy[0].cpu().numpy()
                            cls_id = int(box.cls[0].cpu().numpy())  # 确保是标准 int

                            # 将 numpy float32 转换为标准 float 列表
                            pixel_coords = [float(x) for x in coords]

                            yolo_coord = convert_to_yolo((w, h), pixel_coords)

                            # 再次确保写入 list 的每一项都是标准 float
                            safe_yolo_coord = [float(x) for x in yolo_coord]
                            yolo_data_list.append((cls_id, safe_yolo_coord))

                        # 2. 持久化到数据库（现在 json.dumps 不会报错了）
                        self.db.save_label(path, json.dumps(yolo_data_list), w, h, os.path.getsize(path))

                        # 3. 保存到本地 .txt
                        from utils.yolo_utils import save_yolo_file
                        save_yolo_file(path, yolo_data_list)
                        success_count += 1

                progress.setValue(i + 1)
            except Exception as e:
                # --- 核心修复：将不存在的 self.append_log 改为 print ---
                print(f"⚠️ 批量AI处理跳过 {os.path.basename(path)}: {e}")

        progress.close()
        QMessageBox.information(self, "批量完成",
                                f"任务结束！成功预标注 {success_count} 张图。\n\n请刷新队列并切换到“已标注”模式进行校对。")
        self.refresh_queue()

    def refresh_queue(self):
        self.config = load_config()
        folder = self.config.get("local_path", "")
        if not folder:
            return QMessageBox.warning(self, "提醒", "请先在去重页面选择工作目录")
        # --- 修改点：根据模式加载不同的列表 ---
        if self.btn_mode_done.isChecked():
            # 加载已标注列表 (你需要确保 DatabaseManager 有这个方法)
            self.image_list = self.db.get_labeled_images(folder)
        else:
            # 加载待标注列表 (原有逻辑)
            self.image_list = self.db.get_unlabeled_images(folder)

        self.task_list.clear()
        for p in self.image_list:
            self.task_list.addItem(os.path.basename(p))

        if self.image_list:
            self.load_image(0)
        else:
            self.canvas.scene.clear()  # 如果列表为空，清空画布

        self.update_stats_display() # 原有的图片总数统计
        self.update_class_counters()  # 新增：单项类别统计

    def on_task_row_changed(self, index):
        """任务 5：处理列表行切换事件（包括键盘和点击）"""
        if index == -1: return # 避免列表为空时触发
        idx = index
        self.load_image(idx)

    def switch_mode(self):
        """处理待标注/已标注模式切换"""
        sender = self.sender()
        # 确保点击其中一个，另一个就取消选中
        for btn in self.mode_group:
            btn.setChecked(btn == sender)
        # 切换后自动刷新列表
        self.refresh_queue()

    def load_image(self, index):
        """加载图片并同步回显标注框及类别选择"""
        if 0 <= index < len(self.image_list):
            self.current_img_index = index
            path = self.image_list[index]
            self.label_table.setRowCount(0) # 任务 2：加载新图前清空列表
            self.canvas.load_image(path)

            if self.canvas.pixmap_item:
                pix = self.canvas.pixmap_item.pixmap()
                size = (pix.width(), pix.height())

                # 1. 从磁盘加载标注数据
                from utils.yolo_utils import load_yolo_file
                existing_boxes = load_yolo_file(path, size)

                if existing_boxes:
                    # --- [核心逻辑：同步类别选择] ---
                    # 取第一个标注框的类别 ID
                    first_class_id = existing_boxes[0]['class_id']

                    # 检查索引合法性，防止因配置文件类别删减导致的越界
                    if 0 <= first_class_id < self.class_list.count():
                        # 自动高亮右侧类别列表中的对应项
                        self.class_list.setCurrentRow(first_class_id)
                        # 显式触发类别变更逻辑，确保画布绘画 ID 和颜色同步更新
                        self.on_class_changed(first_class_id)

                    # 磁盘有则用磁盘的
                    for item in existing_boxes:
                        r = item['rect']
                        # 这里的 r 是 (x, y, w, h)
                        self.canvas.add_label_box(
                            QRectF(r[0], r[1], r[2], r[3]),
                            item['class_id']
                        )
                elif self.check_inherit.isChecked() and self.last_labels_cache:
                    # 否则检查继承模式
                    for cls_id, rect in self.last_labels_cache:
                        self.canvas.add_label_box(rect, cls_id)
            # 同步左侧任务列表的选中状态
            self.task_list.blockSignals(True)  # 防止触发不必要的点击事件
            self.task_list.setCurrentRow(index)
            self.task_list.blockSignals(False)

    def load_next(self):
        self.load_image(self.current_img_index + 1)

    def save_current_labels(self):
        if self.current_img_index == -1 or not self.image_list: return

        path = self.image_list[self.current_img_index]
        # 获取画布上所有的框及其各自的类别 ID
        all_labeled_items = self.canvas.get_all_boxes()

        # 存入缓存
        self.last_labels_cache = all_labeled_items

        if self.canvas.pixmap_item:
            pix = self.canvas.pixmap_item.pixmap()
            w, h = pix.width(), pix.height()

            # 构造多类别数据结构 [(cls_id, [cx, cy, w, h]), ...]
            yolo_data_list = []
            for cls_id, rb in all_labeled_items:
                yolo_coord = convert_to_yolo((w, h), (rb.left(), rb.top(), rb.right(), rb.bottom()))
                yolo_data_list.append((cls_id, yolo_coord))

            # 1. 保存到数据库 (JSON 格式存储完整列表)
            label_json = json.dumps(yolo_data_list)
            if self.db.save_label(path, label_json, w, h, os.path.getsize(path)):

                # 2. 保存到本地 .txt (调用修复后的多类别保存函数)
                from utils.yolo_utils import save_yolo_file
                save_yolo_file(path, yolo_data_list)

                # 3. 仅在“待标注”模式下执行 UI 剔除
                if self.btn_mode_todo.isChecked():
                    self.image_list.pop(self.current_img_index)
                    self.task_list.takeItem(self.current_img_index)

                self.update_stats_display()
                self.update_class_counters()

                # 4. 加载下一张
                if self.image_list:
                    if self.current_img_index >= len(self.image_list):
                        self.current_img_index = len(self.image_list) - 1
                    self.load_image(self.current_img_index)
                else:
                    self.canvas.scene.clear()
                    self.current_img_index = -1
                    QMessageBox.information(self, "完成", "所有任务已标注完毕！")

    def write_yolo_file_multi_class(self, img_path, data_list):
        """辅助方法：将多类别数据写入物理文件"""
        txt_path = os.path.splitext(img_path)[0] + ".txt"
        try:
            with open(txt_path, 'w') as f:
                for cls_id, box in data_list:
                    f.write(f"{cls_id} {' '.join(map(str, box))}\n")
            print(f"✅ 标注已更新: {txt_path}")
        except Exception as e:
            print(f"❌ 文件写入失败: {e}")

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # --- 核心修复：增加对表格删除的处理 ---
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.label_table.hasFocus():
                # 如果焦点在审计表格，执行表格专用删除
                self.delete_selected_from_table()
                return
            else:
                # 否则交给画布处理（原有逻辑）
                self.canvas.keyPressEvent(event)
                return

        # 如果按下 Ctrl 键，优先让画布处理（复制粘贴）
        if modifiers == Qt.ControlModifier:
            self.canvas.keyPressEvent(event)
            return

        # 需求 7：数字键 1-9 快捷切换类别
        if Qt.Key_1 <= key <= Qt.Key_9:
            idx = key - Qt.Key_1
            if idx < self.class_list.count():
                self.class_list.setCurrentRow(idx)
                return

        if key == Qt.Key_Space:
            self.load_next()
        elif key == Qt.Key_S:
            self.save_current_labels()
        else:
            super().keyPressEvent(event)

    def update_stats_display(self):
        """刷新统计数字"""
        folder = self.config.get("local_path", "")
        if not folder or not os.path.exists(folder):
            return

        # 1. 先清理数据库中的无效记录（比如被去重模块删掉的图）
        self.db.clean_orphaned_labels()

        # 1. 计算磁盘总数 (排除 _backup)
        valid_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
        physical_files = []
        for root, dirs, files in os.walk(folder):
            # 任务 9：统计数字也要同步过滤隔离目录
            blacklist = ['_backup']
            dirs[:] = [d for d in dirs if d not in blacklist and not d.startswith('yolo_dataset')]
            
            for f in files:
                if f.lower().endswith(valid_exts):
                    full_path = os.path.normpath(os.path.abspath(os.path.join(root, f)))
                    physical_files.append(full_path)

            # 3. 统计逻辑对齐
            # 总数：磁盘上看得见的物理图片总数
        total_count = len(physical_files)

        # 3. 获取数据库中已标注的路径集合
        self.db.cursor.execute("SELECT local_path FROM label_records WHERE is_labeled = 1")
        labeled_in_db = {os.path.normpath(row[0]) for row in self.db.cursor.fetchall()}

        # 4. 计算交集：物理存在且数据库记录为已标注
        actual_done_list = [p for p in physical_files if p in labeled_in_db]
        done_count = len(actual_done_list)

        # 5. 待标注 = 总数 - 已标注
        todo_count = total_count - done_count

        # 6. 更新 UI
        self.lbl_stat_total.setText(f"📊 总数量: {total_count}")
        self.lbl_stat_done.setText(f"✅ 已标注: {done_count}")
        self.lbl_stat_todo.setText(f"⏳ 待标注: {todo_count}")

    def cancel_annotation(self):
        if self.current_img_index == -1: return
        path = self.image_list[self.current_img_index]
        if QMessageBox.question(self, "确认", "确定要取消这张图的标注并删除.txt吗？") == QMessageBox.Yes:
            # 1. 删数据库
            self.db.reset_label(path)
            # 2. 删文件
            txt_path = os.path.splitext(path)[0] + ".txt"
            if os.path.exists(txt_path): os.remove(txt_path)
            # 3. 刷UI
            self.refresh_queue()

    def delete_physical_image(self):
        """任务 6：物理删除原图、标注及数据库记录"""
        if self.current_img_index == -1 or not self.image_list: return
        path = self.image_list[self.current_img_index]
        
        msg = f"确定要从磁盘彻底删除这张图片吗？\n\n警告：此操作不可恢复，且会同步删除标注文件！\n文件名: {os.path.basename(path)}"
        if QMessageBox.question(self, "物理删除确认", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                # 1. 物理移除文件
                if os.path.exists(path): os.remove(path)
                txt_path = os.path.splitext(path)[0] + ".txt"
                if os.path.exists(txt_path): os.remove(txt_path)
                
                # 2. 清理数据库（去重特征 + 标注记录）
                self.db.remove_mapping(path)
                self.db.reset_label(path)
                
                # 3. UI 剔除与状态重置
                self.image_list.pop(self.current_img_index)
                self.task_list.takeItem(self.current_img_index)
                self.label_table.setRowCount(0)
                self.update_stats_display()
                self.update_class_counters()
                
                # 4. 加载下一张
                if self.image_list:
                    if self.current_img_index >= len(self.image_list):
                        self.current_img_index = len(self.image_list) - 1
                    self.load_image(self.current_img_index)
                else:
                    self.canvas.scene.clear()
                    self.current_img_index = -1
            except Exception as e:
                QMessageBox.critical(self, "删除失败", f"文件可能被占用或权限不足: {e}")

    def on_class_changed(self, row):
        if row < 0: return

        # 核心修复：直接从 config 获取纯净名称，不从 QListWidget 拿
        base_classes = self.config.get("classes", [])
        if row >= len(base_classes): return

        class_name = base_classes[row]
        color = get_color(row)

        self.canvas.current_class_id = row
        self.canvas.current_color = color
        self.canvas.current_class_name = class_name
        self.canvas.update_selected_boxes_class(row, color, class_name)

    def update_class_counters(self):
        """实时更新右侧类别列表的数字统计"""
        # 1. 获取数据库最新统计结果 {cls_id: count}
        stats = self.db.get_all_class_counts()

        # 2. 获取原始类别定义（不含数字的纯名称）
        base_classes = self.config.get("classes", [])

        # 3. 阻塞信号，防止更新文字触发选择变更死循环
        self.class_list.blockSignals(True)

        for i in range(self.class_list.count()):
            item = self.class_list.item(i)
            # 获取该索引对应的基础名称
            if i < len(base_classes):
                base_name = base_classes[i]
                count = stats.get(i, 0)
                # 更新显示格式为：name (count)
                item.setText(f"{base_name} ({count})")

                # 视觉优化：如果没有数据，颜色变淡点；有数据则加粗
                if count > 0:
                    item.setForeground(QColor("#2c3e50"))
                else:
                    item.setForeground(QColor("#909399"))

        self.class_list.blockSignals(False)

    def delete_selected_from_table(self):
        """核心辅助：从审计表格触发删除画布上的物体"""
        selected_ranges = self.label_table.selectedRanges()
        if not selected_ranges:
            return

        # 获取当前选中的行
        row = selected_ranges[0].topRow()
        item_cls = self.label_table.item(row, 0)
        if not item_cls:
            return

        # 获取存储在 UserRole 中的唯一 UID
        uid = item_cls.data(Qt.UserRole)

        # 在画布中寻找并移除
        for item in self.canvas.scene.items():
            if isinstance(item, LabelRect) and item.uid == uid:
                print(f"[LOG] 从表格快捷键触发删除, UID: {uid}")
                self.canvas.scene.removeItem(item)
                # 发射移除信号，会自动触发 self.remove_table_row 逻辑
                self.canvas.item_removed.emit(uid)
                break
