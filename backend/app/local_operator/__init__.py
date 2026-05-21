"""Local Operator 提供本地 read/write/exec 能力的底层模块。

当前已开放 read 工具和第一版 write_file 整文件写入工具。这里的模块不直接绑定
Memory Chat Graph，让后续桌面精灵、独立操作面板也可以复用同一套能力。
"""
