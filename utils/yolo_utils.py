
import os

def convert_to_yolo(size, box):
    """
    将绝对坐标转换为 YOLO 归一化格式 (x_center, y_center, width, height)
    size: (w, h)
    box: (x_min, y_min, x_max, y_max)
    """
    dw = 1.0 / size[0]
    dh = 1.0 / size[1]
    x = (box[0] + box[2]) / 2.0
    y = (box[1] + box[3]) / 2.0
    w = box[2] - box[0]
    h = box[3] - box[1]
    return (round(x * dw, 6), round(y * dh, 6), round(w * dw, 6), round(h * dh, 6))


def yolo_to_pixel(size, yolo_box):
    """YOLO 归一化 -> 像素坐标 (x, y, w, h)"""
    dw, dh = size[0], size[1]
    # yolo_box: [cx, cy, nw, nh]
    cx, cy, nw, nh = yolo_box
    w = nw * dw
    h = nh * dh
    x = (cx * dw) - (w / 2.0)
    y = (cy * dh) - (h / 2.0)
    return (x, y, w, h)


def load_yolo_file(img_path, size):
    """从 .txt 加载标注数据"""
    txt_path = os.path.splitext(img_path)[0] + ".txt"
    if not os.path.exists(txt_path):
        return []

    boxes = []
    try:
        with open(txt_path, 'r') as f:
            for line in f.readlines():
                parts = line.strip().split()
                if len(parts) == 5:
                    cls_id = int(parts[0])
                    yolo_box = [float(x) for x in parts[1:]]
                    pixel_box = yolo_to_pixel(size, yolo_box)
                    boxes.append({"class_id": cls_id, "rect": pixel_box})
        return boxes
    except Exception as e:
        print(f"读取标注失败: {e}")
        return []


def save_yolo_file(img_path, boxes_data):
    """
    生成多类别 YOLO 标注文件
    boxes_data: [(class_id, [cx, cy, nw, nh]), ...]
    """
    txt_path = os.path.splitext(img_path)[0] + ".txt"
    try:
        # 覆盖写入
        with open(txt_path, 'w') as f:
            for cls_id, box in boxes_data:
                line = f"{cls_id} {' '.join(map(str, box))}\n"
                f.write(line)
        return True
    except Exception as e:
        print(f"YOLO写入失败: {e}")
        return False
