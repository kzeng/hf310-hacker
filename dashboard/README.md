# HF310 状态仪表盘

https://hf310.mitoto.cn —— 影腾 HF310（MSM8916 / Debian 12 / aarch64）小主机状态面板。

## 架构

```
浏览器 --HTTPS--> kzeng nginx(443, hf310.mitoto.cn)
                     -> 127.0.0.1:8083 (frps 回环)
                        -> frpc 隧道 -> HF310 127.0.0.1:8082 (本服务)
```

## 组成

| 文件 | 作用 |
|---|---|
| app.py | 纯标准库 HTTP 服务（认证、会话、缓存首屏、POST /api/refresh） |
| collector.py | 单次快照采集（操作系统/CPU/内存/温度/磁盘/网卡/进程/定时任务/frp） |
| templates/ | index.html / login.html |
| static/ | 复用 status.mitoto.cn 的 CSS + 本项目的 dashboard.js |
| ops/hf310-dashboard.service | systemd 单元（User=user，Restart=always） |
| ops/kzeng-publish.sh | kzeng 侧发布脚本（frps 放行 + nginx vhost + 证书） |

## 操作系统信息

仪表盘顶部的「操作系统」面板读取 /etc/os-release、/proc/sys/kernel/osrelease、uname、/etc/timezone、/var/lib/dpkg/status 与设备树/DMI 型号，展示系统版本、内核、架构、主机名、硬件型号、时区、启动时间、systemd 版本和已安装软件包数。

## 采集契约

- 只在页面刷新时采集一次；10 秒内重复刷新复用同一快照（throttled: true）。
- 首屏渲染缓存快照，页面加载后只发一次 POST /api/refresh。
- 无 setInterval / 无 WebSocket / 无后台采集线程。

## 运维

```bash
# 状态
sudo systemctl status hf310-dashboard --no-pager

# 重启
sudo systemctl restart hf310-dashboard

# 改密码（在 HF310 上执行）
printf '新密码: '; read -s NEWPASS; echo
HASH=$(python3 -c "import sys; sys.path.insert(0, /home/user/hf310-dashboard); from app import make_password_hash; print(make_password_hash(sys.argv[1]))" "$NEWPASS")
echo "HASH=$HASH"   # 确认后再写入
```

写入 .env 并重启：

```bash
sudo sed -i "s|^DASHBOARD_PASSWORD_HASH=.*|DASHBOARD_PASSWORD_HASH=$HASH|" /home/user/hf310-dashboard/.env
sudo systemctl restart hf310-dashboard
```

## 环境变量（/home/user/hf310-dashboard/.env，权限 600）

| 变量 | 说明 |
|---|---|
| DASHBOARD_PASSWORD_HASH | PBKDF2-SHA256 哈希 |
| DASHBOARD_SESSION_SECRET | 会话签名密钥 |
| DASHBOARD_PORT / DASHBOARD_HOST | 默认 8082 / 127.0.0.1 |
| DASHBOARD_MIN_REFRESH_SECONDS | 快照节流窗口，默认 10 |

## 依赖

HF310 上只有 python3（3.11），无第三方包。
