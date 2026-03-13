from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox,
                             QDialog, QHBoxLayout, QScrollArea, QFrame)
from PyQt5.QtGui import QPixmap, QFont, QWheelEvent, QGuiApplication
from PyQt5.QtCore import Qt, QSize
import os


# =============================================================
# FILE: ui\components.py
# 核心组件库：包含高清预览对话框与图片信息展示卡片
# =============================================================

class ImagePreviewDialog(QDialog):
    """
    高级预览对话框：支持长截图优化显示。
    功能特性：
    1. 初始加载自动计算自适应缩放比例（Fit-to-Window）。
    2. 按住 Shift 键配合滚轮进行 10% - 1000% 的精准缩放。
    3. 支持鼠标左键抓手拖拽查看高清大图细节。
    4. 左右方向键切换相似组内的其他图片。
    """

    def __init__(self, image_paths, current_index, parent=None):
        super().__init__(parent)
        self.image_paths = image_paths
        self.index = current_index
        self.scale_factor = 1.0  # 初始缩放因子，将在 load_image 中重新计算

        # 鼠标拖拽平移相关状态
        self.is_dragging = False
        self.last_mouse_pos = None

        self.initUI()
        self.load_image()

    def initUI(self):
        # 获取屏幕可用区域，限制对话框最大尺寸
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.max_w, self.max_h = screen.width() * 0.9, screen.height() * 0.9

        self.setWindowTitle("图片高清对比预览")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # ---------------------------------------------------------
        # 1. 顶部控制栏：高度锁定为 60px，确保按钮与标签视觉对齐
        # ---------------------------------------------------------
        self.tool_bar = QFrame()
        self.tool_bar.setObjectName("ToolBar")
        self.tool_bar.setStyleSheet("""
            QFrame#ToolBar { 
                background-color: #2c3e50; 
                border-bottom: 2px solid #1a252f;
            }
        """)
        self.tool_bar.setFixedHeight(60)

        # 工具栏布局：上下边距设为 0，靠内容组件的 min-height 撑起
        tool_layout = QHBoxLayout(self.tool_bar)
        tool_layout.setContentsMargins(15, 0, 15, 0)
        tool_layout.setSpacing(12)

        # 统一样式模板：强制所有组件高度为 32px，解决视觉不协调问题
        common_item_css = """
            height: 32px; 
            padding: 0px 15px; 
            font-weight: bold; 
            border-radius: 4px;
        """

        # 上一张 / 下一张按钮
        self.btn_prev = QPushButton("◀ 上一张")
        self.btn_next = QPushButton("下一张 ▶")

        btn_style = f"""
            QPushButton {{ 
                background-color: #34495e; 
                color: white; 
                border: 1px solid #5d6d7e;
                {common_item_css}
            }}
            QPushButton:hover {{ 
                background-color: #5d6d7e; 
                border-color: #3498db; 
            }}
        """
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_prev.setCursor(Qt.PointingHandCursor)
        self.btn_next.setCursor(Qt.PointingHandCursor)

        # 文件名显示标签 (蓝色药丸风格)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet(f"""
            QLabel {{ 
                color: #3498db; 
                background: #1a252f; 
                border-radius: 16px; 
                font-size: 13px;
                {common_item_css}
            }}
        """)

        # 缩放比例显示标签
        self.zoom_label = QLabel("缩放: 100%")
        self.zoom_label.setStyleSheet(f"""
            QLabel {{ 
                background: #34495e; 
                color: white; 
                border-radius: 16px;
                {common_item_css}
            }}
        """)

        # 将组件加入工具栏并强制垂直居中对齐
        tool_layout.addWidget(self.btn_prev, 0, Qt.AlignCenter)
        tool_layout.addStretch()
        tool_layout.addWidget(self.info_label, 0, Qt.AlignCenter)
        tool_layout.addStretch()
        tool_layout.addWidget(self.zoom_label, 0, Qt.AlignCenter)
        tool_layout.addWidget(self.btn_next, 0, Qt.AlignCenter)

        self.layout.addWidget(self.tool_bar)

        # ---------------------------------------------------------
        # 2. 核心滚动显示区
        # ---------------------------------------------------------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet("background-color: #1a1a1a; border: none;")

        self.img_display = QLabel()
        self.img_display.setAlignment(Qt.AlignCenter)
        self.img_display.setCursor(Qt.OpenHandCursor)  # 默认抓手形状

        self.scroll.setWidget(self.img_display)
        self.layout.addWidget(self.scroll)

        # 事件绑定
        self.btn_prev.clicked.connect(lambda: self.switch_image(-1))
        self.btn_next.clicked.connect(lambda: self.switch_image(1))

    def load_image(self):
        """加载当前索引的图片并计算初始自适应缩放比"""
        path = self.image_paths[self.index]
        fname = os.path.basename(path)

        # 文件名截断逻辑
        if len(fname) > 40:
            fname = fname[:20] + "..." + fname[-15:]
        self.info_label.setText(fname)

        self.pixmap = QPixmap(path)
        if self.pixmap.isNull():
            return

        # 获取原始尺寸
        img_w, img_h = self.pixmap.width(), self.pixmap.height()

        # 第一步：初步调整窗口大小以适应图片，但不超过屏幕
        win_w = int(min(img_w + 50, self.max_w))
        win_h = int(min(img_h + 120, self.max_h))
        self.resize(win_w, win_h)

        # 第二步：计算自适应缩放因子 (Fit-to-Window)
        # 减去工具栏高度(60)及内部边距偏移
        available_w = win_w - 20
        available_h = win_h - 100

        scale_w = available_w / img_w
        scale_h = available_h / img_h

        # 取最小比例确保图片完整可见，且最大缩放限制在 1.0 (防止小图拉伸模糊)
        self.scale_factor = min(1.0, scale_w, scale_h)

        self.update_display()

    def update_display(self):
        """执行实际的缩放渲染逻辑"""
        if self.pixmap.isNull():
            return

        target_w = int(self.pixmap.width() * self.scale_factor)
        target_h = int(self.pixmap.height() * self.scale_factor)

        display_pix = self.pixmap.scaled(
            target_w, target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.img_display.setPixmap(display_pix)
        self.zoom_label.setText(f"缩放: {int(self.scale_factor * 100)}%")

    def wheelEvent(self, event: QWheelEvent):
        """滚轮事件：按住 Shift 键进行缩放，否则执行默认滚动"""
        if event.modifiers() == Qt.ShiftModifier:
            # 缩放步长计算
            angle = event.angleDelta().y()
            factor = 1.1 if angle > 0 else 0.9
            new_factor = self.scale_factor * factor

            # 限制缩放范围在 10% 到 1000% 之间
            if 0.1 <= new_factor <= 10.0:
                self.scale_factor = new_factor
                self.update_display()

            event.accept()  # 拦截事件，防止触发滚动条滑动
        else:
            # 默认逻辑，允许滚轮上下查看长截图
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        """鼠标按下：开启大图抓手拖拽模式"""
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.last_mouse_pos = event.pos()
            self.img_display.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """鼠标移动：计算偏移量并手动调整滚动条位置"""
        if self.is_dragging and self.last_mouse_pos:
            delta = event.pos() - self.last_mouse_pos
            self.last_mouse_pos = event.pos()

            h_bar = self.scroll.horizontalScrollBar()
            v_bar = self.scroll.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())

    def mouseReleaseEvent(self, event):
        """鼠标释放：结束拖拽"""
        self.is_dragging = False
        self.img_display.setCursor(Qt.OpenHandCursor)

    def keyPressEvent(self, event):
        """键盘支持：左右方向键快速切换预览图"""
        if event.key() == Qt.Key_Left:
            self.switch_image(-1)
        elif event.key() == Qt.Key_Right:
            self.switch_image(1)
        else:
            super().keyPressEvent(event)

    def switch_image(self, delta):
        """组内导航逻辑"""
        self.index = (self.index + delta) % len(self.image_paths)
        self.load_image()


class ImageCard(QWidget):
    """
    图片展示卡片：用于在分析结果列表中显示单张图片。
    针对广告素材（长截图）优化了纵横比展示。
    """

    def __init__(self, local_path, remote_key, metadata, group_paths, on_delete_callback):
        super().__init__()
        self.local_path = local_path
        self.remote_key = remote_key
        self.metadata = metadata  # 格式: (path, key, width, height, size)
        self.group_paths = group_paths  # 组内所有图片的路径，用于双击大图切换
        self.on_delete_callback = on_delete_callback

        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        self.setFixedWidth(240)  # 卡片固定宽度
        self.setStyleSheet("""
            ImageCard { 
                background: white; 
                border: 2px solid #e0e0e0; 
                border-radius: 10px; 
            } 
            ImageCard:hover { 
                border-color: #3498db; 
                background-color: #f7f9fa; 
            }
        """)

        # 1. 预览缩略图 (220x380，适配手机端长图比例)
        self.img_label = QLabel()
        self.img_label.setFixedSize(220, 380)
        self.img_label.setCursor(Qt.PointingHandCursor)
        self.img_label.setAlignment(Qt.AlignCenter)

        if os.path.exists(self.local_path):
            pix = QPixmap(self.local_path)
            self.img_label.setPixmap(
                pix.scaled(220, 380, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        layout.addWidget(self.img_label)

        # 2. 元数据信息显示 (分辨率与文件大小)
        if self.metadata:
            info_text = f"📐 {self.metadata[2]}x{self.metadata[3]}\n📦 {self.metadata[4] / 1024 / 1024:.2f}MB"
        else:
            info_text = "元数据未加载"

        info_label = QLabel(info_text)
        info_label.setStyleSheet("color: #2980b9; font-weight: bold; font-size: 12px;")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        # 3. 彻底删除按钮 (样式增强版)
        self.btn_del = QPushButton("彻底删除")
        self.btn_del.setFixedHeight(45)
        self.btn_del.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setStyleSheet("""
            QPushButton { 
                background-color: #e74c3c; 
                color: white; 
                border-radius: 6px; 
            }
            QPushButton:hover { 
                background-color: #c0392b; 
            }
        """)
        self.btn_del.clicked.connect(self.request_delete)
        layout.addWidget(self.btn_del)

    def mouseDoubleClickEvent(self, event):
        """响应双击：弹出高清对比预览对话框"""
        if event.button() == Qt.LeftButton:
            try:
                # 寻找当前图片在组内的索引
                idx = self.group_paths.index(self.local_path)
            except ValueError:
                idx = 0

            dialog = ImagePreviewDialog(self.group_paths, idx, self)
            dialog.exec_()

    def request_delete(self):
        """
        触发删除确认弹窗：
        采用 HTML 格式化展示，突出显示备份逻辑及待处理的文件名。
        """
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("清理确认")
        msg_box.setIcon(QMessageBox.Question)

        file_name = os.path.basename(self.local_path)

        # 构造 Rich Text 格式的确认信息
        msg_text = f"""
            <div style='font-family: "Microsoft YaHei"; min-width: 320px;'>
                <p style='font-size: 15px; color: #2c3e50; font-weight: bold;'>确定要移除这张图片吗？</p>
                <p style='font-size: 13px; color: #7f8c8d; line-height: 1.6;'>
                    该操作将执行以下流程：<br>
                    1. 将本地文件移至 <b style='color: #e67e22;'>_backup</b> 备份目录<br>
                    2. 从云端 <b style='color: #e67e22;'>Minio</b> 桶同步删除该对象
                </p>
                <hr style='border: 0; border-top: 1px solid #eee;'>
                <p style='font-size: 12px; color: #3498db; word-break: break-all;'>
                    <b>待清理文件：</b><br>{file_name}
                </p>
            </div>
        """
        msg_box.setText(msg_text)

        # 自定义交互按钮，增强操作预期
        btn_confirm = msg_box.addButton(" 确认移动并删除 ", QMessageBox.AcceptRole)
        btn_cancel = msg_box.addButton(" 保留图片 ", QMessageBox.RejectRole)

        # 默认焦点设为取消，防止误删
        msg_box.setDefaultButton(btn_cancel)

        # 应用按钮层级样式
        msg_box.setStyleSheet("""
            QMessageBox { background-color: white; }
            QPushButton { 
                padding: 7px 20px; 
                border-radius: 4px; 
                font-weight: bold; 
                min-width: 110px;
            }
            QPushButton[text=" 确认移动并删除 "] { 
                background-color: #e74c3c; color: white; border: none; 
            }
            QPushButton[text=" 确认移动并删除 "]:hover { 
                background-color: #c0392b; 
            }
            QPushButton[text=" 保留图片 "] { 
                background-color: #ecf0f1; color: #2c3e50; border: 1px solid #bdc3c7; 
            }
            QPushButton[text=" 保留图片 "]:hover { 
                background-color: #bdc3c7; 
            }
        """)

        msg_box.exec_()

        # 如果用户点击了确认按钮
        if msg_box.clickedButton() == btn_confirm:
            self.on_delete_callback(self.local_path, self.remote_key, self)

# =============================================================
# END OF FILE: ui\components.py
# =============================================================