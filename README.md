# 王者荣耀体验服 AMS 兑换逆向

这是一个面向学习与研究的王者荣耀体验服 AMS 奖励兑换工具，提供扫码登录、单项或批量兑换、Web 操作界面，以及每日自动运行与漏跑补偿能力。

## 功能

- `wzry-login`：通过本机 Chrome/CDP 完成 QQ 扫码登录，并在完整兑换凭据就绪后生成 Credential Bundle。
- `wzry-exchange`：列出奖励、兑换单项奖励或执行完整 Exchange Plan。
- `wzry-web`：在浏览器中管理凭据、查看状态并兑换奖励。
- `wzry-watchdog`：从独立调度器检查当天的 GitHub Actions Daily Run，按需请求补偿执行。

“已经兑换”与“本期只能兑换一次”等结果视为 **Already Satisfied**：奖励目标已经满足，批量执行和自动补跑不应因此失败。登录失效、传输异常、协议异常、无效配置或明确的业务拒绝会使命令返回非零退出码。

## 环境要求

- Python 3.10 或更高版本。
- [uv 0.9.28](https://docs.astral.sh/uv/)；项目会校验工具版本，依赖由已提交的 `uv.lock` 锁定。
- 使用 `wzry-login` 时，需要本机安装 Chrome/Chromium，并具备图形桌面或可显示二维码的环境。

## 快速开始

安装锁定依赖：

```bash
uv sync --locked
```

扫码登录并将凭据写入 `cookies.txt`：

```bash
uv run wzry-login --output cookies.txt
```

命令只有在代理票据和 Activity Identity 也已取得、Credential Bundle 可以直接用于兑换时才返回 `0`。如果 QQ 登录已完成但活动 Cookie 尚未就绪，命令会继续等待，并在超时后明确报告凭据不完整而不会覆盖现有文件。

查看奖励并执行兑换：

```bash
uv run wzry-exchange --list
uv run wzry-exchange --cookies cookies.txt --reward 3
uv run wzry-exchange --cookies cookies.txt --all
```

`--all` 按稳定顺序检查所有奖励，并以一次 Exchange Report 决定整个进程的退出码。需要查看完整参数时使用：

```bash
uv run wzry-login --help
uv run wzry-exchange --help
uv run wzry-web --help
uv run wzry-watchdog --help
```

## Web 应用

本地启动时默认只监听 `127.0.0.1:8080`，不会直接暴露到局域网或公网：

```bash
uv run wzry-web --cookies cookies.txt
```

默认凭据与日志路径分别是当前工作目录下的 `cookies.txt` 和 `exchange.log`。服务化运行时可显式指定受控路径：

```bash
WZRY_COOKIE_FILE=/var/lib/wzry-ams/cookies.txt \
WZRY_LOG_FILE=/var/lib/wzry-ams/exchange.log \
uv run wzry-web
```

浏览器打开 <http://127.0.0.1:8080>。只有在明确配置了网络边界、认证和 TLS 后，才应监听所有网卡：

```bash
uv run wzry-web --host 0.0.0.0 --port 8080 --cookies cookies.txt
```

Web API：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 页面 |
| `GET` | `/api/status` | Credential Bundle 状态与用户信息 |
| `POST` | `/api/cookies` | 设置凭据，JSON body 为 `{"raw":"..."}` |
| `POST` | `/api/exchange` | 兑换奖励，JSON body 为 `{"reward":"3"}` |
| `GET` | `/api/log` | 最近的兑换日志 |
| `DELETE` | `/api/cookies` | 清除当前凭据 |

## Cookie 安全

`cookies.txt` 包含可代表账号登录状态的 `access_token`、OpenID 和活动身份信息，应按密码或 API Token 的标准处理：

- 不要提交到 Git，不要放入镜像，不要粘贴到 Issue、Action 日志或聊天记录。
- 使用默认的 `cookies.txt`，或采用 `*.credentials` / `*.credentials.json` 命名；仓库会忽略这些约定名称。任意自定义文件名无法由忽略规则自动识别。
- 本地文件建议执行 `chmod 600 cookies.txt`，仅允许当前用户读取。
- 不使用时及时删除；发现泄露时立即重新登录并使旧登录态失效。
- GitHub Actions 只通过加密 Secret `COOKIES_FILE` 注入，Secret 内容应是完整文件文本。
- Web 页面没有面向公网的账号系统。默认本地监听是安全边界的一部分，不能替代反向代理认证、访问控制与 TLS。
- Cookie 可能在数小时内过期；自动调度成功不代表长期凭据永远有效，应关注认证失效的失败结果。

仓库的 `.gitignore` 和 `.dockerignore` 会排除常见凭据文件，但这只是最后一道保护，不能替代凭据管理。

Credential Bundle 是面向单一 AMS 端点的 `name -> value` 投影，不保存浏览器 Cookie 的 domain/path 元数据。CDP 登录会优先保留活动页可见的同名 Cookie，文本或 Netscape 格式则保留文件中第一个同名值；不要手工拼接多个账号或多个浏览器 profile 的导出内容。

## Docker

NAS 独立每日兑换部署见 [DOCKER.md](DOCKER.md)，专用配置位于 `ops/nas/docker-compose.yml`。
该模式包含容器内调度、持久化每日结果和 Web 密码认证，不依赖 GitHub Actions。

镜像采用固定版本及 digest 的 Python 与 uv、多阶段锁定安装，并以非 root 用户运行。构建时不会包含 `cookies.txt`、日志、Git 元数据或本地虚拟环境：

```bash
docker build --tag wzry-ams-exchange:local .
```

默认入口为 `wzry-web --host 0.0.0.0 --port 8080`。将宿主端口绑定到回环地址，可保持 Web 只对本机可见：

```bash
docker volume create wzry-ams-data

docker run --rm --name wzry-ams-exchange \
  --publish 127.0.0.1:8080:8080 \
  --mount type=volume,src=wzry-ams-data,dst=/data \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  wzry-ams-exchange:local
```

打开 <http://127.0.0.1:8080> 后，通过页面粘贴或上传 Credential Bundle。镜像把 `WZRY_COOKIE_FILE` 和 `WZRY_LOG_FILE` 分别设置为 `/data/cookies.txt` 与 `/data/exchange.log`，上述命名卷会持久化这两个文件。不要用 `COPY`、构建参数或环境变量把 Cookie 烘焙进镜像；在编排平台上应优先用运行时 Secret 初始化 `/data/cookies.txt`，并保持 Secret 与数据卷的访问权限最小化。

## 每日自动运行

`.github/workflows/daily-exchange.yml` 是远端执行器：它只接受 `workflow_dispatch`，在 GitHub Actions 中执行每日 Exchange Plan。当前计划只兑换 Reward `3`（星币福袋，60 体验券）和 Reward `4`（碎片福袋，80 体验券）。主触发器是本机的 systemd user timer，仓库 Secret `COOKIES_FILE` 必须包含有效凭据。

每次重新扫码后，用 GitHub CLI 轮换 Secret：

```bash
uv run wzry-login --output cookies.txt
gh secret set COOKIES_FILE < cookies.txt
```

GitHub Actions 可以执行兑换，但无法代替交互式 QQ 登录续期。静态 Secret 过期后，主任务和 watchdog 补跑都会如实失败；watchdog 解决的是调度漏跑，不是认证续期。需要真正长期无人值守时，必须在受控的外部执行端实现凭据刷新并轮换 Secret，或把 Daily Run 一并迁移到该执行端。

自动运行分成三个角色：

| 角色 | 责任 | 边界 |
|------|------|------|
| 本机 dispatch timer | 每天北京时间 `09:17` 请求一次 `workflow_dispatch` | 机器关机时不会补触发 |
| Daily workflow | 执行兑换并产出可信的成功/失败状态 | 不负责调度，也不在 runner 内等待 |
| 可选 watchdog | 检查当天是否已有成功或仍在运行的 Daily Run | 默认只检查；只有显式授权才补触发 |

本机 timer 直接使用当前用户的 GitHub CLI 登录，不复制 Token。先确认 `gh` 已登录，再安装并启用 unit：

```bash
gh auth status
install -d ~/.config/systemd/user
install -m 644 ops/systemd/wzry-daily-dispatch.service ~/.config/systemd/user/
install -m 644 ops/systemd/wzry-daily-dispatch.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now wzry-daily-dispatch.timer
systemctl --user list-timers wzry-daily-dispatch.timer
```

timer 不使用 `Persistent=true`，因此不会在次日启动机器时补发昨天的任务。无常驻登录会话的机器还需要为当前用户启用 systemd lingering；机器本身必须在触发时间保持运行：

```bash
sudo loginctl enable-linger "$USER"
```

通过 journal 查看触发结果：

```bash
journalctl --user -u wzry-daily-dispatch.service --since today
```

### 可选 watchdog

watchdog 的推荐调用方式：

```bash
# 私有仓库读取 run 时需要 Token；触发补跑时 Token 需要 Actions 写权限
export WZRY_GITHUB_TOKEN=github_pat_xxx

# 只检查，不改变 GitHub 状态
uv run wzry-watchdog --repo OWNER/REPO --workflow daily-exchange.yml

# 缺少有效 Daily Run 时请求 workflow_dispatch
uv run wzry-watchdog --repo OWNER/REPO --workflow daily-exchange.yml --dispatch
```

watchdog 从 `WZRY_GITHUB_TOKEN` 或 `GITHUB_TOKEN` 读取凭据。默认检查模式发现缺少有效 Daily Run 时返回非零退出码，方便监控系统告警；`--dispatch` 才会改变 GitHub 状态。不要把 watchdog 安排在主 timer 同一时刻，应留出正常运行所需的缓冲时间。

仓库提供了 [systemd user timer 模板](ops/systemd/)，默认在北京时间 `11:30` 和 `13:30` 检查。timer 不使用 `Persistent=true`，因此不会在次日登录时把昨日漏掉的检查错误补到今天。GitHub API 的临时网络失败会在单次检查中进行三次有界重试。

先在仓库内创建由 `uv.lock` 驱动的运行环境，再编辑环境文件中的仓库、绝对项目路径与 Token：

```bash
uv sync --locked --no-dev
install -d ~/.config/systemd/user
install -m 644 ops/systemd/wzry-watchdog.service ~/.config/systemd/user/
install -m 644 ops/systemd/wzry-watchdog.timer ~/.config/systemd/user/
install -m 600 ops/systemd/wzry-watchdog.env.example ~/.config/wzry-watchdog.env
${EDITOR:-vi} ~/.config/wzry-watchdog.env

systemctl --user daemon-reload
systemctl --user enable --now wzry-watchdog.timer
systemctl --user list-timers wzry-watchdog.timer
```

watchdog 和主 dispatch timer 使用相同的 user systemd 实例，因此共用前述 lingering 要求。

## 项目结构

```text
wzry-ams-exchange/
├── .github/workflows/       # CI 与 Daily Run
├── .dockerignore            # Docker 构建上下文排除规则
├── ops/systemd/             # 外部 watchdog 的 user timer 模板
├── src/wzry_ams/            # 核心包、CLI、Web、登录与 watchdog
├── tests/                   # 单元与集成测试
├── CONTEXT.md               # 领域术语与行为契约
├── Dockerfile               # 生产运行镜像
├── LICENSE                  # MIT 许可证
├── pyproject.toml           # 包元数据与 console entry points
├── uv.lock                  # 可复现依赖锁
├── cookies.example.txt      # Credential Bundle 格式示例
└── README.md                # 使用说明与逆向分析报告
```

---

## 逆向分析报告

### 1. 页面结构

```
https://pvp.qq.com/cp/a20161115tyf/page2.shtml
```

- 主活动号: `iAMSActivityId = 126433`
- 模块实例号: `activityId = 188528`
- 服务类型: `sServiceType = yxzj`（王者荣耀代号）

### 2. 奖励与 flowId 映射

页面 JS 中通过 value 属性索引，展开为 AMS flow 提交：

```javascript
var btn_num = $(this).attr('value');  // 1~6
var flowid = (btn_num == 1) ? 407551 :
             (btn_num == 2) ? 407552 :
             (btn_num == 3) ? 407553 :
             (btn_num == 4) ? 407554 :
             (btn_num == 5) ? 407555 : 407556;
amsSubmit(126433, flowid);
```

| value | 物品 | flowId | 体验币 |
|-------|------|--------|--------|
| 1 | 亲密玫瑰 | 407551 | 40 |
| 2 | 大型钻石福袋 | 407552 | 50 |
| 3 | 星币福袋 | 407553 | 60 |
| 4 | 碎片福袋 | 407554 | 80 |
| 5 | 浓情玫瑰 | 407555 | 80 |
| 6 | 体验服专属头像框 | 407556 | 900 |

### 3. 接口逆向

#### 3.1 请求方式

**form-encoded POST**（非 JSON）。这是关键发现，JSON 格式的请求全部返回 `error actid or flowid`。

#### 3.2 端点

```
POST https://smoba.ams.game.qq.com/ams/ame/amesvr
```

Query 参数：
```
ameVersion=0.3
sServiceType=yxzj
iActivityId=126433
sServiceDepartment=group_g
sSDID={随机MD5}
isXhrPost=true
```

#### 3.3 请求体（form-encoded）

| 字段 | 来源 | 说明 |
|------|------|------|
| `iActivityId` | `126433` | 主活动 ID |
| `iFlowId` | `407551~407556` | 流程 ID |
| `g_tk` | `ameCSRFToken(skey)` | CSRF Token，优先使用 Cookie 中的 `skey`，缺失时回退到 `a1b2c3` |
| `sArea` | `1` | 平台（1=手Q） |
| `sPartition` | `1306` | 游戏分区 |
| `sPlatId` | `1` | 平台 ID |
| `iUin` | `zf_openid` | 正式服 OpenID |
| `ty_openid` | `ty_openid` | 体验服 OpenID |
| `sAMSTimestamp` | 当前秒时间戳 | 请求时间 |

#### 3.4 iUin 来源

`iUin` ≠ MD5(QQ号)。实际是 `zf_openid`，从浏览器 Cookie `a20161115tyf_tyinfo` 中提取：

```
a20161115tyf_tyinfo=
  iRet,0
  @sMsg,ok
  @exp_voucher,9981     ← 体验币余额
  @zf_openid,1B4B3...   ← iUin
  @zf_area,1
  @zf_partition,1306
  @ty_openid,00410B...  ← 体验服 OpenID
  @code,0
  @holdUsed,0
```

#### 3.5 g_tk 算法

来自 `flowengine.js` 的 `ameCSRFToken()`：

```javascript
function ameCSRFToken() {
    var sAMEStr = milo.cookie.get('skey') || 'a1b2c3';
    hash = 5381;
    for (var i = 0, len = sAMEStr.length; i < len; ++i) {
        hash += (hash << 5) + sAMEStr.charAt(i).charCodeAt();
    }
    return hash & 0x7fffffff;
}
```

Python 实现：

```python
def g_tk(skey="a1b2c3"):
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF
```

#### 3.6 必须 Cookie

| Cookie | 说明 |
|--------|------|
| `openid` | QQ OpenID |
| `access_token` | QQ Access Token |
| `appid` | 应用 ID (101491592) |
| `acctype` | 账号类型 (qc=QQ) |
| `iegams_milo_proxylogin_qc` | MILO 代理登录票据 |
| `a20161115tyf_tyinfo` | 活动专用信息（含 iUin, ty_openid, 余额, 分区） |

### 4. AMS SDK 调用链

```
amsSubmit(126433, 407553)
  → amsInit(126433, 407553, callback)
    → getAmsFile(126433)  // 加载活动描述符
    → FlowEngine.submit(window["amsCfg_407553"])
      → 合并 sData: {iActivityId, iFlowId, g_tk}
      → 合并 amsCfg.sData: {sArea, sPartition, sPlatId, ty_openid}
      → form-encoded POST → /ams/ame/amesvr
```

### 5. 响应结构

```json
{
  "flowRet": { "iRet": "0" },       // 流程级结果: 0=成功
  "modRet": {
    "iRet": 0,                      // 模块级结果: 0=成功
    "bRealSendSucc": 0,             // 实际发货状态 (0=24h内到账)
    "sMsg": "恭喜您获得了礼包： XXX",
    "iActivityId": "860721",        // 服务端解析的子活动ID
    "iPackageId": "7951763",        // 服务端解析的礼包ID
    "sPackageName": "星币福袋",
    "jReqParams": {                 // 请求回显
      "iUin": "...",
      "sAMSTimestamp": "...",
      "sArea": "1",
      "sPartition": "1306"
    }
  }
}
```

### 6. 踩坑记录

| 尝试 | 结果 |
|------|------|
| JSON POST body | `error actid or flowid` |
| 用 `iPackageId` 代替 `iFlowId` | `error actid or flowid` |
| body 中 `iActivityId=188528` | `error actid or flowid` |
| body 中 `iActivityId=860721` | `error actid or flowid` |
| form-encoded，无 `g_tk` | `no g_tk` |
| form-encoded，`g_tk` 用 `a1b2c3` | `访问人数过多`（被 iUin 错误触发限流）|
| form-encoded，正确 `iUin`=`zf_openid` | **✅ 成功** |

### 7. 限制

- 每个奖励每天限兑换 1 次
- 兑换后物品 24 小时内到账游戏内
- 需要 QQ 登录态（Cookie 有效期约 2 小时，access_token 有较长有效期）
