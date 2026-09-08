"""Persist readable failure evidence without implying a completed prediction."""

import html
import json
from pathlib import Path


def write_failure(output: Path, *, error: str, error_code: str, details=None,
                  elapsed_s=0.0, stage="simulation"):
    summary = {"execution_status": "failed", "engineering_acceptance": "not_assessed",
               "error": error, "error_code": error_code, "error_details": details or {},
               "stage": stage, "elapsed_s": round(elapsed_s, 2),
               "artifacts": {"report": "report.html"}}
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                  allow_nan=False) + "\n", encoding="utf-8")
    escape = html.escape
    detail = escape(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HIPForm | Validation Failed</title>
<style>body{{margin:0;font:16px/1.6 Arial,sans-serif;color:#20242a;background:#fff}}
main{{max-width:960px;margin:auto;padding:28px}}h1{{font-size:26px}}h2{{font-size:20px}}
.status{{color:#b91c1c}}pre{{padding:16px;background:#f3f5f7;white-space:pre-wrap;overflow-wrap:anywhere}}
p{{overflow-wrap:anywhere}}</style></head><body><main>
<h1>HIPForm</h1><h2 class="status">Validation Failed / 验证失败</h2>
<p>{escape(error)}</p><p>阶段：{escape(stage)}；错误代码：{escape(error_code)}</p>
<p>本次未完成成形预测，未生成有效的公差判定。工程验收：未评估。</p>
<pre>{detail}</pre></main></body></html>'''
    (output / "report.html").write_text(document, encoding="utf-8")
    return summary
