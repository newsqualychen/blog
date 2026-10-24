#!/usr/bin/env python3

import argparse
import hashlib
import html as html_module
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


# ============================================================
# 配置
# ============================================================

IMAGE_HOST = "photo.aaza.top:666"

# 支持：
# https://photo.aaza.top:666/i/2024/06/30/6680e46d69da6.jpg
IMAGE_URL_RE = re.compile(
    r'https?://photo\.aaza\.top:666/i/[^\s"\'<>\\)]+'
    r'(?:\.[A-Za-z0-9]{2,6})',
    re.IGNORECASE
)

# Markdown 图片：
# ![xxx](URL)
# ![xxx]\(URL\)
MARKDOWN_IMAGE_RE = re.compile(
    r'!\[([^\]]*)\]\s*\\?\(\s*(https?://photo\.aaza\.top:666/i/[^\s"\'<>\\)]+)\s*\\?\)',
    re.IGNORECASE
)


# ============================================================
# 工具
# ============================================================

def log(msg):
    print(msg)


def sha256_file(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def safe_filename(name):
    name = urllib.parse.unquote(name)

    # Mac / Linux 不允许这些字符作为路径分隔
    name = name.replace("/", "_")
    name = name.replace("\\", "_")

    # 去掉控制字符
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)

    if not name:
        name = "image"

    return name


def filename_from_url(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path)

    if not name:
        name = "image"

    return safe_filename(name)


def download_image(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 已存在就不重复下载
    if destination.exists() and destination.stat().st_size > 0:
        return "exists"

    log(f"    下载: {url}")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X) "
                "AppleWebKit/537.36 "
                "Chrome/154 Safari/537.36"
            )
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()

        if not data:
            raise RuntimeError("empty response")

        with open(destination, "wb") as f:
            f.write(data)

        log(
            f"    保存: {destination.name} "
            f"({len(data) / 1024:.1f} KB)"
        )

        return "downloaded"

    except Exception as e:
        log(f"    !!! 下载失败: {e}")

        if destination.exists():
            destination.unlink()

        return "failed"


# ============================================================
# HTML → Markdown
# ============================================================

def convert_html_to_markdown(html_file, dry_run=True):
    md_file = html_file.with_suffix(".md")

    if md_file.exists():
        log(
            f"HTML跳过（目标已存在）: "
            f"{html_file} -> {md_file}"
        )
        return None

    log(f"HTML → Markdown: {html_file}")

    if dry_run:
        log(f"    [DRY RUN] 将生成: {md_file}")
        return md_file

    cmd = [
        "pandoc",
        "-f", "html",
        "-t", "gfm",
        "--wrap=none",
        str(html_file),
        "-o",
        str(md_file),
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        log("!!! 找不到 pandoc，请先安装 pandoc")
        sys.exit(1)

    except subprocess.CalledProcessError as e:
        log(f"!!! Pandoc转换失败: {e.stderr}")
        return None

    log(f"    生成: {md_file}")

    return md_file


# ============================================================
# 找图片 URL
# ============================================================

def find_image_urls(text):
    urls = []

    # 优先识别标准 Markdown 图片
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        urls.append(match.group(2))

    # 再扫描所有旧图床 URL
    for match in IMAGE_URL_RE.finditer(text):
        url = match.group(0)

        if url not in urls:
            urls.append(url)

    return urls


# ============================================================
# 文章目录
# ============================================================

def get_bundle_info(md_file, content_root):
    """
    返回：
        bundle_dir
        content_file
    """

    # 已经是：
    #
    # content/posts/foo/index.md
    #
    # 直接使用
    if md_file.name.lower() == "index.md":
        return md_file.parent, md_file

    # 普通：
    #
    # content/posts/foo.md
    #
    # 转成：
    #
    # content/posts/foo/index.md

    bundle_dir = md_file.parent / md_file.stem
    content_file = bundle_dir / "index.md"

    return bundle_dir, content_file


# ============================================================
# 迁移单篇 Markdown
# ============================================================

def migrate_markdown(md_file, content_root, dry_run=True):
    log("")
    log("=" * 70)
    log(f"文章: {md_file}")

    try:
        text = md_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        log("!!! 不是 UTF-8，跳过")
        return False

    urls = find_image_urls(text)

    bundle_dir, content_file = get_bundle_info(
        md_file,
        content_root
    )

    log(f"Bundle: {bundle_dir}")

    if urls:
        log(f"发现图片: {len(urls)} 张")
    else:
        log("没有发现旧图床图片")

    # --------------------------------------------
    # 如果本来就是 index.md
    # --------------------------------------------

    if md_file.name.lower() == "index.md":

        new_text = text

    # --------------------------------------------
    # 普通 xxx.md
    # --------------------------------------------

    else:

        new_text = text

    replacements = {}

    # --------------------------------------------
    # 下载图片
    # --------------------------------------------

    used_names = {}

    for url in urls:

        filename = filename_from_url(url)

        # 如果同一文章出现同名文件但 URL 不一样
        # 加 URL hash 防止覆盖
        if filename in used_names:

            if used_names[filename] != url:

                stem = Path(filename).stem
                suffix = Path(filename).suffix

                url_hash = hashlib.md5(
                    url.encode("utf-8")
                ).hexdigest()[:8]

                filename = (
                    f"{stem}-{url_hash}{suffix}"
                )

        used_names[filename] = url

        destination = bundle_dir / filename

        if not dry_run:
            status = download_image(
                url,
                destination
            )

            if status == "failed":
                continue

        else:
            log(
                f"    [DRY RUN] "
                f"{url} -> {destination}"
            )

        replacements[url] = filename

    # --------------------------------------------
    # 替换 URL
    # --------------------------------------------

    for old_url, filename in replacements.items():

        # Markdown正常形式
        new_text = new_text.replace(
            old_url,
            filename
        )

    # --------------------------------------------
    # 处理你这种：
    #
    # ![xxx]\([URL]\)
    #
    # 替换后可能变成：
    #
    # ![xxx]\(xxx.jpg\)
    #
    # Hugo Markdown其实不需要反斜杠
    # --------------------------------------------

    new_text = re.sub(
        r'!\[([^\]]*)\]\s*\\\(\s*([^)]+?)\s*\\\)',
        r'![\1](\2)',
        new_text
    )

    # --------------------------------------------
    # 普通 md → Page Bundle
    # --------------------------------------------

    if md_file.name.lower() != "index.md":

        if dry_run:

            log(
                f"    [DRY RUN] "
                f"{md_file} -> {content_file}"
            )

        else:

            bundle_dir.mkdir(
                parents=True,
                exist_ok=True
            )

            content_file.write_text(
                new_text,
                encoding="utf-8"
            )

            # 原文件改名/移动
            #
            # 但图片如果原本在文章附近，
            # 暂时不删除任何东西。
            #
            # 迁移完成后才建议人工清理。
            log(
                f"    已生成: {content_file}"
            )

    else:

        if not dry_run and new_text != text:

            md_file.write_text(
                new_text,
                encoding="utf-8"
            )

            log("    已更新 index.md")

    return True


# ============================================================
# 主程序
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Hugo旧图床 + HTML → Markdown迁移工具"
        )
    )

    parser.add_argument(
        "content",
        help="Hugo content目录"
    )

    parser.add_argument(
        "--run",
        action="store_true",
        help="真正执行修改/下载，默认只是预览"
    )

    parser.add_argument(
        "--html",
        action="store_true",
        help="同时把 HTML 转换为 Markdown"
    )

    parser.add_argument(
        "--html-only",
        action="store_true",
        help="只转换 HTML，不处理图片"
    )

    args = parser.parse_args()

    content_root = Path(
        args.content
    ).expanduser().resolve()

    if not content_root.exists():
        log(
            f"错误：目录不存在: "
            f"{content_root}"
        )
        sys.exit(1)

    dry_run = not args.run

    log("")
    log("=" * 70)
    log("Hugo 文章 / 图片迁移工具")
    log("=" * 70)

    if dry_run:
        log("")
        log("当前是 DRY RUN 预览模式")
        log("不会修改任何文件，也不会下载图片")
        log("")
        log("确认无误后使用:")
        log("")
        log(
            f"python3 {Path(sys.argv[0]).name} "
            f'"{content_root}" --run'
        )

    # ========================================================
    # HTML → Markdown
    # ========================================================

    if args.html or args.html_only:

        html_files = list(
            content_root.rglob("*.html")
        )

        # 排除 Hugo 模板目录可能存在的 HTML
        html_files = [
            f for f in html_files
            if "layouts" not in f.parts
            and "themes" not in f.parts
        ]

        log("")
        log(
            f"发现 HTML 文件: "
            f"{len(html_files)}"
        )

        for html_file in html_files:
            convert_html_to_markdown(
                html_file,
                dry_run=dry_run
            )

        if args.html_only:
            return

    # ========================================================
    # Markdown
    # ========================================================

    md_files = list(
        content_root.rglob("*.md")
    )

    # 排除 Hugo / Git / 主题
    md_files = [
        f for f in md_files
        if ".git" not in f.parts
        and "themes" not in f.parts
    ]

    log("")
    log(
        f"发现 Markdown 文件: "
        f"{len(md_files)}"
    )

    total = 0

    for md_file in md_files:

        # 如果是已经生成的 bundle index.md
        # 允许继续处理
        if migrate_markdown(
            md_file,
            content_root,
            dry_run=dry_run
        ):
            total += 1

    log("")
    log("=" * 70)
    log(f"处理完成: {total} 篇")
    log("=" * 70)

    if dry_run:
        log("")
        log("⚠️ 以上只是预览，没有任何文件被修改。")
        log("")
        log("确认结果没问题后运行:")
        log("")
        log(
            f'python3 "{Path(sys.argv[0])}" '
            f'"{content_root}" --run'
        )


if __name__ == "__main__":
    main()
