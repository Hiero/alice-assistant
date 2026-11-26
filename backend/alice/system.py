"""Read-only facts about this computer: load, memory, temperatures, disks and busy processes.

Nothing here changes anything, so none of it needs the user's approval.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

REAL_FILESYSTEMS = {"ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs3", "ntfs", "zfs", "bcachefs"}


async def output(*argv: str, timeout: float = 5) -> str:
    try:
        process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        data, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (OSError, TimeoutError):
        return ""
    return data.decode(errors="replace").strip() if process.returncode == 0 else ""


def gib(n_bytes: float) -> str:
    return f"{n_bytes / 2**30:.1f} GiB"


def memory() -> str:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, _, value = line.partition(":")
        info[name] = int(value.split()[0]) * 1024
    total, available = info["MemTotal"], info["MemAvailable"]
    swap_used = info["SwapTotal"] - info["SwapFree"]
    text = f"RAM {gib(total - available)} used of {gib(total)} ({100 * (total - available) / total:.0f}%)"
    if info["SwapTotal"]:
        text += f", swap {gib(swap_used)} of {gib(info['SwapTotal'])}"
    return text


async def cpu() -> str:
    def sample() -> tuple[int, int]:
        fields = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(fields), fields[3] + fields[4]  # idle and iowait

    total_a, idle_a = sample()
    await asyncio.sleep(0.5)
    total_b, idle_b = sample()
    busy = 100 * (1 - (idle_b - idle_a) / max(total_b - total_a, 1))
    load = ", ".join(f"{x:.1f}" for x in os.getloadavg())
    return f"CPU {busy:.0f}% busy over {os.cpu_count()} threads, load average {load}"


async def gpu() -> str:
    found = await output(
        "nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,fan.speed",
        "--format=csv,noheader,nounits",
    )
    lines = []
    for row in found.splitlines():
        name, temp, util, used, total, power, fan = [v.strip() for v in row.split(",")]
        lines.append(f"GPU {name}: {temp}°C, {util}% busy, {used} of {total} MiB video memory, {power} W, fan {fan}%")
    return "; ".join(lines)


async def temperatures() -> str:
    found = await output("sensors", "-j")
    best: dict[str, float] = {}
    try:
        chips = json.loads(found) if found else {}
    except json.JSONDecodeError:
        chips = {}
    for chip, readings in chips.items():
        values = [
            (label, value)
            for label, sensor in readings.items() if isinstance(sensor, dict)
            for key, value in sensor.items() if key.startswith("temp") and key.endswith("_input")
        ]
        if not values:
            continue
        kind = "CPU" if chip.startswith(("k10temp", "coretemp", "zenpower")) else "SSD" if chip.startswith("nvme") else \
            "RAM" if chip.startswith("spd") else "Wi-Fi" if "phy" in chip else \
            "Ethernet" if chip.startswith(("r8169", "r8125", "igc", "e1000")) else chip.split("-")[0]
        hottest = max(v for _, v in values)
        best[kind] = max(best.get(kind, hottest), hottest)  # Two RAM sticks become one RAM reading.
    return ", ".join(f"{kind} {value:.0f}°C" for kind, value in best.items())


def battery() -> str:
    for supply in Path("/sys/class/power_supply").glob("BAT*"):
        try:
            level = (supply / "capacity").read_text().strip()
            status = (supply / "status").read_text().strip().lower()
        except OSError:
            continue
        return f"battery {level}%, {status}"
    return ""


def disks() -> str:
    seen, lines = set(), []
    for line in Path("/proc/mounts").read_text().splitlines():
        device, mount, kind = line.split()[:3]
        if kind not in REAL_FILESYSTEMS or device in seen or mount.startswith(("/boot", "/efi", "/snap")):
            continue
        seen.add(device)  # btrfs subvolumes share a device; count it once.
        mount = mount.replace("\\040", " ")
        usage = shutil.disk_usage(mount)
        lines.append(f"{mount}: {gib(usage.free)} free of {gib(usage.total)} ({100 * usage.used / usage.total:.0f}% used)")
    return "; ".join(lines)


async def processes(count: int = 8) -> str:
    memory_rows = []
    for line in (await output("ps", "-eo", "pid,rss,comm", "--sort=-rss")).splitlines()[1: count + 1]:
        pid, rss, name = line.split(None, 2)  # The name goes last: it can contain spaces.
        memory_rows.append(f"{name} (pid {pid}): {int(rss) / 1024:.0f} MiB")
    # top's second sample is the CPU use over the last half second; ps only gives lifetime averages.
    process = await asyncio.create_subprocess_exec(
        "top", "-b", "-n", "2", "-d", "0.5", "-o", "%CPU", "-w", "200",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"},
    )
    found, _ = await process.communicate()
    last = found.decode(errors="replace").split("\ntop -")[-1].splitlines()
    header = next((i for i, line in enumerate(last) if line.strip().startswith("PID")), len(last))
    cpu_rows = []
    for line in last[header + 1: header + 1 + count]:
        fields = line.split(None, 11)
        if len(fields) == 12 and float(fields[8]) > 0:
            cpu_rows.append(f"{fields[11]} (pid {fields[0]}): {fields[8]}% of one CPU thread")
    return (
        "Using the most memory:\n" + "\n".join(memory_rows)
        + "\n\nUsing the most CPU right now:\n" + ("\n".join(cpu_rows) or "nothing is busy")
    )


def uptime() -> str:
    seconds = float(Path("/proc/uptime").read_text().split()[0])
    days, rest = divmod(int(seconds), 86400)
    return f"up {days} days, {rest // 3600} h {rest % 3600 // 60} min" if days else f"up {rest // 3600} h {rest % 3600 // 60} min"


async def system_status(action: str = "overview") -> str:
    if action == "disks":
        return "Disks: " + disks()
    if action == "processes":
        return await processes()
    if action == "temperatures":
        return "Temperatures: " + "; ".join(p for p in (await temperatures(), await gpu()) if p)
    parts = [await cpu(), memory(), await gpu(), "temperatures " + await temperatures(), "disks " + disks(), battery(), uptime()]
    return "This computer now: " + "; ".join(p for p in parts if p and not p.endswith("temperatures ")) + "."
