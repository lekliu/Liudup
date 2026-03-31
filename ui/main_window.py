
from PyQt5.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QStackedWidget, QSplitter
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from core.database import DatabaseManager
from ui.sidebar import NavigationSidebar
from ui.pages.cleaner_page import CleanerPage
from ui.pages.labeller_page import LabellerPage
from ui.pages.trainer_page import TrainerPage
from ui.pages.cloud_page import CloudPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Liudup v3.0 - 一体化 AI 数据工厂")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 创建拆分器
        self.splitter = QSplitter(Qt.Horizontal)

        # 2. 业界通用样式：必须显式定义 handle 样式，否则在 Win10/11 上几乎不可见
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #dcdfe6; /* 默认浅灰色线条 */
                width: 4px;               /* 手柄视觉宽度 */
            }
            QSplitter::handle:hover {
                background-color: #3498db; /* 鼠标悬停变蓝，指示可拖拽 */
            }
            QSplitter::handle:pressed {
                background-color: #2980b9; /* 点击时加深蓝色 */
            }
        """)

        self.splitter.setCollapsible(0, False)

        # 3. 初始组件
        self.sidebar = NavigationSidebar()
        self.stack = QStackedWidget()

        # 加载页面（逻辑保持不变）
        self.cleaner_page = CleanerPage(self.db)
        self.labeller_page = LabellerPage(self.db)
        self.trainer_page = TrainerPage(self.db)
        self.cloud_page = CloudPage(self.db)
        self.stack.addWidget(self.cleaner_page)
        self.stack.addWidget(self.labeller_page)
        self.stack.addWidget(self.trainer_page)
        self.stack.addWidget(self.cloud_page)

        # 4. 组装拆分器
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.stack)

        # 业界通用设置：
        self.splitter.setHandleWidth(4)  # 设置手柄的交互热区宽度
        self.splitter.setCollapsible(0, False)  # 禁止侧边栏被拖拽到 0 像素（彻底消失）
        self.splitter.setStretchFactor(1, 1)  # 设置右侧工作区随窗口缩放自动拉伸

        # 设置初始分布：左侧 180px，右侧占据剩余全部空间
        self.splitter.setSizes([180, 1000])

        main_layout.addWidget(self.splitter)
        self.sidebar.changed_page.connect(self.on_page_changed)

    def on_page_changed(self, index):
        self.stack.setCurrentIndex(index)
        if index == 2:
            self.trainer_page.refresh_status()

    def showMaximized(self):
        super().showMaximized()
