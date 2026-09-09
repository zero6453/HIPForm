# HIPForm HTTP API 与 Swagger 使用说明

HIPForm 直接读取服务工作目录中的两份文件：`cavity.step` 是目标成品，`capsule.step` 是包套壁和抽气管材料实体。调用任务接口只需工艺配置，不需要上传文件、输入 ID 或逐次指定 STEP 路径，也不读取 `assembly.step`。

## 启动

先在 HIPForm 项目目录执行 `uv sync --locked`。随后进入包含两份 STEP 的目录，使用已安装的 `hipform serve` 启动服务。例如本机：

```bash
cd /Users/zeroduan/Documents/Codex/freecad
/Users/zeroduan/Documents/GitHub/HIPForm/.venv/bin/hipform serve --port 8000
```

- Swagger：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>
- 健康检查：<http://127.0.0.1:8000/health>

服务启动时确定输入目录。之后每次提交都对其中的 `cavity.step`、`capsule.step` 保存独立快照；已经提交的任务不受源文件后续修改影响。部署时可用 `--input-dir` 固定共享输入目录，这属于服务配置，不是每次请求的参数。不要在任务受理完成前替换正在读取的文件。

默认结果目录为服务工作目录下的 `simulation-runs/api`，可通过 `--data-dir` 配置。默认求解超时为 3600 秒，可通过 `--job-timeout` 配置。服务只绑定回环地址，单进程串行求解；每个结果目录只允许一个服务使用。

## 最小调用

```json
{
  "name": "当前包套验证",
  "config": {}
}
```

在 Swagger 打开 `POST /api/jobs` → `Try it out`，填入上面的内容并执行。`config: {}` 使用当前默认材料与工艺；也可以填写完整或部分配置。

任务返回 HTTP 202 和 `id`、`status_url`。用 `GET /api/jobs/{job_id}` 查询，完成后打开返回的 `report_url`。完整报告的磁盘位置为：

```text
simulation-runs/api/jobs/<job_id>/run/report.html
```

如果使用已保存参数，提交 `{"config_id": "配置接口返回的32位ID"}`。每次必须在 `config` 和 `config_id` 中选择一个。旧的 `input_id`、`cavity_path`、`capsule_path` 和 `workflow` 请求字段会返回 422；上传接口已移除。

## 配置接口

`GET /api/configs/default` 返回全部默认参数、单位和未标定状态。可修改后用 `POST /api/configs` 保存，用 `PUT /api/configs/{id}` 更新；历史任务保持提交时的配置快照。

| 数据 | 默认值或说明 |
| --- | --- |
| 包套 | 20号钢；屈服强度 250 MPa，抗拉强度 400 MPa |
| 粉末 | TC4；烧结态屈服强度 800 MPa，抗拉强度 950 MPa |
| 初始相对密度 | 0.65 |
| 目标密度参考 | 0.97 |
| 体积收缩经验参考 | 0.30 |
| 保温保压 | 900℃、120 MPa、10800秒 |
| `cycle[]` | 每个时间点含 `time_s`、`temperature_c`、`pressure_mpa`，点间线性插值 |
| `powder`、`capsule` | 材料弹性、热膨胀、致密化和松弛参数 |
| `solver` | 网格尺寸、时间步、最大步数 |
| `tolerances` | DIFF 采样设置；`flat_mm` / `angular_mm` 为烧后粉末到包套内壁的平面 / 非平面间隙上限，默认 10 / 20 mm |

单位为 mm、MPa、s、℃；质量密度为 kg/mm³。完整配置以 Swagger 和 `config.resolved.json` 为准。

屈服强度、抗拉强度与经验致密度是已记录的材料和工艺参考，不能据此自动标定高温本构。0.65 到 0.97 的质量守恒体积收缩约为 33%，30% 是经验参考，程序不强行同时满足两项数值。升温、降温段也属于可配置假设。

## 几何处理与判定

1. 从 `capsule.step` 独立提取主粉末域，目标成品不参与粉末域生成。
2. 识别支持的直圆抽气管，在仿真副本的管口虚拟封口，保留原包套几何；主粉末域不包含残余管腔。
3. 检查材料连通、粉末与包套贴合和密封；不支持或有歧义的几何会给出失败报告。
4. 共同求解包套与粉末的位移，输出成型粉末 `cavity+<job_id>.step`，与目标 `cavity.step` 在原坐标系比较，不做自动配准或缩放。

目标 DIFF 只测量有符号偏差：预测零件比目标小为负，比目标大为正。`comparison.json.status` 为 `measured`，不将目标尺寸偏差与间隙阈值比较，也不从 DIFF 自动推导包套修正量。

平面 0～10 mm、非平面 0～20 mm 指**烧制后粉末表面到包套内壁的间隙**，不是到包套外壁的厚度，也不是到目标成品的距离。当前绑定界面共享节点，两材料接触处的间隙被强制为零，无法评估真实分离；允许保留的抽气管残余空间不属于此材料接口。`contact-assessment.json` 因此返回 `status: not_assessed` 和 `reason_code: bonded_interface_prevents_separation`。需要有限应变接触求解及高温材料标定后，才能使用这些阈值作实际判定。

必须区分以下状态：

| 字段 | 含义 |
| --- | --- |
| `status: completed` | 数值流程完成，有可查看的预测结果 |
| `result.validation_status: not_assessed` | 当前模型不能评估真实烧后间隙 |
| `comparison.status: measured` | 目标 DIFF 数值测量完成，无通过/失败判定 |
| `contact_assessment.status: not_assessed` | 绑定界面不能预测分离间隙，详见 `reason_code` |
| `status: failed` | 几何、配置、求解、超时或服务中断导致流程失败 |
| `engineering_acceptance: not_assessed` | 未做工程验收；当前模型未经标定 |

计算完成后保留预测 STEP、DIFF、间隙评估说明和报告；`not_assessed` 不能视为通过。执行失败时保留错误报告及日志，不将中间模型作为有效预测下载。初始粉末与包套材料不连接时错误消息为 `Assembly contains disconnected material bodies in the mesh.`，这与烧制后的分离评估是不同检查。

## 接口表

| 方法和路径 | 用途 |
| --- | --- |
| `GET /api/configs/default` | 完整默认配置与单位 |
| `POST /api/configs` | 保存配置 |
| `GET /api/configs` | 配置列表 |
| `GET /api/configs/{config_id}` | 查询配置 |
| `PUT /api/configs/{config_id}` | 更新配置 |
| `POST /api/jobs` | 使用固定两份文件创建异步验证任务 |
| `GET /api/jobs` | 任务列表 |
| `GET /api/jobs/{job_id}` | 状态、配置快照、结果与下载链接 |
| `GET /api/jobs/{job_id}/report` | HTML 报告 |
| `GET /api/jobs/{job_id}/artifacts/{name}` | 允许下载的结果文件 |

缺少输入文件或配置非法会在入队前返回 422；未知 ID 返回 404。未完成任务的报告返回 409。浏览器写入请求须与服务同源；普通 Python 客户端无需 Origin。服务中断会终止计算，重启后相关任务标为 `interrupted`。

## 上游自动调用

上游生成成功后，将这次的 `cavity.step` 和 `capsule.step` 写入双方约定的共享目录，再调用一次任务接口。以下函数假设两份文件已经写好且 HIPForm 服务已经启动：

```python
import time
import httpx


def verify_current_pair(process_config=None):
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=120) as client:
        response = client.post("/api/jobs", json={
            "name": "CAD 包套验证",
            "config": {} if process_config is None else process_config,
        })
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 7200
        while job["status"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"请继续查询 {job['status_url']}")
            time.sleep(2)
            response = client.get(job["status_url"])
            response.raise_for_status()
            job = response.json()
        print("Report:", "http://127.0.0.1:8000" + job["report_url"])
        return job
```

当前仓库已实现该调用契约；没有修改上游仓库，也没有自动监听上游生成事件。需要将调用放到上游的成功分支。若上游仍使用独立 `attempt-N` 目录，由上游在调用前将本次两份文件放到固定共享目录。

## 结果文件

`artifacts` 返回本任务可下载文件的 URL，包括 `cavity+<job_id>.step`、预测 STL/VTU、`report.html`、`history.csv`、`comparison.json`、`contact-assessment.json`、`upstream-advice.json`、`solver.json`、`result.json` 和 `config.resolved.json`。当前 `upstream-advice.json` 返回 `status: not_assessed`、`recommendations: []` 及 `blocked_reason`，说明缺少可评估分离的接触模型与材料标定。调用方不应据绑定的零间隙或目标 DIFF 自动重生成包套。

预测 STEP 是成型粉末有限元表面的三角面 BREP，不是解析 CAD 特征重建；其位置对应包套仍附着的终态，未模拟去包套后的应力释放。报告显示烧制前后包套与粉末、目标 DIFF、温压密度曲线及模型限制，应结合模型适用性、网格和采样误差阅读。结果不会自动发布到 GitHub Pages。
