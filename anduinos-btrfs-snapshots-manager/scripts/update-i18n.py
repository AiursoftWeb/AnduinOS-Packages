#!/usr/bin/env python3
"""Regenerate the Disk Snapshots Manager 2.0 POT and zh_CN catalog from live source."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check-i18n.py"
POT = ROOT / "po" / "anduinos-btrfs-snapshots-manager.pot"
ZH_CN = ROOT / "po" / "zh_CN.po"

NEW_TRANSLATIONS = {
    "About Disk Snapshots Manager": "关于 Disk Snapshots Manager",
    "Checking recovery state…": "正在检查恢复状态…",
    "Preparing rollback to {0}…": "正在准备回滚到 {0}…",
    "Recovery for {0} is in an unknown state ({1}).": "{0} 的恢复事务处于未知状态（{1}）。",
    "Requesting restart…": "正在请求重启…",
    "Retry Recovery": "重试恢复确认",
    "Rollback confirmation failed. The protected previous system is being restored.": "回滚确认失败，正在恢复受保护的先前系统。",
    "Rollback to {0} is being applied during startup.": "启动期间正在应用到 {0} 的回滚。",
    "Rollback to {0} is ready. Restart to apply it.": "已准备回滚到 {0}，重启后即可应用。",
    "Rollback to {0} was applied, but system confirmation has not completed.": "已应用到 {0} 的回滚，但系统确认尚未完成。",
    "The rollback completed, but recovery cleanup has not completed.": "回滚已完成，但恢复清理尚未完成。",
    "The rollback failed. Recovery cleanup has not completed.": "回滚失败，恢复清理尚未完成。",
    "The rollback failed: {0}": "回滚失败：{0}",
    "The rollback was reverted, but recovery cleanup has not completed.": "回滚已撤销，但恢复清理尚未完成。",
    "After Package Change": "软件包变更后",
    "Automatic System Snapshot Created": "自动系统快照创建成功",
    "AnduinOS Team": "AnduinOS 团队",
    "Before Package Change": "软件包变更前",
    "Browse files in this snapshot": "浏览此快照中的文件",
    "Cancelling rollback…": "正在取消回滚…",
    "Checking rollback safety…": "正在检查回滚安全性…",
    "Checking snapshot…": "正在检查快照…",
    "Calculating snapshot size…": "正在计算快照大小…",
    "Copy This Folder…": "复制此文件夹…",
    "Creating snapshot…": "正在创建快照…",
    "Current system": "当前系统",
    "Deleting snapshots…": "正在删除快照…",
    "Details": "详情",
    "Exclusive Data": "独占数据",
    "Files Recovered": "文件已恢复",
    "Home Snapshot Created": "用户目录快照创建成功",
    "Kernel": "内核",
    "Main Menu": "主菜单",
    "Measured": "测量时间",
    "No matching snapshots": "没有匹配的快照",
    "Not calculated": "尚未计算",
    "Pending rollback": "等待回滚",
    "Permanently protected": "永久保留",
    "Personal files": "个人文件",
    "Prepare a safe system rollback": "准备安全的系统回滚",
    "Properties": "属性",
    "Preparing safe rollback…": "正在准备安全回滚…",
    "Preserved permanently as a fallback": "永久保留为回退系统",
    "Recorded snapshot kernel": "快照中记录的内核",
    "Recovering Files": "正在恢复文件",
    "Recovering files…": "正在恢复文件…",
    "Refresh snapshots": "刷新快照",
    "Renaming snapshot…": "正在重命名快照…",
    "Required to complete the rollback": "完成回滚需要重启",
    "Restart": "重启",
    "Return to the selected snapshot": "恢复到所选快照的状态",
    "Review what will happen before preparing the rollback.": "准备回滚前，请确认将发生的更改。",
    "Rollback reverted": "回滚已撤销",
    "Rollback to {0} is prepared ({1})": "已准备回滚到 {0}（{1}）",
    "Saving…": "正在保存…",
    "Select Snapshots": "选择快照",
    "Select snapshot": "选择快照",
    "Snapshot Actions": "快照操作",
    "Snapshot {0} · Folder": "快照 {0} · 文件夹",
    "Snapshot {0} · {1} bytes · modified {2}": "快照 {0} · {1} 字节 · 修改于 {2}",
    "Shared Data": "共享数据",
    "Size {0}": "大小 {0}",
    "Snapshot Details": "快照详情",
    "Snapshot Size Unavailable": "无法获取快照大小",
    "Snapshots are not available on this computer": "此计算机无法使用快照",
    "Snapshots could not be loaded": "无法加载快照",
    "Some snapshots could not be loaded": "部分快照无法加载",
    "System files and packages": "系统文件和软件包",
    "System Snapshot Created": "系统快照创建成功",
    "The Home snapshot was created successfully.": "用户目录快照创建成功。",
    "Btrfs quota accounting is disabled or unavailable. Disk Snapshots Manager will not start a quota rescan automatically.": "Btrfs 配额统计已禁用或不可用。Disk Snapshots Manager 不会自动启动配额重新扫描。",
    "The current system was preserved permanently. Restart now to roll back, or cancel from the System Recovery banner.": "当前系统已永久保留。现在重启即可回滚，也可在“系统恢复”横幅中取消。",
    "The root filesystem is {0}.": "根文件系统为 {0}。",
    "The scheduled Home snapshot was created successfully.": "计划的用户目录快照创建成功。",
    "The scheduled system snapshot was created successfully.": "计划的系统快照创建成功。",
    "The system snapshot was created successfully.": "系统快照创建成功。",
    "Total": "总量",
    "The selected files were recovered successfully.": "所选文件已成功恢复。",
    "This snapshot is not available for recovery.": "此快照不能用于恢复。",
    "This item was not present in the available Home snapshots.": "可用的用户目录快照中没有此项目。",
    "Try a different name, date, or snapshot reason.": "请尝试其他名称、日期或快照原因。",
    "Unknown state": "未知状态",
    "Updating snapshot protection…": "正在更新快照保护状态…",
    "Warnings": "警告",
    "old Home snapshot(s) were removed.": "个旧用户目录快照已删除。",
    "old system snapshot(s) and": "个旧系统快照和",
    "old system snapshot(s) were removed.": "个旧系统快照已删除。",
    "Disk Snapshots Manager requires the standard AnduinOS Btrfs layout.": "Disk Snapshots Manager 需要标准的 AnduinOS Btrfs 布局。",
    "Will not change": "不会改变",
    "{0} snapshot record(s) need attention": "有 {0} 条快照记录需要处理",
    "{0} available": "可用 {0}",
}


def load_check_module():
    spec = importlib.util.spec_from_file_location("btrfs-snapshots-manager_check_i18n", CHECK)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load check-i18n.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def header(project: str, language: str | None = None) -> list[str]:
    lines = [
        "msgid \"\"",
        "msgstr \"\"",
        quote(f"Project-Id-Version: {project}\n"),
        quote("POT-Creation-Date: 2026-08-06 00:00+0800\n"),
        quote("PO-Revision-Date: 2026-08-06 00:00+0800\n"),
        quote("Last-Translator: AnduinOS Team <anduin@aiursoft.com>\n"),
        quote("Language-Team: AnduinOS Team\n"),
        quote("MIME-Version: 1.0\n"),
        quote("Content-Type: text/plain; charset=UTF-8\n"),
        quote("Content-Transfer-Encoding: 8bit\n"),
    ]
    if language:
        lines.extend(
            [
                quote(f"Language: {language}\n"),
                quote("Plural-Forms: nplurals=1; plural=0;\n"),
            ]
        )
    return lines


def write_catalog(path: Path, messages: dict[str, set[str]], translations=None) -> None:
    lines = header("anduinos-btrfs-snapshots-manager 0.1.0", "zh_CN" if translations else None)
    for message in sorted(messages, key=str.casefold):
        lines.append("")
        lines.append("#: " + " ".join(sorted(messages[message])))
        lines.append("msgid " + quote(message))
        lines.append("msgstr " + quote(translations[message] if translations else ""))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    check = load_check_module()
    messages = check.rust_messages()
    for message, locations in check.python_messages().items():
        messages.setdefault(message, set()).update(locations)

    existing = check.po_entries(ZH_CN)
    translations = {}
    missing = []
    for message in messages:
        value = NEW_TRANSLATIONS.get(message) or existing.get(message, "")
        if not value:
            missing.append(message)
        else:
            translations[message] = value
    if missing:
        raise SystemExit("missing zh_CN translations:\n" + "\n".join(sorted(missing)))

    write_catalog(POT, messages)
    write_catalog(ZH_CN, messages, translations)
    print(f"Regenerated {len(messages)} current Disk Snapshots Manager 2.0 messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
