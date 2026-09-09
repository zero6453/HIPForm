"""Self-contained scientific HTML report; Plotly is embedded for offline use."""

import html
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from .geometry import material_boundary
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
        colorbar={"title": {"text": "偏差 (mm)", "side": "bottom"}, "orientation": "h",
                  "thickness": 12, "len": 0.7, "x": 0.5, "xanchor": "center",
                  "y": -0.08, "yanchor": "top"}, flatshading=True,
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

    def visibility(reference, predicted):
        return [reference, predicted] + ([True] if count == 3 else [])

    figure.update_layout(
        template="plotly_white", font={"family": "Arial, sans-serif", "size": 13},
        margin={"l": 0, "r": 0, "b": 85, "t": 60}, height=610,
        scene={"aspectmode": "data", "xaxis_title": "X (mm)", "yaxis_title": "Y (mm)",
               "zaxis_title": "Z (mm)", "camera": {"eye": {"x": 1.5, "y": 1.5, "z": 1.1}}},
        legend={"orientation": "h", "y": 1.01},
        updatemenus=[{"type": "buttons", "direction": "left", "x": 0, "y": 1.10,
                     "xanchor": "left",
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
    figure.update_layout(template="plotly_white", height=620, margin={"l": 65, "r": 20, "t": 110, "b": 50},
                         legend={"orientation": "h", "x": 0, "xanchor": "left", "y": 1.02, "yanchor": "bottom"},
                         font={"family": "Arial, sans-serif", "size": 13}, hovermode="x unified")
    return figure


def write_report(output_path: Path, mesh: SimulationMesh, result: SimulationResult,
                 comparison: dict, config: dict, *, downloads: dict | None = None) -> None:
    """Write offline plots and escaped provenance, settings, and sampled metrics."""
    responsive_scene = """
    const graph = document.getElementById('{plot_id}');
    const narrow = window.matchMedia('(max-width: 600px)');
    function frameScene() {
        return Plotly.relayout(graph, {
            height: narrow.matches ? 500 : 610,
            'scene.camera.eye': narrow.matches ? {x: 2.5, y: 2.5, z: 1.9} : {x: 1.5, y: 1.5, z: 1.1}
        });
    }
    narrow.addEventListener('change', frameScene);
    return frameScene();
    """
    surface = pio.to_html(_surface_figure(mesh, result, comparison), full_html=False,
                          include_plotlyjs=True, div_id="surface-plot",
                          post_script=responsive_scene,
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


def _material_trace(points, triangles, name, **style):
    nodes, local = np.unique(triangles, return_inverse=True)
    vertices = points[nodes]
    faces = local.reshape(-1, 3)
    return go.Mesh3d(
        x=vertices[:, 0].tolist(), y=vertices[:, 1].tolist(), z=vertices[:, 2].tolist(),
        i=faces[:, 0].tolist(), j=faces[:, 1].tolist(), k=faces[:, 2].tolist(),
        name=name, showlegend=True, flatshading=True, **style,
    )


def _assembly_figure(points, powder_triangles, capsule_triangles, ranges):
    figure = go.Figure([
        _material_trace(points, capsule_triangles, "包套（20号钢）", color="#87939d", opacity=.30),
        _material_trace(points, powder_triangles, "粉末（TC4）", color="#148a82"),
    ])
    figure.update_layout(
        template="plotly_white", height=560, margin={"t": 20, "l": 0, "r": 0, "b": 20},
        legend={"orientation": "h", "y": 1.05},
        scene={"aspectmode": "data", "dragmode": "orbit", **{
            f"{axis}axis": {"title": f"{axis.upper()} mm", "range": limits}
            for axis, limits in zip("xyz", ranges, strict=True)
        }},
    )
    return figure


def _target_difference_figure(mesh, result, comparison, target):
    field = np.asarray(comparison["fields"]["predicted_triangle_signed_mm"])
    extent = max(float(np.abs(field).max()), 1e-9)
    figure = go.Figure()
    for name, points, triangles, style in (
        ("目标 cavity.step", target[0], target[1], {"color": "#8899aa", "opacity": .28}),
        ("预测成型粉末", mesh.points + result.displacement, mesh.powder_triangles,
         {"intensity": field, "intensitymode": "cell", "colorscale": "RdBu", "cmin": -extent,
          "cmax": extent, "colorbar": {"title": "有符号偏差 mm", "orientation": "h", "y": -.08}}),
    ):
        figure.add_trace(_material_trace(points, triangles, name, **style))
    figure.update_layout(template="plotly_white", height=600, margin={"t": 20, "l": 0, "r": 0, "b": 80},
                         legend={"orientation": "h", "y": 1.05},
                         scene={"aspectmode": "data", "dragmode": "orbit", "xaxis_title": "X mm",
                                "yaxis_title": "Y mm", "zaxis_title": "Z mm"})
    return figure


def write_verification_report(output_path, comparison, config, metadata, *, mesh, result, target,
                              contact_assessment):
    """Write offline assembly views, target DIFF and a distinct contact assessment."""
    final_points = mesh.points + result.displacement
    capsule_triangles = material_boundary(mesh, 1)
    bounds = np.vstack((mesh.points, final_points))
    lower, upper = bounds.min(axis=0), bounds.max(axis=0)
    padding = np.maximum(upper - lower, 1.) * .05
    ranges = np.column_stack((lower - padding, upper + padding)).tolist()
    plots = []
    for plot_id, figure in (
        ("assembly-before", _assembly_figure(mesh.points, mesh.powder_triangles, capsule_triangles, ranges)),
        ("assembly-after", _assembly_figure(final_points, mesh.powder_triangles, capsule_triangles, ranges)),
        ("target-diff", _target_difference_figure(mesh, result, comparison, target)),
        ("history-plot", _history_figure(result)),
    ):
        plots.append(pio.to_html(figure, full_html=False, include_plotlyjs=not plots, div_id=plot_id,
                                 config={"responsive": True, "displaylogo": False}))
    before, after, surface, history = plots
    names = {"planar": "平面", "nonplanar": "非平面"}
    rows = []
    for name, region in comparison["regions"].items():
        verdict = {"no_samples": "目标无此类面", "measured": "已测量"}[region["status"]]
        rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            names[name], verdict, _number(region.get("min_signed_mm")), _number(region.get("max_signed_mm")))) + "</tr>")
    contact_rows = []
    for name, region in contact_assessment["regions"].items():
        verdict = {"not_assessed": "未评估", "no_interface": "无此类接触界面"}[region["status"]]
        contact_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            names[name], f"{_number(region['lower_limit_mm'])} ～ {_number(region['upper_limit_mm'])}",
            verdict)) + "</tr>")
    warnings = "".join(f"<li>{_escape(warning)}</li>" for warning in result.warnings + comparison["warnings"])
    details = {"config": config, "metadata": metadata, "geometry": mesh.metadata, "solver": result.metadata,
               "contact_assessment": contact_assessment,
               "comparison": {key: value for key, value in comparison.items() if key != "fields"}}
    validity = "超出小应变或密度适用范围" if result.metadata.get("model_validity") == "outside_small_strain_or_density_range" else "未经材料试验标定"
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>HIPForm 成品验证报告</title>
<style>*{{box-sizing:border-box}}body{{font:15px/1.65 Arial,'PingFang SC',sans-serif;letter-spacing:0;max-width:1100px;margin:24px auto;padding:0 16px;color:#24313e}}
h1{{font-size:28px;line-height:1.3}}h2{{font-size:21px;margin-top:30px}}p,li{{overflow-wrap:anywhere}}.table-wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}}.status{{font-weight:bold;color:#926600}}
@media(max-width:600px){{body{{padding:0 12px}}h1{{font-size:24px}}h2{{font-size:19px}}}}</style></head><body>
<h1>HIPForm 成品验证报告</h1><p class="status">内壁间隙判定：未评估</p>
<p><strong>模型状态：{validity}；工程验收未评估。</strong>终态仍附着包套，未模拟去包套后的应力释放。</p>
<p>材料屈服强度与抗拉强度目前仅作为参考数据，未用于塑性屈服判据；当前模型不能保证重现实物照片中的高温大变形。</p>
<p>包套与粉末均参与有限元求解。粉末域从 capsule.step 提取，成型粉末导出为 {_escape(metadata['predicted_step'])}。</p>
<h2>成型粉末与包套内壁的间隙</h2><p>{_escape(contact_assessment['reason'])}</p>
<p>当前界面约束规定间隙为 {_number(contact_assessment['imposed_interface_gap_mm'])} mm，属于模型假设，不能作为真实间隙测量值或合格依据。</p>
<div class="table-wrap"><table><tr><th>接触界面类型</th><th>间隙要求 mm</th><th>评估状态</th></tr>{''.join(contact_rows)}</table></div>
<h2>B · 烧制前：包套与装填粉末</h2>{before}
<h2>C · 烧制后：包套与成型粉末</h2>{after}
<p>两种材料均使用求解器的同一终态位移场，变形比例为 1，前后坐标范围一致。C 为本次计算结果，未按示意照片重塑。
虚拟封口保留在仿真包套中；允许的抽气管残余空间不计为粉末与内壁的接触间隙。</p>
<h2>原型 DIFF：目标 cavity.step 与预测成型粉末</h2>
<p>负值表示相对目标欠尺寸，正值表示余量。平面与非平面按目标 CAD 面类型分类；固定原坐标，无对齐或缩放。
原型 DIFF 只报告尺寸偏差；上方间隙要求不用于原型 DIFF 的通过或失败判定。</p>
<div class="table-wrap"><table><tr><th>目标面类型</th><th>测量状态</th><th>最小偏差 mm</th><th>最大偏差 mm</th></tr>{''.join(rows)}</table></div>
<p>表面采样间距 {_number(comparison['sampling']['spacing_mm'])} mm。STEP 为完整预测网格的三角面 BREP，未简化表面。
采样结果不证明连续 CAD 公差极值；本次为流程验证，未完成网格收敛验证。</p>
{surface}<h2>温度、压力与密度历程</h2>{history}
<h2>上游包套设计反馈</h2><p>当前模型不能评估内壁分离间隙，尚不能提供基于间隙超限的包套修正量。
原型 DIFF 可供检查目标符合程度；后续需用支持接触分离的模型评估间隙，并用材料试验标定收缩行为。</p>
<h2>模型限制</h2><ul>{warnings}</ul>
<details><summary>配置、输入哈希、封口、DIFF 与间隙评估记录</summary><pre>{_json(details)}</pre></details>
</body></html>"""
    Path(output_path).write_text(document, encoding="utf-8")
