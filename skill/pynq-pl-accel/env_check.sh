#!/usr/bin/env bash
# Zynq Linux（PYNQ / PetaLinux 通用）上板前环境自检
#
# 用法：  bash env_check.sh            # 只读检查
#         bash env_check.sh --fix-ftdi # 额外尝试把 FTDI latency_timer 改为 1ms
#
# 退出码：0 = 无明显阻塞；1 = 发现需要处理的问题
#
# 说明：只做只读探测，不安装任何东西，不改系统文件（--fix-ftdi 除外，需 root）。

set -u
BOARD="${BOARD:-unknown}"
FAIL=0
WARN=0

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }
hdr()  { printf '\n=== %s ===\n' "$*"; }

hdr "1. 平台识别"
if [ -f /etc/pynq/version ] 2>/dev/null || [ -f /etc/pynq_version ]; then
    V=$(cat /etc/pynq/version 2>/dev/null || cat /etc/pynq_version 2>/dev/null)
    ok "PYNQ 版本: $V"
    case "$V" in
        4.*|5.*) warn "PYNQ $V 已不再支持 Zynq-7020（Z1/Z2）；确认你的板卡在支持列表内" ;;
    esac
else
    ok "未见 PYNQ 版本文件（可能是 PetaLinux 或其它发行版）"
fi
[ -f /etc/os-release ] && ok "OS: $(. /etc/os-release; echo "$PRETTY_NAME")"
ok "内核: $(uname -r)"
ok "架构: $(uname -m)"

hdr "2. FPGA 管理器可用性"
if [ -e /dev/xdevcfg ]; then
    ok "/dev/xdevcfg 存在（可加载 bitstream）"
elif [ -e /sys/class/fpga_manager ]; then
    ok "fpga_manager 存在"
else
    bad "未发现 /dev/xdevcfg 或 fpga_manager —— 无法加载 PL"
fi
command -v fpgautil >/dev/null 2>&1 && ok "fpgautil 可用" || warn "未见 fpgautil（PYNQ 通常不需要）"

hdr "3. PL 时钟（关键）"
warn "确认你的 PL 时钟来自 PS 的 FCLK_CLK0，而不是板载晶振"
warn "  原因：换板时板载晶振频率不同（50/100/125 MHz），会导致时序与分频全错"

hdr "4. 串口 / USB 设备"
if ls /dev/ttyUSB* >/dev/null 2>&1; then
    for d in /dev/ttyUSB*; do
        ok "串口 $d"
        LAT="/sys/bus/usb-serial/devices/$(basename "$d")/latency_timer"
        if [ -r "$LAT" ]; then
            V=$(cat "$LAT")
            if [ "$V" -gt 2 ]; then
                warn "$(basename "$d") latency_timer=$V ms（默认常为 16）—— 会直接抬高端到端延迟"
                if [ "${1:-}" = "--fix-ftdi" ]; then
                    if [ "$(id -u)" -eq 0 ]; then
                        echo 1 > "$LAT" && ok "  已改为 1 ms"
                    else
                        warn "  需要 root 才能修改；请用 sudo 重跑"
                    fi
                else
                    warn "  修复：echo 1 | sudo tee $LAT"
                fi
            else
                ok "$(basename "$d") latency_timer=$V ms"
            fi
        fi
    done
else
    warn "未发现 /dev/ttyUSB*（若你的数据源不用 USB 串口可忽略）"
fi
if ls /dev/ttyPS* >/dev/null 2>&1; then
    ok "PS UART: $(ls /dev/ttyPS* | tr '\n' ' ')"
fi

hdr "5. 常见抢占风险"
if pgrep -x ModemManager >/dev/null 2>&1; then
    warn "ModemManager 正在运行 —— 可能试探串口并干扰读取"
    warn "  规避：给设备加 udev 标签 ENV{ID_MM_DEVICE_IGNORE}=\"1\""
else
    ok "未发现 ModemManager"
fi
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet ModemManager 2>/dev/null; then
        warn "systemd 中 ModemManager 处于 active"
    fi
fi

hdr "6. 内存与存储"
ok "内存: $(awk '/MemTotal/{printf "%.1f GB", $2/1048576}' /proc/meminfo)"
for m in / /mnt /media/sd*; do
    [ -d "$m" ] && ok "挂载 $m 可用空间 $(df -h "$m" 2>/dev/null | awk 'NR==2{print $4}')"
done

hdr "7. 采集前自检建议（人工确认）"
cat <<'EOF'
  □ bitstream 的哈希与参数文件版本一致
  □ 参数下发后**回读校验**通过（不要只看写入成功）
  □ 复位一次，确认状态寄存器回到已知初值
  □ 先跑回放（固定测试向量）跑通，再上实时源
  □ 记录板卡型号 / 器件 / 时钟 / 引脚，写进部署清单
EOF

hdr "汇总"
printf '  FAIL=%d  WARN=%d\n' "$FAIL" "$WARN"
if [ "$FAIL" -gt 0 ]; then
    echo "  → 有阻塞项，先处理 FAIL 再上板"
    exit 1
fi
if [ "$WARN" -gt 0 ]; then
    echo "  → 无阻塞项，但有告警，建议逐条确认"
else
    echo "  → 环境未见明显问题"
fi
exit 0
