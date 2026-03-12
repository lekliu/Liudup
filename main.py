import os
from core.scanner import ImageScanner


def main():
    # 获取当前目录下的测试图片文件夹（你可以先放几张重复图片在 test_imgs 里）
    test_path = input("请输入要扫描的图片文件夹路径: ").strip()

    if not os.path.exists(test_path):
        print("路径不存在！")
        return

    print(f"正在扫描: {test_path} ...")
    scanner = ImageScanner()

    # 执行去重分析
    duplicates = scanner.find_duplicates(test_path)

    # 打印结果
    print("\n--- 分析结果 ---")
    found_any = False
    for master, dups in duplicates.items():
        if dups:
            print(f"图片 [ {os.path.basename(master)} ] 有以下重复项:")
            for d in dups:
                print(f"  - {os.path.basename(d)}")
            found_any = True

    if not found_any:
        print("未发现重复图片。")


if __name__ == "__main__":
    main()