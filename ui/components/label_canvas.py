import traceback
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem, QGraphicsLineItem, \
    QMenu, QAction
from PyQt5.QtCore import Qt, QRectF, pyqtSignal, QPoint, QPointF
from PyQt5.QtGui import QPen, QColor, QPixmap, QPainter, QCursor
from ui.components.label_rect import LabelRect


class LabelCanvas(QGraphicsView):
    box_added = pyqtSignal(QRectF)
    selection_changed = pyqtSignal(int)
    item_added = pyqtSignal(object)  # 任务 2：发射整个物体
    item_removed = pyqtSignal(object)  # 核心修复：改用 object 防止 64 位 ID 溢出截断
    item_selected = pyqtSignal(object) # 任务 3：物体被选中信号
    item_updated = pyqtSignal(object)  # 任务 4：物体属性或几何变动信号

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.pixmap_item = None

        # === 崩溃修复：scene.clear 生命周期保护 ===
        self.is_clearing = False
        self.clipboard = []  # 【新增】剪贴板
        self.class_names = []
        self.current_class_name = "Unknown"

        self.v_line = None
        self.h_line = None

        # 状态控制：绘画
        self.is_drawing = False
        self.start_pos = None
        self.current_rect = None
        self.current_color = "#e74c3c"

        # 状态控制：平移 (新增)
        self.is_panning = False
        self.last_pan_pos = QPoint()

        self.current_class_id = 0  # 新增：当前选中的类别ID
        self.current_color = "#e74c3c"  # 新增：当前颜色

        # 开启鼠标追踪，否则不按住鼠标时准心不会动
        self.setMouseTracking(True)

        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        # 隐藏滚动条让界面更干净（可选，平移依然有效）
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("background: #2c3e50; border: none;")

        # === 安全封装 selectionChanged ===
        self.scene.selectionChanged.connect(self.safe_selection_changed)

    def safe_selection_changed(self):
        if self.is_clearing:
            return
        self.on_scene_selection_changed()

    def on_scene_selection_changed(self):
        selected = self.scene.selectedItems()
        if len(selected) > 0:
            print(f"[DIAG] 画布选区变更, 当前选中项数: {len(selected)}")
        if len(selected) == 1 and isinstance(selected[0], LabelRect):
            self.selection_changed.emit(selected[0].label_id)
            self.item_selected.emit(selected[0]) # 发射选中物体信号

    def set_class_names(self, names):
        self.class_names = names

    def get_color(self, idx):
        colors = ["#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6", "#1abc9c", "#e67e22"]
        return colors[idx % len(colors)]

    def set_current_class(self, class_id, color, name="Unknown"):
        """新增：供外部页面调用，切换当前画框的类别"""
        self.current_class_id = class_id
        self.current_color = color
        self.current_class_name = name

    def load_image(self, path):
        items_count = len(self.scene.items())
        print(f"[TRACE] 画布入口: load_image | 当前场景物体数: {items_count}")

        # === 崩溃修复：clear 保护 ===
        self.is_clearing = True
        try:
            self.scene.clear()
        finally:
            self.is_clearing = False

        print(f"[TRACE] 画布执行: scene.clear() 完成")
        pixmap = QPixmap(path)
        if pixmap.isNull(): return

        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self.pixmap_item)
        self.scene.setSceneRect(QRectF(pixmap.rect()))

        # 2. 重新创建十字准心（因为 scene.clear 把它删了）
        pen = QPen(QColor(255, 255, 255, 180), 1, Qt.DashLine)  # 半透明白色虚线
        self.v_line = self.scene.addLine(0, 0, 0, 0, pen)
        self.h_line = self.scene.addLine(0, 0, 0, 0, pen)
        # 确保准心在最顶层
        self.v_line.setZValue(999)
        self.h_line.setZValue(999)

        # 初始重置缩放并自适应显示
        self.resetTransform()
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        # 1. 尝试将事件传递给场景中的物体 (LabelRect 等)
        # super().mousePressEvent 会调用场景中物体的 mousePressEvent
        super().mousePressEvent(event)

        # 2. 【状态检查】检查事件是否已被物体消费
        if event.isAccepted():
            # 如果 LabelRect 处理了缩放或移动，这里直接返回，不再执行下方的画图逻辑
            return

        # 3. 只有未被处理的左键点击才视为“开始画新框”
        if event.button() == Qt.LeftButton and self.pixmap_item:
            item = self.itemAt(event.pos())
            if item is None or isinstance(item, QGraphicsLineItem) or item == self.pixmap_item:
                self.start_pos = self.mapToScene(event.pos())
                self.is_drawing = True
                self.current_rect = self.scene.addRect(
                    QRectF(self.start_pos, self.start_pos),
                    QPen(QColor(self.current_color), 2)
                )
        # 右键：开启平移模式 (新增)
        elif event.button() == Qt.RightButton:
            self.is_panning = True
            self.last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        rect = self.scene.sceneRect()

        # 3. 更新十字准心位置
        if self.v_line and self.h_line:
            self.v_line.setLine(scene_pos.x(), rect.top(), scene_pos.x(), rect.bottom())
            self.h_line.setLine(rect.left(), scene_pos.y(), rect.right(), scene_pos.y())

        # 处理绘画
        if self.is_drawing and self.start_pos:
            curr_pos = self.mapToScene(event.pos())
            rect = QRectF(self.start_pos, curr_pos).normalized()
            rect = rect.intersected(self.scene.sceneRect())
            self.current_rect.setRect(rect)

        # 处理平移 (新增)
        elif self.is_panning:
            delta = event.pos() - self.last_pan_pos
            self.last_pan_pos = event.pos()
            # 调整滚动条实现平移效果
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # 结束绘画
        if event.button() == Qt.LeftButton and self.is_drawing:
            final_rect = self.current_rect.rect()
            self.scene.removeItem(self.current_rect)
            if final_rect.width() > 5 and final_rect.height() > 5:
                color = self.get_color(self.current_class_id)
                name = self.class_names[self.current_class_id] if self.current_class_id < len(
                    self.class_names) else "Unknown"

                box = LabelRect(final_rect, self.current_class_id, color, name)
                # 任务 4 修正：手动画框也必须绑定回调
                box.set_update_callback(lambda b=box: self.item_updated.emit(b))
                self.scene.addItem(box)
                self.item_added.emit(box)  # 同步到列表
                self.box_added.emit(final_rect)
            self.is_drawing = False
            self.start_pos = None

        # 结束平移 (新增)
        elif event.button() == Qt.RightButton:
            self.is_panning = False
            self.setCursor(Qt.ArrowCursor)

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        """增强滚轮缩放：以鼠标指针为中心缩放"""
        zoom_in_factor = 1.25
        zoom_out_factor = 1 / zoom_in_factor

        if event.angleDelta().y() > 0:
            self.scale(zoom_in_factor, zoom_in_factor)
        else:
            self.scale(zoom_out_factor, zoom_out_factor)

    def contextMenuEvent(self, event):
        """需求 4：右键快捷菜单"""
        item = self.itemAt(event.pos())
        if isinstance(item, LabelRect):
            menu = QMenu()

            # 修改类别子菜单
            change_menu = menu.addMenu("🏷 修改类别为")
            for i, name in enumerate(self.class_names):
                action = change_menu.addAction(name)
                # 使用 lambda 捕获当前索引
                action.triggered.connect(
                    lambda chk, idx=i, n=name: self.update_selected_boxes_class(idx, self.get_color(idx), n))

            menu.addSeparator()

            # 删除操作
            del_action = menu.addAction("🗑 删除 (Delete)")
            del_action.triggered.connect(lambda: print(f"[LOG] 右键菜单触发删除, UID: {item.uid}"))
            del_action.triggered.connect(lambda: self.scene.removeItem(item))
            del_action.triggered.connect(lambda: self.item_removed.emit(item.uid))

            menu.exec_(event.globalPos())

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # 需求 11：Ctrl + C 复制
        if key == Qt.Key_C and modifiers == Qt.ControlModifier:
            self.clipboard = []
            selected = self.scene.selectedItems()
            for item in selected:
                if isinstance(item, LabelRect):
                    self.clipboard.append({
                        'class_id': item.label_id,
                        'rect': item.rect()
                    })
            return

        # 需求 11：Ctrl + V 粘贴
        if key == Qt.Key_V and modifiers == Qt.ControlModifier:
            if not self.clipboard: return

            # 取消当前选中，方便观察新粘贴的框
            self.scene.clearSelection()

            for data in self.clipboard:
                # 粘贴时，如果是在同一张图，可以略微偏移 10 像素以示区分
                # 但跨图通常需要原位，这里默认原位，你可以根据需要调整
                new_box = LabelRect(data['rect'], data['class_id'],
                                    self.get_color(data['class_id']),
                                    self.class_names[data['class_id']] if data['class_id'] < len(
                                        self.class_names) else "Unknown")
                self.scene.addItem(new_box)
                new_box.setSelected(True)
            return

        # 删除逻辑
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            for item in self.scene.selectedItems():
                if isinstance(item, LabelRect):
                    print(f"[LOG] 键盘触发删除, UID: {item.uid}")
                    self.item_removed.emit(item.uid)  # 同步到列表
                    self.scene.removeItem(item)
        else:
            # 必须调用父类，否则保存(S)等快捷键会失效
            super().keyPressEvent(event)

    def add_label_box(self, rect, class_id, color="#e74c3c"):
        """外部注入标注框"""
        draw_color = color if color != "#e74c3c" else self.get_color(class_id)
        name = self.class_names[class_id] if class_id < len(self.class_names) else "Unknown"
        box = LabelRect(rect, class_id, draw_color, name)
        # 任务 4 修正：必须先设置回调，再 addItem，防止初始化触发变动导致崩溃
        box.set_update_callback(lambda b=box: self.item_updated.emit(b))
        self.scene.addItem(box)
        self.item_added.emit(box) # 初始化或 AI 注入时同步

    def get_all_boxes(self):
        """获取所有标注框坐标"""
        boxes = []
        for item in self.scene.items():
            if isinstance(item, LabelRect):
                # 不要用 item.rect()，因为它不包含位移信息
                # 使用 sceneBoundingRect() 获取物体在画布上的真实像素坐标
                boxes.append((item.label_id, item.sceneBoundingRect()))
        return boxes

    def update_selected_boxes_class(self, class_id, color, class_name):
        """【新增】将当前画布上所有被选中的框，修改为指定的类别"""
        selected_items = self.scene.selectedItems()
        for item in selected_items:
            if isinstance(item, LabelRect):
                item.update_class(class_id, color, class_name)
                self.item_updated.emit(item) # 任务 4：属性修改同步

        # 同时更新全局“当前类别”，确保后续画的新框也是这个类
        self.current_class_id = class_id
        self.current_color = color
