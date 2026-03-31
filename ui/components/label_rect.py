from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem, QGraphicsLineItem
from PyQt5.QtCore import Qt, QRectF, pyqtSignal, QPoint, QPointF
from PyQt5.QtGui import QPen, QColor, QPixmap, QPainter, QCursor


class LabelRect(QGraphicsRectItem):
    HANDLE_SIZE = 8

    def __init__(self, rect, class_id, color, class_name="Unknown"):
        super().__init__(rect)
        self.label_id = class_id
        self.class_name = class_name
        self.color = color
        self.uid = id(self)  # 任务 2：唯一标识符
        self._update_callback = None # 任务 4：回调函数
        self.update_style()
        self.setOpacity(0.6)
        self.setFlags(
            QGraphicsRectItem.ItemIsSelectable |
            QGraphicsRectItem.ItemIsFocusable |
            QGraphicsRectItem.ItemIsMovable |
            QGraphicsRectItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.handle_type = None

    def set_update_callback(self, callback):
        """任务 4：绑定变动时的通知回调"""
        self._update_callback = callback

    def get_handle(self, pos):
        """判断当前鼠标点落在哪个缩放手柄上"""
        r = self.rect()
        w, h = self.HANDLE_SIZE, self.HANDLE_SIZE
        handles = {
            'tl': QRectF(r.left(), r.top(), w, h),
            'tr': QRectF(r.right() - w, r.top(), w, h),
            'bl': QRectF(r.left(), r.bottom() - h, w, h),
            'br': QRectF(r.right() - w, r.bottom() - h, w, h),
            't': QRectF(r.left() + r.width() / 2 - w / 2, r.top(), w, h),
            'b': QRectF(r.left() + r.width() / 2 - w / 2, r.bottom() - h, w, h),
            'l': QRectF(r.left(), r.top() + r.height() / 2 - h / 2, w, h),
            'r': QRectF(r.right() - w, r.top() + r.height() / 2 - h / 2, w, h)
        }
        for name, rect in handles.items():
            if rect.contains(pos):
                return name
        return None

    def hoverMoveEvent(self, event):
        """悬停在手柄上时切换光标形状"""
        handle = self.get_handle(event.pos())
        if handle in ['tl', 'br']:
            self.setCursor(Qt.SizeFDiagCursor)
        elif handle in ['tr', 'bl']:
            self.setCursor(Qt.SizeBDiagCursor)
        elif handle in ['t', 'b']:
            self.setCursor(Qt.SizeVerCursor)
        elif handle in ['l', 'r']:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.handle_type = self.get_handle(event.pos())
            if self.handle_type:
                # 关键：点中手柄，标记事件已处理
                event.accept()
                return
        # 如果没点中手柄，交给父类（QGraphicsRectItem）处理移动
        super().mousePressEvent(event)
        # 如果物体是选中的，super() 会处理移动，我们也应该标记事件已处理
        if self.isSelected():
            event.accept()

    def mouseMoveEvent(self, event):
        if self.handle_type:
            self.prepareGeometryChange()
            pos = event.pos()
            new_rect = self.rect()
            min_size = 5

            if 't' in self.handle_type:
                new_rect.setTop(min(pos.y(), new_rect.bottom() - min_size))
            if 'b' in self.handle_type:
                new_rect.setBottom(max(pos.y(), new_rect.top() + min_size))
            if 'l' in self.handle_type:
                new_rect.setLeft(min(pos.x(), new_rect.right() - min_size))
            if 'r' in self.handle_type:
                new_rect.setRight(max(pos.x(), new_rect.left() + min_size))

            self.setRect(new_rect.normalized())
            if self._update_callback: self._update_callback() 
        else:
            super().mouseMoveEvent(event)
            if self.isSelected() and self._update_callback: 
                self._update_callback() 

    def itemChange(self, change, value):
        """任务 4：监听物体移动状态变更"""
        if change == QGraphicsRectItem.ItemPositionHasChanged and self._update_callback:
            self._update_callback()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        self.handle_type = None
        super().mouseReleaseEvent(event)

    def paint(self, painter, option, widget):
        color = QColor(self.color)
        if self.isSelected():
            painter.setPen(QPen(color.lighter(130), 5, Qt.SolidLine))
            painter.drawRect(self.rect().adjusted(-1, -1, 1, 1))
            painter.setPen(QPen(Qt.white, 2, Qt.DashLine))
        else:
            painter.setPen(QPen(color, 3, Qt.SolidLine))

        painter.setBrush(self.brush())
        r = self.rect()
        painter.drawRect(r)

        # --- 新增：绘制顶部标签 ---
        label_h = 22
        # 安全检查：如果 Box 靠顶，标签显示在 Box 内部上方
        draw_y = r.top() - label_h
        if draw_y < 0: draw_y = r.top()

        # 计算文字宽度
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.width(self.class_name) + 12

        label_rect = QRectF(r.left(), draw_y, text_w, label_h)

        painter.setBrush(color)  # 标签背景与框同色
        painter.setPen(Qt.NoPen)
        painter.drawRect(label_rect)
        painter.setPen(Qt.white)
        painter.drawText(label_rect, Qt.AlignCenter, self.class_name)

        if self.isSelected():
            painter.setPen(QPen(Qt.white, 1))
            painter.setBrush(QColor(self.color))
            r = self.rect()
            w, h = self.HANDLE_SIZE, self.HANDLE_SIZE
            pts = [
                r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                QPointF(r.left() + r.width() / 2, r.top()),
                QPointF(r.left() + r.width() / 2, r.bottom()),
                QPointF(r.left(), r.top() + r.height() / 2),
                QPointF(r.right(), r.top() + r.height() / 2)
            ]
            for pt in pts:
                painter.drawRect(QRectF(pt.x() - w / 2, pt.y() - h / 2, w, h))

    def update_style(self):
        """根据当前颜色更新画笔和刷子"""
        c = QColor(self.color)
        self.setPen(QPen(c, 2))
        self.setBrush(QColor(c.red(), c.green(), c.blue(), 100))  # 半透明填充
        self.update()  # 强制重绘

    def update_class(self, class_id, color, class_name):
        """【新增】修改这个框的类别和颜色"""
        self.label_id = int(class_id)
        self.class_name = class_name
        self.color = color
        self.update_style()
