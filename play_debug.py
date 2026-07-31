"""
帶崩潰日誌的啟動器。用它代替直接運行 tictactoe3d_pygame。
用法（在倉庫根目錄）：  python play_debug.py
正常玩；如果一落子崩潰退出，錯誤會寫進同目錄的 crash_log.txt。
把 crash_log.txt 的內容發給我即可定位。
"""
import faulthandler
import sys
import traceback

logf = open("crash_log.txt", "w", encoding="utf-8")
faulthandler.enable(file=logf)   # 捕獲原生段錯誤 (segfault) 之類

def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    logf.write(line + "\n")
    logf.flush()

log("=== play_debug 啟動 ===")
log("python:", sys.version)
try:
    import numpy, torch, pygame
    log("numpy", numpy.__version__, "| torch", torch.__version__, "| pygame", pygame.__version__)
except Exception as e:
    log("依賴導入異常:", repr(e))

try:
    import tictactoe3d_pygame as pg
    log("模塊導入成功，啟動 main() ...")
    pg.main()
    log("main() 正常結束")
except SystemExit:
    log("正常退出 (SystemExit)")
except BaseException as e:
    log("!!! 崩潰:", repr(e))
    traceback.print_exc()
    traceback.print_exc(file=logf)
finally:
    logf.flush()
    logf.close()
