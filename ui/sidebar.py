
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy
from PyQt5.QtCore import Qt, pyqtSignal


class NavigationSidebar(QWidget):
    changed_page = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.initUI()

    def initUI(self):
        # 业界通用：设置最小宽度防止侧边栏被拖没，设置最大宽度防止过度拉伸
        self.setMinimumWidth(85)
        self.setMaximumWidth(300)

        # 纵向尺寸策略：强制拉伸填满全高
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # 关键：使用 .NavigationSidebar 确保样式仅作用于本组件及其子类，不污染 Splitter
        self.setStyleSheet("""
            NavigationSidebar { 
                background-color: #2c3e50; /* 深蓝色背景 */
                border-right: 1px solid #1a252f;
            }
            QPushButton { 
                background-color: transparent; 
                color: #ecf0f1; 
                border: none; 
                padding: 25px 0px;   
                font-size: 20px; 
                font-weight: bold;
                width: 100%;
                text-align: center;
            }
            QPushButton:hover { 
                background-color: #34495e; 
                color: white;
            }
            QPushButton[active="true"] { 
                background-color: #3498db; 
                color: white; 
                border-left: 6px solid #ffffff; /* 增加左侧亮条，增强工业感 */
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(0)

        logo = QLabel("LIUDUP\nv3.0")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("color: #3498db; font-weight: 900; margin-bottom: 30px; font-size: 20px;")
        layout.addWidget(logo)

        self.btn_clean = QPushButton("✨\n智能去重")
        self.btn_label = QPushButton("✏️\n交互标注")
        self.btn_train = QPushButton("🚀\n模型训练")

        self.buttons = [self.btn_clean, self.btn_label, self.btn_train]
        for i, btn in enumerate(self.buttons):
            btn.setCursor(Qt.PointingHandCursor)
            # 关键：设置按钮的尺寸策略，使其在水平方向自动拉伸
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda checked, idx=i: self.on_click(idx))
            layout.addWidget(btn)

        layout.addStretch()
        self.on_click(0)

    def on_click(self, index):
        for i, btn in enumerate(self.buttons):
            btn.setProperty("active", "true" if i == index else "false")
            btn.setStyle(btn.style())
        self.changed_page.emit(index)
