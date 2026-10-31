"""Opt-in real API checks of full-sentence replacements using synthetic data."""

import json
import argparse
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.bilingual import prepare_request, apply_response
from backend.credentials import load_deepseek_key
from backend.models import Document, Segment
from backend.providers.deepseek import DeepSeekEngine
from backend.providers.protocol import build_prompt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=PROJECT / "data")
    key = load_deepseek_key(parser.parse_args().data_dir / "deepseek_key.bin")
    if not key:
        raise RuntimeError("Saved DeepSeek key is unavailable")
    engine = DeepSeekEngine(key, timeout=90)
    report = []
    started = time.monotonic()
    try:
        for direction in ("zh", "en"):
            units = [Segment("a", "When the test starts, ", "测试开始时，"),
                     Segment("b", "we collect 10 samples. ", "我们收集10个样本。"),
                     Segment("c", "The budget remains unchanged.", "预算保持不变。")]
            source = "".join(s.source for s in units)
            document = Document(source=source, baseline=source, segments=units)
            if direction == "zh":
                document.edit_target(document.target.replace("10个样本", "25个样本，并保存全部原始记录"))
            else:
                document.edit(document.source.replace("10 samples", "25 samples and save all raw records"))
            request = prepare_request(document)
            assert request.mode == "sentence" and len(request.sentences) == 1
            assert request.sentences[0].segment_ids == ("a", "b")
            sent = json.loads(build_prompt(request).split("\n", 1)[1])
            engine.begin_operation()
            response = engine.translate_document(request)
            assert not response.patches and set(response.translations) == {"a"}
            assert apply_response(document, request, response)
            assert not document.pending and document.segments[-1] == units[-1]
            assert "25" in document.source and "25" in document.target
            report.append({"direction": direction, "mode": request.mode, "sent": sent["units"],
                           "result": response.translations, "unaffected_sentence_preserved": True,
                           "pending": document.pending})
            print(f"PASS {direction} complete sentence; unrelated sentence preserved", flush=True)
        # Verify a legacy large document's clause alignment also expands safely.
        large_path = PROJECT / "build" / "large_document" / "initialized.json"
        if large_path.exists():
            from backend.storage import load_document
            document = load_document(large_path)  # Synthetic fixture from the earlier integration check.
            previous = document.to_dict()
            document.edit_target(document.target.replace("内存占用", "峰值内存占用", 1))
            request = prepare_request(document)
            assert len(request.sentences) == 1
            assert "研究团队" in request.sentences[0].source_after
            assert "任务完成率" in request.sentences[0].source_after
            old_other = {s.id: s for s in document.segments if s.id not in request.affected_ids}
            engine.begin_operation()
            response = engine.translate_document(request)
            assert apply_response(document, request, response) and not document.pending
            assert all(s == old_other[s.id] for s in document.segments if s.id in old_other)
            report.append({"direction": "zh", "large_document_characters": len(previous["source"]),
                           "sentences_sent": 1, "legacy_clauses_joined": len(request.sentences[0].segment_ids),
                           "all_unaffected_units_preserved": True})
            print("PASS legacy large-document sentence update", flush=True)
        directory = PROJECT / "build" / "sentence_updates"
        directory.mkdir(parents=True, exist_ok=True)
        result = {"provider": "DeepSeek", "cases": report, "seconds": round(time.monotonic()-started, 2)}
        (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ALL PASS cases={len(report)} seconds={result['seconds']}", flush=True)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
