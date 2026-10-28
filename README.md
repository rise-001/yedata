# 余额哨兵

一个无需单独部署数据库的多站点余额监控面板，使用 React + Vite 提供前端、Django 提供 API，并用内置 SQLite 存储数据。你可以添加多个网站，每个站点独立配置接口地址、账号或 Token、余额路径和阈值，所有站点共用一个企业微信 Webhook。服务端负责代理登录和余额请求，避免浏览器跨域；余额低于阈值时通过企业微信机器人发送 Markdown 预警。余额统一显示为两位小数。

站点配置保存到 Docker 数据卷。每个站点可设置独立查询间隔，最短 30 秒；定时任务运行在后端，浏览器关闭后仍会继续检查和预警。

除阈值预警外，每天 08:00（按站点时区判断）还会推送一次余额日报：按时区分别汇总该时区所有启用站点的当前余额，余额低于阈值的站点会标出 ⚠️，查询失败的站点会标出 ❌ 并附上失败原因，末尾给出总余额和昨日总用量。日报成功后当天不再发送，不依赖阈值，即使所有站点余额充足也会推送。发送失败每分钟重试，当天错过推送小时会补发。推送小时可通过 `DAILY_REPORT_HOUR` 修改（0-23，默认 `8`）。

昨日用量有两种来源，程序按站点自动选择：

- **用量接口**（Sub2）：Sub2 的余额接口不返回任何累计用量，但其用量看板接口支持按日期查询。程序在日报时按站点时区请求昨天一整天（`start_date` = `end_date` = 昨天，`granularity=day`），直接取该日的 `actual_cost` 之和。**部署当天就有数据**，不需要等两天。
- **累计快照**（New API）：New API 的 `used_quota` 是自账号创建以来的累计值，服务端按站点时区每天记录快照，昨日用量等于昨日最后一次快照减去前一天最后一次快照。因此需要连续运行两天以上才有数据，首次部署的次日会显示“昨日用量未知（需连续运行两天）”。快照保存在数据卷的 `daily-usage.json`，保留最近 40 天。

用量接口每次日报只调用一次（该接口在上游属于重查询并带限流），站点配置中的 `usagePath` 留空即表示该站点不查询用量。

账号密码模式会在服务端缓存目标网站返回的 Token 或 Cookie，后续定时查询直接复用会话；会话失效时自动重新登录并重试一次。同一站点并发查询会合并为一次登录，避免短时间内重复创建会话。会话会保存到 Docker 数据卷的 `upstream-sessions.json`（仅后端可读），容器重启后也会优先复用；只有会话失效或账号密码变更时才重新登录。
如果目标网站提示活跃登录会话已达上限，程序会暂停该站点登录 10 分钟，避免定时任务持续重试；请先在目标网站退出其他登录或重置密码。

## 认证方式

每个站点可选三种认证方式：

- **账号密码登录**：服务端调用登录接口，自动获取并缓存 Token / Cookie，支持失效自动重登。
- **访问令牌 / Bearer Token**：填写网站生成的访问令牌，服务端按 `Authorization` 头发送。New API 的访问令牌通常需要不带 `Bearer` 前缀，程序会先按该站点的要求尝试，401/403 时自动换成另一种形式重试。
- **登录 Cookie（手动粘贴）**：在浏览器登录目标网站后，从 F12 → 网络 → 任一请求 → 请求头复制整段 `Cookie`，粘贴到站点配置中。程序会把内容清洗成合法的 Cookie 头（自动去掉 `Cookie:` 前缀、`path=/`、`HttpOnly`、`SameSite` 等属性以及换行），之后按 `Cookie` 头请求余额接口。

Cookie 模式适用于目标网站只认浏览器会话、不提供访问令牌的情况。注意两点：

1. **不会自动续期。** 这条路径没有密码可用于重新登录，Cookie 失效后该站点会持续报「登录 Cookie 无效或已过期」，需要重新登录网站并粘贴新的 Cookie。若希望长期无人值守，优先使用账号密码或 Token 模式。
2. **无需填写 New-Api-User ID。** 会话本身即可识别用户，且部分 New API 分支在请求头与会话不一致时会返回 409，因此 Cookie 模式下程序不会携带该请求头。

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

New API 站点还可以填写独立的“消耗查询 API Key”（通常以 `sk-` 开头）。点击“消耗查询”时，服务端请求该站点的 `/api/usage/token/`，并按接口要求发送 `Authorization: Bearer <API Key>`；它与余额查询使用的账号、密码、控制台访问令牌和 `New-Api-User ID` 互相独立。页面会展示令牌名称、已用额度、剩余额度、总额度、到期时间和模型限制。API Key 只保存在服务端数据库，不会返回浏览器。

余额看板和站点设置中的“进入网站”按钮会直接在新标签页打开该站点保存的接口地址。跨域网站的浏览器登录态无法由本服务通用注入；自动登录需要目标网站提供 SSO、一次性登录链接或专用换票接口。

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

更新代码后重新构建：

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

填写接口地址、登录路径、余额路径和账号密码，点击“检查余额”。如果上游已经提供 Bearer token，也可以只填 token。企业微信机器人 Webhook 在站点设置顶部统一填写，各站点只需设置自己的预警阈值和是否启用预警；可先用“测试通知”验证连接。

网站凭据和全局 Webhook 只保存在服务端数据文件中，不会回传到浏览器。余额触发后，同一站点账户 6 小时内只发送一次提醒，不同站点独立计时；企业微信消息会显示网站名称、账户、当前余额和预警阈值。只有企业微信返回 `errcode=0` 才视为发送成功；通知失败会在余额概览和站点详情显示，未触发预警的原因可在站点详情查看。
