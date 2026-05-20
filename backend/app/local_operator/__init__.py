"""Local Operator 提供本地 read/write/exec 能力的底层模块。

第一阶段只开放 read-only 工具。这里的模块不直接绑定 Memory Chat Graph，
让后续桌面精灵、独立操作面板也可以复用同一套能力。
"""
