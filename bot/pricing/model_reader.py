"""
bot/pricing/model_reader.py

讀取 STL/OBJ 3D 模型，回傳體積（ml）與件數（body_count）。

業務規則：
- 禁止使用 mesh.split()，改用 mesh.body_count 計件
- 體積單位：trimesh 回傳 mm³，需 ÷1000 轉換為 ml
- 非 watertight 或體積 ≤ 0 的檔案視為損毀，加入 error_files，不中斷流程
- CPU 密集的 trimesh 計算透過 asyncio.run_in_executor 執行，不阻塞事件迴圈
"""

import asyncio
import pathlib
from dataclasses import dataclass
from typing import Union

import trimesh


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
        ValueError: 若檔案損毀、非 watertight 或體積 ≤ 0
        Exception: trimesh 拋出的任何其他錯誤
    """
    # 使用 trimesh.load，force='mesh' 確保回傳單一 Trimesh 物件
    mesh = trimesh.load(str(path), force="mesh")

    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"無法解析為 Trimesh 物件：{path.name}")

    # 體積 mm³ → ml（÷ 1000）；abs() 處理 Linux 上 trimesh 可能回傳負值的情況
    volume_mm3 = abs(mesh.volume)
    if volume_mm3 <= 0:
        raise ValueError(
            f"模型體積 ≤ 0（{volume_mm3:.4f} mm³），可能非 watertight：{path.name}"
        )

    volume_ml = volume_mm3 / 1000.0

    # 使用 body_count 計件，禁止使用 mesh.split()
    body_count = int(mesh.body_count)

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
        except Exception:
            error_files.append(path.name)

    return results, error_files
