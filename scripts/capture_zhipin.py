#!/usr/bin/env python3
"""Boss直聘 半自动采集脚本（采集你本人账号的搜索结果，供个人分析）。

用法：
    uv pip install "infogap[capture]" && playwright install chromium
    .venv/bin/python scripts/capture_zhipin.py "Agent 开发" --pages 3
    # 浏览器打开后手动登录 → 搜索到结果列表页 → 回到终端按回车开始采集
    # 采集结果写入 data/imports/zhipin_capture.jsonl，随后在 Web「数据导入」页导入

合规提示：仅采集你自己登录态下的搜索结果页，勿高频翻页、勿用于转售数据。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

JS_EXTRACT = """
() => Array.from(document.querySelectorAll(
  'li.job-card-wrapper, div.job-card-wrapper, li.job-card-left'
)).map(el => {
  const pick = s => el.querySelector(s)?.innerText?.trim() || '';
  const tags = Array.from(el.querySelectorAll('.tag-list li, .job-tags span'))
    .map(t => t.innerText.trim()).slice(0, 8).join(' ');
  return [pick('.job-name, .job-title'), pick('.salary, .job-salary'),
          pick('.company-name'), pick('.job-area'), tags]
    .filter(Boolean).join('\\n');
}).filter(t => t.length > 4)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keyword", help="搜索关键词（登录后也可手动换）")
    parser.add_argument("--pages", type=int, default=2, help="翻页采集页数")
    parser.add_argument("--out", default=str(DATA / "imports" / "zhipin_capture.jsonl"))
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("需要先安装：uv pip install 'infogap[capture]' && playwright install chromium")

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "browser_profile").mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(DATA / "browser_profile"), headless=False,
            viewport={"width": 1440, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.zhipin.com/")
        input(f"\n>>> 在打开的浏览器中登录并搜索「{args.keyword}」，看到结果列表后回车开始采集...")
        collected: list[str] = []
        for i in range(args.pages):
            page.wait_for_timeout(1500)
            try:
                cards = page.evaluate(JS_EXTRACT)
            except Exception as e:  # noqa: BLE001
                print(f"第{i + 1}页提取失败：{e}")
                cards = []
            collected += cards
            print(f"第 {i + 1}/{args.pages} 页：{len(cards)} 条，累计 {len(collected)}")
            if i + 1 < args.pages:
                try:
                    page.click(".options-next", timeout=5000)
                    page.wait_for_timeout(2500)   # 温和翻页
                except Exception:
                    print("无法点击下一页（可能已到末页）")
                    break
        ctx.close()

    with out_path.open("w", encoding="utf-8") as f:
        for t in collected:
            f.write(json.dumps({"text": t}, ensure_ascii=False) + "\n")
    print(f"\n完成：{len(collected)} 条 → {out_path}")
    print("到 Web「数据导入」页上传该文件，或在终端执行：")
    print(f"  .venv/bin/infogap ingest-file {out_path}")


if __name__ == "__main__":
    main()
