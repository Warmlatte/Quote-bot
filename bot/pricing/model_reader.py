"""
bot/pricing/model_reader.py

讀取 STL/OBJ 3D 模型，回傳體積（ml）與件數（body_count）。

業務規則：
- 禁止使用 mesh.body_count，改用 mesh.split(only_watertight=False) 計件
- 以 len(faces) >= 100 過濾雜訊碎片，取有效連通分量數作為 body_count
- 體積單位：trimesh 回傳 mm³，需 ÷1000 轉換為 ml
- 非 watertight 模型不視為損毀；僅載入失敗或所有分量被過濾時加入 error_files
- CPU 密集的 trimesh 計算透過 asyncio.run_in_executor 執行，不阻塞事件迴圈
"""

import asyncio
import pathlib
from dataclasses import dataclass
from typing import Union

import trimesh
import trimesh.repair


@dataclass(frozen=True)
class ModelReadResult:
    """單一模型檔案的讀取結果。"""

    filename: str
    volume_ml: float
    body_count: int


def _load_model_sync(path: pathlib.Path) -> ModelReadResult:
    """
    同步讀取並解析模型檔案（供 run_in_executor 使用）。

    Args:
        path: 模型檔案路徑

    Returns:
        ModelReadResult

    Raises:
        ValueError: 若無法解析為 Trimesh 物件，或所有連通分量均被面數過濾器移除
        Exception: trimesh 拋出的任何其他錯誤
    """
    # force='mesh' 確保回傳單一 Trimesh 物件
    mesh = trimesh.load(str(path), force="mesh")

    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"無法解析為 Trimesh 物件：{path.name}")

    # 禁止使用 mesh.body_count；改用 split + faces >= 100 過濾雜訊碎片
    parts = mesh.split(only_watertight=False)
    real = [p for p in parts if len(p.faces) >= 100]
    body_count = len(real)

    if body_count == 0:
        raise ValueError(f"所有連通分量均被面數過濾器移除（無有效幾何）：{path.name}")

    # 修正每個 part 的法線方向（解決多件 STL 合併導致的混合 winding 問題）
    # 混合法線會使有號體積相消歸零；fix_normals 使所有 faces 指向一致方向
    for part in real:
        try:
            trimesh.repair.fix_normals(part)
        except Exception:
            pass  # 修正失敗視為 non-fatal；abs() 仍可處理全反轉情況

    # 各件獨立計算體積再加總；abs() 處理 trimesh 可能回傳負值；÷1000 轉換 mm³ → ml
    volume_ml = sum(abs(p.volume) for p in real) / 1000.0

    return ModelReadResult(
        filename=path.name,
        volume_ml=float(volume_ml),
        body_count=body_count,
    )


async def read_models(
    paths: list[Union[pathlib.Path, str]],
) -> tuple[list[ModelReadResult], list[str]]:
    """
    非同步讀取多個 3D 模型檔案。

    trimesh 計算透過 asyncio.run_in_executor 移至背景執行緒，
    避免阻塞 Discord 事件迴圈。

    Args:
        paths: 模型檔案路徑列表（pathlib.Path 或字串）

    Returns:
        (results, error_files)
        - results: 成功解析的 ModelReadResult 列表
        - error_files: 解析失敗的檔案名稱列表
    """
    results: list[ModelReadResult] = []
    error_files: list[str] = []

    loop = asyncio.get_running_loop()

    for raw_path in paths:
        path = pathlib.Path(raw_path)
        try:
            result = await loop.run_in_executor(None, _load_model_sync, path)
            results.append(result)
        except Exception as _exc:
            import traceback as _tb
            print(f"[DEBUG model_reader] {path.name}: {type(_exc).__name__}: {_exc}")
            _tb.print_exc()
            error_files.append(path.name)

    return results, error_files
