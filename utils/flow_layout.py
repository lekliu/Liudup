
from PyQt5.QtWidgets import QLayout, QSizePolicy
from PyQt5.QtCore import QPoint, QRect, QSize, Qt

class FlowLayout(QLayout):
    """业界标准流式布局：支持子控件自动换行"""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        if parent is not None: self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.items = []

    def __del__(self):
        del self.items

    def addItem(self, item): self.items.append(item)

    def count(self): return len(self.items)

    def itemAt(self, index):
        if 0 <= index < len(self.items): return self.items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.items): return self.items.pop(index)
        return None

    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self): return True

    def heightForWidth(self, width): return self.doLayout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items: size = size.expandedTo(item.minimumSize())
        size += QSize(2 * self.contentsMargins().top(), 2 * self.contentsMargins().top())
        return size

    def doLayout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self.items:
            wid = item.widget()
            space_x, space_y = self.spacing(), self.spacing()
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()
