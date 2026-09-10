# HIPForm

从包套预测热等静压成品，并与目标模型比较。

HIPForm 直接读取工作目录中的 `cavity.step`（目标成品）和 `capsule.step`（包套材料实体），从包套推导粉末域，共同求解包套与粉末的变形，生成成型粉末 `cavity+<job_id>.step` 和报告。与目标模型的 DIFF 保留有符号偏差，欠尺寸为负，不据此判定通过或失败。

**平面 0～10 mm、非平面 0～20 mm** 指烧制后粉末表面到包套内壁的间隙。当前未经标定的小应变模型将两材料界面绑定，共享节点强制间隙为零，不能预测实际分离，因此间隙验证返回 `not_assessed`，不据此生成包套修正量。默认 HIP 工况还可能超出模型适用范围；成型粉末 STEP 未包含去包套后的应力释放。

## 运行当前两份 STEP

调用远程 ANSYS/MAPDL 服务请使用 [ANSYS 服务接入说明](docs/ansys-service.zh-CN.md) 中的 `scripts/run_ansys.py`。该入口自动读取两份 STEP、提交远程任务并取回结果。当前服务的 `full3d-hip` 为 `smoke` 联调模型，尚不提供真实烧结成型 STEP 或接触间隙判定。下面的 `hipform verify` 和 `/api/jobs` 仍是本地模型入口。

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

`status: completed` 表示计算完成，当前 `result.validation_status: not_assessed` 表示尚不能评估真实烧后间隙。报告包含烧制前后包套与粉末的三维显示、目标 DIFF、工艺曲线和模型限制；`contact-assessment.json` 说明未评估原因，`upstream-advice.json` 返回空建议及阻塞原因。

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
