"""
tests/test_model_reader.py

TDD 測試：bot.pricing.model_reader 模組
測試 ModelReadResult dataclass 與 read_models() 函式。

業務規則（來自 CLAUDE.md）：
- 禁止使用 mesh.split()，改用 mesh.body_count 計件
- 體積單位：trimesh 回傳 mm³，需 ÷1000 轉換為 ml
- 非 watertight 或體積 ≤ 0 的檔案視為損毀，加入 error_files，不中斷流程
- 計算移至 asyncio.run_in_executor 避免阻塞 Discord 事件迴圈
"""

import io
import math
import pathlib
import struct
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import trimesh

from bot.pricing.model_reader import ModelReadResult, read_models


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_binary_stl(triangles: list[tuple]) -> bytes:
    """產生合法的 binary STL bytes。triangles 為 (normal, v0, v1, v2) 的 list。"""
    header = b"\x00" * 80
    count = struct.pack("<I", len(triangles))
    body = b""
    for normal, v0, v1, v2 in triangles:
        body += struct.pack("<fff", *normal)
        body += struct.pack("<fff", *v0)
        body += struct.pack("<fff", *v1)
        body += struct.pack("<fff", *v2)
        body += struct.pack("<H", 0)  # attribute byte count
    return header + count + body


def _unit_cube_stl() -> bytes:
    """
    製作一個近似 10mm × 10mm × 10mm 立方體的 binary STL。
    使用 trimesh 原生 box 生成，確保 watertight。
    """
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def _make_temp_stl(tmp_path: pathlib.Path, filename: str, content: bytes) -> pathlib.Path:
    p = tmp_path / filename
    p.write_bytes(content)
    return p


# ─── ModelReadResult 單元測試 ─────────────────────────────────────────────────

class TestModelReadResult:
    def test_fields_accessible(self):
        result = ModelReadResult(
            filename="test.stl",
            volume_ml=12.5,
            body_count=3,
        )
        assert result.filename == "test.stl"
        assert result.volume_ml == 12.5
        assert result.body_count == 3

    def test_is_frozen_dataclass(self):
        result = ModelReadResult(filename="x.stl", volume_ml=1.0, body_count=1)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.filename = "y.stl"  # type: ignore[misc]

    def test_volume_ml_float(self):
        result = ModelReadResult(filename="a.stl", volume_ml=0.001, body_count=1)
        assert isinstance(result.volume_ml, float)

    def test_body_count_int(self):
        result = ModelReadResult(filename="b.stl", volume_ml=5.0, body_count=2)
        assert isinstance(result.body_count, int)


# ─── read_models：正常流程 ────────────────────────────────────────────────────

class TestReadModels:
    @pytest.mark.asyncio
    async def test_single_valid_stl(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        results, error_files = await read_models([p])

        assert len(results) == 1
        assert len(error_files) == 0
        result = results[0]
        assert result.filename == "cube.stl"
        # 10×10×10 mm³ = 1000 mm³ = 1.0 ml
        assert math.isclose(result.volume_ml, 1.0, rel_tol=0.05)
        assert result.body_count >= 1

    @pytest.mark.asyncio
    async def test_volume_unit_is_ml_not_mm3(self, tmp_path):
        """確認回傳的是 ml（mm³ ÷ 1000），而非 mm³。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        results, _ = await read_models([p])

        # 10^3 mm³ = 1 ml；若錯誤回傳 mm³ 則值為 1000
        assert results[0].volume_ml < 100  # 合理的 ml 值，不可能是 mm³

    @pytest.mark.asyncio
    async def test_multiple_valid_files(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        files = [
            _make_temp_stl(tmp_path, f"cube_{i}.stl", stl_bytes)
            for i in range(3)
        ]

        results, error_files = await read_models(files)

        assert len(results) == 3
        assert len(error_files) == 0

    @pytest.mark.asyncio
    async def test_empty_file_list(self):
        results, error_files = await read_models([])

        assert results == []
        assert error_files == []

    @pytest.mark.asyncio
    async def test_filename_preserved(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "my_model.stl", stl_bytes)

        results, _ = await read_models([p])

        assert results[0].filename == "my_model.stl"


# ─── read_models：錯誤處理 ────────────────────────────────────────────────────

class TestReadModelsErrorHandling:
    @pytest.mark.asyncio
    async def test_corrupted_file_goes_to_error_files(self, tmp_path):
        """損毀/非 watertight 且體積 ≤ 0 的檔案加入 error_files，不中斷流程。"""
        bad_path = tmp_path / "bad.stl"
        bad_path.write_bytes(b"this is not a valid stl file at all")

        results, error_files = await read_models([bad_path])

        assert len(results) == 0
        assert "bad.stl" in error_files

    @pytest.mark.asyncio
    async def test_mix_valid_and_invalid(self, tmp_path):
        good_stl = _unit_cube_stl()
        good_path = _make_temp_stl(tmp_path, "good.stl", good_stl)

        bad_path = tmp_path / "bad.stl"
        bad_path.write_bytes(b"garbage data")

        results, error_files = await read_models([good_path, bad_path])

        assert len(results) == 1
        assert results[0].filename == "good.stl"
        assert "bad.stl" in error_files

    @pytest.mark.asyncio
    async def test_non_watertight_mesh_goes_to_error(self, tmp_path):
        """非 watertight mesh（體積 ≤ 0）應被視為損毀。"""
        # 製作一個開放的平面 mesh（non-watertight），trimesh 體積為 0
        open_mesh = trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
        )
        buf = io.BytesIO()
        open_mesh.export(buf, file_type="stl")

        bad_path = tmp_path / "open_mesh.stl"
        bad_path.write_bytes(buf.getvalue())

        results, error_files = await read_models([bad_path])

        # 非 watertight 體積 ≤ 0 → error
        assert "open_mesh.stl" in error_files

    @pytest.mark.asyncio
    async def test_error_does_not_stop_remaining_files(self, tmp_path):
        """第一個檔案損毀，後續檔案仍要繼續處理。"""
        bad_path = tmp_path / "first.stl"
        bad_path.write_bytes(b"bad")

        good_stl = _unit_cube_stl()
        good_path = _make_temp_stl(tmp_path, "second.stl", good_stl)

        results, error_files = await read_models([bad_path, good_path])

        assert len(results) == 1
        assert results[0].filename == "second.stl"
        assert "first.stl" in error_files


# ─── read_models：非同步行為 ──────────────────────────────────────────────────

class TestReadModelsAsync:
    @pytest.mark.asyncio
    async def test_returns_coroutine(self, tmp_path):
        """read_models 必須是 async 函式，可被 await。"""
        import inspect
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)
        coro = read_models([p])
        assert inspect.isawaitable(coro), "read_models 應回傳可 await 的 coroutine"
        await coro  # consume it

    @pytest.mark.asyncio
    async def test_uses_executor_for_cpu_bound_work(self, tmp_path):
        """trimesh 計算應透過 run_in_executor 執行，不阻塞事件迴圈。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value = MagicMock()
            mock_loop.return_value.run_in_executor = AsyncMock(
                return_value=ModelReadResult(
                    filename="cube.stl",
                    volume_ml=1.0,
                    body_count=1,
                )
            )
            results, error_files = await read_models([p])

        # 只要 run_in_executor 有被呼叫即可
        mock_loop.return_value.run_in_executor.assert_called()


# ─── body_count：使用 body_count，禁止 split() ───────────────────────────────

class TestBodyCount:
    @pytest.mark.asyncio
    async def test_body_count_single_body(self, tmp_path):
        """單一 watertight 物件應回傳 body_count = 1（或更多，視 trimesh 實作）。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "single.stl", stl_bytes)

        results, _ = await read_models([p])

        assert results[0].body_count >= 1

    @pytest.mark.asyncio
    async def test_model_reader_does_not_call_split(self, tmp_path):
        """確認實作中沒有呼叫 mesh.split()，而是用 mesh.body_count。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        original_load = trimesh.load

        split_called = []

        def mock_load(file_obj, **kwargs):
            mesh = original_load(file_obj, **kwargs)
            # 包裝 split，若被呼叫就記錄
            original_split = getattr(mesh, "split", None)
            if original_split is not None:
                def tracked_split(*a, **kw):
                    split_called.append(True)
                    return original_split(*a, **kw)
                mesh.split = tracked_split
            return mesh

        with patch("trimesh.load", side_effect=mock_load):
            await read_models([p])

        assert len(split_called) == 0, "model_reader 不應呼叫 mesh.split()"
