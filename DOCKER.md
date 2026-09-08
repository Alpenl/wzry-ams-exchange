# NAS Docker 部署

GitHub Actions 在 master 更新时运行测试、构建 linux/amd64 镜像并发布到
`ghcr.io/alpenl/wzry-ams-exchange`。默认分支发布 `latest` 和 `sha-完整提交号`；
版本标签发布相应版本标签，不覆盖 latest。

NAS 仅拉取镜像，不需要源码、Git、Python、构建工具或安装脚本。

## NAS 目录

```text
/vol1/1000/Docker/wzry-ams-exchange/
  docker-compose.yml
  data/
```

使用仓库的 [NAS Compose](ops/nas/docker-compose.yml)，将其放到上述目录。
`data/` 必须提前创建，由 NAS 用户 UID/GID 1000:1000 可读写；Compose 同样使用
`user: "1000:1000"`。应用只写 data，根文件系统只读。
不要把源码、镜像 tar 或密码文件放在顶层。

在 NAS 上启动：

```bash
cd /vol1/1000/Docker/wzry-ams-exchange
mkdir -p data
chmod 700 data
sudo docker compose pull
sudo docker compose up -d --wait
```

访问 **http://192.168.110.200:18080**。首次打开页面设置管理员密码（至少 12 位）；
以后在同一页面登录。首次初始化应在可信 LAN 中立即完成。
HTTP 适用于可信 LAN；公网入口应使用 VPN 或 HTTPS 反向代理。

## Web 管理台

- 概览：凭据记录余额、今日自动任务、执行时间、最近结果。
- 奖励兑换：六项奖励，手动兑换前确认体验券消耗。
- 任务记录：自动任务与手动兑换记录、结果筛选。
- 凭据管理：上传 Credential Bundle 文件，粘贴 Cookie，清除凭据。
- 设置：暂停或启用每日任务，修改执行时间并持久化。

在有 Chrome 的电脑运行 `uv run wzry-login --output cookies.txt`，
随后通过凭据管理页面上传文件。浏览器表格复制内容不是标准 Cookie 文件；
上传内容需为 JSON 对象、Netscape 格式或 `name=value` 文本。
兑换所需字段为 `openid`、`access_token`、`appid`、`acctype`、
`iegams_milo_proxylogin_qc` 和 `a20161115tyf_tyinfo`。
GitHub Secret 无法读回，NAS 首次部署需导入有效凭据。

网页保存的密码是 PBKDF2 哈希，不保存明文。会话最长 8 小时，容器重启后需要重新登录。
浏览器 Cookie 的 Expires 不等于 OAuth Token 服务端有效期；以实际鉴权结果为准。
本服务不自动完成 QQ 扫码续期。

## 调度和数据

默认每天 09:17（Asia/Shanghai）兑换 3、4，总计 140 体验券。
页面时间和开关一旦保存，优先于环境变量默认值。

每 30 秒检查一次，重启只补当天；当天已经成功的奖励不再自动领取。
临时网络失败间隔 5 分钟，最多 4 次；鉴权失败或业务拒绝不循环重试。
更新凭据后可以重试当天失败任务。请求成功但结果尚未落盘时进程退出，
可能再次请求，由上游限制重复领取。必须单容器、单 worker 运行。

`data/` 中包含：

| 文件 | 内容 |
| --- | --- |
| cookies.txt | 游戏登录凭据 |
| auth.json | 管理员密码哈希 |
| schedule.sqlite3 | 自动任务记录、时间与开关 |
| exchange.log | 手动兑换记录 |

备份时停止容器后复制整个 data；不要只复制运行中的 SQLite 主文件。
更换镜像、重建容器不会删除 bind mount 数据。
忘记管理员密码时，停容器，把 data/auth.json 移到受限备份位置，重启后立即重新初始化。

## 更新和回滚

```bash
cd /vol1/1000/Docker/wzry-ams-exchange
sudo docker compose pull
sudo docker compose up -d --wait
sudo docker compose logs --tail 100
```

生产 Compose 可固定 `sha-完整提交号` 或 `@sha256:镜像摘要`，
避免 latest 漂移。回滚时改回之前的镜像引用并重建容器，保留 data。

`/healthz` 仅判断 Web 进程健康；业务结果在登录后的 `/api/schedule` 和任务记录页。
NAS 兑换验收成功后，再停用旧电脑调度，避免双重执行：

```bash
systemctl --user disable --now wzry-daily-dispatch.timer wzry-watchdog.timer
```

## 开发

根目录 Compose 可本机构建，默认只监听 `127.0.0.1:18080` 并使用命名卷：

```bash
docker compose up -d --build
```

Dockerfile 使用固定版本与 digest，最终镜像非 root。
CI 覆盖 Python 3.10 及较新版本、单元测试、静态检查、wheel 和容器健康检查。
镜像发布工作流使用 GITHUB_TOKEN，不需要额外 PAT。首次发布后 GHCR 包需要允许
NAS 拉取（公开包可匿名拉取，私有包需要 docker login）。
