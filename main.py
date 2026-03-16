
import os
import sys

# =============================================================================
# 核心修正：模仿参考代码的加载顺序，在主线程最顶端解决 DLL 冲突
# =============================================================================
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
# 禁止 numpy 2.0 可能带来的路径干扰
os.environ['OPENBLAS_MAIN_FREE'] = '1'

try:
    import torch
    # 在主线程预先初始化 c10.dll，防止子线程初始化 1114 错误
    print(f"[*] 引擎初始化成功:  Torch {torch.__version__}")
except Exception as e:
    print(f"[!] 预加载预警: {e}")

from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)

    # --- 增加：大字号全局设置 ---
    font = app.font()
    font.setPointSize(11)
    font.setFamily("Microsoft YaHei")
    app.setFont(font)

    app.setStyle("Fusion")
    try:
        window = MainWindow()
        # window.show()
        # 在对象完全构建且布局锁定后，执行唯一的最大化指令
        window.showMaximized()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"程序运行崩溃: {e}")

if __name__ == "__main__":
    main()