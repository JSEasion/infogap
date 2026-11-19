"""PyInstaller 打包入口（勿改动模块路径，spec 以此为 Analysis 脚本）。"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from market_lens.desktop import main

    main()
