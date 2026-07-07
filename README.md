# 王者荣耀体验服 AMS 兑换逆向

## 目录结构

```
wzry-ams-exchange/
├── README.md              # 本文档：逆向分析报告
├── app.py                 # Web 应用 (FastAPI 单文件部署)
├── wzry_exchange.py       # CLI 兑换脚本
├── wzry_login.py          # CDP 扫码登录获取 Cookie
├── cookies.txt            # 你的 Cookie（勿提交）
├── cookies.example.txt    # Cookie 模板
└── .gitignore
```

## 快速开始

### Web 应用 (推荐，可部署服务器)

```bash
# 1. 安装依赖
pip install fastapi uvicorn requests

# 2. 启动 Web 应用
python app.py                        # 默认 http://0.0.0.0:8080
python app.py --port 80              # 指定端口
python app.py --cookies cookies.txt  # 启动时加载 Cookie

# 3. 浏览器打开 http://localhost:8080
#    - 粘贴 Cookie 或上传 cookies.txt
#    - 点击奖励卡片直接兑换
```

### CLI 命令行

```bash
# 1. 安装依赖
pip install websocket-client requests

# 2. 扫码登录获取 Cookie (需要 Chrome 浏览器 + 显示器)
python wzry_login.py

# 3. 兑换
python wzry_exchange.py -c cookies.txt -r 3   # 星币福袋
python wzry_exchange.py -c cookies.txt --list  # 查看列表
```

---

## Web 应用 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 页面 |
| GET | `/api/status` | Cookie 状态 + 用户信息 |
| POST | `/api/cookies` | 设置 Cookie `{"raw":"..."}` |
| POST | `/api/exchange` | 兑换 `{"reward":"3"}` |
| GET | `/api/log` | 兑换日志 |
| DELETE | `/api/cookies` | 清除 Cookie |

## 部署建议

```bash
# 生产环境 (gunicorn + uvicorn)
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:80

# Docker
docker run -d -p 8080:8080 -v ./cookies.txt:/app/cookies.txt \
    python:3.11-slim sh -c "pip install fastapi uvicorn requests && python app.py --port 8080"
```

Cookie 持久化在本地文件 `cookies.txt`，生产环境可改为 Redis/数据库。

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
| `g_tk` | `ameCSRFToken(skey)` | CSRF Token，默认 skey=`a1b2c3` |
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
