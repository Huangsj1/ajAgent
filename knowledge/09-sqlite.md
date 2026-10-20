# SQLite 工具 v1

数据库使用只读 URI 连接。查询默认最多返回 50 行，可调整到 100 行。写入、ATTACH 和 PRAGMA 不允许执行。递归查询通过 progress handler 检查超时。
