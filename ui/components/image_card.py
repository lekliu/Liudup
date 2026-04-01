import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox, QFrame, QHBoxLayout
from PyQt5.QtGui import QPixmap, QFont, QColor
from PyQt5.QtCore import Qt, pyqtSignal

from ui.components.image_preview_dialog import ImagePreviewDialog


class ImageCard(QFrame):
    def __init__(self, local_path, remote_key, metadata, group_paths, on_delete_callback, is_best=False):
        super().__init__()
        self.local_path = local_path
        self.remote_key = remote_key
        self.metadata = metadata
        self.group_paths = group_paths
        self.on_delete_callback = on_delete_callback
        self.is_best = is_best  # 是否为组内最高质量
        self.initUI()

    def initUI(self):
        self.setFixedWidth(240)
        self.setObjectName("ImageCard")

        # 1. 构造悬浮提示文字 (Tooltip)
        w = self.metadata[2]
        h = self.metadata[3]
        size_mb = self.metadata[4] / 1024 / 1024
        tooltip_text = f"分辨率: {w}x{h}\n文件大小: {size_mb:.2f} MB\n路径: {self.local_path}"
        self.setToolTip(tooltip_text)  # 设置给整个卡片

        # 2. 界面样式：保持极简
        self.setStyleSheet("""
                    QFrame#ImageCard { 
                        background: #ffffff; 
                        border: 1px solid #f0f0f0; 
                        border-radius: 12px; 
                    }
                    QFrame#ImageCard:hover { 
                        border-color: #409eff; 
                        background: #fdfdfd; 
                    }
                    /* 顺便放大 Tooltip 的字体 */
                    QToolTip {
                        background-color: #303133;
                        color: white;
                        border: none;
                        padding: 5px;
                        font-size: 14px;
                    }
                """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 3. 缩略图区域
        img_container = QFrame()
        img_container.setFixedHeight(360)
        img_container.setStyleSheet("background: #f5f7fa; border-radius: 8px;")
        img_layout = QVBoxLayout(img_container)
        img_layout.setContentsMargins(0, 0, 0, 0)

        self.img_label = QLabel()
        if os.path.exists(self.local_path):
            pix = QPixmap(self.local_path)
            self.img_label.setPixmap(pix.scaled(240, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img_label.setAlignment(Qt.AlignCenter)
        img_layout.addWidget(self.img_label)
        layout.addWidget(img_container)

        # 4. 剔除此图按钮
        self.btn_del = QPushButton("剔除此图")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFixedHeight(65)
        self.btn_del.setStyleSheet("""
                    QPushButton { 
                        background: #ff4d4f; color: white; border-radius: 6px; 
                        font-weight: bold; font-size: 20px; border: none;
                    }
                    QPushButton:hover { background: #ff7875; }
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
        """快速剔除逻辑：不再弹出确认框"""
        self.on_delete_callback(self.local_path, self.remote_key, self)