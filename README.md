# 重庆大学宿舍电费监控（虎溪）

> [!IMPORTANT]
>
> 本项目仅存活半个月就被学校系统升级干坏了，目前无法正常登录，等待后续修复。如果你能帮忙修复，欢迎提交 PR 喵！

定时登录重庆大学缴费平台，抓取宿舍电费余额和电表累计读数，可生成用电图表，并通过 SMTP 邮件定时发送当前电费情况。

<img src="screenshot.png" width="400">

## 配置文件

复制 `.env.example` 为 `.env` 后进行编辑。完整配置和说明见 [.env.example](.env.example)，核心配置为：

```dotenv
CQU_ACCOUNT=你的学号
CQU_PASSWORD=你的查询密码
CQU_ROOM=D1102
CQU_BUILDING=兰园1栋
SCHEDULE_TIME=12:00            # 每日抓取时间
EMAIL_ENABLED=true             # 电费邮件通知
BALANCE_WARNING_ENABLED=true   # 余额不足警告
```

## 直接运行

本项目基于 Python 3，安装依赖：

```bash
pip install -r requirements.txt
```

运行方式：

```bash
python -m cqu_electricity once   # 单次抓取并追加到 history.csv
python -m cqu_electricity daemon # 按 .env 中的每日抓取和每周邮件计划持续运行
python -m cqu_electricity plot   # 根据 history.csv 生成图表 history.png
python -m cqu_electricity email  # 使用 history.csv 最新记录生成图表并立即发送邮件
```

## 容器运行

构建镜像：

```bash
docker build -t cqu-electricity-bill .
```

启动容器，环境变量的内容与 `.env` 文件一致：

```bash
docker run -d \
  --name cqu-electricity-bill \
  --restart unless-stopped \
  -e ... \
  -v "${PWD}/data:/data" \
  cqu-electricity-bill
```

## 关于贡献

欢迎在 Issue 中提出问题或建议，或通过 Pull Request 贡献代码，包括 AI 生成的代码。当前项目还缺少重庆大学 A/B/C 区的宿舍电费监控功能，欢迎贡献。
