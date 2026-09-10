# Functions 打包与本机宿主验收

2026-09-10。已使用真实 Core Tools 4.13.0、Python 3.12、Azurite 3.36.0 运行现有 Functions 应用。数据库为隔离 SQLite，事件为明确标识的合成数据；不是 Azure 部署证明。

## 可复现命令

```sh
uv sync --frozen
uv run python -m scripts.package_functions
uv run python -m experiments.functions_runtime --output evidence/functions-runtime-local.json
```

第二条生成 `data/anke-sports-functions.zip` 和同名 `.manifest.json`，仅含 Git 已跟踪的 `app/**/*.py`、`migrations/**/*.py` 及四个入口/依赖/迁移配置文件。读取当前工作区内容，包含已跟踪文件的未提交修改；新增运行时文件必须先明确加入 Git。文件哈希与 ZIP 哈希可核对，相同输入产生相同归档。拒绝符号链接和缺失入口。

归档只含源码，需要目标 Linux 环境安装锁定的 `requirements.txt`；不是含本机虚拟环境的可直接运行包。`.funcignore` 同时排除本机数据、环境、服务账号、缓存和测试文档，但正式打包以显式白名单脚本为准。CI增加安全打包检查，尚未推送或远程运行。依据：[Python 构建方式](https://learn.microsoft.com/en-us/azure/azure-functions/python-build-options)。

第三条要求已安装 `func`、`azurite`。实验解压实际归档，使用随机本机端口、一次性模拟器账号、独立 SQLite/加密密钥，关闭本地体验登录及真实 Provider；不读取现有云凭据。进程、数据和原始日志位于私有临时目录，完成或失败均终止进程组并清理。第一次启动可能下载官方 extension bundle。依据：[Core Tools 本地运行](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)。

## 已验证的流程

- 实际宿主 HTTP 状态返回200；匿名个人日历401，本地体验登录404。
- 未直接调用 Python 调度函数：真实分钟定时器投递 JSON，队列触发器执行 outbox，发布含一场合成比赛的 Feed；相同 ETag 返回304。
- 同一已完成任务重复投递两条消息，队列排空后业务尝试次数仍为1，Feed正文与ETag不变。
- 公共 MCP 在同一 ASGI 宿主完成三个工具发现及真实 `get_event` 调用，返回合成比赛。
- 经宿主本机管理接口触发内容维护，等待数据库中的过期授权请求实际移除；这部分验证宿主触发及业务结果，不代表自然五分钟周期观察。
- 本次宿主日志未出现测试Feed令牌或模拟器存储密钥；不能外推 Azure 平台请求遥测、代理或 Application Insights。

起初 Queue SDK 默认REST版本超出模拟器支持范围，模拟器返回400 `InvalidHeaderValue`。现固定双方支持的 `2025-11-05`，没有关闭模拟器版本检查。Azure支持显式选择受支持的服务版本，见[Storage版本规则](https://learn.microsoft.com/en-us/rest/api/storageservices/versioning-for-the-azure-storage-services)。队列编码仍为 `none`，与原明文 JSON 契约一致。

## 证据与剩余边界

结果见 [functions-runtime-2026-09-10.json](../evidence/functions-runtime-2026-09-10.json)。本机后端180项通过、2项MySQL条件跳过（本批没有重启MySQL）、ruff通过。新增3项打包测试包含已跟踪敏感文件、未跟踪源码、符号链接和缺失入口；运行依赖与 `uv.lock` 导出一致。

独立 Azure 资源、云端 MySQL TLS、Functions Firebase身份、队列死信与告警、远端构建、平台日志、HTTPS及手机刷新仍未验收。`T05/T13/T28/T33`保持 `in_progress`。部署前须冻结具体开发资源、费用边界与恢复方案，不复用其他产品资源。
