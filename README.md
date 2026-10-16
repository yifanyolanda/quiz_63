# 话题知识小测

Flask + SQLite，无需前端构建。Python 3.12。

## 本地运行

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 将 ADMIN_PASSWORD 设置为自己的管理员密码，不要提交到 Git。
.venv/bin/python app.py
```

默认端口 5000。管理员入口 `/admin`。首次启动前通过进程环境设置 `ADMIN_PASSWORD`，密码以哈希保存；不设置时保留已有密码，无默认密码。每次设置该变量启动都会更新后台密码。`PORT` 可调整端口。

下载后台 CSV 模板，使用 Excel 填写，另存为 CSV UTF-8，上传恰好 100 题。表头必须为 `question,A,B,C,D,answer`，答案为 A/B/C/D。上传成功立即发布，不会改变已开始参与者使用的题库。请自行准备真实题目；没有题库不能开始考试。

ID 每次填写限时 60 秒，累计第二次超时后禁止参与。每浏览器最多两场，每场 3 题、每题 10 秒。开始即扣次数。提交立即进入下一题；超时依次进入下一题，离线也继续计时。两个场次六题不重复，后台判分，不下发正确答案。ID 不去重，次数由浏览器签名 Cookie 关联服务器记录；清除 Cookie、换浏览器可绕过，不能防代考。

`instance/` 保存 SQLite 数据库和会话签名密钥，需持久化并备份，勿公开。多个进程应共享同一密钥、数据库。生产部署使用 HTTPS，设置 `COOKIE_SECURE=1`，通过生产 WSGI 服务及反向代理运行（不要使用 Flask 开发服务器对外上线）。管理员登录建议在反向代理设置访问限制及登录速率限制。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## Render 托管

仓库提供 `render.yaml`。该方案使用付费 Web Service 和持久化磁盘，创建前请在 Render 确认价格；不会自动开通或扣费。

1. 将代码推送到 GitHub 仓库。
2. 注册并登录 Render，连接 GitHub，选择 New → Blueprint，选择本仓库。
3. Render 读取 `render.yaml` 后，在私密配置栏填写 `ADMIN_PASSWORD`，确认费用后创建服务。
4. 部署成功后使用 Render 提供的 HTTPS 地址访问，后台为该地址加 `/admin`。
5. 登录后台上传真实题库，然后用手机测试完整流程。

`/var/data` 磁盘保留题库和考试记录；`SECRET_KEY` 由 Render 生成。不要更换签名密钥，否则原浏览器的次数关联会失效。免费实例不提供此配置所需的持久化磁盘，仅适合数据可丢失的演示，不适合正式活动。
