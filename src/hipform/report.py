"""Self-contained scientific HTML report; Plotly is embedded for offline use."""

import html
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from .types import SimulationMesh, SimulationResult


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _json(value) -> str:
    def convert(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Unsupported report metadata type: {type(item).__name__}")

    return _escape(json.dumps(value, ensure_ascii=False, indent=2, default=convert, allow_nan=False))


def _number(value) -> str:
    return "未分配" if value is None else f"{float(value):.3f}"


def _surface_figure(mesh, result, comparison):
    points = np.asarray(mesh.points, dtype=float)
    displacement = np.asarray(result.displacement, dtype=float)
    if points.shape != displacement.shape or not np.isfinite(displacement).all():
        raise ValueError("Report displacement must be finite and match mesh points")
    predicted = points + displacement
    triangles = np.asarray(mesh.powder_triangles)
    surface_nodes, local_triangles = np.unique(triangles, return_inverse=True)
    points = points[surface_nodes]
    predicted = predicted[surface_nodes]
    triangles = local_triangles.reshape(-1, 3)
    field = np.asarray(comparison["fields"]["predicted_triangle_max_mm"], dtype=float)
    if field.shape != (len(triangles),) or not np.isfinite(field).all():
        raise ValueError("Report comparison field must match powder triangles")
    figure = go.Figure()
    triangle_args = dict(i=triangles[:, 0], j=triangles[:, 1], k=triangles[:, 2])
    figure.add_trace(go.Mesh3d(
        x=points[:, 0], y=points[:, 1], z=points[:, 2], **triangle_args,
        name="初始参考型腔", color="#738495", opacity=0.25, flatshading=True,
        hovertemplate="初始参考型腔<br>x=%{x:.2f} mm<br>y=%{y:.2f} mm<br>z=%{z:.2f} mm<extra></extra>",
    ))
    figure.add_trace(go.Mesh3d(
        x=predicted[:, 0], y=predicted[:, 1], z=predicted[:, 2], **triangle_args,
        name="预测粉末终态", intensity=field, intensitymode="cell", cmin=0,
        cmax=max(float(comparison["global"]["max_mm"]), 1e-9), colorscale="YlOrRd",
        colorbar={"title": "偏差 (mm)", "thickness": 16, "len": 0.75}, flatshading=True,
        hovertemplate="预测粉末终态<br>x=%{x:.2f} mm<br>y=%{y:.2f} mm<br>z=%{z:.2f} mm"
                      "<br>三角形采样最大偏差=%{intensity:.3f} mm<extra></extra>",
    ))
    maximum = comparison["global"]["max_location"]
    if maximum:
        locations = np.array([maximum["point_mm"], maximum["closest_point_mm"]])
        figure.add_trace(go.Scatter3d(
            x=locations[:, 0], y=locations[:, 1], z=locations[:, 2], mode="lines+markers",
            name="双向最大偏差位置", marker={"size": 4, "color": "#9d174d"},
            line={"width": 5, "color": "#9d174d"}, hovertemplate="最大偏差位置<extra></extra>",
        ))
    count = len(figure.data)
    visibility = lambda ref, pred: [ref, pred] + ([True] if count == 3 else [])
    figure.update_layout(
        template="plotly_white", font={"family": "Arial, sans-serif", "size": 13},
        margin={"l": 0, "r": 20, "b": 10, "t": 60}, height=610,
        scene={"aspectmode": "data", "xaxis_title": "X (mm)", "yaxis_title": "Y (mm)",
               "zaxis_title": "Z (mm)", "camera": {"eye": {"x": 1.5, "y": 1.5, "z": 1.1}}},
        legend={"orientation": "h", "y": 1.01},
        updatemenus=[{"type": "buttons", "direction": "left", "x": 0, "y": 1.10,
                     "buttons": [
                         {"label": "参考与预测叠加", "method": "restyle",
                          "args": [{"visible": visibility(True, True), "opacity": [0.25, 1., 1.][:count]}]},
                         {"label": "初始参考", "method": "restyle",
                          "args": [{"visible": visibility(True, False), "opacity": [1., 1., 1.][:count]}]},
                         {"label": "预测终态", "method": "restyle",
                          "args": [{"visible": visibility(False, True), "opacity": [0.25, 1., 1.][:count]}]},
                     ]}],
    )
    return figure


def _history_figure(result):
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.09)
    history = result.history
    time = [row["time_s"] for row in history]
    for row, key, name, color, dash in (
        (1, "temperature_c", "温度", "#c2410c", "solid"),
        (2, "pressure_mpa", "压力", "#0369a1", "solid"),
        (3, "prescribed_relative_density", "规定自由收缩密度", "#7c3aed", "dash"),
        (3, "mean_relative_density", "有限元体积反算密度", "#15803d", "solid"),
    ):
        if all(key in entry for entry in history):
            figure.add_trace(go.Scatter(
                x=time, y=[entry[key] for entry in history], name=name, mode="lines+markers",
                line={"color": color, "dash": dash}, marker={"size": 3},
            ), row=row, col=1)
    figure.update_yaxes(title_text="温度 (°C)", row=1, col=1)
    figure.update_yaxes(title_text="压力 (MPa)", row=2, col=1)
    figure.update_yaxes(title_text="相对密度", row=3, col=1)
    figure.update_xaxes(title_text="时间 (s)", row=3, col=1)
    figure.update_layout(template="plotly_white", height=570, margin={"l": 65, "r": 20, "t": 35, "b": 50},
                         legend={"orientation": "h", "y": 1.1},
                         font={"family": "Arial, sans-serif", "size": 13}, hovermode="x unified")
    return figure


def write_report(output_path: Path, mesh: SimulationMesh, result: SimulationResult,
                 comparison: dict, config: dict, *, downloads: dict | None = None) -> None:
    """Write offline plots and escaped provenance, settings, and sampled metrics."""
    surface = pio.to_html(_surface_figure(mesh, result, comparison), full_html=False,
                          include_plotlyjs=True, div_id="surface-plot",
                          config={"responsive": True, "displaylogo": False})
    history = pio.to_html(_history_figure(result), full_html=False,
                          include_plotlyjs=False, div_id="history-plot",
                          config={"responsive": True, "displaylogo": False})
    exceeded = comparison["status"] == "exceeds_limits"
    status = "采样表面超出限值" if exceeded else "采样表面满足限值"
    synthetic = bool(mesh.metadata.get("synthetic_capsule") or mesh.metadata.get("synthetic_example"))
    provenance = ("合成包套示例：由型腔外扩包围盒减去原型腔构造演示包套，并非用户实际包套。"
                  if synthetic else "几何来源与输入标识见下方记录。")
    validity = {"outside_small_strain_or_density_range": "超出小应变或密度适用范围，预测值仅供数值演示",
                "uncalibrated": "模型未经标定"}.get(result.metadata.get("model_validity"), "模型适用性未评估")
    final_state = "包套仍附着，尚未模拟脱模或去包套" if result.metadata.get("final_state") == "capsule_attached" else "终态包套状态未提供"
    volume_error = mesh.metadata.get("relative_volume_error", {}).get("powder")
    volume_summary = "未提供" if volume_error is None else f"{float(volume_error) * 100:.3f}%"
    metrics = comparison["global"]
    rows = []
    names = {"flat": "普通 / 未分配面", "angular": "显式指定角部面"}
    for region_name, region in comparison["regions"].items():
        region_status = {"no_samples": "无指定面", "exceeds_limits": "超限",
                         "within_limits_on_sampled_surface": "采样通过"}.get(region["status"], region["status"])
        rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            names.get(region_name, region_name), ", ".join(map(str, region["face_ids"])) or "无",
            _number(region["tolerance_mm"]), _number(region["max_mm"]),
            _number(region["p95_mm"]), _number(region["rms_mm"]), region_status)) + "</tr>")
    warnings = "".join(f"<li>{_escape(item)}</li>" for item in [*result.warnings, *comparison["warnings"]])
    details = {"mesh": mesh.metadata, "solver": result.metadata, "resolved_config": config,
               "comparison": {key: value for key, value in comparison.items() if key != "fields"}}
    download_section = ""
    if downloads:
        links = "".join(f'<a href="{_escape(name)}" download>{_escape(label)}</a>'
                        for name, label in downloads.items())
        download_section = f'<h2>结果下载</h2><nav class="downloads" aria-label="结果下载">{links}</nav>'
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HIPForm | 成形预测与公差报告</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;color:#20242a;background:#fff;font:15px/1.6 Arial,"PingFang SC",sans-serif;letter-spacing:0}}
main{{max-width:1200px;margin:auto;padding:24px}}h1{{font-size:28px;line-height:1.3;margin:0 0 12px}}h2{{font-size:20px;margin:28px 0 10px}}
p{{margin:8px 0}}.notice{{border-left:4px solid #ca8a04;padding-left:14px}}.status{{color:{'#b91c1c' if exceeded else '#166534'};font-weight:700}}
.metrics{{display:flex;flex-wrap:wrap;gap:20px 48px;border-block:1px solid #d8dde2;margin:20px 0;padding:14px 0}}
.metrics strong{{display:block;font-size:25px;font-variant-numeric:tabular-nums}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:9px 12px;border-bottom:1px solid #d8dde2;text-align:left}}th{{background:#f3f5f7}}td:nth-child(2){{white-space:normal;max-width:240px;min-width:110px;overflow-wrap:anywhere}}.caption{{color:#535b65;font-size:13px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f3f5f7;padding:16px}}details{{margin:20px 0}}li{{overflow-wrap:anywhere}}
a{{color:#0369a1;text-underline-offset:3px}}.downloads{{display:flex;flex-wrap:wrap;gap:12px 24px}}.project-links{{display:flex;flex-wrap:wrap;gap:18px;margin-bottom:18px;font-size:13px}}
@media(max-width:600px){{main{{padding:14px}}h1{{font-size:24px}}.metrics{{gap:12px 24px}}.metrics strong{{font-size:22px}}}}
</style></head><body><main>
<nav class="project-links" aria-label="项目"><a href="https://github.com/zero6453/HIPForm">GitHub</a><a href="https://github.com/zero6453/HIPForm/blob/main/docs/simulation.md">模型与参数</a></nav>
<h1>HIPForm</h1><p>成形预测与公差报告 · V0.1</p>
<div class="notice"><p><strong>理想参数 / 未经标定</strong> · 工程验收：未评估</p><p>{_escape(provenance)}</p></div>
<p class="status">{status}</p>
<p><strong>{_escape(validity)}</strong>。{_escape(final_state)}。</p>
<p class="caption">粉末初始网格相对 CAD 体积误差：{_escape(volume_summary)}。</p>
<div class="metrics"><div>双向采样最大偏差<strong>{_number(metrics['max_mm'])} mm</strong></div>
<div>P95 偏差<strong>{_number(metrics['p95_mm'])} mm</strong></div>
<div>RMS 偏差<strong>{_number(metrics['rms_mm'])} mm</strong></div></div>
<p>固定源 CAD 坐标，无对齐、缩放或最佳拟合。所有未显式指定的参考面使用严格普通区域限值。</p>
<h2>参考型腔与预测粉末表面</h2>{surface}
<p class="caption">初始参考型腔为灰色透明面；预测粉末表面按各三角形采样点到参考三角面的最大绝对距离着色，单位 mm。
坐标轴等比例；标记连线表示双向采样最大偏差。该预测是加载历程最终时刻的形状。</p>
<h2>区域限值检查</h2><div class="table-wrap"><table><thead><tr><th>区域</th><th>参考面 ID</th><th>限值 mm</th><th>最大 mm</th><th>P95 mm</th><th>RMS mm</th><th>采样判定</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="caption">双向点到三角形距离；反向样本由最近参考三角形归属区域。采样间距 {_number(comparison['sampling']['spacing_mm'])} mm，
参考 / 预测采样数 {_escape(comparison['sampling']['reference_count'])} / {_escape(comparison['sampling']['predicted_count'])}。
P95 和 RMS 使用等样本权重；采样结果与网格相关，不证明连续 CAD 表面的真实最大偏差，也不构成工程验收。</p>
<h2>加载历程与致密化</h2>{history}
<p class="caption">规定自由收缩密度由理想动力学给出；有限元体积反算密度反映包套约束后的粉末体积变化。</p>
{download_section}
<h2>模型限制与计算提示</h2><ul>{warnings}</ul>
<details><summary>输入来源、完整参数与数值记录</summary><pre>{_json(details)}</pre></details>
</main></body></html>"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
