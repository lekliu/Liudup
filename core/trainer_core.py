import sys
import time

from PyQt5.QtCore import QThread, pyqtSignal
from ultralytics import YOLO
import os
from utils.config_manager import ProjectPaths


class StreamRedirector:
    """将标准输出重定向到信号"""

    def __init__(self, signal):
        self.signal = signal

    def write(self, text):
        if text.strip():
            self.signal.emit(text)

    def flush(self):
        pass


class TrainingWorker(QThread):
    """
    YOLOv8 训练执行线程
    """
    log_signal = pyqtSignal(str)
    metrics_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(str)

    def __init__(self, yaml_path, epochs=10, batch_size=16, base_model='yolov8n.pt'):
        super().__init__()
        self.yaml_path = yaml_path
        self.epochs = epochs
        self.batch_size = batch_size
        self.base_model = base_model  # 新增：指定起点
        self._stop_flag = False  # 自定义中止标志

    def stop(self):
        """温和的中止请求"""
        self._stop_flag = True

    def run(self):
        try:
            # 使用指定的起点进行训练
            model = YOLO(self.base_model)
            self.batch_cnt = 0  # 任务 3：手动批次计数器
            self.train_start_time = time.time()  # 任务 6：记录启动时间

            def on_train_epoch_start(trainer):
                self.batch_cnt = 0  # 每轮开始重置计数

            # --- 定义回调函数，安全地将进度发给 UI ---
            def on_train_batch_end(trainer):
                if self._stop_flag:
                    # 抛出异常是强制 YOLO 中止训练的最有效方法
                    raise Exception("USER_STOP")

                # 任务 3：手动累加计数，解决 trainer 缺少 batch 属性的问题
                self.batch_cnt += 1
                batch_i = self.batch_cnt

                # 【任务 3 修正】：多路径获取总批次数，确保 UI 显示真实的进度（如 1/8）
                nb = getattr(trainer, 'nb', 0)

                if nb == 0 and hasattr(trainer, 'train_loader'):
                    nb = len(trainer.train_loader)
                nb = nb if nb > 0 else 1

                # --- 任务 6：简单平滑 ETA 计算 ---
                elapsed = time.time() - self.train_start_time
                # 计算当前总进度 (基于已完成的 epoch 和 当前 batch)
                total_epochs = getattr(trainer, 'epochs', 1)
                current_epoch = getattr(trainer, 'epoch', 0)
                total_done_batches = current_epoch * nb + batch_i
                total_all_batches = total_epochs * nb

                eta_seconds = 0
                if total_done_batches > 0:
                    speed = elapsed / total_done_batches
                    eta_seconds = speed * (total_all_batches - total_done_batches)

                # 发送 Batch 级实时指标
                self.metrics_signal.emit({
                    "type": "batch",
                    "batch_idx": batch_i,
                    "total_batches": nb,
                    "box_loss": float(trainer.loss_items[0]) if hasattr(trainer, 'loss_items') else 0,
                    "cls_loss": float(trainer.loss_items[1]) if hasattr(trainer, 'loss_items') and len(
                        trainer.loss_items) > 1 else 0,
                    "dfl_loss": float(trainer.loss_items[2]) if hasattr(trainer, 'loss_items') and len(
                        trainer.loss_items) > 2 else 0,
                    "eta": eta_seconds  # 任务 6 新增
                })

            def on_fit_epoch_end(trainer):
                """在训练和验证全部结束后触发，此时 metrics 才有数据"""
                metrics = {
                    "type": "epoch",
                    "epoch": getattr(trainer, 'epoch', 0) + 1,
                    "epochs": getattr(trainer, 'epochs', 0),
                    "box_loss": float(trainer.loss_items[0]) if hasattr(trainer, 'loss_items') else 0,
                    "cls_loss": float(trainer.loss_items[1]) if hasattr(trainer, 'loss_items') and len(
                        trainer.loss_items) > 1 else 0,
                    "dfl_loss": float(trainer.loss_items[2]) if hasattr(trainer, 'loss_items') and len(
                        trainer.loss_items) > 2 else 0,
                }

                # --- 任务 8：提取学习率 ---
                lr_val = 0
                if hasattr(trainer, 'optimizer'):
                    lr_val = trainer.optimizer.param_groups[0]['lr']
                elif hasattr(trainer, 'lr'):  # 备选路径
                    lr_val = trainer.lr[0] if isinstance(trainer.lr, list) else trainer.lr
                metrics["lr"] = lr_val

                # --- 任务 8 新增：提取当前学习率 ---
                if hasattr(trainer, 'optimizer'):
                    # 通常取第一个参数组的学习率
                    metrics["lr"] = trainer.optimizer.param_groups[0]['lr']

                # 1. 获取全局指标 (Precision, Recall, mAP50)
                t_metrics = getattr(trainer, 'metrics', {})
                if t_metrics:
                    # 使用您诊断出的确切 Key 名进行提取
                    metrics["map50"] = t_metrics.get('metrics/mAP50(B)', 0)
                    metrics["precision"] = t_metrics.get('metrics/precision(B)', 0)
                    metrics["recall"] = t_metrics.get('metrics/recall(B)', 0)
                else:
                    # 严格保留警告诊断逻辑
                    self.log_signal.emit("🔎 警告: trainer.metrics 为空字典")

                # 2. 提取每个类别的明细指标 (解决类别数据 0 长度问题)
                if hasattr(trainer, 'validator'):
                    v_metrics = getattr(trainer.validator, 'metrics', None)
                    if v_metrics:
                        # 核心兼容逻辑：针对新版 YOLO 指标嵌套在 .box 属性中的情况
                        box_metrics = getattr(v_metrics, 'box', v_metrics)
                        aps = getattr(box_metrics, 'ap50', [])

                        # 从模型中抓取类别 ID 到名称的映射
                        names = getattr(trainer.model, 'names', {})
                        class_map = {}
                        for i, ap in enumerate(aps):
                            if i in names:
                                class_map[names[i]] = float(ap)
                        # 将构造好的类别明细存入 metrics
                        metrics["class_data"] = class_map

                # 3. 发送信号给 UI 并打印带结果的日志
                self.metrics_signal.emit(metrics)
                self.log_signal.emit(f"✅ Epoch {metrics['epoch']} 完成 - mAP50: {metrics['map50']:.4f}")

            model.add_callback("on_train_epoch_start", on_train_epoch_start)
            model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
            # 注册回调
            model.add_callback("on_train_batch_end", on_train_batch_end)

            # 启动训练
            results = model.train(
                data=self.yaml_path,
                epochs=self.epochs,
                batch=self.batch_size,
                imgsz=640,
                project=ProjectPaths.RUNS_DIR,
                name=ProjectPaths.TRAIN_NAME,
                exist_ok=True,
                verbose=True,
                workers=0  # CPU 环境务必设为 0 防止多进程内存冲突
            )

            # 【核心修复】results.save_dir 是 YOLO 实际创建的那个“套娃”后的文件夹
            # 比如：D:\...\runs\detect\runs\liudup_train
            actual_save_dir = str(results.save_dir)

            # 将这个实际路径拼上 weights/best.pt 传回 UI
            real_best_path = os.path.join(actual_save_dir, "weights", "best.pt")

            # 发送信号，把这个“真实路径”带过去
            self.finished_signal.emit(real_best_path)

        except Exception as e:
            if str(e) == "USER_STOP":
                self.log_signal.emit("\n🛑 训练已被用户中止。")
            else:
                self.log_signal.emit(f"❌ 训练中断: {str(e)}")
            self.finished_signal.emit("")