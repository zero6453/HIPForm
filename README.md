# HIPForm

从包套预测热等静压成品，并与目标模型比较。

HIPForm 直接读取工作目录中的 `cavity.step`（目标成品）和 `capsule.step`（包套材料实体），从包套推导粉末域，生成 `cavity+<job_id>.step` 和验证报告。预测零件欠尺寸为负，判定失败；平面允许 **0～10 mm**，非平面允许 **0～20 mm**。失败结果包含上游重新生成包套的建议。

当前版本是未经标定的小应变仿真原型。默认 HIP 工况可能超出模型适用范围；尺寸采样通过不代表真实工件验收通过。

## 运行当前两份 STEP

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。Ubuntu 还需安装 `libglu1-mesa`。

先在 HIPForm 项目目录安装依赖：

```bash
uv sync --locked
```

随后进入包含 `cavity.step` 与 `capsule.step` 的目录，使用已安装的 `hipform` 命令：

```bash
hipform verify --output simulation-runs/verify-001
```

如果没有将虚拟环境加入 PATH，请使用虚拟环境中的命令绝对路径。本机示例：

```bash
cd /Users/zeroduan/Documents/Codex/freecad
/Users/zeroduan/Documents/GitHub/HIPForm/.venv/bin/hipform verify --output simulation-runs/verify-001
```

报告为 `simulation-runs/verify-001/report.html`，预测 STEP 为同目录的 `cavity+<job_id>.step`。重新计算时选择新的输出目录；HTTP 接口会自动为每次提交生成独立任务目录。

## Swagger 与自动调用

在包含两份 STEP 的目录启动服务：

```bash
hipform serve --port 8000
```

打开 Swagger：<http://127.0.0.1:8000/docs>。`POST /api/jobs` 最小请求是：

```json
{"name": "当前包套验证", "config": {}}
```

接口直接读取固定目录中的两份文件，不需要上传或逐次传 STEP 路径。上游生成成功后调用此接口，即可创建验证任务；查询任务后打开 `report_url`。默认材料为20号钢和TC4，保温保压为900℃、120 MPa、3小时，粉末初始相对密度为0.65；全部配置可通过 Swagger 查询、保存和修改。

`status: completed` 表示计算完成，`result.validation_status` 表示尺寸通过或失败。尺寸失败仍保留预测 STEP、报告和 `upstream_regeneration_advice`。

## 演示与文档

```bash
uv run python scripts/run_demo.py
uv run pytest -q
uv build
```

公开演示使用合成 L 形型腔，沿用 `run` 命令的初始粉末域契约，不能作为当前包套的测试结果。**[查看公开演示报告](https://zero6453.github.io/HIPForm/)**。

- [HTTP API、Swagger 与工艺配置](docs/api.md)
- [CadQuery 接入流程和报告查看](docs/cadquery-integration.zh-CN.md)
- [模型和数值限制](docs/simulation.md)
- [GitHub Pages 发布说明](docs/pages.md)

GitHub Pages 展示静态报告，计算在本机执行；结果不会自动公开发布。本仓库不包含上游 CAD 生成器或用户的原始 STEP。第三方依赖遵循各自许可证。
