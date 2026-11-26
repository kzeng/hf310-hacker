"""Collect one structured HF310 snapshot. No background loop: refresh-triggered only."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

SYS = Path("/sys")
PROC = Path("/proc")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return default


def _run(cmd, timeout=3.0):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           check=False, env={**os.environ, "LC_ALL": "C"})
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def human_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return ("%d B" % int(n)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def human_duration(sec):
    try:
        sec = int(sec or 0)
    except Exception:
        sec = 0
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    out = []
    if d:
        out.append("%d天" % d)
    if h or d:
        out.append("%d小时" % h)
    if m or h or d:
        out.append("%d分" % m)
    out.append("%d秒" % s)
    return " ".join(out)


def _pct(used, total):
    try:
        total = float(total)
        return round(float(used) / total * 100, 1) if total > 0 else 0.0
    except Exception:
        return 0.0


def _cpu_line():
    for line in _read("/proc/stat").splitlines():
        if line.startswith("cpu "):
            vals = [int(x) for x in line.split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals[:8]), idle
    return 0, 0


def _per_core():
    out = []
    for line in _read("/proc/stat").splitlines():
        if line.startswith("cpu") and line[3:4].isdigit():
            name = line.split()[0]
            vals = [int(x) for x in line.split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            out.append({"name": name.replace("cpu", "核心 "), "total": sum(vals[:8]), "idle": idle})
    return out


def collect_cpu():
    t1, i1 = _cpu_line()
    cores1 = _per_core()
    time.sleep(0.25)
    t2, i2 = _cpu_line()
    cores2 = _per_core()
    dt = max(t2 - t1, 1)
    usage = round((1.0 - max(i2 - i1, 0) / float(dt)) * 100.0, 1)
    per_core = []
    for a, b in zip(cores1, cores2):
        d = max(b["total"] - a["total"], 1)
        idle_d = max(b["idle"] - a["idle"], 0)
        per_core.append({"name": a["name"], "usage_percent": round((1.0 - idle_d / float(d)) * 100.0, 1)})
    freqs = []
    for p in sorted(SYS.glob("devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
        v = _read(p)
        if v.isdigit():
            freqs.append(int(v) // 1000)
    model = ""
    for line in _read("/proc/cpuinfo").splitlines():
        low = line.lower()
        if low.startswith("hardware") or low.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    if not model:
        model = "ARM Cortex-A53 (MSM8916)"
    return {
        "model": model,
        "logical_cores": len(per_core) or os.cpu_count() or 0,
        "usage_percent": usage,
        "idle_percent": round(100.0 - usage, 1),
        "per_core": per_core,
        "freq_mhz": freqs,
    }


def collect_memory():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            parts = v.strip().split()
            if parts and parts[0].isdigit():
                info[k.strip()] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(total - avail, 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    return {
        "ram": {"total_bytes": total, "used_bytes": used, "available_bytes": avail,
                "total_human": human_bytes(total), "used_human": human_bytes(used),
                "percent": _pct(used, total)},
        "swap": {"total_bytes": swap_total, "used_bytes": swap_used,
                 "total_human": human_bytes(swap_total), "used_human": human_bytes(swap_used),
                 "percent": _pct(swap_used, swap_total)},
    }


def collect_temperatures():
    items = []
    for zone in sorted(SYS.glob("class/thermal/thermal_zone*")):
        raw = _read(zone / "temp")
        if not raw.lstrip("-").isdigit():
            continue
        celsius = int(raw) / 1000.0
        if celsius <= 0 or celsius > 200:
            continue
        name = _read(zone / "type") or zone.name
        items.append({"name": name, "celsius": round(celsius, 1)})
    items.sort(key=lambda x: x["celsius"], reverse=True)
    return items


def collect_disks():
    out = []
    seen = set()
    for mount in ("/", "/boot"):
        try:
            st = os.statvfs(mount)
            dev = os.stat(mount).st_dev
        except Exception:
            continue
        if st.f_blocks == 0 or dev in seen:
            continue
        seen.add(dev)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        out.append({"mountpoint": mount, "fs": _fs_type(mount), "total_bytes": total,
                    "used_bytes": used, "free_bytes": free,
                    "total_human": human_bytes(total), "used_human": human_bytes(used),
                    "free_human": human_bytes(free), "percent": _pct(used, total)})
    return out


def _fs_type(mount):
    raw = _read("/proc/mounts")
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == mount:
            return parts[2]
    return ""


def _ipv4(dev):
    raw = _run(["ip", "-4", "-o", "addr", "show", "dev", dev], timeout=2)
    for tok in raw.split():
        if "/" in tok and tok[0].isdigit():
            return tok
    return ""


def collect_network():
    ifaces = []
    for dev_dir in sorted(SYS.glob("class/net/*")):
        name = dev_dir.name
        if name == "lo":
            continue
        state = _read(dev_dir / "operstate") or "unknown"
        rx = _read(dev_dir / "statistics/rx_bytes")
        tx = _read(dev_dir / "statistics/tx_bytes")
        speed = _read(dev_dir / "speed")
        ifaces.append({
            "name": name,
            "state": state,
            "carrier": _read(dev_dir / "carrier"),
            "mac": _read(dev_dir / "address"),
            "ipv4": _ipv4(name),
            "rx_bytes": int(rx) if rx.isdigit() else 0,
            "tx_bytes": int(tx) if tx.isdigit() else 0,
            "rx_human": human_bytes(int(rx) if rx.isdigit() else 0),
            "tx_human": human_bytes(int(tx) if tx.isdigit() else 0),
            "speed": (speed + " Mb/s") if speed.isdigit() else "",
        })
    default_dev, gateway = "", ""
    for line in _read("/proc/net/route").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "00000000":
            default_dev = parts[0]
            try:
                gw = int(parts[2], 16)
                gateway = socket.inet_ntoa(gw.to_bytes(4, "little"))
            except Exception:
                gateway = ""
            break
    internet_ok, latency = False, None
    started = time.time()
    s = socket.socket()
    try:
        s.settimeout(3)
        s.connect(("223.5.5.5", 53))
        internet_ok = True
        latency = round((time.time() - started) * 1000.0, 1)
    except Exception:
        pass
    finally:
        s.close()
    return {"interfaces": ifaces, "default_dev": default_dev, "gateway": gateway,
            "internet": {"ok": internet_ok, "latency_ms": latency}}


def _all_pids():
    out = []
    for entry in PROC.iterdir():
        if entry.name.isdigit():
            out.append(int(entry.name))
    return out


def _proc_sample(pids):
    sample = {}
    for pid in pids:
        raw = _read(PROC / str(pid) / "stat")
        if not raw:
            continue
        try:
            rest = raw[raw.rfind(")") + 2:].split()
            utime, stime = int(rest[11]), int(rest[12])
            threads = int(rest[17])
        except Exception:
            continue
        rss = 0
        for line in _read(PROC / str(pid) / "status").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    rss = int(parts[1]) * 1024
                break
        name = _read(PROC / str(pid) / "comm") or "?"
        sample[pid] = {"utime": utime + stime, "rss": rss, "name": name, "threads": threads}
    return sample


def collect_processes():
    pids = _all_pids()
    first = _proc_sample(pids)
    time.sleep(0.25)
    second = _proc_sample(list(first.keys()))
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    interval = 0.25
    total_threads = 0
    rows = []
    for pid, b in second.items():
        a = first.get(pid)
        if not a:
            continue
        total_threads += b.get("threads", 0)
        delta = max(b["utime"] - a["utime"], 0)
        cpu = round(delta / float(hz) / interval * 100.0, 1)
        rows.append({"pid": pid, "name": b["name"], "cpu_percent": cpu,
                     "rss_bytes": b["rss"], "rss_human": human_bytes(b["rss"])})
    by_cpu = sorted(rows, key=lambda r: r["cpu_percent"], reverse=True)[:8]
    by_mem = sorted(rows, key=lambda r: r["rss_bytes"], reverse=True)[:8]
    return {"count": len(rows), "threads": total_threads, "cpu": by_cpu, "memory": by_mem}


def collect_tasks():
    items, errors = [], []
    cron = _run(["crontab", "-l"])
    if cron:
        pending_name = ""
        for line in cron.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                pending_name = line.lstrip("#").strip()
                continue
            if line.startswith("@"):
                schedule, rest = line.split(None, 1) if " " in line else (line, "")
            else:
                parts = line.split(None, 5)
                if len(parts) < 6:
                    continue
                schedule, rest = " ".join(parts[:5]), parts[5]
            items.append({"name": pending_name or _describe_command(rest), "source": "cron",
                          "schedule": schedule, "next": "—", "status": "enabled"})
            pending_name = ""
    timers = _run(["systemctl", "list-timers", "--all", "--no-pager", "--output=json"], timeout=4)
    if timers:
        try:
            for t in json.loads(timers):
                if not isinstance(t, dict):
                    continue
                unit = t.get("unit") or ""
                if unit.endswith(".timer"):
                    items.append({"name": _describe_unit(unit), "source": "systemd",
                                  "schedule": t.get("activates") or "timer",
                                  "next": t.get("next") or "—",
                                  "status": "enabled" if t.get("next") else "waiting"})
        except Exception as exc:
            errors.append("systemd timers: %s" % exc)
    failed = _run(["systemctl", "--failed", "--no-legend", "--no-pager"])
    failed_units = [l.split()[0] for l in failed.splitlines() if l.strip()] if failed else []
    return {"items": items, "errors": errors, "failed_units": failed_units,
            "summary": "%d 个定时任务" % len(items)}


def _describe_command(cmd):
    parts = cmd.split()
    if not parts:
        return "任务"
    # unwrap common wrappers so the display name is the real job, not "flock"
    while parts and parts[0] in ("flock", "nice", "ionice", "timeout", "env"):
        wrapper = parts[0]
        idx = 1
        while idx < len(parts) and parts[idx].startswith("-"):
            idx += 1
        if wrapper == "flock" and idx < len(parts):
            idx += 1  # skip the lock file path
        if idx >= len(parts):
            break
        parts = parts[idx:]
    if parts[0].lower() in ("cd", "for", "if", "while", "export", "set", "exec", "sudo"):
        return "Shell 脚本"
    base = os.path.basename(parts[0])
    friendly = {"python3": "Python 脚本", "python": "Python 脚本", "sh": "Shell 脚本",
                "bash": "Shell 脚本", "node": "Node 脚本", "rsync": "文件同步",
                "backup": "备份任务", "certbot": "证书续期",
                "update-readme.sh": "README 时间戳更新"}
    label = friendly.get(base)
    if label:
        return label
    return base[:24]


def _describe_unit(unit):
    base = unit.replace(".timer", "")
    friendly = {"logrotate": "日志轮转", "apt-daily": "系统更新检查",
                "apt-daily-upgrade": "系统自动升级", "fstrim": "磁盘 TRIM",
                "man-db": "手册页索引", "e2scrub_all": "文件系统巡检",
                "systemd-tmpfiles-clean": "临时文件清理"}
    return friendly.get(base, base)


def collect_frp():
    active = _run(["systemctl", "is-active", "frpc"], timeout=3)
    cfg = _read("/etc/frp/frpc.toml")
    server, proxies = "", []
    for line in cfg.splitlines():
        line = line.strip()
        if line.startswith("serverAddr") and '"' in line:
            server = line.split('"')[1]
        elif line.startswith("serverPort"):
            port = line.split("=", 1)[1].strip()
            if server:
                server = server + ":" + port
        elif line.startswith("name") and '"' in line:
            proxies.append(line.split('"')[1])
    log = _run(["tail", "-n", "40", "/var/log/frpc.log"], timeout=3)
    last = ""
    for line in reversed(log.splitlines()):
        if "login to server success" in line or "start proxy success" in line or "error" in line.lower():
            last = line.split("] ", 1)[-1][:120]
            break
    return {"active": active == "active", "server": server, "proxies": proxies, "last": last}


def collect_system():
    uptime = 0.0
    raw = _read("/proc/uptime").split()
    if raw:
        try:
            uptime = float(raw[0])
        except Exception:
            uptime = 0.0
    load = []
    parts = _read("/proc/loadavg").split()
    if len(parts) >= 3:
        load = parts[:3]
    release = _os_release()
    os_name = release.get("PRETTY_NAME", "")
    return {
        "hostname": socket.gethostname(),
        "device": "HF310 4G Modem Stick",
        "os": os_name or "Debian",
        "os_name": os_name or "Debian",
        "os_id": release.get("ID", ""),
        "os_version": release.get("VERSION") or release.get("VERSION_ID", ""),
        "kernel": _read("/proc/sys/kernel/osrelease"),
        "machine": os.uname().machine,
        "hardware_model": _device_model(),
        "timezone": _timezone_name(),
        "systemd_version": _systemd_version(),
        "package_count": _package_count(),
        "uptime_seconds": uptime,
        "uptime_human": human_duration(uptime),
        "load": load,
        "boot_time": _boot_time(),
    }


def _boot_time():
    raw = _read("/proc/stat")
    for line in raw.splitlines():
        if line.startswith("btime"):
            try:
                return datetime.fromtimestamp(int(line.split()[1])).astimezone().isoformat(timespec="seconds")
            except Exception:
                return ""
    return ""


def _summary(cpu, memory, disks, temps, net):
    warnings = []
    if memory["ram"]["percent"] >= 90:
        warnings.append("内存紧张")
    if disks and disks[0]["percent"] >= 90:
        warnings.append("磁盘快满")
    if temps and temps[0]["celsius"] >= 80:
        warnings.append("温度偏高")
    if not net["internet"]["ok"]:
        warnings.append("外网不通")
    if warnings:
        return {"level": "warning", "title": "需要关注", "message": "、".join(warnings)}
    return {"level": "healthy", "title": "运行正常", "message": "各项指标在正常范围"}



def _os_release():
    info = {}
    for line in _read("/etc/os-release").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key.strip()] = value.strip().strip('"')
    return info


def _package_count():
    """Count installed dpkg packages; one cheap file read, no dpkg-query fork."""
    count = 0
    try:
        with open("/var/lib/dpkg/status", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("Package:"):
                    count += 1
    except Exception:
        return None
    return count


def _systemd_version():
    out = _run(["systemctl", "--version"], timeout=2)
    if out:
        return out.splitlines()[0].strip().replace("systemd ", "").split(" ")[0]
    return ""


def _device_model():
    for path in ("/sys/firmware/devicetree/base/model", "/proc/device-tree/model",
                 "/sys/class/dmi/id/product_name"):
        value = _read(path).replace("\x00", "").strip()
        if value:
            return value
    return ""


def _timezone_name():
    zone = _read("/etc/timezone")
    if zone:
        return zone
    try:
        return datetime.now().astimezone().tzname() or ""
    except Exception:
        return ""


def collect_snapshot():
    started = time.time()
    cpu = collect_cpu()
    memory = collect_memory()
    temperatures = collect_temperatures()
    disks = collect_disks()
    network = collect_network()
    processes = collect_processes()
    tasks = collect_tasks()
    frp = collect_frp()
    system = collect_system()
    return {
        "collected_at": _now_iso(),
        "collection_ms": int((time.time() - started) * 1000),
        "summary": _summary(cpu, memory, disks, temperatures, network),
        "system": system,
        "cpu": cpu,
        "memory": memory,
        "temperatures": temperatures,
        "disks": disks,
        "network": network,
        "processes": processes,
        "tasks": tasks,
        "frp": frp,
    }


if __name__ == "__main__":
    print(json.dumps(collect_snapshot(), ensure_ascii=False, indent=2))
