# MySQL 运行与验收

目标数据库为 Azure MySQL 8.4；本机 SQLite 是独立适配器。2026-09-10 已在实际 MySQL Community 8.4.11 ARM64 进程运行后端测试和迁移。此结果不代表 Azure TLS、网络、触发器或云端容量验收。

## 连接与身份约束

- 应用连接显式使用 `READ COMMITTED`。业务代码在网络返回、取得账号锁后重新读取版本；MySQL 默认的 `REPEATABLE READ` 会继续读取旧事务快照，导致旧确认错误通过。已有账号锁、版本和任务租约检查继续保留。
- 25 张业务表使用 `utf8mb4_0900_bin`，精确区分大小写、重音与尾空格。Firebase UID、外部 ID 和权限主体不能使用默认的大小写不敏感比较。
- MySQL 的项目预算首次插入使用保持原值的 upsert，直接取得排他锁。重复 INSERT 后再升级共享锁的旧路径，在多请求首次创建预算行时出现死锁。
- 非本地连接继续使用系统 CA 与主机名验证的 TLS；本地 socket 测试不替代 Azure TLS 验收。

## 可重复测试

仅使用专门启动的、可丢弃的 loopback MySQL 实例。测试凭据不要对应任何已有业务实例。`ANKE_TEST_MYSQL_URL` 必须没有数据库名，驱动为 `mysql+pymysql`；每个 fixture 创建随机 `anke_test_<uuid>` 库并在退出时删除自己创建的库。没有设置变量时默认运行 SQLite。

```sh
ANKE_TEST_MYSQL_DISPOSABLE=1 \
ANKE_TEST_MYSQL_URL='mysql+pymysql://TEST_USER:TEST_PASSWORD@127.0.0.1:3306/?charset=utf8mb4' \
uv run pytest -q
```

实际凭据通过本地环境注入，不提交到 Git。也支持 URL 的 `unix_socket` 参数。CI 使用固定版本和摘要的官方 MySQL 镜像，独立测试密码以及仅绑定 loopback 的端口。配置已加入 checks workflow；尚未 push 或运行远程 CI。

## 迁移与恢复

`f809a45c2d71` 将已有 MySQL 业务表转换为精确比较；SQLite 为无数据操作。MySQL ALTER TABLE 可能重建表，DDL 不能当作整批事务回滚。云执行前应核对目标、备份、容量和维护窗口；本机结果不能授权云执行。

回退到 `e42c08f771d3` **保留二进制排序规则**。回退为大小写不敏感比较可能合并后来创建的独立身份，因此不会自动逆转。旧版业务代码仍可使用这些表。真正需要恢复旧排序规则时，应另行审计冲突并制定恢复方案。

升级旧库、原身份保留、两个仅大小写不同身份并存、回退后保留、再升级及 `alembic check` 已在实际 MySQL 临时库通过。测试不连接现有 FormaLM 数据库。

官方资料：[MySQL 8.4 下载](https://dev.mysql.com/downloads/mysql/8.4.html)、[初始化数据目录](https://dev.mysql.com/doc/refman/8.4/en/data-directory-initialization.html)、[Azure MySQL 版本策略](https://learn.microsoft.com/en-us/azure/mysql/concepts-version-policy)。
