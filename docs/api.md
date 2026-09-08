# HIPForm HTTP API 与 Swagger 使用说明

## 启动

在 HIPForm 项目根目录运行：

```bash
uv sync --locked
uv run --locked hipform serve --port 8000
```

- Swagger：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>
- 健康检查：<http://127.0.0.1:8000/health>

这是供同机 CAD 项目调用的本地服务，仅绑定回环地址。远端部署的认证、TLS、访问控制及代理不在本版本中。
浏览器写入请求必须来自服务自身的 Origin；外站与 `Origin: null` 的写入返回 403。
Swagger 同源操作，以及不带 Origin 的 Python/HTTP 客户端调用可正常使用。
默认数据目录为 `simulation-runs/api`，可用 `--data-dir` 设置绝对路径；默认单次求解超时 3600 秒，
可用 `--job-timeout` 修改。每个数据目录只能启动一个服务，内部串行求解，避免多个 Gmsh 作业并发占用内存。

## 输入与检查规则

提交两份毫米单位的单实体 STEP：

- `cavity.step`：初始粉末所占区域，预测零件模型由该区域求解得到。
- `capsule.step`：包套材料本体，包括已建模的抽气管；不包含参考零件实体。

CAD 项目中的 `assembly.step` 适合装配查看，不能整体传给当前单材料输入。
对于 `hip-demo-cadquery`，取成功任务 `result.json` 中的 `output_dir`，使用该目录的
`cavity.step` 与 `capsule.step`，不要用装配中的原始参考件替代粉末域。

检查发生在求解前，材料间隙与开放抽气孔先在 CAD 几何层检查，避免粗网格掩盖问题：

| 条件 | 行为 |
| --- | --- |
| 粉末与包套完全分离或存在局部未贴合空间 | 失败，`error.code = material_gap` |
| 抽气孔开放、粉末经孔连通外界 | 失败，`error.code = open_capsule` |
| 粉末和包套材料体积重叠 | 失败，保留几何错误详情 |
| 主粉末域贴合，抽气管已封口且存在残余密闭管腔 | 允许，残余管腔不施加外压 |
| 检查通过 | 按配置进行现有有限元求解、表面比较并生成报告 |

间隙错误保留指定原文，即使是局部间隙也使用同一接口错误：

```text
Assembly contains disconnected material bodies in the mesh.
```

允许的抽气管残余空间目前限定为直圆管：圆形粉末端面、同轴圆柱管壁与平面封口，
入口周围有贴合壁面，外部可识别同轴管颈。旋转后的同类管道也支持。
`mesh-info.json` 中的 `sealed_vent_voids` 记录识别结果。复杂弯管、分段管或不符合该形状的残余空腔
会保守拒绝，不能把任意缺失粉末空间标为抽气管。完全位于包套材料内部、未接触粉末的密闭空洞延续原有处理。

程序不会自动填补间隙、改变粉末几何或封口。可传入预先封口的 STEP，也可在工艺配置中显式提供
`seal`：`center_mm` 为圆柱底面中心，`radius_mm` 为半径，`height_mm` 为沿 +Z 的高度。
这些数值必须对应本次封口方案；上游默认的开放抽气孔不能因为没有材料间隙就跳过密封检查。

## 在 Swagger 中调用

1. `GET /api/configs/default` → `Try it out` → `Execute`，取得完整默认配置。
2. `POST /api/configs`，填写 `name`，将返回的 `config` 对象放入请求并调整温压曲线及材料数据，保存后得到配置 `id`。
3. `POST /api/inputs`，上传 `cavity` 和 `capsule` 两份 STEP，得到输入 `id`。
4. `POST /api/jobs`，填写 `input_id` 和 `config_id`，执行后立即返回任务 `id` 和 `status_url`。
5. `GET /api/jobs/{job_id}` 查看状态。状态成为 `completed` 或 `failed` 后，打开返回的 `report_url`。

无需上传时，也可在任务中提供 API 服务所在机器上的 `cavity_path` 和 `capsule_path` 绝对路径。
Swagger 中有“本地路径与内联配置”和“上传输入与配置 ID”两种请求示例。
请删去不用的输入来源，不要同时提供 `input_id` 和文件路径；也不要同时提供 `config` 和 `config_id`。

## 可配置数据

沿用 `SimulationConfig`，所有字段及取值限制会展示在 Swagger Schemas 中。

| 字段 | 含义 |
| --- | --- |
| `cycle[]` | 时间点数组，每点包含 `time_s`、`temperature_c`、`pressure_mpa`；点间线性插值 |
| `powder.initial_relative_density` | TC4 粉末初始相对密度，必须大于 0 且不超过 1 |
| `powder.limiting_relative_density` | 致密化上限，不得低于初始密度 |
| `powder` 其余字段 | 全致密密度、冷/热弹性模量、泊松比、热膨胀、致密化速率、活化能、压力指数等 |
| `capsule` | 包套材料冷/热弹性模量、泊松比、热膨胀、松弛时间和活化能等 |
| `solver` | 网格尺寸、时间步、最大步数及求解提示阈值 |
| `tolerances` | 普通/指定角度区域公差与表面采样设置 |
| `seal` | 显式圆柱封口；已密封 STEP 可为 `null` |

单位为 mm、MPa、s、摄氏度；全致密质量密度为 kg/mm³。
时间从 0 开始且严格递增，初始表压为 0，温度必须高于绝对零度；非有限值、未知字段、
非法相对密度和超过最大步数的曲线都会在入队前返回 HTTP 422。

每次任务必须提供 `config` 或 `config_id`。`config` 可覆盖模型中的部分字段，其余字段使用明确的默认值；
推荐从默认接口取得完整参数再修改。响应和 `config.resolved.json` 都包含最终完整配置。
提交时复制 STEP 与配置快照；之后 `PUT` 修改配置不会影响已提交任务。

默认材料仍是未经标定的演示数据。修改材料名称或数值不会自动把模型变成已标定材料模型。

## 接口表

| 方法和路径 | 用途 |
| --- | --- |
| `GET /api/configs/default` | 完整默认参数与单位 |
| `POST /api/configs` | 创建配置，返回 201 |
| `GET /api/configs` | 查询配置列表 |
| `GET /api/configs/{config_id}` | 查询某份配置 |
| `PUT /api/configs/{config_id}` | 替换配置，已有任务保持快照 |
| `POST /api/inputs` | multipart 上传两份 STEP，每份最多 100 MiB |
| `POST /api/jobs` | 创建异步任务，返回 202 |
| `GET /api/jobs` | 查询任务列表，按创建时间倒序 |
| `GET /api/jobs/{job_id}` | 状态、配置快照、结构化错误和结果 URL |
| `GET /api/jobs/{job_id}/report` | 成功/失败 HTML 报告；尚未完成返回 409 |
| `GET /api/jobs/{job_id}/artifacts/{name}` | 下载允许的模型、配置、数值文件及日志 |

参数错误返回 422，未知 ID 或结果文件返回 404。202 仅代表已受理，不表示几何通过或求解成功。
状态依次为 `queued`、`running`，最后为 `completed` 或 `failed`。
正常停止会终止进行中的求解；异常停止后进行中的任务会标为 `interrupted`，需重新提交。
求解子进程通过父进程管道受监督；工作中输出在独立目录，完成后才发布到任务结果目录。

## 在 CAD 项目中自动调用

可在上游任务完成并写出 `result.json` 后调用。下面示例在调用方环境安装 `httpx` 即可；
CAD 解释器不需要安装 HIPForm 或求解依赖。仿真服务需已启动。

```python
import json
from pathlib import Path
import time

import httpx


def verify_cad_output(cad_result_path, process_config):
    cad = json.loads(Path(cad_result_path).read_text(encoding="utf-8-sig"))
    if cad.get("status") != "completed":
        raise ValueError("CAD task did not complete")
    attempt = Path(cad["output_dir"])
    validation = json.loads((attempt / "validation.json").read_text(encoding="utf-8-sig"))
    if not validation.get("ok"):
        raise ValueError("CAD geometry validation failed")

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=120) as client:
        with (attempt / "cavity.step").open("rb") as cavity, (attempt / "capsule.step").open("rb") as capsule:
            response = client.post("/api/inputs", files={
                "cavity": ("cavity.step", cavity), "capsule": ("capsule.step", capsule)})
        response.raise_for_status()
        response = client.post("/api/jobs", json={
            "name": attempt.name, "input_id": response.json()["id"], "config": process_config})
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 7200
        while job["status"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Polling stopped; job continues at {job['status_url']}")
            time.sleep(2)
            response = client.get(job["status_url"])
            response.raise_for_status()
            job = response.json()
        print("Report:", "http://127.0.0.1:8000" + job["report_url"])
        return job
```

`process_config` 是已选定的工艺/材料参数字典。也可以先保存配置，然后把任务中的 `config`
替换为 `config_id`。示例展示接入位置与 HTTP 调用，本次没有改动上游仓库的 `run_job.py`。
本地路径方式会保留并校验同名 `.provenance.json`；上传方式只上传这两个 STEP，
如需携带合成几何来源信息，应使用保留 sidecar 的同机路径方式。

## 报告与“还原零件”输出

任务的 `report_url` 可直接在浏览器打开。成功时有交互式三维预测、偏差和温压/密度曲线；
失败时展示原始错误、错误代码和阶段，不生成虚假的预测结论。
入队前被拒绝的请求只有 HTTP 错误响应，不会创建任务报告。

成功任务返回的 `artifacts` 包含：

- `predicted-powder.stl`：预测的粉末成形表面，即当前模型能输出的零件几何。
- `predicted-assembly.vtu`：粉末与包套预测体网格。
- `history.csv`：温度、压力、相对密度等完整历程。
- `config.resolved.json`：本次完整工艺/材料参数。
- `comparison.json`、`solver.json`、`result.json`：公差、求解及状态数据。

失败任务只提供失败报告、结果、已生成的配置和日志，不将中间模型当成有效预测供下载。
当前模型不是根据包套逆向唯一求出成品设计，也不重建参数化 STEP；它需要明确的粉末域、
材料和工艺输入，输出既有小应变模型下的预测 STL/VTU。
公差比较对象仍是预测终态与初始型腔，尚未加入独立目标 STEP 比较。

`status: completed` 表示数值流程完成。必须同时看 `result.sampled_tolerance_status`、
`result.model_validity` 和警告。`engineering_acceptance` 保持 `not_assessed`。
结果不会自动发布到 GitHub Pages；需要公开展示时另走已有 `hipform publish` 流程。
