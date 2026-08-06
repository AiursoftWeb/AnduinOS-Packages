#!/usr/bin/env python3
"""Regenerate the Waypoint 2.0 POT and zh_CN catalog from live source."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check-i18n.py"
POT = ROOT / "po" / "anduinos-waypoint-gtk.pot"
ZH_CN = ROOT / "po" / "zh_CN.po"

NEW_TRANSLATIONS = {
    "About Waypoint": "关于 Waypoint",
    "After Package Change": "软件包变更后",
    "AnduinOS Team": "AnduinOS 团队",
    "Before Package Change": "软件包变更前",
    "Browse files in this snapshot": "浏览此快照中的文件",
    "Cancelling rollback…": "正在取消回滚…",
    "Checking rollback safety…": "正在检查回滚安全性…",
    "Checking snapshot…": "正在检查快照…",
    "Copy This Folder…": "复制此文件夹…",
    "Creating snapshot…": "正在创建快照…",
    "Current system": "当前系统",
    "Deleting snapshots…": "正在删除快照…",
    "Files Recovered": "文件已恢复",
    "Kernel": "内核",
    "Main Menu": "主菜单",
    "No matching snapshots": "没有匹配的快照",
    "Pending rollback": "等待回滚",
    "Permanently protected": "永久保留",
    "Personal files": "个人文件",
    "Prepare a safe system rollback": "准备安全的系统回滚",
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
    "Snapshots are not available on this computer": "此计算机无法使用快照",
    "Snapshots could not be loaded": "无法加载快照",
    "System files and packages": "系统文件和软件包",
    "The current system was preserved permanently. Restart now to roll back, or cancel from the System Recovery banner.": "当前系统已永久保留。现在重启即可回滚，也可在“系统恢复”横幅中取消。",
    "The root filesystem is {0}.": "根文件系统为 {0}。",
    "The selected files were recovered successfully.": "所选文件已成功恢复。",
    "This snapshot is not available for recovery.": "此快照不能用于恢复。",
    "Try a different name, date, or snapshot reason.": "请尝试其他名称、日期或快照原因。",
    "Unknown state": "未知状态",
    "Updating snapshot protection…": "正在更新快照保护状态…",
    "Warnings": "警告",
    "Waypoint requires the standard AnduinOS Btrfs layout.": "Waypoint 需要标准的 AnduinOS Btrfs 布局。",
    "Will not change": "不会改变",
    "{0} snapshot record(s) need attention": "有 {0} 条快照记录需要处理",
}


def load_check_module():
    spec = importlib.util.spec_from_file_location("waypoint_check_i18n", CHECK)
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
    lines = header("anduinos-waypoint-gtk 0.1.0", "zh_CN" if translations else None)
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
    print(f"Regenerated {len(messages)} current Waypoint 2.0 messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
