"""Opt-in live integration check using synthetic text and the workspace API key."""

import json
from pathlib import Path
import sys
import re
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.batching import batches
from backend.bilingual import prepare_request, apply_response
from backend.credentials import load_deepseek_key
from backend.models import Document
from backend.providers.deepseek import DeepSeekEngine
from backend.storage import save_document


def main():
    directory = PROJECT / "build" / "large_document"
    directory.mkdir(parents=True, exist_ok=True)
    paragraphs = []
    for index in range(1, 81):
        paragraphs.append(
            f"## 第{index}节：项目评估 `CHK{index:03d}`\n\n"
            f"本节记录第{index}组试验的设计与结果。研究团队在相同的环境中运行三个版本，"
            "分别测量初始化耗时、内存占用和任务完成率。每次试验开始前都重新加载输入文件，"
            "避免缓存干扰测量结果。团队保留原始记录，并在发现异常时重复测量。\n\n"
            "结果表明，分批处理能够降低单次请求的输出压力。即使中间发生网络中断，"
            "系统也应保留已完成的批次，允许用户在内容不变时继续。最终文档必须包含所有段落，"
            "保持标题顺序和数字准确，不能用摘要替代正文。测试结束后，检查人员会逐段核对内容。\n")
    chinese = "\n".join(paragraphs)
    document = Document(title="大文档真实接口验证（合成数据）", target_text=chinese)
    request = prepare_request(document)
    chunks = batches(request)
    print(f"START chars={len(chinese)} units={len(request.segments)} batches={len(chunks)}", flush=True)
    key = load_deepseek_key(PROJECT / "data" / "deepseek_key.bin")
    if not key:
        raise RuntimeError("Workspace DeepSeek key is unavailable")
    engine = DeepSeekEngine(key, timeout=90)
    original_translate = engine.translate
    calls = 0

    def translate(chunk):
        nonlocal calls
        calls += 1
        print(f"REQUEST {calls} mode={chunk.mode} chars={len(chunk.source_after)}", flush=True)
        result = original_translate(chunk)
        print(f"RESPONSE {calls} seconds={engine.last_timing.get('total_seconds')}", flush=True)
        return result

    engine.translate = translate
    started = time.monotonic()
    try:
        engine.begin_operation()
        response = engine.translate_document(request)
        expected = {s.id for s in request.segments if s.source.strip()}
        assert set(response.translations) == expected
        assert apply_response(document, request, response)
        assert not document.pending and document.target == chinese
        assert all(f"CHK{i:03d}" in document.source for i in range(1, 81))
        assert not re.search(r"[.,][A-Za-z]", document.source)
        report = {"provider": "DeepSeek", "model": engine.MODEL, "input_characters": len(chinese),
                  "nonblank_units": len(expected), "batches": len(chunks),
                  "output_characters": len(document.source), "all_units_received": True,
                  "all_80_section_markers_present": True, "chinese_preserved": True,
                  "pending": document.pending, "initial_seconds": round(time.monotonic()-started, 2)}
        report["english_boundary_spacing"] = True
        save_document(directory / "initialized.json", document)
        (directory / "input-zh.md").write_text(chinese, encoding="utf-8")
        (directory / "output-en.md").write_text(document.source, encoding="utf-8")
        # Exercise both update directions against the real aligned document.
        document.edit_target(document.target.replace("第1组试验", "第101组试验", 1))
        engine.begin_operation()
        update = prepare_request(document)
        assert update.source_language == "zh"
        assert apply_response(document, update, engine.translate_document(update))
        assert not document.pending
        first = next(s for s in document.segments if "101" in s.source)
        document.edit(document.source.replace(first.source, first.source.replace("101", "202", 1), 1))
        engine.begin_operation()
        update = prepare_request(document)
        assert update.source_language == "en"
        assert apply_response(document, update, engine.translate_document(update))
        assert not document.pending and "202" in document.target
        report["live_bidirectional_updates"] = True
        report["total_seconds"] = round(time.monotonic()-started, 2)
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
