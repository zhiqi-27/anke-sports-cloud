# Azure 开发环境：原 MySQL 方案暂缓

**最新状态（2026-09-10）：用户未批准此费用方案，要求参考 Anke Money 寻找更便宜的组合。已提出 [Cosmos Serverless 低成本候选](azure-low-cost-proposal.md)。下文保留原 MySQL 方案和验证记录供比较，不代表当前获准创建的范围。**

2026-09-10。Bicep已通过本机编译和Azure订阅级`validate`（Succeeded），没有创建资源。验证使用明确标识的HTTPS占位域名；真实Web域名、私网迁移和部署后验收仍未完成。

## 创建范围

| 项目 | 拟创建配置 |
| --- | --- |
| 订阅/资源组 | Azure subscription 1 / 新建 `anke-sports-dev` |
| 区域 | East Asia（香港），已查到支持Flex和MySQL8.4/B1ms |
| 后端 | Python3.12，Linux Functions Flex Consumption，2GB/实例，常驻实例0，平台允许的最小伸缩上限40 |
| 数据库 | 独立MySQL8.4，B1ms/1vCPU/2GB、20GB存储、7天备份，不启用HA或异地备份，不自动扩存储 |
| 网络 | 专用VNet与Functions/MySQL两个子网；MySQL无公共网络入口 |
| 存储 | 独立Standard LRS，代码容器/队列；禁用公共Blob和共享密钥，运行身份限定在本资源范围 |
| 凭据 | 独立Key Vault，Firebase JSON、运行数据库URL和Feed加密密钥由引用注入，运行账号不使用迁移管理员 |

不引用现有FormaLM、Recap或Anke Money资源。资源名以`anke-sports-dev`及订阅/资源组派生的短后缀生成；确切名称在what-if和部署结果中读回。运行时API地址使用Azure返回的`defaultHostName`，不猜测新域名格式。

## 费用与上限边界

已查询[Azure官方零售价格API](https://prices.azure.com/api/retail/prices)，原始选取结果在[价格记录](../evidence/azure-retail-pricing-2026-09-10.json)，币种USD。以下不扣除免费额度或现有账户优惠，不含税费与第三方体育/YouTube费用。

| 项目 | 官方单价与假设 | 月度参考 |
| --- | --- | ---: |
| MySQL B1ms | US$0.0286/小时 × 730小时 | US$20.878 |
| MySQL存储 | US$0.15/GB/月 × 20GB | US$3.00 |
| 数据库基础合计 | 全月运行 | **US$23.88** |
| Functions示例 | 10万次/月，每次2GB × 1秒；US$0.000037/GB秒及US$0.000004/10次 | US$7.44 |

该示例的小计约US$31.32，另加Blob/Queue操作、Key Vault、DNS、出站流量和可能的超额备份等实际用量。Functions例子是预算假设，不是已测量的月负载。建议先以**US$40/月作为开发预算参考**；Azure按量计费，此数不是自动断电或账单硬上限。此规格仅供小规模开发，不能作为千用户云容量承诺。

零售API返回的数据库ARM SKU价格名为`B1MS`，资源规格名为`Standard_B1ms`。首次Pricing工具按ARM规格查询为空；已改用官方零售API核对。补查价格阶梯时遇到429，保留原成功价格记录，不将其伪报为免费额度证明。

## 已完成准备

- 模板：[infra/main.bicep](../infra/main.bicep)、[resources.bicep](../infra/resources.bicep)。Bicep0.46.1编译通过；[Azure validate结果](../evidence/azure-dev-template-validation-2026-09-10.json)为Succeeded。没有执行create、what-if或代码发布。
- Queue SDK发送器支持与Functions绑定同名的`AzureQueueConnection__queueServiceUri/__credential/__clientId`。本地连接字符串仍用于隔离Azurite；云端只用指定托管身份，不回退本机Azure CLI账号。
- Firebase可读取`ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON`，仅接受匹配本项目的服务账号。无此值时保留本机`GOOGLE_APPLICATION_CREDENTIALS`/ADC流程；非法JSON/其他项目/解析错误均脱敏失败。使用独立真实Firebase账号完成Admin只读验证，尚未经过真实Key Vault。
- 后端191项pytest通过，2项MySQL条件跳过，ruff通过；OpenAPI/config契约未变。真实Core Tools/Azurite回归仍通过分钟Timer→Queue→ICS、重复投递、MCP及过期清理；托管身份分支只完成受控SDK边界测试，云端RBAC未验收。

## 创建后必须继续的工作

1. 用真实Web HTTPS来源替换占位值，执行what-if核对新增资源范围和费用，再创建该开发环境。
2. 凭据只写本产品Key Vault；创建受限业务数据库账号。私网迁移执行器尚需准备，迁移前必须核对空库目标并保留恢复方案；不通过开放全网数据库解决可达性。
3. 验证身份角色传播、Key Vault解析、MySQL TLS，然后远端Linux依赖构建并发布安全源码包。
4. 真实Google登录、个人关注/订阅、Timer/Queue/MCP/ICS与异常恢复分别验收。平台请求遥测可能包含私人Feed路径，Application Insights接入要先完成脱敏设计与检查；当前未启用其连接，不宣称监控已完成。
5. 预算提醒、死信告警、云端备份恢复、长期更新、真实YouTube及设备日历仍须完成。回滚保留上一代码包；数据库失败先判断事务/迁移状态，不自动删除资源组或数据库。

当前先明确数据库选型与相应费用，不执行原 MySQL 资源创建。Firebase项目与已运行本机服务保持原状。
