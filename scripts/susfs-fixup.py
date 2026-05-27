#!/usr/bin/env python3
"""SUSFS patch hunk-fail 后的幂等兜底修复。

背景: simonpunk/susfs4ksu 的 `50_add_susfs_in_gki-*.patch` 在部分 OnePlus 机型
源码上 hunk 失败但脚本用 `|| true` 吞掉错误, 导致后续 hunk 引入的代码引用了
未声明的符号/变量, 编译报错:

    fs/proc/base.c:    SUSFS_IS_INODE_OPEN_REDIRECT / SUSFS_IS_INODE_SUS_MAP undeclared
    mm/memory.c:       SUSFS_IS_INODE_SUS_MAP undeclared
    fs/proc/task_mmu.c: use of undeclared identifier 'vma' (pagemap_read)

本脚本扫描相关文件, 必要时:
  1) 注入 `#include <linux/susfs_def.h>`
  2) 在 pagemap_read() 内补 `struct vm_area_struct *vma;` 局部声明

已经被 patch 正确改过的文件 (含 susfs_def.h 或已声明 vma) 不会被二次修改。

用法: scripts/susfs-fixup.py <kernel_common_dir>
"""

import re
import sys
from pathlib import Path

# 任意一个 SUSFS_* config 启用都会触发 susfs_def.h 的引用
SUSFS_INCLUDE_GUARD = (
    "#if defined(CONFIG_KSU_SUSFS) || defined(CONFIG_KSU_SUSFS_SUS_PATH) "
    "|| defined(CONFIG_KSU_SUSFS_SUS_MAP) || defined(CONFIG_KSU_SUSFS_SUS_KSTAT) "
    "|| defined(CONFIG_KSU_SUSFS_SUS_MOUNT) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)"
)
SUSFS_INCLUDE_BLOCK = (
    f"{SUSFS_INCLUDE_GUARD}\n"
    "#include <linux/susfs_def.h>\n"
    "#endif\n"
)


def inject_susfs_include(path: Path) -> bool:
    """在文件第一个 `#include` 行后插入 susfs_def.h 条件 include。

    返回 True 表示发生了修改。
    """
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if "linux/susfs_def.h" in text:
        print(f"  skip {path} (already has susfs_def.h)")
        return False

    lines = text.splitlines(keepends=True)
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.lstrip().startswith("#include"):
            # 保证插入块也以换行结束
            block = SUSFS_INCLUDE_BLOCK if SUSFS_INCLUDE_BLOCK.endswith("\n") else SUSFS_INCLUDE_BLOCK + "\n"
            out.append(block)
            inserted = True

    if not inserted:
        print(f"  warn {path}: no #include line found, skipped")
        return False

    path.write_text("".join(out), encoding="utf-8")
    print(f"  inject susfs_def.h -> {path}")
    return True


def inject_pagemap_vma(path: Path) -> bool:
    """在 pagemap_read() 函数体里补 `struct vm_area_struct *vma;` 声明。

    仅当 SUSFS patch 已经在 task_mmu.c 中注入了对 vma 的引用 (BIT_SUS_MAPS)
    但 pagemap_read 内本身没有 vma 声明时才动作。
    """
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")

    if "BIT_SUS_MAPS" not in text or "susfs_is_current_proc_umounted" not in text:
        # SUSFS hunk 没注入 -> 不需要 fixup
        return False

    # 定位 pagemap_read 函数体: 从签名到第一个独立 `}` 行
    m = re.search(r"^static ssize_t pagemap_read\b[^\n]*\n", text, flags=re.MULTILINE)
    if not m:
        return False
    start = m.start()
    # 简单 brace 匹配: 从函数签名后第一个 `{` 开始数
    body_start = text.find("{", m.end() - 1)
    if body_start < 0:
        return False
    depth = 0
    i = body_start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                body_end = i + 1
                break
        i += 1
    else:
        return False

    body = text[start:body_end]
    if re.search(r"struct\s+vm_area_struct\s*\*\s*vma\s*;", body):
        # 已声明
        return False

    # 在 body 内第一处 "pagemap_entry_t *res = NULL;" 之后插入声明
    anchor = "pagemap_entry_t *res = NULL;"
    anchor_pos = body.find(anchor)
    if anchor_pos < 0:
        # 退而求其次: 在 body 第一行后插
        first_nl = body.find("\n")
        if first_nl < 0:
            return False
        insert_at = start + first_nl + 1
    else:
        insert_at = start + anchor_pos + len(anchor)
        # 跳到该行末尾的换行
        nl = text.find("\n", insert_at)
        if nl < 0:
            return False
        insert_at = nl + 1

    snippet = (
        "#ifdef CONFIG_KSU_SUSFS_SUS_MAP\n"
        "\tstruct vm_area_struct *vma;\n"
        "#endif\n"
    )
    new_text = text[:insert_at] + snippet + text[insert_at:]
    path.write_text(new_text, encoding="utf-8")
    print(f"  inject 'struct vm_area_struct *vma' -> {path}::pagemap_read")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    common = Path(sys.argv[1]).resolve()
    if not common.is_dir():
        print(f"::error::not a directory: {common}", file=sys.stderr)
        return 2

    # 1) include 兜底
    for rel in ("fs/proc/base.c", "fs/proc/task_mmu.c", "fs/proc_namespace.c", "mm/memory.c"):
        inject_susfs_include(common / rel)

    # 2) task_mmu.c::pagemap_read 漏掉的 vma 局部变量声明
    inject_pagemap_vma(common / "fs/proc/task_mmu.c")

    print("✅ post-patch SUSFS fixups 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
