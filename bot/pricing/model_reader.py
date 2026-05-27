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
from typing import Literal, Union

import trimesh

ErrorKind = Literal["too_small", "wrong_format", "load_failed"]


@dataclass(frozen=True)
class ModelLoadError:
    """單一模型檔案的讀取失敗結果。"""

    filename: str
    kind: ErrorKind
    detail: str  # 中文說明，顯示於 Discord embed


@dataclass(frozen=True)
class ModelReadResult:
    """單一模型檔案的讀取結果。"""

    filename: str
    volume_ml: float
    body_count: int
    warning: str | None = None  # 非 None 時顯示 ⚠️ 警告行


def _load_model_sync(path: pathlib.Path) -> "ModelReadResult | ModelLoadError":
    """
    同步讀取並解析模型檔案（供 run_in_executor 使用）。

    所有錯誤路徑以 ModelLoadError 回傳值表達，不拋出例外。
    """
    try:
        mesh = trimesh.load(str(path), force="mesh")
    except Exception:
        return ModelLoadError(
            filename=path.name,
            kind="load_failed",
            detail="載入失敗，檔案可能損毀",
        )

    if not isinstance(mesh, trimesh.Trimesh):
        return ModelLoadError(
            filename=path.name,
            kind="wrong_format",
            detail="非 3D 網格格式",
        )

    # 禁止使用 mesh.body_count；改用 split + faces >= 100 過濾雜訊碎片
    parts = mesh.split(only_watertight=False)
    real = [p for p in parts if len(p.faces) >= 100]
    body_count = len(real)

    if body_count == 0:
        return ModelLoadError(
            filename=path.name,
            kind="too_small",
            detail="幾何過小（無有效連通分量），可能為空模型或雜訊",
        )

    # 體積 mm³ → ml（÷ 1000）；abs() 處理各分量可能回傳負值的情況
    volume_ml = sum(abs(p.volume) for p in real) / 1000.0

    warnings: list[str] = []
    if volume_ml == 0.0:
        warnings.append("體積計算結果為零，模型法線可能不一致，建議人工確認")
    size = path.stat().st_size
    if size > 500_000_000:
        warnings.append(f"檔案過大（{size // 1_000_000} MB），估算體積僅供參考")

    return ModelReadResult(
        filename=path.name,
        volume_ml=float(volume_ml),
        body_count=body_count,
        warning="；".join(warnings) if warnings else None,
    )


async def read_models(
    paths: list[Union[pathlib.Path, str]],
) -> tuple[list[ModelReadResult], list[ModelLoadError]]:
    """
    非同步讀取多個 3D 模型檔案。

    trimesh 計算透過 asyncio.run_in_executor 移至背景執行緒，
    避免阻塞 Discord 事件迴圈。

    Args:
        paths: 模型檔案路徑列表（pathlib.Path 或字串）

    Returns:
        (results, load_errors)
        - results: 成功解析的 ModelReadResult 列表（含 warning 的結果仍放入此列表）
        - load_errors: 解析失敗的 ModelLoadError 列表
    """
    results: list[ModelReadResult] = []
    load_errors: list[ModelLoadError] = []

    loop = asyncio.get_running_loop()

    for raw_path in paths:
        path = pathlib.Path(raw_path)
        result = await loop.run_in_executor(None, _load_model_sync, path)
        if isinstance(result, ModelLoadError):
            load_errors.append(result)
        else:
            results.append(result)

    return results, load_errors
