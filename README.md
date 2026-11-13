# 数据中心

一个集中管理多站点数据的面板，统一展示账户余额、API Key 消耗与监控状态，支持自动检查、企业微信预警和每日晨报。使用 React + Vite 提供前端、Django 提供 API，并用内置 SQLite 存储数据，无需单独部署数据库。你可以添加多个网站，每个站点独立配置接口地址、账号或 Token、余额路径和阈值，余额预警和每日晨报分别配置企业微信 Webhook。服务端负责代理登录和数据请求，避免浏览器跨域；余额低于阈值时通过企业微信机器人发送 Markdown 预警。余额统一显示为两位小数。

站点配置保存到 Docker 数据卷。每个站点可设置独立查询间隔，最短 30 秒；定时任务运行在后端，浏览器关闭后仍会继续检查和预警。

除阈值预警外，每天 08:00（按站点时区判断）还会通过独立的晨报 Webhook 推送一次数据中心晨报图片：采用白底细灰色网格的表格，顶部依次显示标题、日期、总余额、总消耗，下方按“站点 / 余额 / 消耗”三列列出该时区所有启用站点的明细，余额和消耗的表头、数值居中，金额保留两位小数。消耗统计昨日全天。不同币种分别汇总；部分消费数据缺失时注明已获取部分小计，全部未获取时显示 `--`。查询失败的站点保留在表格中，并在图片下方说明原因。晨报成功后当天不再发送，不依赖阈值，即使所有站点余额充足也会推送。发送失败每分钟重试，当天错过推送小时会补发。推送小时可通过 `DAILY_REPORT_HOUR` 修改（0-23，默认 `8`）。

请在站点设置的“每日晨报 · 企业微信通知”中独立填写“晨报 Webhook”，保存配置或成功发送“测试晨报通知”后生效。测试按钮发送相同样式的表格图片，图片明确标注为测试数据。升级后的晨报 Webhook 初始为空，必须单独配置才会发送晨报，不会自动沿用余额预警 Webhook；已保存后的空白输入表示保留原值。

晨报 PNG 由服务端使用 Pillow 绘制，以企业微信原生图片消息发送，无需公开图片地址。Docker 镜像已安装中文字体；本地 Windows 自动使用宋体或微软雅黑，Linux 可安装 `fonts-wqy-zenhei` / `fonts-noto-cjk`，也可通过 `REPORT_FONT_PATH` 指定中文字体文件。图片生成或发送失败不会记为发送成功。

晨报的昨日消耗只统计站点已配置的“消耗查询 API Keys”，按站点时区查询昨天零点至当天零点前的完整一天，再将各 Key 金额显示在站点行内：

- **Sub2**：逐个 Key 请求 `/v1/usage?days=2&timezone=...`，只读取返回的 `daily_usage` 中昨日的 `actual_cost`。上游需支持 Key 历史日用量；未返回 `daily_usage` 时提示未获取，不会把今日或累计消耗当作昨日消耗。
- **New API**：逐个 Key 获取上游名称，再使用已保存的访问令牌和 `New-Api-User ID`，按 `token_name` 与昨日完整时间范围查询 `/api/log/self/stat`，金额为 `data.quota / 500000`。同名 Key 不重复计入。

部分 Key 失败时标注成功项小计及未获取数量；未配置 Key 时显示“消耗未配置”，查询失败或上游不支持时显示未获取，不回退到账户总用量或累计快照，也不受 `usagePath`、余额倍率和换算除数影响。无需连续运行两天即可查询昨日数据。

账号密码模式会在服务端缓存目标网站返回的 Token 或 Cookie，后续定时查询直接复用会话；会话失效时自动重新登录并重试一次。同一站点并发查询会合并为一次登录，避免短时间内重复创建会话。会话会保存到 Docker 数据卷的 `upstream-sessions.json`（仅后端可读），容器重启后也会优先复用；只有会话失效或账号密码变更时才重新登录。
如果目标网站提示活跃登录会话已达上限，程序会暂停该站点登录 10 分钟，避免定时任务持续重试；请先在目标网站退出其他登录或重置密码。

## 认证方式

每个站点可选三种认证方式：

- **账号密码登录**：服务端调用登录接口，自动获取并缓存 Token / Cookie，支持失效自动重登。
- **访问令牌 / Bearer Token**：填写网站生成的访问令牌，服务端按 `Authorization` 头发送。New API 的访问令牌通常需要不带 `Bearer` 前缀，程序会先按该站点的要求尝试，401/403 时自动换成另一种形式重试。
- **登录 Cookie（手动粘贴）**：在浏览器登录目标网站后，从 F12 → 网络 → 任一请求 → 请求头复制整段 `Cookie`，粘贴到站点配置中。程序会把内容清洗成合法的 Cookie 头（自动去掉 `Cookie:` 前缀、`path=/`、`HttpOnly`、`SameSite` 等属性以及换行），之后按 `Cookie` 头请求余额接口。

Cookie 模式适用于目标网站只认浏览器会话、不提供访问令牌的情况。注意两点：

1. **不会自动续期。** 这条路径没有密码可用于重新登录，Cookie 失效后该站点会持续报「登录 Cookie 无效或已过期」，需要重新登录网站并粘贴新的 Cookie。若希望长期无人值守，优先使用账号密码或 Token 模式。
2. **Cookie 余额查询无需填写 New-Api-User ID。** 会话本身即可识别用户，且部分 New API 分支在请求头与会话不一致时会返回 409，因此 Cookie 模式下余额查询不会携带该请求头。独立的 Key 消耗查询仍会携带已填写的 `New-Api-User ID`。

Cookie 与密码、Token 一样只保存在服务端，不会返回给浏览器。

余额字段支持自动适配：接口返回 `balance` 时直接使用；返回 `quota` 时默认按 `quota / 500000` 换算并显示为 CNY。也可以在站点配置中自行指定余额字段、换算除数和币种。

每个站点还可设置余额倍率，默认值为 `1`。最终余额计算公式为：`原始余额 / 换算除数 * 余额倍率`。

内置站点类型：

- Sub2：登录 `/api/v1/auth/login`，余额 `/api/v1/auth/me`，读取 `balance`；用量 `/api/v1/usage/dashboard/trend`
- New API：登录 `/api/user/login`，余额 `/api/user/self`，读取 `quota / 500000`
- 自定义：手工指定接口路径、余额字段、换算除数和币种

站点类型选“自动识别”时，程序会按 `mePath` 推断出对应的内置预设，用量接口也能正常生效，老配置无需改动。

New API 查询会在填写 `New-Api-User ID` 时携带对应请求头。账号密码登录时程序会尝试从登录响应自动获取用户 ID；如果目标站点返回 409 `Conflict`，请填写该账号对应的数字 ID。使用 Token 时，按目标站点要求填写该 ID。

已保存的网站密码、Bearer Token 和企业微信 Webhook 不会由后端返回到浏览器。设置页面只显示“已安全保存”，留空保存会保留原值；只有填写新值时才会覆盖服务器上的凭据。

Sub2 和 New API 站点均支持独立的“消耗查询 API Keys”（通常以 `sk-` 开头），每个站点最多添加 20 个，可设置名称、修改或删除。点击“消耗查询”会先保存配置，再分别查询各个 Key；单个 Key 失败不影响其他结果。

- **Sub2**：请求站点接口地址下的 `GET /v1/usage`（例如 `https://api.deepspacecode.chat/v1/usage`），发送 `Authorization: Bearer <API Key>`，超时为 20 秒。今日使用金额读取 `usage.today.actual_cost`（同时兼容顶层 `today.actual_cost` 和 `data` 包装），不套用余额换算除数或倍率；展示每个 Key 的今日金额及合计，部分失败时显示仅成功项的小计。金额单位沿用上游接口含义，不自动换算币种。此接口与日报使用的 `/api/v1/usage/dashboard/trend` 独立。
- **New API**：请求 `GET /api/usage/token`，发送 `Authorization: Bearer <API Key>`，填写 `New-Api-User ID` 时同时发送 `New-Api-User` 请求头。名称读取 `name`，累计消耗金额为 `total_used / 500000`，剩余金额和总金额分别为 `total_available / 500000`、`total_granted / 500000`，同时兼容旧版 `used_quota` 等字段。此换算固定使用 `500000`，不套用站点的余额倍率或除数；不限额的 Key 仍显示累计消耗，`expires_at` 为 `0` 或 `-1` 时显示永不过期。

New API 今日用量使用已保存的“访问令牌 / Bearer Token”（控制台访问令牌，不是 `sk-` 模型调用 Key）和 `New-Api-User ID`，无需重复填写。程序先从令牌接口取得 `name`，再请求 `GET /api/log/self/stat?token_name=...&start_timestamp=...&end_timestamp=...`，使用 `Authorization: Bearer <访问令牌>` 和 `New-Api-User`，超时 30 秒。统计范围为站点时区当天零点至本次查询时刻，默认 `Asia/Shanghai`（UTC+8）；今日金额为 `data.quota / 500000`。页面分别显示今日金额与累计金额，今日查询失败不影响累计结果，合计只统计成功取得的今日金额。

今日接口按名称筛选，请为上游 Key 设置唯一名称。多个已配置 Key 返回相同名称时，页面会提示修改上游名称，避免将同一份按名称统计的今日用量重复计入合计。

这些 Key 与余额查询使用的账号、密码和控制台访问令牌独立；New API 消耗查询复用站点配置中的 `New-Api-User ID`。Key 只保存在服务端数据库，页面仅收到名称和是否已保存的标记；留空保存保留对应 Key，删除并保存会移除对应 Key。旧的单 Key 配置会自动迁移。

余额看板和站点设置中的“进入网站”按钮会直接在新标签页打开该站点保存的接口地址。跨域网站的浏览器登录态无法由本服务通用注入；自动登录需要目标网站提供 SSO、一次性登录链接或专用换票接口。

New API 查询成功后，未填写的 Key 名称以及默认的 `默认 Key`、`Key 1` 等名称会自动使用接口返回的 `name` 并保存；手动填写的自定义名称会保留。今日用量筛选始终使用接口返回的名称。

## Docker 部署

在服务器安装 Docker 和 Docker Compose 后，在项目目录执行：

```bash
docker compose -f docker-compose.django.yml up -d --build
docker compose -f docker-compose.django.yml logs balance-watch
```

镜像使用多阶段构建：Node 阶段自动安装依赖并生成 React 静态文件，随后由 Django/Gunicorn 提供页面和 API。服务器无需单独安装 Node.js 或手动执行前端构建。

无需创建 `.env`。首次启动自动生成随机管理员密码和 Django 密钥，管理员账号默认为 `admin`。初始密码只在生成时输出到容器日志，找到 `Initial administrator credentials` 下的 `Username` 和 `Password` 即可登录；Django 密钥不会输出到日志。

凭据保存在数据卷的 `/data/bootstrap.json`（仅文件所有者可读写），数据库保存在 `/data/db.sqlite3`。重启、重建容器和更新镜像均复用原有凭据。请保留并备份 `balance_data` 数据卷，勿执行 `docker compose ... down -v`，该操作会删除数据和凭据。

如果初始日志已清理，可在服务器上单独查看当前管理员密码：

```bash
docker compose -f docker-compose.django.yml exec balance-watch python -c 'import json; print(json.load(open("/data/bootstrap.json"))["APP_PASSWORD"])'
```

需要自定义配置时，将 `.env.example` 复制为 `.env` 后编辑（Linux 使用 `cp .env.example .env`，PowerShell 使用 `Copy-Item .env.example .env`）：

```env
HOST_PORT=3010
APP_USERNAME=admin
APP_PASSWORD=
DJANGO_SECRET_KEY=
COOKIE_SECURE=false
CHECK_CONCURRENCY=5
DAILY_REPORT_HOUR=8
```

`APP_PASSWORD` 和 `DJANGO_SECRET_KEY` 留空时使用已保存的值，首次部署则自动生成；填写非空值会覆盖并保存对应凭据。旧部署若在 `.env` 中设置了固定密码或密钥，会继续使用这些配置；只有未配置且未保存的值才会生成。修改 `.env` 后重新执行启动命令使配置生效。

首次打开页面会跳转到独立登录页，使用管理员账号和密码登录；登录成功后进入余额看板。管理员登录只保护本系统后台，各监控站点仍使用各自的账号密码或 Bearer Token。

查看运行状态和日志：

```bash
docker compose -f docker-compose.django.yml ps
docker compose -f docker-compose.django.yml logs -f --tail=100 balance-watch
```

默认访问 `http://服务器IP:3010`。如果需要修改外部端口，在 `.env` 设置 `HOST_PORT=3020` 后重新执行启动命令，此时访问 `http://服务器IP:3020`。

更新代码后重新构建（仅执行 `git pull` 不会更新容器中的页面或后端）：

```bash
git pull --ff-only
docker compose -f docker-compose.django.yml build --no-cache balance-watch
docker compose -f docker-compose.django.yml up -d --force-recreate balance-watch
docker compose -f docker-compose.django.yml logs --tail=100 balance-watch
```

如果页面仍显示旧版本，先确认服务器确实拉取到了最新提交，然后检查容器内的前端包是否包含新功能：

```bash
git log -1 --oneline
docker compose -f docker-compose.django.yml exec balance-watch sh -c 'grep -R "消耗查询 API Key" /app/frontend/dist/assets/*.js'
```

命令能找到内容但浏览器仍显示旧页面时，通常是浏览器、CDN 或 Nginx 缓存。请先使用 Ctrl+F5 强制刷新；若使用 CDN，再清理 CDN 页面缓存。应用会对 HTML 返回 `no-store`，避免后续升级继续加载旧入口。不要执行 `down -v`，否则会删除数据库和已保存凭据。

如果服务器已经有 Nginx/Caddy，建议将域名反向代理到 `127.0.0.1:3010`（或实际 `HOST_PORT`），并启用 HTTPS。通过 HTTPS 访问时，在 `.env` 设置 `COOKIE_SECURE=true` 后重新执行启动命令。保持单个容器、一个 Gunicorn worker，以免后台定时任务重复执行。

## 本地运行

需要 Node.js 22、Python 3.11 或更高版本。以接近 Docker 的方式运行生产构建：

```powershell
Set-Location frontend
npm ci
npm run build
Set-Location ..
python -m pip install -r requirements.txt
python manage.py runserver 0.0.0.0:3000 --noreload
```

浏览器打开 <http://localhost:3000>。本地运行同样自动生成凭据，默认保存在 `data/bootstrap.json`，初始管理员密码输出到终端。

前端开发时，保持 Django 运行在 `127.0.0.1:3000`，另开终端启动 Vite：

```powershell
Set-Location frontend
npm run dev
```

浏览器打开 <http://localhost:5173>。Vite 会将 `/api` 请求代理到 Django，修改 React 源码后页面自动刷新。

## 测试

```powershell
Set-Location frontend
npm test
npm run build
npm run test:e2e
Set-Location ..
python -m unittest discover -s tests -v
```

端到端测试在 Windows 上默认使用标准路径安装的 Google Chrome；其他环境可先执行 `npx playwright install chromium`，或通过 `PLAYWRIGHT_CHROME_PATH` 指定浏览器。后端集成测试会读取 `frontend/dist`，因此运行前需先完成前端构建。

## 使用说明

“数据差异分析”自动保存每次手动、定时余额检查的余额与消耗快照。差额 = 本次余额 − 上次有效余额 + 区间消耗，容差为 0.01。首次有效采集建立基准；非零差额保留原基准并进入待复核，后台最长每 60 秒复查一次，差额稳定至少 60 秒后确认。正差额标记为“充值（推算）”，负差额标记为异常；延迟后恢复的记录不计入充值或异常，待复核金额也不计入汇总。重启后继续使用数据库中的基准与待复核记录。

New API 在账户响应同时包含 `quota` 和 `used_quota` 时，按账户全部累计消耗对账。否则，New API / Sub2 使用所有已配置 Key 的完整今日消耗，明确标记“仅已配置 Key”，负差额称为“未解释扣减”，可能包含其他 Key 的消费。对账独立将消耗换算至余额的相同单位，概览和晨报的原始 Key 消耗保持原口径。存在冻结额度、字段不支持、部分 Key 失败或日期不一致时不推算金额。跨天时重新查询完整历史日消耗，最多跨 31 天；缺失时保留基准并等待补查，不将今日清零算作扣减。账户累计消耗回退时记录计数重置；账户、Key 或换算配置改变后重新建立基准。

页面支持按站点、结果和北京时间日期筛选、分页、查看计算过程及原始金额、保存核实备注；“刷新记录”只读数据库，“立即采集”查询上游。顶部金额按币种汇总所选站点和日期内已确认记录，不受结果筛选影响。充值为采集区间的净增额度，可能包含赠送、退款或调账，不能推断实际支付金额或充值笔数。历史从功能启用后积累，旧日消耗缓存不能还原旧余额。基准、快照、事件和备注随 SQLite 数据卷保留，启动时自动迁移；“重建基准”保留历史，下一次完整采集建立新基准。本板块第一版在页面内展示结果，不新增企业微信推送。

页面中的余额、消耗、合计及图表数值统一保留两位小数（例如 `0.00`、`1.20`），后台计算和汇总保留原始精度。

数据概览中的“最近八天消耗”以天为单位展示最近八个自然日（含今天）的柱状图，按币种和站点时区分别汇总启用站点已配置的 API Key，可切换统计分组并点击“刷新消耗”重新查询。今天统计截至查询时刻；历史日期统计完整一天。Sub2 从 `/v1/usage?days=8` 获取历史日用量，New API 按 Key 名称逐日查询日志统计；未获取显示“—”，部分成功标注小计，不使用账号累计消耗快照代替每日消耗。

八天图表默认从 SQLite 数据库读取，每 15 秒同步一次，不因打开页面而请求上游。后台每 15 秒检查缓存：首次补齐八天、今天按站点查询间隔更新、昨日每小时（不短于站点查询间隔）补查，较早的完整数据直接复用；缺失或失败的数据按站点间隔重试。点击“刷新消耗”会重新查询八天数据，保存后再从数据库返回。更新失败时保留已有成功金额及原更新时间，并标注旧数据；站点地址、Key、币种或时区改变后重新缓存。每日记录最多保留约 40 天，重启后仍可读取。余额和今日总消耗的原有自动检查流程保持不变，检查成功的今日 Key 消耗同时写入图表缓存。

概览顶部的“今日总消耗”按币种汇总所有启用站点已获取的今日 API Key 消耗，随自动检查和手动消耗查询更新，不受站点筛选影响。零消耗显示为 `0.00`，没有有效数据时显示“—”；部分站点或 Key 未获取时标注成功项小计及汇总站点数。统计沿用各站点时区，不计入历史数据、失败结果或停用站点，不跨币种换算。

数据概览的“今日实时消耗”列仅汇总该站点已配置 API Key 的今日消耗，复用“消耗查询”的今日金额合计，随自动余额检查或手动消耗查询更新，不再查询账号总消耗，也不套用余额倍率或除数。部分 Key 失败时标注成功项小计及未获取数量；未配置 Key 或全部未获取时显示“—”，不影响余额展示。晨报则独立查询这些 Key 的昨日全天消耗。

填写接口地址、登录路径、余额路径和账号密码，点击“检查余额”。如果上游已经提供 Bearer token，也可以只填 token。企业微信机器人 Webhook 在站点设置顶部分别填写：余额预警使用“全局 Webhook”，每日晨报使用“晨报 Webhook”，各自提供测试通知按钮。各站点设置自己的预警阈值和是否启用预警。

网站凭据和两个 Webhook 只保存在服务端数据文件中，不会回传到浏览器。余额触发后，同一站点账户 6 小时内只发送一次提醒，不同站点独立计时；企业微信消息会显示网站名称、账户、当前余额和预警阈值。只有企业微信返回 `errcode=0` 才视为发送成功；通知失败会在数据概览和站点详情显示，未触发预警的原因可在站点详情查看。
