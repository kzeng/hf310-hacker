# 影腾 HF310 折腾记录与代码

影腾 HF310（MSM8916）从原厂 Android 设备侦察、EDL/刷机到 Debian 运行服务的实测记录和相关代码。

## 内容索引

- [完整折腾笔记](docs/HF310-折腾笔记.md)：硬件、拆机、EDL、刷机、网络、配网、远程访问、LED 与故障经验。
- [Android 属性记录（脱敏）](docs/HF310-props-sanitized.txt) 与 [GPT 分区表输出](docs/gpt_printgpt.txt)。
- `scripts/`：HF310 实机导出的配网守护、LED 状态守护、Wi-Fi 开机恢复脚本。
- `systemd/`：对应服务单元及仪表盘单元。
- `config/`：captive portal 的 dnsmasq 规则、mDNS 服务声明。
- `dashboard/`：HF310 状态仪表盘源码、静态资源及独立 README；其独立仓库为 [hf310-dashboard](https://github.com/kzeng/hf310-dashboard)。

## 适用范围

这些文件记录一台具体 HF310 的调试过程，硬件批次、分区布局、LED 接线可能不同。刷写前请核对板型并自行备份。部署时按本机网络、路径和账户调整服务配置。

公开版已移除密码、设备唯一标识和家庭 Wi-Fi 信息。原厂固件、分区二进制备份、QCN、运行快照、私有 `.env` 和密钥不入库。
